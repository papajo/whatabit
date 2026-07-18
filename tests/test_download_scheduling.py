import asyncio
from pathlib import Path

import src.download as download_module
from src.bencode import encode
from src.download import DownloadManager


def write_torrent(path: Path, *, pieces: int = 2) -> None:
    piece_length = download_module.DEFAULT_BLOCK_SIZE
    data = {
        b"announce": b"http://tracker.example/announce",
        b"info": {
            b"name": b"payload.bin",
            b"piece length": piece_length,
            b"length": piece_length * pieces,
            b"pieces": b"0" * 20 * pieces,
        },
    }
    path.write_bytes(encode(data))


class FakePeer:
    peer_choking = False

    def __init__(self, ip, bitfield):
        self.ip = ip
        self.port = 6881
        self.bitfield = bitfield
        self.requests = []
        self.disconnected = False

    async def send_request(self, index, begin, length):
        self.requests.append((index, begin, length))

    async def disconnect(self):
        self.disconnected = True

    @property
    def is_connected(self):
        return not self.disconnected


def test_peers_do_not_get_same_piece_when_alternatives_exist(tmp_path):
    torrent_path = tmp_path / "test.torrent"
    write_torrent(torrent_path, pieces=2)
    manager = DownloadManager(str(torrent_path), output_dir=str(tmp_path / "downloads"))
    manager.piece_queue = [0, 1]
    peer_a = FakePeer("127.0.0.1", [True, True])
    peer_b = FakePeer("127.0.0.2", [True, True])

    asyncio.run(manager._request_next_block(peer_a))
    asyncio.run(manager._request_next_block(peer_b))

    assigned = set(manager.peer_current_piece.values())
    assert assigned == {0, 1}
    assert peer_a.requests[0][0] != peer_b.requests[0][0]
    assert manager.piece_queue == []


def test_unusable_piece_stays_queued_for_peer_that_has_it(tmp_path):
    torrent_path = tmp_path / "test.torrent"
    write_torrent(torrent_path, pieces=2)
    manager = DownloadManager(str(torrent_path), output_dir=str(tmp_path / "downloads"))
    manager.piece_queue = [0, 1]
    peer_without_piece_0 = FakePeer("127.0.0.1", [False, True])

    asyncio.run(manager._request_next_block(peer_without_piece_0))

    assert peer_without_piece_0.requests[0][0] == 1
    assert manager.piece_queue == [0]


def test_disconnect_requeues_current_piece_once(tmp_path):
    torrent_path = tmp_path / "test.torrent"
    write_torrent(torrent_path, pieces=1)
    manager = DownloadManager(str(torrent_path), output_dir=str(tmp_path / "downloads"))
    manager.piece_queue = []
    peer = FakePeer("127.0.0.1", [True])
    manager.peer_current_piece["127.0.0.1:6881"] = 0

    asyncio.run(manager._handle_peer_disconnect(peer))
    asyncio.run(manager._handle_peer_disconnect(peer))

    assert manager.piece_queue == [0]
    assert manager.peer_current_piece == {}
