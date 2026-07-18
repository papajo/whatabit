import asyncio
from pathlib import Path

import src.download as download_module
from src.bencode import encode
from src.download import DownloadManager


def write_torrent(path: Path, *, length: int = 32768) -> None:
    data = {
        b"announce": b"http://tracker.example/announce",
        b"info": {
            b"name": b"payload.bin",
            b"piece length": length,
            b"length": length,
            b"pieces": b"0" * 20,
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


def test_request_next_block_tracks_distinct_inflight_blocks(tmp_path):
    torrent_path = tmp_path / "test.torrent"
    write_torrent(torrent_path, length=download_module.DEFAULT_BLOCK_SIZE * 2)
    manager = DownloadManager(str(torrent_path), output_dir=str(tmp_path / "downloads"))
    peer = FakePeer()

    asyncio.run(manager._request_next_block(peer))
    asyncio.run(manager._request_next_block(peer))

    assert peer.requests == [
        (0, 0, download_module.DEFAULT_BLOCK_SIZE),
        (0, download_module.DEFAULT_BLOCK_SIZE, download_module.DEFAULT_BLOCK_SIZE),
    ]
    assert len(manager.pending_requests) == 2


def test_timed_out_block_becomes_eligible_for_retry(tmp_path, monkeypatch):
    torrent_path = tmp_path / "test.torrent"
    write_torrent(torrent_path, length=download_module.DEFAULT_BLOCK_SIZE)
    manager = DownloadManager(str(torrent_path), output_dir=str(tmp_path / "downloads"))
    peer = FakePeer()

    asyncio.run(manager._request_next_block(peer))
    assert peer.requests == [(0, 0, download_module.DEFAULT_BLOCK_SIZE)]

    original_time = download_module.time.time
    monkeypatch.setattr(
        download_module.time,
        "time",
        lambda: original_time() + download_module.BLOCK_REQUEST_TIMEOUT + 1,
    )

    expired = asyncio.run(manager._check_request_timeouts())
    assert expired == 1
    assert manager.pending_requests == {}
    assert manager.peer_pending_count["127.0.0.1:6881"] == 0

    asyncio.run(manager._request_next_block(peer))
    assert peer.requests[-1] == (0, 0, download_module.DEFAULT_BLOCK_SIZE)
    assert manager.timed_out_requests == 1
