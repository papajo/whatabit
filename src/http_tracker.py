"""HTTP tracker protocol support.

Many BitTorrent trackers still use HTTP/HTTPS for announce requests.
Implements BEP 0003 HTTP tracker protocol.
"""

import logging
import random
import socket
import struct
import urllib.parse
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


def _parse_compact_peers(data: bytes) -> list[tuple[str, int]]:
    """Parse compact peer list (6 bytes per peer: 4 IP, 2 port)."""
    peers = []
    for i in range(0, len(data), 6):
        if i + 6 > len(data):
            break
        ip_raw, port = struct.unpack(">IH", data[i:i+6])
        ip = socket.inet_ntoa(struct.pack(">I", ip_raw))
        peers.append((ip, port))
    return peers


def _parse_dictionary_peers(data: bytes) -> list[tuple[str, int]]:
    """Parse non-compact (dictionary-style) peer list."""
    from .bencode import decode
    decoded = decode(data)
    peers = []
    if isinstance(decoded, list):
        for peer in decoded:
            ip = peer.get(b"ip", b"").decode("utf-8", errors="replace")
            port = peer.get(b"port", 0)
            if ip and port:
                peers.append((ip, port))
    return peers


def url_quote(text: bytes) -> str:
    """URL percent-encode raw bytes (for info_hash and peer_id in HTTP announce)."""
    return urllib.parse.quote_from_bytes(text)


def build_announce_url(
    announce_url: str,
    info_hash: bytes,
    peer_id: bytes,
    port: int = 6881,
    uploaded: int = 0,
    downloaded: int = 0,
    left: int = 0,
    event: str = "started",
    num_want: int = 200,
    compact: int = 1,
) -> str:
    """Build an HTTP tracker announce URL with query parameters."""
    params = {
        "info_hash": url_quote(info_hash),
        "peer_id": url_quote(peer_id),
        "port": str(port),
        "uploaded": str(uploaded),
        "downloaded": str(downloaded),
        "left": str(left),
        "event": event,
        "num_want": str(num_want),
        "compact": str(compact),
    }
    separator = "&" if "?" in announce_url else "?"
    return announce_url + separator + "&".join(f"{k}={v}" for k, v in params.items())


async def http_announce(
    announce_url: str,
    info_hash: bytes,
    peer_id: bytes,
    port: int = 6881,
    uploaded: int = 0,
    downloaded: int = 0,
    left: int = 0,
    event: str = "started",
    num_want: int = 200,
    timeout: float = 30,
) -> tuple:
    """Send HTTP tracker announce and return (interval, leechers, seeders, peers).
    
    peers is a list of (ip_str, port) tuples.
    
    Raises on connection or protocol errors.
    """
    url = build_announce_url(
        announce_url, info_hash, peer_id, port,
        uploaded, downloaded, left, event, num_want,
    )
    
    from .bencode import decode
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status != 200:
                    raise Exception(f"Tracker returned HTTP {resp.status}")
                
                raw = await resp.read()
                decoded = decode(raw)
                
                if b"failure reason" in decoded:
                    failure = decoded[b"failure reason"].decode("utf-8", errors="replace")
                    raise Exception(f"Tracker failure: {failure}")
                
                interval = decoded.get(b"interval", 1800)
                leechers = decoded.get(b"incomplete", 0)
                seeders = decoded.get(b"complete", 0)
                
                peers_raw = decoded.get(b"peers", b"")
                if isinstance(peers_raw, bytes):
                    peers = _parse_compact_peers(peers_raw)
                elif isinstance(peers_raw, list):
                    # Non-compact peer list
                    peers = []
                    for peer in peers_raw:
                        ip = peer.get(b"ip", b"").decode("utf-8", errors="replace")
                        port = peer.get(b"port", 0)
                        if ip and port:
                            peers.append((ip, port))
                else:
                    peers = []
                
                return (interval, leechers, seeders, peers)
        except Exception as e:
            logger.warning(f"HTTP tracker announce failed for {announce_url}: {e}")
            raise
