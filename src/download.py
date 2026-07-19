"""Download manager - main orchestrator for BitTorrent downloads.

Manages the full download lifecycle:
1. Parse .torrent file
2. Contact tracker (UDP or HTTP) for peer list
3. Connect to peers and perform handshake
4. Manage piece/block request queue
5. Verify SHA-1 piece hashes
6. Write completed data to output file
"""

import asyncio
import hashlib
import logging
import os
from pathlib import Path
import random
import time
from typing import Optional, Callable

from .bencode import encode
from .torrent import TorrentFile
from .tracker import (
    udp_connect, udp_announce, generate_peer_id,
    EVENT_NONE, EVENT_STARTED, EVENT_STOPPED, EVENT_COMPLETED,
    TrackerError,
)
from .http_tracker import http_announce, build_announce_url
from .peer import (
    PeerConnection, parse_piece, parse_have, parse_bitfield,
    DEFAULT_BLOCK_SIZE, MSG_CHOKE, MSG_UNCHOKE, MSG_INTERESTED,
    MSG_NOT_INTERESTED, MSG_HAVE, MSG_BITFIELD, MSG_PIECE, MSG_REQUEST,
)

logger = logging.getLogger("whatabit.download")

# Block request safety defaults. These keep stalled peers from blocking a job
# forever while keeping the implementation intentionally simple for 0.2.
BLOCK_REQUEST_TIMEOUT = 20.0
MAX_BLOCK_RETRIES = 3
MAX_PEER_TIMEOUTS = 5
MAX_PEER_HASH_FAILURES = 2
MAX_PIECE_HASH_FAILURES = 5
MAX_PIPELINED_REQUESTS = 5


class PieceState:
    """Tracks download state of a single piece."""
    __slots__ = ("index", "length", "blocks", "received", "hash", "is_complete", "failed_peers")

    def __init__(self, index: int, length: int, piece_hash: bytes):
        self.index = index
        self.length = length
        self.hash = piece_hash
        self.blocks: dict[int, bytes] = {}  # begin offset -> block data
        self.received = 0
        self.is_complete = False
        self.failed_peers: set[str] = set()

    @property
    def is_full(self) -> bool:
        return self.received >= self.length

    def add_block(self, begin: int, data: bytes):
        if begin not in self.blocks:
            self.blocks[begin] = data
            self.received += len(data)
            if self.received >= self.length:
                self.is_complete = True

    def get_data(self) -> bytes:
        """Assemble block data in order."""
        result = bytearray(self.length)
        for begin, data in self.blocks.items():
            result[begin:begin+len(data)] = data
        return bytes(result)

    def verify(self) -> bool:
        """Verify SHA-1 hash against the piece data."""
        data = self.get_data()
        return hashlib.sha1(data).digest() == self.hash

    def __repr__(self):
        return f"Piece({self.index}, {self.received}/{self.length}, complete={self.is_complete})"


class DownloadManager:
    """Orchestrates the complete BitTorrent download process."""

    def __init__(
        self,
        torrent_path: str,
        output_dir: str = ".",
        max_peers: int = 50,
        max_connections: int = 20,
        port: int = 6881,
        progress_callback: Optional[Callable] = None,
    ):
        self.torrent_path = torrent_path
        self.output_dir = output_dir
        self.max_peers = max_peers
        self.max_connections = max_connections
        self.port = port
        self.progress_callback = progress_callback

        # Parse torrent
        self.torrent = TorrentFile(torrent_path)
        self.info_hash = self.torrent.info_hash
        self.pieces: list[PieceState] = []
        self.peer_id = generate_peer_id()

        # Peer management
        self.peer_pool: list[tuple[str, int]] = []
        self.active_connections: list[PeerConnection] = []
        self.banned_peers: set[str] = set()

        # Piece management
        self.piece_queue: list[int] = []
        self.pending_pieces: dict[int, PieceState] = {}
        self.downloaded_pieces: list[Optional[bytes]] = []
        self.bytes_downloaded = 0

        # Piece assignment tracking
        self.peer_current_piece: dict[str, int] = {}  # ip:port -> current piece index
        self.peer_pending_count: dict[str, int] = {}  # ip:port -> pending request count
        self.pending_requests: dict[tuple[int, int], dict] = {}  # (piece, begin) -> request metadata
        self.block_retry_count: dict[tuple[int, int], int] = {}
        self.peer_timeout_count: dict[str, int] = {}
        self.timed_out_requests = 0
        self.bad_pieces = 0
        self.piece_hash_failures: dict[int, int] = {}
        self.peer_hash_failures: dict[str, int] = {}

        # Output file
        self.output_path = str(self._safe_output_path(self.torrent.name))
        self.output_file: Optional[asyncio.FileIO] = None

        # Control and observable status
        self.running = False
        self.completed = False
        self.start_time = 0.0
        self.phase = "created"
        self.status_message = "Ready"
        self.last_error = ""
        self.peers_discovered = 0
        self.tracker_events: list[dict] = []
        self.tracker_attempts = 0
        self.tracker_successes = 0
        self.tracker_failures = 0
        self.output_written = False

        # Compute piece sizes
        self._init_pieces()

    def _set_status(self, phase: str, message: str, *, error: str = "") -> None:
        """Update user-visible download status fields."""
        self.phase = phase
        self.status_message = message
        if error:
            self.last_error = error

    def _init_pieces(self):
        """Initialize piece state from torrent metadata."""
        piece_length = self.torrent.piece_length
        total_length = self.torrent.total_length
        num_pieces = len(self.torrent.pieces)

        for i in range(num_pieces):
            if i == num_pieces - 1:
                # Last piece may be shorter
                p_len = total_length - (i * piece_length)
            else:
                p_len = piece_length
            self.pieces.append(PieceState(i, p_len, self.torrent.pieces[i]))
            self.piece_queue.append(i)

        self.downloaded_pieces = [None] * num_pieces

        random.shuffle(self.piece_queue)

    def _record_tracker_event(
        self,
        url: str,
        status: str,
        *,
        peers: int = 0,
        error: str = "",
    ) -> None:
        """Record a tracker attempt/result for UI and diagnostics."""

        event = {
            "url": url,
            "status": status,
            "peers": peers,
            "error": error,
            "time": time.time(),
        }
        self.tracker_events.append(event)
        self.tracker_events = self.tracker_events[-25:]
        if status == "attempt":
            self.tracker_attempts += 1
        elif status == "success":
            self.tracker_successes += 1
        elif status == "failed":
            self.tracker_failures += 1

    async def _contact_udp_tracker(self, url: str | None = None) -> list[tuple[str, int]]:
        """Contact UDP tracker and return list of peers."""
        tracker_url = url or self.torrent.announce
        if not tracker_url.startswith("udp://"):
            return []

        try:
            self._record_tracker_event(tracker_url, "attempt")
            self._set_status("tracker", f"Contacting UDP tracker {tracker_url}")
            conn_id, _ = udp_connect(tracker_url)
            interval, leechers, seeders, peers = udp_announce(
                conn_id,
                tracker_url,
                self.info_hash,
                self.peer_id,
                left=self.torrent.total_length,
                event=EVENT_STARTED,
            )
            logger.info(f"UDP tracker: {len(peers)} peers, seeders={seeders}, leechers={leechers}")
            self._record_tracker_event(tracker_url, "success", peers=len(peers))
            self._set_status("tracker", f"UDP tracker returned {len(peers)} peers")
            return peers
        except TrackerError as e:
            logger.warning(f"UDP tracker failed: {e}")
            self._record_tracker_event(tracker_url, "failed", error=str(e))
            self._set_status("tracker", f"UDP tracker failed: {e}", error=str(e))
            return []

    async def _contact_http_tracker(self, url: str | None = None) -> list[tuple[str, int]]:
        """Contact HTTP tracker and return list of peers."""
        tracker_url = url or self.torrent.announce
        if not (tracker_url.startswith("http://") or tracker_url.startswith("https://")):
            return []

        try:
            self._record_tracker_event(tracker_url, "attempt")
            self._set_status("tracker", f"Contacting HTTP tracker {tracker_url}")
            interval, leechers, seeders, peers = await http_announce(
                tracker_url,
                self.info_hash,
                self.peer_id,
                port=self.port,
                left=self.torrent.total_length,
            )
            logger.info(f"HTTP tracker: {len(peers)} peers, seeders={seeders}, leechers={leechers}")
            self._record_tracker_event(tracker_url, "success", peers=len(peers))
            self._set_status("tracker", f"HTTP tracker returned {len(peers)} peers")
            return peers
        except Exception as e:
            logger.warning(f"HTTP tracker failed: {e}")
            self._record_tracker_event(tracker_url, "failed", error=str(e))
            self._set_status("tracker", f"HTTP tracker failed: {e}", error=str(e))
            return []

    async def get_peers(self) -> list[tuple[str, int]]:
        """Get peers from tracker(s). Supports both UDP and HTTP."""
        self._set_status("tracker", "Contacting tracker(s)")
        all_peers = []

        # Try primary announce URL
        if self.torrent.announce.startswith("udp://"):
            peers = await self._contact_udp_tracker(self.torrent.announce)
            all_peers.extend(peers)
        elif self.torrent.announce.startswith(("http://", "https://")):
            peers = await self._contact_http_tracker(self.torrent.announce)
            all_peers.extend(peers)

        # Try announce-list if available
        if self.torrent.announce_list:
            for tier in self.torrent.announce_list:
                for url in tier:
                    url_str = url.decode("utf-8", errors="replace") if isinstance(url, bytes) else url
                    if url_str == self.torrent.announce:
                        continue  # Already tried
                    if url_str.startswith("udp://"):
                        peers = await self._contact_udp_tracker(url_str)
                        all_peers.extend(peers)
                    elif url_str.startswith(("http://", "https://")):
                        peers = await self._contact_http_tracker(url_str)
                        all_peers.extend(peers)

        # Deduplicate
        seen = set()
        unique_peers = []
        for ip, port in all_peers:
            key = f"{ip}:{port}"
            if key not in seen and key not in self.banned_peers:
                seen.add(key)
                unique_peers.append((ip, port))

        random.shuffle(unique_peers)
        selected = unique_peers[:self.max_peers]
        self.peers_discovered = len(selected)
        self._set_status("tracker", f"Found {len(selected)} unique peers")
        return selected

    async def _handle_message(self, peer: PeerConnection, msg: dict):
        """Handle an incoming message from a peer."""
        msg_id = msg["msg_id"]
        payload = msg["payload"]

        if msg_id == MSG_CHOKE:
            peer.peer_choking = True
            logger.debug(f"{peer} choked us")

        elif msg_id == MSG_UNCHOKE:
            peer.peer_choking = False
            logger.debug(f"{peer} unchoked us")
            # Request next block from this peer
            await self._request_next_block(peer)

        elif msg_id == MSG_INTERESTED:
            peer.peer_interested = True

        elif msg_id == MSG_NOT_INTERESTED:
            peer.peer_interested = False

        elif msg_id == MSG_HAVE:
            piece_index = parse_have(payload)
            if peer.bitfield and piece_index < len(peer.bitfield):
                peer.bitfield[piece_index] = True

        elif msg_id == MSG_BITFIELD:
            peer.bitfield = parse_bitfield(payload, len(self.pieces))
            # Send interested if peer has pieces we need
            if not peer.am_interested and any(
                peer.bitfield[i] and not self.pieces[i].is_complete
                for i in range(len(self.pieces))
            ):
                await peer.send_interested()

        elif msg_id == MSG_PIECE:
            index, begin, block = parse_piece(payload)
            await self._handle_piece_data(peer, index, begin, block)

    def _peer_key(self, peer: PeerConnection) -> str:
        return f"{peer.ip}:{peer.port}"

    def _next_missing_block(self, piece: PieceState) -> tuple[int, int] | None:
        """Return the next unreceived and not-in-flight block for a piece."""

        for begin in range(0, piece.length, DEFAULT_BLOCK_SIZE):
            key = (piece.index, begin)
            if begin in piece.blocks or key in self.pending_requests:
                continue
            return begin, min(DEFAULT_BLOCK_SIZE, piece.length - begin)
        return None

    async def _send_block_request(
        self,
        peer: PeerConnection,
        piece_index: int,
        begin: int,
        length: int,
    ) -> None:
        """Send and track a block request so timeouts can be retried."""

        await peer.send_request(piece_index, begin, length)
        peer_key = self._peer_key(peer)
        request_key = (piece_index, begin)
        self.pending_requests[request_key] = {
            "peer_key": peer_key,
            "length": length,
            "requested_at": time.time(),
            "attempt": self.block_retry_count.get(request_key, 0) + 1,
        }
        self.peer_pending_count[peer_key] = self.peer_pending_count.get(peer_key, 0) + 1

    def _clear_pending_request(self, index: int, begin: int) -> None:
        request = self.pending_requests.pop((index, begin), None)
        if not request:
            return
        peer_key = str(request.get("peer_key") or "")
        if peer_key:
            self.peer_pending_count[peer_key] = max(
                0,
                self.peer_pending_count.get(peer_key, 0) - 1,
            )

    def _clear_piece_pending_requests(self, index: int) -> None:
        for piece_index, begin in list(self.pending_requests):
            if piece_index == index:
                self._clear_pending_request(piece_index, begin)

    async def _check_request_timeouts(self) -> int:
        """Release stale block requests so they can be retried.

        Returns the number of timed-out block requests. Timed-out blocks become
        available to `_request_next_block` again. Peers with repeated timeouts
        are disconnected and banned for this session.
        """

        now = time.time()
        expired: list[tuple[int, int, dict]] = []
        for key, request in list(self.pending_requests.items()):
            requested_at = float(request.get("requested_at") or now)
            if now - requested_at >= BLOCK_REQUEST_TIMEOUT:
                expired.append((key[0], key[1], request))

        for index, begin, request in expired:
            peer_key = str(request.get("peer_key") or "")
            self._clear_pending_request(index, begin)
            request_key = (index, begin)
            self.block_retry_count[request_key] = self.block_retry_count.get(request_key, 0) + 1
            self.timed_out_requests += 1
            if peer_key:
                self.peer_timeout_count[peer_key] = self.peer_timeout_count.get(peer_key, 0) + 1
            logger.warning(
                "Block request timed out: piece=%s begin=%s peer=%s retry=%s",
                index,
                begin,
                peer_key or "unknown",
                self.block_retry_count[request_key],
            )

            if self.block_retry_count[request_key] >= MAX_BLOCK_RETRIES:
                logger.warning(
                    "Block piece=%s begin=%s exceeded retry target; keeping it eligible for retry",
                    index,
                    begin,
                )

            if peer_key and self.peer_timeout_count.get(peer_key, 0) >= MAX_PEER_TIMEOUTS:
                self.banned_peers.add(peer_key)
                for conn in list(self.active_connections):
                    if self._peer_key(conn) == peer_key:
                        await conn.disconnect()
                        break

        if expired:
            self._set_status(
                "downloading",
                f"Retried {len(expired)} timed-out block request(s)",
            )
        return len(expired)

    def _assigned_piece_indices(self, *, except_peer_key: str = "") -> set[int]:
        return {
            piece_index
            for peer_key, piece_index in self.peer_current_piece.items()
            if peer_key != except_peer_key
        }

    def _remove_piece_from_queue(self, index: int) -> None:
        self.piece_queue = [candidate for candidate in self.piece_queue if candidate != index]

    def _queue_piece(self, index: int) -> None:
        if index < 0 or index >= len(self.pieces):
            return
        if self.pieces[index].is_complete:
            return
        if index in self.piece_queue:
            return
        if index in self._assigned_piece_indices():
            return
        self.piece_queue.append(index)

    def _peer_has_piece(self, peer: PeerConnection, index: int) -> bool:
        return bool(peer.bitfield and index < len(peer.bitfield) and peer.bitfield[index])

    def _pop_next_piece_for_peer(self, peer: PeerConnection, peer_key: str) -> int | None:
        assigned_elsewhere = self._assigned_piece_indices(except_peer_key=peer_key)
        kept: list[int] = []
        selected: int | None = None

        while self.piece_queue:
            candidate = self.piece_queue.pop(0)
            if candidate in kept:
                continue
            if candidate >= len(self.pieces) or self.pieces[candidate].is_complete:
                continue
            if candidate in assigned_elsewhere:
                kept.append(candidate)
                continue
            if not self._peer_has_piece(peer, candidate):
                kept.append(candidate)
                continue
            if self._next_missing_block(self.pieces[candidate]) is None:
                kept.append(candidate)
                continue
            selected = candidate
            break

        self.piece_queue = kept + [
            candidate for candidate in self.piece_queue if candidate not in kept
        ]
        return selected

    def _release_peer_piece(self, peer_key: str, *, requeue: bool = True) -> int | None:
        current_idx = self.peer_current_piece.pop(peer_key, None)
        if current_idx is not None and requeue:
            self._queue_piece(current_idx)
        return current_idx

    async def _handle_piece_hash_failure(self, peer: PeerConnection, index: int, piece: PieceState) -> None:
        """Discard a corrupt piece, requeue it, and penalize the sender."""

        peer_key = self._peer_key(peer)
        self.bad_pieces += 1
        self.piece_hash_failures[index] = self.piece_hash_failures.get(index, 0) + 1
        self.peer_hash_failures[peer_key] = self.peer_hash_failures.get(peer_key, 0) + 1
        piece.failed_peers.add(peer_key)

        logger.warning(
            "Piece %s hash mismatch from %s; requeueing (piece failures=%s, peer failures=%s)",
            index,
            peer_key,
            self.piece_hash_failures[index],
            self.peer_hash_failures[peer_key],
        )

        self._clear_piece_pending_requests(index)
        self.pieces[index] = PieceState(index, piece.length, piece.hash)
        self.pieces[index].failed_peers = set(piece.failed_peers)
        for key in list(self.block_retry_count):
            if key[0] == index:
                self.block_retry_count.pop(key, None)

        self._release_peer_piece(peer_key, requeue=False)
        self._queue_piece(index)
        self.peer_pending_count[peer_key] = 0
        self._set_status(
            "downloading",
            f"Piece {index} failed hash verification; requeued for retry",
        )

        if self.peer_hash_failures[peer_key] >= MAX_PEER_HASH_FAILURES:
            self.banned_peers.add(peer_key)
            await peer.disconnect()

        if self.piece_hash_failures[index] >= MAX_PIECE_HASH_FAILURES:
            self._set_status(
                "downloading",
                f"Piece {index} has failed hash verification {self.piece_hash_failures[index]} times",
                error=f"Repeated hash failures for piece {index}",
            )

    async def _handle_piece_data(self, peer: PeerConnection, index: int, begin: int, block: bytes):
        """Handle received piece data."""
        if index >= len(self.pieces):
            return

        piece = self.pieces[index]
        if piece.is_complete:
            return

        self._clear_pending_request(index, begin)
        piece.add_block(begin, block)
        self.bytes_downloaded += len(block)

        if piece.is_complete:
            # Verify piece hash
            if piece.verify():
                logger.info(f"Piece {index}/{len(self.pieces)} verified OK")
                await self._write_piece(index, piece.get_data())
                
                # Notify other peers we have this piece
                for conn in self.active_connections:
                    if conn.is_connected:
                        await conn.send_have(index)
                
                self._update_progress()
                await self._request_next_block(peer)
            else:
                await self._handle_piece_hash_failure(peer, index, piece)
        else:
            # Request next block for this piece or a new piece
            await self._request_next_block(peer)

    async def _write_piece(self, index: int, data: bytes):
        """Record a completed piece and clear scheduling state for it."""
        self.downloaded_pieces[index] = data
        self.pending_pieces.pop(index, None)
        self._remove_piece_from_queue(index)
        for peer_key, piece_index in list(self.peer_current_piece.items()):
            if piece_index == index:
                self.peer_current_piece.pop(peer_key, None)

    def _update_progress(self):
        """Calculate and report progress."""
        completed = sum(1 for p in self.downloaded_pieces if p is not None)
        total = len(self.downloaded_pieces)
        elapsed = time.time() - self.start_time
        speed = self.bytes_downloaded / elapsed if elapsed > 0 else 0

        logger.info(f"Progress: {completed}/{total} pieces complete, {self.bytes_downloaded / (1024*1024):.1f} MiB, {speed / 1024:.1f} KiB/s")

        percent = (completed / total * 100) if total else 0
        if not self.completed:
            self._set_status("downloading", f"Downloading: {completed}/{total} pieces ({percent:.1f}%)")

        if self.progress_callback:
            self.progress_callback(completed, total, self.bytes_downloaded, speed)

        if completed >= total:
            self.completed = True
            self._set_status("complete", "All pieces downloaded and verified")

    def _safe_output_path(self, *parts: str) -> Path:
        """Build an output path that cannot escape the configured output dir."""

        base = Path(self.output_dir).expanduser().resolve(strict=False)
        candidate = base
        for raw_part in parts:
            for part in Path(str(raw_part)).parts:
                if part in {"", "."}:
                    continue
                if part == ".." or Path(part).is_absolute():
                    raise ValueError(f"Unsafe torrent output path component: {raw_part}")
                candidate = candidate / part

        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(base)
        except ValueError as exc:
            raise ValueError(f"Unsafe torrent output path: {resolved}") from exc
        return resolved

    def _assembled_payload(self) -> bytes:
        """Return the full verified payload bytes or raise if any piece is missing."""

        missing = [i for i, data in enumerate(self.downloaded_pieces) if data is None]
        if missing:
            raise ValueError(f"Cannot assemble output; missing {len(missing)} piece(s)")
        return b"".join(data or b"" for data in self.downloaded_pieces)[: self.torrent.total_length]

    def _write_file_atomic(self, path: Path, data: bytes) -> None:
        """Write a file via a sibling .part file, then replace the final path."""

        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(path.name + ".part")
        with temp_path.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        temp_path.replace(path)

    def _write_single_file_output(self, payload: bytes) -> None:
        self._write_file_atomic(Path(self.output_path), payload)

    def _write_multi_file_output(self, payload: bytes) -> None:
        offset = 0
        root = self._safe_output_path(self.torrent.name)
        for file_info in self.torrent.files:
            length = int(file_info["length"])
            relative_path = str(file_info["path"])
            target = self._safe_output_path(self.torrent.name, *relative_path.split("/"))
            target.relative_to(root)  # Defensive: ensures file remains under torrent root.
            self._write_file_atomic(target, payload[offset : offset + length])
            offset += length

    async def _flush_output(self) -> bool:
        """Assemble and write all pieces to the output file.

        Returns True when a complete verified payload was written. Incomplete
        downloads are intentionally not flushed because writing zero-filled
        placeholders makes stopped jobs look like completed downloads.
        """
        missing = [i for i, data in enumerate(self.downloaded_pieces) if data is None]
        if missing or not self.completed:
            message = (
                "Skipping output write: download incomplete "
                f"({len(self.downloaded_pieces) - len(missing)}/{len(self.downloaded_pieces)} pieces complete)"
            )
            logger.warning(message)
            self._set_status("stopped", "Download incomplete; no output file was written")
            return False

        os.makedirs(self.output_dir, exist_ok=True)

        self._set_status("writing", f"Writing output to {self.output_path}")
        logger.info(f"Writing output to {self.output_path}")
        try:
            payload = self._assembled_payload()
            if self.torrent.is_multi_file:
                self._write_multi_file_output(payload)
            else:
                self._write_single_file_output(payload)
        except Exception as exc:
            self._set_status("error", f"Failed to write output: {exc}", error=str(exc))
            logger.exception("Failed to write output")
            return False

        self.output_written = True
        self._set_status("complete", f"Output written to {self.output_path}")
        logger.info(f"Output written to {self.output_path}")
        return True

    async def _request_next_block(self, peer: PeerConnection):
        """Request the next block from a peer."""
        if peer.peer_choking:
            return

        if not peer.bitfield:
            return

        # Find a piece this peer has that we need
        peer_key = self._peer_key(peer)
        current_piece_idx = self.peer_current_piece.get(peer_key)

        if current_piece_idx is not None:
            piece = self.pieces[current_piece_idx]
            if piece.is_complete:
                self._release_peer_piece(peer_key, requeue=False)
                current_piece_idx = None
            elif self.peer_pending_count.get(peer_key, 0) < MAX_PIPELINED_REQUESTS:
                next_block = self._next_missing_block(piece)
                if next_block is not None:
                    block_begin, block_size = next_block
                    await self._send_block_request(peer, current_piece_idx, block_begin, block_size)
                    return

        if current_piece_idx is not None:
            return

        candidate = self._pop_next_piece_for_peer(peer, peer_key)
        if candidate is None:
            return

        self.peer_current_piece[peer_key] = candidate
        next_block = self._next_missing_block(self.pieces[candidate])
        if next_block is None:
            self._release_peer_piece(peer_key, requeue=True)
            return
        block_begin, block_size = next_block
        await self._send_block_request(peer, candidate, block_begin, block_size)

    async def _handle_peer_connect(self, peer: PeerConnection) -> bool:
        """Try to connect to a peer, send interested on success."""
        success = await peer.connect()
        if success:
            self.active_connections.append(peer)
            peer.on_message = self._handle_message
            # Start reading messages in background
            asyncio.create_task(peer.read_loop())
            return True
        return False

    async def _handle_peer_disconnect(self, peer: PeerConnection):
        """Handle peer disconnection."""
        if peer in self.active_connections:
            self.active_connections.remove(peer)

        peer_key = self._peer_key(peer)
        # Re-queue any piece this peer was working on
        self._release_peer_piece(peer_key, requeue=True)
        for (piece_index, begin), request in list(self.pending_requests.items()):
            if request.get("peer_key") == peer_key:
                self._clear_pending_request(piece_index, begin)
        self.peer_pending_count.pop(peer_key, None)

    async def connect_to_peers(self):
        """Connect to peers from the pool."""
        self._set_status("connecting", f"Connecting to peers ({len(self.peer_pool)} available)")
        while self.running and not self.completed and self.peer_pool:
            # Fill up to max_connections
            while len(self.active_connections) < self.max_connections and self.peer_pool:
                ip, port = self.peer_pool.pop(0)
                peer = PeerConnection(
                    ip, port,
                    self.info_hash,
                    self.peer_id,
                    on_message=self._handle_message,
                    on_disconnect=self._handle_peer_disconnect,
                )
                success = await peer.connect()
                if success:
                    self.active_connections.append(peer)
                    self._set_status("downloading", f"Connected to {len(self.active_connections)} peer(s)")
                    peer.on_message = self._handle_message
                    peer.on_disconnect = self._handle_peer_disconnect
                    asyncio.create_task(peer.read_loop())
                else:
                    self.banned_peers.add(f"{ip}:{port}")
                    self._set_status("connecting", f"Peer {ip}:{port} failed to connect")

            if not self.active_connections:
                await asyncio.sleep(5)
                continue

            await asyncio.sleep(0.5)

            # Clean disconnected peers
            self.active_connections = [
                c for c in self.active_connections if c.is_connected
            ]

    async def download(self):
        """Main download loop."""
        logger.info(f"Starting download of {self.torrent.name}")
        logger.info(f"Info hash: {self.torrent.info_hash_hex}")
        logger.info(f"Size: {self.torrent.total_length / (1024*1024):.1f} MiB in {len(self.pieces)} pieces")

        self.running = True
        self.completed = False
        self.output_written = False
        self.last_error = ""
        self.start_time = time.time()
        self._set_status("starting", f"Starting download of {self.torrent.name}")

        # Step 1: Get peers from tracker
        self._set_status("tracker", "Contacting tracker(s)")
        logger.info("Contacting tracker...")
        self.peer_pool = await self.get_peers()
        logger.info(f"Got {len(self.peer_pool)} unique peers")

        if not self.peer_pool:
            logger.warning("No peers found from tracker!")
            self.running = False
            self._set_status("no_peers", "No peers found from tracker(s)")
            return

        # Step 2: Connect to peers and download
        await self.connect_to_peers()

        # Step 3: Wait for completion or user stop
        while self.running and not self.completed:
            await asyncio.sleep(2)

            await self._check_request_timeouts()
            for conn in list(self.active_connections):
                if conn.is_connected:
                    await self._request_next_block(conn)

            # Check if all pieces are done
            all_done = all(p.is_complete for p in self.pieces)
            if all_done:
                self.completed = True
                break

            # If no active connections, try to find more peers
            if not self.active_connections or all(
                not c.is_connected for c in self.active_connections
            ):
                self._set_status("tracker", "No active peers; re-contacting tracker(s)")
                logger.info("No active peers, re-contacting tracker...")
                self.peer_pool = await self.get_peers()
                if self.peer_pool:
                    await self.connect_to_peers()

            self._update_progress()

        # Step 4: Write output only when complete
        if self.completed:
            await self._flush_output()
        else:
            self._set_status("stopped", "Download stopped before completion; no output file was written")
            logger.info("Download stopped before completion; no output file was written")

        elapsed = time.time() - self.start_time
        speed = self.bytes_downloaded / elapsed if elapsed > 0 else 0
        logger.info(f"Download {'completed' if self.completed else 'stopped'}")
        logger.info(f"Elapsed: {elapsed:.1f}s, Avg speed: {speed/1024:.1f} KiB/s")

    def stop(self):
        """Stop the download."""
        self.running = False
        if not self.completed:
            self._set_status("stopping", "Stopping download")

    def get_stats(self) -> dict:
        """Get current download statistics."""
        completed = sum(1 for p in self.downloaded_pieces if p is not None)
        total = len(self.pieces)
        elapsed = time.time() - self.start_time if self.start_time > 0 else 0
        speed = self.bytes_downloaded / elapsed if elapsed > 0 else 0
        percent = (completed / total * 100) if total else 0
        return {
            "name": self.torrent.name,
            "info_hash": self.torrent.info_hash_hex,
            "total_size": self.torrent.total_length,
            "downloaded": self.bytes_downloaded,
            "pieces_complete": completed,
            "pieces_total": total,
            "percent": percent,
            "connected_peers": len([c for c in self.active_connections if c.is_connected]),
            "peers_discovered": self.peers_discovered,
            "tracker_attempts": self.tracker_attempts,
            "tracker_successes": self.tracker_successes,
            "tracker_failures": self.tracker_failures,
            "tracker_events": list(self.tracker_events),
            "queued_peers": len(self.peer_pool),
            "banned_peers": len(self.banned_peers),
            "queued_pieces": len(self.piece_queue),
            "assigned_pieces": len(self._assigned_piece_indices()),
            "pending_requests": len(self.pending_requests),
            "timed_out_requests": self.timed_out_requests,
            "bad_pieces": self.bad_pieces,
            "piece_hash_failures": dict(self.piece_hash_failures),
            "peer_hash_failures": dict(self.peer_hash_failures),
            "speed": speed,
            "elapsed": elapsed,
            "phase": self.phase,
            "status_message": self.status_message,
            "last_error": self.last_error,
            "is_running": self.running,
            "is_complete": self.completed,
            "output_path": self.output_path,
            "output_exists": os.path.exists(self.output_path),
            "output_is_dir": os.path.isdir(self.output_path),
            "output_written": self.output_written,
        }
