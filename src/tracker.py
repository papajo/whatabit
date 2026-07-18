"""UDP Tracker Protocol (BEP 0015).

Communicates with BitTorrent trackers over UDP to get peer lists.
Implements:
  - UDP connect request/response
  - UDP announce request/response
  - Peer list extraction
"""

import random
import socket
import struct
import time
from typing import Optional

# UDP tracker action constants
ACTION_CONNECT = 0
ACTION_ANNOUNCE = 1
ACTION_SCRAPE = 2
ACTION_ERROR = 3

# Announce event constants
EVENT_NONE = 0
EVENT_COMPLETED = 1
EVENT_STARTED = 2
EVENT_STOPPED = 3

# Default timeout for UDP tracker
UDP_TIMEOUT = 15  # seconds
UDP_RETRIES = 2


class TrackerError(Exception):
    """Tracker communication error."""
    pass


def _generate_transaction_id() -> int:
    """Generate a random 32-bit transaction ID."""
    return random.randint(0, 0xFFFFFFFF)


def _send_recv(sock: socket.socket, addr: tuple, data: bytes, timeout: float = UDP_TIMEOUT) -> bytes:
    """Send UDP datagram and receive response with timeout.
    Returns the response bytes or raises on failure/timeout."""
    sock.sendto(data, addr)
    sock.settimeout(timeout)
    try:
        resp, _ = sock.recvfrom(2048)
        return resp
    except socket.timeout:
        raise TrackerError("Tracker UDP request timed out")


def udp_connect(tracker_url: str) -> tuple:
    """Send UDP connect request to tracker.
    
    Args:
        tracker_url: e.g. "udp://tracker.opentrackr.org:1337"
    
    Returns:
        (connection_id_bytes, transaction_id) on success
        
    Raises TrackerError on failure.
    """
    # Parse tracker URL
    if tracker_url.startswith("udp://"):
        tracker_url = tracker_url[6:]
    
    if "/" in tracker_url:
        host_port, _ = tracker_url.split("/", 1)
    else:
        host_port = tracker_url
    
    if ":" in host_port:
        host, port_str = host_port.rsplit(":", 1)
        port = int(port_str)
    else:
        host = host_port
        port = 80
    
    # Resolve hostname
    try:
        ip = socket.gethostbyname(host)
    except socket.gaierror as e:
        raise TrackerError(f"Failed to resolve tracker host {host}: {e}")
    
    addr = (ip, port)
    protocol_id = 0x41727101980  # magic connection ID for connect request
    transaction_id = _generate_transaction_id()
    
    # Build connect request (16 bytes):
    # 8 bytes protocol_id, 4 bytes action=0, 4 bytes transaction_id
    req = struct.pack(">QII", protocol_id, ACTION_CONNECT, transaction_id)
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    try:
        resp = _send_recv(sock, addr, req)
        
        if len(resp) < 16:
            raise TrackerError(f"UDP connect response too short: {len(resp)} bytes")
        
        action, resp_transaction_id, connection_id = struct.unpack(">IIQ", resp[:16])
        
        if action == ACTION_ERROR:
            error_msg = resp[8:].decode("utf-8", errors="replace")
            raise TrackerError(f"Tracker error: {error_msg}")
        
        if resp_transaction_id != transaction_id:
            raise TrackerError("Transaction ID mismatch in connect response")
        
        if action != ACTION_CONNECT:
            raise TrackerError(f"Unexpected action in connect response: {action}")
        
        return (connection_id, transaction_id)
    finally:
        sock.close()


def udp_announce(
    connection_id: int,
    tracker_url: str,
    info_hash: bytes,
    peer_id: bytes,
    downloaded: int = 0,
    left: int = 0,
    uploaded: int = 0,
    event: int = EVENT_STARTED,
    ip: int = 0,
    key: int = 0,
    num_want: int = 200,
    port: int = 6881,
) -> tuple:
    """Send UDP announce request to get peers from tracker.
    
    Args:
        connection_id: from connect handshake
        tracker_url: tracker URL string
        info_hash: 20-byte SHA-1 of bencoded info dict
        peer_id: 20-byte unique client ID
        downloaded, left, uploaded: byte counters
        event: EVENT_NONE, EVENT_STARTED, EVENT_STOPPED, EVENT_COMPLETED
        ip: 0 means tracker detects our IP
        key: random client key
        num_want: max number of peers wanted (-1 for all)
        port: listening port for incoming connections
        
    Returns:
        (interval, leechers, seeders, peers_list) where
        peers_list is list of (ip_str, port) tuples
        
    Raises TrackerError on failure.
    """
    # Parse tracker URL for address
    if tracker_url.startswith("udp://"):
        tracker_url = tracker_url[6:]
    
    if "/" in tracker_url:
        host_port, _ = tracker_url.split("/", 1)
    else:
        host_port = tracker_url
    
    if ":" in host_port:
        host, port_str = host_port.rsplit(":", 1)
        tracker_port = int(port_str)
    else:
        host = host_port
        tracker_port = 80
    
    try:
        ip_addr = socket.gethostbyname(host)
    except socket.gaierror as e:
        raise TrackerError(f"Failed to resolve tracker host {host}: {e}")
    
    addr = (ip_addr, tracker_port)
    transaction_id = _generate_transaction_id()
    
    # Build announce request (98 bytes):
    # 8 bytes connection_id, 4 bytes action=1, 4 bytes transaction_id,
    # 20 bytes info_hash, 20 bytes peer_id, 8 bytes downloaded,
    # 8 bytes left, 8 bytes uploaded, 4 bytes event, 4 bytes ip,
    # 4 bytes key, 4 bytes num_want, 2 bytes port
    req = struct.pack(
        ">QII20s20sQQQIIIIH",
        connection_id,
        ACTION_ANNOUNCE,
        transaction_id,
        info_hash,
        peer_id,
        downloaded,
        left,
        uploaded,
        event,
        ip,
        key,
        num_want,
        port,
    )
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    try:
        resp = _send_recv(sock, addr, req)
        
        if len(resp) < 20:
            raise TrackerError(f"UDP announce response too short: {len(resp)} bytes")
        
        action, resp_transaction_id = struct.unpack(">II", resp[:8])
        
        if action == ACTION_ERROR:
            error_msg = resp[8:].decode("utf-8", errors="replace")
            raise TrackerError(f"Tracker error: {error_msg}")
        
        if resp_transaction_id != transaction_id:
            raise TrackerError("Transaction ID mismatch in announce response")
        
        if action != ACTION_ANNOUNCE:
            raise TrackerError(f"Unexpected action in announce response: {action}")
        
        # Parse announce response header: 4+4+4+4+4 = 20 bytes
        # interval (4), leechers (4), seeders (4)
        interval, leechers, seeders = struct.unpack(">III", resp[8:20])
        
        # Remaining bytes are 6-byte peer entries (4 bytes IP, 2 bytes port)
        peers_raw = resp[20:]
        peers = []
        for i in range(0, len(peers_raw), 6):
            if i + 6 > len(peers_raw):
                break
            peer_ip_raw, peer_port = struct.unpack(">IH", peers_raw[i:i+6])
            peer_ip = socket.inet_ntoa(struct.pack(">I", peer_ip_raw))
            peers.append((peer_ip, peer_port))
        
        return (interval, leechers, seeders, peers)
    finally:
        sock.close()


def scrape(
    tracker_url: str,
    info_hash: bytes,
) -> Optional[dict]:
    """Send UDP scrape request (BEP 0015) to get swarm metadata.
    
    Args:
        tracker_url: UDP tracker URL
        info_hash: 20-byte info hash to scrape
        
    Returns:
        Dict with seeders, completed, leechers or None if unsupported.
    """
    # Parse tracker URL
    if tracker_url.startswith("udp://"):
        tracker_url = tracker_url[6:]
    if "/" in tracker_url:
        host_port, _ = tracker_url.split("/", 1)
    else:
        host_port = tracker_url
    
    if ":" in host_port:
        host, port_str = host_port.rsplit(":", 1)
        tracker_port = int(port_str)
    else:
        host = host_port
        tracker_port = 80
    
    try:
        ip_addr = socket.gethostbyname(host)
    except socket.gaierror as e:
        raise TrackerError(f"Failed to resolve tracker host {host}: {e}")
    
    addr = (ip_addr, tracker_port)
    
    # First do a connect
    connection_id, _ = udp_connect(tracker_url)
    
    # Build scrape request
    transaction_id = _generate_transaction_id()
    req = struct.pack(">QII20s", connection_id, ACTION_SCRAPE, transaction_id, info_hash)
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        resp = _send_recv(sock, addr, req)
        
        if len(resp) < 24:
            return None
        
        action, resp_tid = struct.unpack(">II", resp[:8])
        if action == ACTION_ERROR:
            return None
        
        # Scrape response: 4+4+4+4+4+4 = 24 bytes per torrent
        # seeders(4), completed(4), leechers(4)
        seeders, completed, leechers = struct.unpack(">III", resp[8:20])
        return {
            "seeders": seeders,
            "completed": completed,
            "leechers": leechers,
        }
    finally:
        sock.close()


def generate_peer_id() -> bytes:
    """Generate a 20-byte peer ID following BEP 0020 convention.
    
    Format: -XX1234- + 12 random hex digits
    Using -WT0001- for our client identifier (WhataBit).
    """
    suffix = ''.join(random.choice('0123456789abcdef') for _ in range(12))
    peer_id = f"-WT0001-{suffix}"
    return peer_id.encode("ascii")[:20]
