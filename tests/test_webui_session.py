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


def test_stop_download_transitions_running_job_to_stopped(tmp_path):
    import asyncio
    import json
    from types import SimpleNamespace

    async def scenario():
        app = WhataBitWebApp(tmp_path)
        app.upload_dir.mkdir(parents=True)
        torrent_path = app.upload_dir / "torrent789-sample.torrent"
        write_torrent(torrent_path)
        app._load_existing_torrents()

        record = app.torrents["torrent789"]
        manager = DownloadManager(str(record.path), output_dir=str(tmp_path / "downloads"))
        job = DownloadJob(
            id="job789",
            torrent_id=record.id,
            torrent_name=record.metadata["name"],
            output_dir=str(tmp_path / "downloads"),
            manager=manager,
            status="running",
        )

        async def active_download():
            await asyncio.sleep(60)

        job.task = asyncio.create_task(active_download())
        app.jobs[job.id] = job

        response = await app.stop_download(SimpleNamespace(match_info={"job_id": "job789"}))
        payload = json.loads(response.text)

        assert payload["job"]["status"] == "stopped"
        assert payload["job"]["stats"]["phase"] == "stopped"
        assert payload["job"]["stats"]["status_message"] == "Download stopped by user; no output file was written"
        assert job.task.done()
        assert app.session_path.exists()

    asyncio.run(scenario())


def test_completed_job_snapshot_exposes_download_link(tmp_path):
    torrent_path = tmp_path / ".whatabit" / "torrents" / "torrent999-sample.torrent"
    write_torrent(torrent_path)
    app = WhataBitWebApp(tmp_path)
    app.upload_dir.mkdir(parents=True, exist_ok=True)
    app._load_existing_torrents()

    record = app.torrents["torrent999"]
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()
    output_file = output_dir / record.metadata["name"]
    output_file.write_bytes(b"complete payload")

    manager = DownloadManager(str(record.path), output_dir=str(output_dir))
    manager.completed = True
    manager.output_written = True
    manager._set_status("complete", "Output written")
    job = DownloadJob(
        id="job999",
        torrent_id=record.id,
        torrent_name=record.metadata["name"],
        output_dir=str(output_dir),
        manager=manager,
        status="complete",
    )

    snapshot = job.snapshot()

    assert snapshot["download_url"] == "/api/downloads/job999/file"
    assert snapshot["stats"]["output_available"] is True
    assert snapshot["stats"]["output_size"] == len(b"complete payload")
    assert snapshot["stats"]["output_filename"] == "payload.bin"


def test_download_output_file_requires_completed_job(tmp_path):
    import asyncio
    from types import SimpleNamespace

    async def scenario():
        app = WhataBitWebApp(tmp_path)
        app.upload_dir.mkdir(parents=True)
        torrent_path = app.upload_dir / "torrent998-sample.torrent"
        write_torrent(torrent_path)
        app._load_existing_torrents()
        record = app.torrents["torrent998"]

        output_dir = tmp_path / "downloads"
        output_dir.mkdir()
        (output_dir / record.metadata["name"]).write_bytes(b"payload")
        manager = DownloadManager(str(record.path), output_dir=str(output_dir))
        job = DownloadJob(
            id="job998",
            torrent_id=record.id,
            torrent_name=record.metadata["name"],
            output_dir=str(output_dir),
            manager=manager,
            status="stopped",
        )
        app.jobs[job.id] = job

        try:
            await app.download_output_file(SimpleNamespace(match_info={"job_id": "job998"}))
        except Exception as exc:
            assert exc.status == 409
        else:
            raise AssertionError("expected incomplete job to be rejected")

    asyncio.run(scenario())


def test_download_output_file_returns_attachment_response(tmp_path):
    import asyncio
    from types import SimpleNamespace

    async def scenario():
        app = WhataBitWebApp(tmp_path)
        app.upload_dir.mkdir(parents=True)
        torrent_path = app.upload_dir / "torrent997-sample.torrent"
        write_torrent(torrent_path, name='payload "quoted".bin')
        app._load_existing_torrents()
        record = app.torrents["torrent997"]

        output_dir = tmp_path / "downloads"
        output_dir.mkdir()
        output_file = output_dir / record.metadata["name"]
        output_file.write_bytes(b"payload")

        manager = DownloadManager(str(record.path), output_dir=str(output_dir))
        manager.completed = True
        job = DownloadJob(
            id="job997",
            torrent_id=record.id,
            torrent_name=record.metadata["name"],
            output_dir=str(output_dir),
            manager=manager,
            status="complete",
        )
        app.jobs[job.id] = job

        response = await app.download_output_file(SimpleNamespace(match_info={"job_id": "job997"}))

        assert response.status == 200
        assert response.headers["X-WhataBit-Job-Id"] == "job997"
        assert response.headers["Content-Disposition"].startswith("attachment;")
        assert 'filename="payload _quoted_.bin"' in response.headers["Content-Disposition"]

    asyncio.run(scenario())
