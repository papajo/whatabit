import asyncio
from pathlib import Path

import src.download as download_module
from src.bencode import encode
from src.download import DownloadManager


def write_torrent(path: Path, *, announce: bytes, announce_list=None) -> None:
    payload = {
        b"announce": announce,
        b"info": {
            b"name": b"payload.bin",
            b"piece length": 4,
            b"length": 4,
            b"pieces": b"0" * 20,
        },
    }
    if announce_list is not None:
        payload[b"announce-list"] = announce_list
    path.write_bytes(encode(payload))


def test_primary_http_tracker_success_is_recorded(tmp_path, monkeypatch):
    torrent_path = tmp_path / "test.torrent"
    write_torrent(torrent_path, announce=b"http://tracker.example/announce")
    manager = DownloadManager(str(torrent_path), output_dir=str(tmp_path / "downloads"))

    async def fake_http_announce(*args, **kwargs):
        return (1800, 0, 1, [("127.0.0.1", 6881)])

    monkeypatch.setattr(download_module, "http_announce", fake_http_announce)

    peers = asyncio.run(manager.get_peers())

    assert peers == [("127.0.0.1", 6881)]
    assert manager.tracker_attempts == 1
    assert manager.tracker_successes == 1
    assert manager.tracker_failures == 0
    assert manager.tracker_events[-1]["status"] == "success"
    assert manager.get_stats()["tracker_successes"] == 1


def test_announce_list_tracker_failure_and_fallback_are_recorded(tmp_path, monkeypatch):
    torrent_path = tmp_path / "test.torrent"
    write_torrent(
        torrent_path,
        announce=b"http://bad.example/announce",
        announce_list=[[b"http://bad.example/announce"], [b"http://good.example/announce"]],
    )
    manager = DownloadManager(str(torrent_path), output_dir=str(tmp_path / "downloads"))

    async def fake_http_announce(url, *args, **kwargs):
        if "bad" in url:
            raise RuntimeError("tracker down")
        return (1800, 0, 1, [("127.0.0.2", 6881)])

    monkeypatch.setattr(download_module, "http_announce", fake_http_announce)

    peers = asyncio.run(manager.get_peers())

    assert peers == [("127.0.0.2", 6881)]
    assert manager.tracker_attempts == 2
    assert manager.tracker_successes == 1
    assert manager.tracker_failures == 1
    assert [event["status"] for event in manager.tracker_events] == [
        "attempt",
        "failed",
        "attempt",
        "success",
    ]
    assert "tracker down" in manager.tracker_events[1]["error"]
