from pathlib import Path

from src.bencode import encode
from src.download import DownloadManager
from src.webui import DownloadJob, WhataBitWebApp


def write_torrent(path: Path, *, name: str = "payload.bin") -> None:
    data = {
        b"announce": b"http://tracker.example/announce",
        b"info": {
            b"name": name.encode(),
            b"piece length": 4,
            b"length": 4,
            b"pieces": b"0" * 20,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode(data))


def test_session_reload_keeps_completed_job_metadata(tmp_path):
    app = WhataBitWebApp(tmp_path)
    app.upload_dir.mkdir(parents=True)
    torrent_path = app.upload_dir / "torrent123-sample.torrent"
    write_torrent(torrent_path)
    app._load_existing_torrents()

    record = app.torrents["torrent123"]
    output_dir = str(tmp_path / "downloads")
    manager = DownloadManager(str(record.path), output_dir=output_dir)
    manager.completed = True
    manager.output_written = True
    manager._set_status("complete", "Completed in test")
    job = DownloadJob(
        id="job123",
        torrent_id=record.id,
        torrent_name=record.metadata["name"],
        output_dir=output_dir,
        manager=manager,
        status="complete",
        settings={"output_dir": output_dir, "max_peers": 10, "max_connections": 5, "port": 6882},
    )
    app.jobs[job.id] = job
    app._save_session()

    restored = WhataBitWebApp(tmp_path)
    restored.upload_dir.mkdir(parents=True, exist_ok=True)
    restored._load_existing_torrents()
    restored._load_existing_jobs()

    assert "job123" in restored.jobs
    restored_job = restored.jobs["job123"]
    assert restored_job.status == "complete"
    assert restored_job.output_dir == output_dir
    assert restored_job.settings["max_peers"] == 10
    assert restored_job.snapshot()["torrent_name"] == "payload.bin"


def test_session_reload_marks_active_jobs_stopped(tmp_path):
    app = WhataBitWebApp(tmp_path)
    app.upload_dir.mkdir(parents=True)
    torrent_path = app.upload_dir / "torrent456-sample.torrent"
    write_torrent(torrent_path)
    app._load_existing_torrents()

    record = app.torrents["torrent456"]
    manager = DownloadManager(str(record.path), output_dir=str(tmp_path / "downloads"))
    job = DownloadJob(
        id="job456",
        torrent_id=record.id,
        torrent_name=record.metadata["name"],
        output_dir=str(tmp_path / "downloads"),
        manager=manager,
        status="running",
    )
    app.jobs[job.id] = job
    app._save_session()

    restored = WhataBitWebApp(tmp_path)
    restored.upload_dir.mkdir(parents=True, exist_ok=True)
    restored._load_existing_torrents()
    restored._load_existing_jobs()

    restored_job = restored.jobs["job456"]
    assert restored_job.status == "stopped"
    assert "restarted" in restored_job.error
    assert restored_job.snapshot()["stats"]["phase"] == "stopped"
