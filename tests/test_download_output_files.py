import asyncio
from pathlib import Path

from src.bencode import encode
from src.download import DownloadManager


def write_torrent(path: Path, info: dict) -> None:
    path.write_bytes(encode({b"announce": b"http://tracker.example/announce", b"info": info}))


def test_single_file_output_uses_part_then_final_path(tmp_path):
    torrent_path = tmp_path / "single.torrent"
    write_torrent(
        torrent_path,
        {b"name": b"payload.bin", b"piece length": 4, b"length": 4, b"pieces": b"0" * 20},
    )
    manager = DownloadManager(str(torrent_path), output_dir=str(tmp_path / "downloads"))
    manager.downloaded_pieces[0] = b"data"
    manager.completed = True

    assert asyncio.run(manager._flush_output()) is True

    assert (tmp_path / "downloads" / "payload.bin").read_bytes() == b"data"
    assert not (tmp_path / "downloads" / "payload.bin.part").exists()


def test_multi_file_output_splits_payload_safely(tmp_path):
    torrent_path = tmp_path / "multi.torrent"
    write_torrent(
        torrent_path,
        {
            b"name": b"album",
            b"piece length": 8,
            b"pieces": b"0" * 20,
            b"files": [
                {b"length": 3, b"path": [b"disc1", b"a.txt"]},
                {b"length": 5, b"path": [b"disc2", b"b.txt"]},
            ],
        },
    )
    manager = DownloadManager(str(torrent_path), output_dir=str(tmp_path / "downloads"))
    manager.downloaded_pieces[0] = b"abcdefgh"
    manager.completed = True

    assert asyncio.run(manager._flush_output()) is True

    root = tmp_path / "downloads" / "album"
    assert (root / "disc1" / "a.txt").read_bytes() == b"abc"
    assert (root / "disc2" / "b.txt").read_bytes() == b"defgh"
    assert manager.get_stats()["output_is_dir"] is True


def test_output_path_rejects_traversal(tmp_path):
    torrent_path = tmp_path / "bad.torrent"
    write_torrent(
        torrent_path,
        {b"name": b"payload.bin", b"piece length": 4, b"length": 4, b"pieces": b"0" * 20},
    )
    manager = DownloadManager(str(torrent_path), output_dir=str(tmp_path / "downloads"))

    try:
        manager._safe_output_path("..", "escape.bin")
    except ValueError as exc:
        assert "Unsafe torrent output path" in str(exc) or "Unsafe torrent output path component" in str(exc)
    else:
        raise AssertionError("path traversal should be rejected")
