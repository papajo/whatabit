import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

from src.bencode import encode
from src.download import DownloadManager
from src.webui import DownloadJob, WhataBitWebApp


def write_torrent(path: Path, *, data: bytes, name: bytes = b"payload.bin") -> None:
    piece_length = 4
    pieces = b"".join(hashlib.sha1(data[i:i+piece_length]).digest() for i in range(0, len(data), piece_length))
    path.write_bytes(encode({
        b"announce": b"http://tracker.example/announce",
        b"info": {b"name": name, b"piece length": piece_length, b"length": len(data), b"pieces": pieces},
    }))


def test_recheck_existing_single_file_marks_complete(tmp_path):
    payload = b"abcdefgh"
    torrent_path = tmp_path / "test.torrent"
    write_torrent(torrent_path, data=payload)
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()
    (output_dir / "payload.bin").write_bytes(payload)

    manager = DownloadManager(str(torrent_path), output_dir=str(output_dir))
    stats = manager.recheck_existing_output()

    assert manager.completed is True
    assert stats["pieces_complete"] == 2
    assert stats["phase"] == "complete"
    assert manager.piece_queue == []


def test_recheck_existing_single_file_keeps_corrupt_piece_queued(tmp_path):
    torrent_path = tmp_path / "test.torrent"
    write_torrent(torrent_path, data=b"abcdefgh")
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()
    (output_dir / "payload.bin").write_bytes(b"abcdxxxx")

    manager = DownloadManager(str(torrent_path), output_dir=str(output_dir))
    stats = manager.recheck_existing_output()

    assert manager.completed is False
    assert stats["pieces_complete"] == 1
    assert manager.downloaded_pieces[0] == b"abcd"
    assert manager.downloaded_pieces[1] is None
    assert manager.piece_queue == [1]


def test_webui_recheck_endpoint_updates_stopped_job(tmp_path):
    async def scenario():
        payload = b"abcdefgh"
        app = WhataBitWebApp(tmp_path)
        app.upload_dir.mkdir(parents=True)
        torrent_path = app.upload_dir / "torrent123-sample.torrent"
        write_torrent(torrent_path, data=payload)
        app._load_existing_torrents()
        record = app.torrents["torrent123"]
        output_dir = tmp_path / "downloads"
        output_dir.mkdir()
        (output_dir / record.metadata["name"]).write_bytes(payload)

        manager = DownloadManager(str(record.path), output_dir=str(output_dir))
        job = DownloadJob(
            id="job123",
            torrent_id=record.id,
            torrent_name=record.metadata["name"],
            output_dir=str(output_dir),
            manager=manager,
            status="stopped",
        )
        app.jobs[job.id] = job

        response = await app.recheck_download(SimpleNamespace(match_info={"job_id": "job123"}))

        assert response.status == 200
        assert app.jobs["job123"].status == "complete"
        assert app.jobs["job123"].snapshot()["stats"]["output_available"] is True
        assert app.session_path.exists()

    asyncio.run(scenario())
