"""WhataBit - A BitTorrent client implementation.

Following the guide at:
https://allenkim67.github.io/programming/2016/05/04/how-to-make-your-own-bittorrent-client.html
"""

from .bencode import encode, decode
from .torrent import TorrentFile
from .tracker import udp_connect, udp_announce, generate_peer_id
from .peer import PeerConnection, build_handshake, parse_handshake
from .download import DownloadManager

__version__ = "0.1.0"
__all__ = [
    "encode", "decode",
    "TorrentFile",
    "udp_connect", "udp_announce", "generate_peer_id",
    "PeerConnection", "build_handshake", "parse_handshake",
    "DownloadManager",
]
