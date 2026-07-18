import asyncio
from pathlib import Path

from src.bencode import encode
from src.download import DownloadManager, MAX_PEER_HASH_FAILURES


def write_torrent(path: Path, *, piece_hash: bytes = b"0" * 20) -> None:
    data = {
        b"announce": b"http://tracker.example/announce",
        b"info": {
            b"name": b"payload.bin",
            b"piece length": 4,
            b"length": 4,
            b"pieces": piece_hash,
        },
    }
    path.write_bytes(encode(data))


class FakePeer:
    ip = "127.0.0.1"
    port = 6881
    peer_choking = False
    bitfield = [True]

    def __init__(self):
        self.requests = []
        self.disconnected = False

    async def send_request(self, index, begin, length):
        self.requests.append((index, begin, length))

    async def disconnect(self):
        self.disconnected = True

    @property
    def is_connected(self):
        return not self.disconnected


def test_bad_piece_is_discarded_requeued_and_counted(tmp_path):
    torrent_path = tmp_path / "test.torrent"
    write_torrent(torrent_path, piece_hash=b"x" * 20)
    manager = DownloadManager(str(torrent_path), output_dir=str(tmp_path / "downloads"))
    peer = FakePeer()

    asyncio.run(manager._send_block_request(peer, 0, 0, 4))
    asyncio.run(manager._handle_piece_data(peer, 0, 0, b"bad!"))

    assert manager.downloaded_pieces[0] is None
    assert manager.pieces[0].blocks == {}
    assert manager.pieces[0].received == 0
    assert manager.piece_queue == [0]
    assert manager.bad_pieces == 1
    assert manager.piece_hash_failures[0] == 1
    assert manager.peer_hash_failures["127.0.0.1:6881"] == 1
    assert manager.pending_requests == {}
    assert manager.get_stats()["bad_pieces"] == 1


def test_peer_is_banned_after_repeated_bad_pieces(tmp_path):
    torrent_path = tmp_path / "test.torrent"
    write_torrent(torrent_path, piece_hash=b"x" * 20)
    manager = DownloadManager(str(torrent_path), output_dir=str(tmp_path / "downloads"))
    peer = FakePeer()

    for _ in range(MAX_PEER_HASH_FAILURES):
        manager.piece_queue.clear()
        asyncio.run(manager._send_block_request(peer, 0, 0, 4))
        asyncio.run(manager._handle_piece_data(peer, 0, 0, b"bad!"))

    assert "127.0.0.1:6881" in manager.banned_peers
    assert peer.disconnected is True
