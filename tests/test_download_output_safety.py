import asyncio
from pathlib import Path

from src.bencode import encode
from src.download import DownloadManager


def write_torrent(path: Path, *, name: str = "payload.bin", pieces: bytes = b"0" * 20) -> None:
    data = {
        b"announce": b"http://tracker.example/announce",
        b"info": {
            b"name": name.encode(),
            b"piece length": 4,
            b"length": 4,
            b"pieces": pieces,
        },
    }
    path.write_bytes(encode(data))


def test_flush_output_skips_incomplete_download(tmp_path):
    torrent_path = tmp_path / "test.torrent"
    output_dir = tmp_path / "downloads"
    write_torrent(torrent_path)

    manager = DownloadManager(str(torrent_path), output_dir=str(output_dir))

    written = asyncio.run(manager._flush_output())

    assert written is False
    assert not (output_dir / "payload.bin").exists()
    stats = manager.get_stats()
    assert stats["output_exists"] is False
    assert stats["percent"] == 0
    assert stats["phase"] == "stopped"
    assert stats["status_message"] == "Download incomplete; no output file was written"


def test_flush_output_writes_only_when_complete(tmp_path):
    torrent_path = tmp_path / "test.torrent"
    output_dir = tmp_path / "downloads"
    write_torrent(torrent_path)

    manager = DownloadManager(str(torrent_path), output_dir=str(output_dir))
    manager.downloaded_pieces[0] = b"data"
    manager.completed = True

    written = asyncio.run(manager._flush_output())

    assert written is True
    assert (output_dir / "payload.bin").read_bytes() == b"data"
    stats = manager.get_stats()
    assert stats["output_exists"] is True
    assert stats["output_written"] is True
    assert stats["percent"] == 100
    assert stats["phase"] == "complete"
