"""Torrent file parser.

Parses .torrent metainfo files using bencode.
Extracts announce URLs, info hash, piece hashes, and file structure.
Supports both single-file and multi-file torrents.
"""

import hashlib
from typing import Optional
from .bencode import decode


class TorrentFile:
    """Represents a parsed .torrent file with decoded metadata."""

    def __init__(self, path: str):
        with open(path, "rb") as f:
            raw = f.read()
        self._raw_data = decode(raw)
        self._info_raw = self._get_info_bencoded(raw)
        self._parse()

    def _get_info_bencoded(self, raw: bytes) -> bytes:
        """Extract the raw bencoded info dict from the torrent file.
        
        We need the exact byte sequence of the info dict for hashing.
        Find it by scanning the top-level dict for the 'info' key.
        """
        # Decode to find the info value, then re-encode it
        # This gives us the canonical bencoded form for SHA-1 hashing
        info = self._raw_data[b"info"]
        from .bencode import encode
        return encode(info)

    def _parse(self):
        raw = self._raw_data
        self.announce: str = raw.get(b"announce", b"").decode("utf-8", errors="replace")
        
        # Optional announce-list for multiple trackers (BEP 0012)
        self.announce_list: Optional[list] = None
        if b"announce-list" in raw:
            self.announce_list = raw[b"announce-list"]

        # Info dictionary
        info = raw[b"info"]
        self.name: str = info.get(b"name", b"unknown").decode("utf-8", errors="replace")
        self.piece_length: int = info.get(b"piece length", 0)
        self.private: int = info.get(b"private", 0)

        # Piece hashes (concatenated 20-byte SHA-1 hashes)
        pieces_raw: bytes = info.get(b"pieces", b"")
        self.pieces: list[bytes] = []
        for i in range(0, len(pieces_raw), 20):
            self.pieces.append(pieces_raw[i:i+20])

        # Single file vs multi-file
        self.is_multi_file: bool = b"files" in info
        self.length: int = info.get(b"length", 0)
        self.files: list[dict] = []

        if self.is_multi_file:
            for file_entry in info[b"files"]:
                path_parts = [p.decode("utf-8", errors="replace") for p in file_entry[b"path"]]
                self.files.append({
                    "path": "/".join(path_parts),
                    "length": file_entry[b"length"],
                })
        
        # Compute info hash (SHA-1 of the bencoded info dict)
        self.info_hash: bytes = hashlib.sha1(self._info_raw).digest()
        self.info_hash_hex: str = self.info_hash.hex()

        # Total download size
        if self.is_multi_file:
            self.total_length: int = sum(f["length"] for f in self.files)
        else:
            self.total_length: int = self.length

    def __str__(self):
        lines = [
            f"Name: {self.name}",
            f"Announce: {self.announce}",
            f"Info Hash: {self.info_hash_hex}",
            f"Piece Length: {self.piece_length}",
            f"Pieces: {len(self.pieces)}",
            f"Total Size: {self.total_length} bytes ({self.total_length / (1024**3):.2f} GiB)",
        ]
        if self.is_multi_file:
            lines.append(f"Files: {len(self.files)}")
            for f in self.files:
                lines.append(f"  - {f['path']} ({f['length']} bytes)")
        else:
            lines.append(f"Single file: {self.total_length} bytes")
        return "\n".join(lines)
