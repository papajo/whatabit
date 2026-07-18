"""Aiohttp-based Web UI for WhataBit.

The UI is intentionally lightweight: it serves a single-page app with embedded
CSS/JavaScript and exposes small JSON endpoints for torrent upload, metadata,
download start/stop, and live progress polling.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiohttp import web

from .download import DownloadManager
from .torrent import TorrentFile

APP_DIR = ".whatabit"
UPLOAD_DIR = "torrents"
SESSION_FILE = "session.json"
DEFAULT_DOWNLOAD_DIR = "downloads"


@dataclass
class TorrentRecord:
    """A torrent uploaded to the local UI workspace."""

    id: str
    filename: str
    path: Path
    added_at: float
    metadata: dict[str, Any]


@dataclass
class DownloadJob:
    """State for one background download task."""

    id: str
    torrent_id: str
    torrent_name: str
    output_dir: str
    manager: DownloadManager
    task: asyncio.Task | None = None
    status: str = "queued"
    error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    progress: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        stats = self.manager.get_stats()
        merged = {**stats, **self.progress}
        output_path = Path(str(merged.get("output_path") or (Path(self.output_dir).expanduser() / self.torrent_name)))
        return {
            "id": self.id,
            "torrent_id": self.torrent_id,
            "torrent_name": self.torrent_name,
            "output_dir": self.output_dir,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "settings": self.settings,
            "download_url": f"/api/downloads/{self.id}/file" if output_path.exists() and self.status == "complete" else "",
            "stats": merged,
        }


class WhataBitWebApp:
    """Small aiohttp application wrapper for WhataBit."""

    def __init__(self, base_dir: str | os.PathLike[str] = "."):
        self.base_dir = Path(base_dir).resolve()
        self.app_dir = self.base_dir / APP_DIR
        self.upload_dir = self.app_dir / UPLOAD_DIR
        self.session_path = self.app_dir / SESSION_FILE
        self.default_download_dir = self.base_dir / DEFAULT_DOWNLOAD_DIR
        self.torrents: dict[str, TorrentRecord] = {}
        self.jobs: dict[str, DownloadJob] = {}

    def create_app(self) -> web.Application:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.default_download_dir.mkdir(parents=True, exist_ok=True)
        self._load_existing_torrents()
        self._load_existing_jobs()

        app = web.Application(client_max_size=64 * 1024 * 1024)
        app["whatabit"] = self
        app.router.add_get("/", self.index)
        app.router.add_get("/api/torrents", self.list_torrents)
        app.router.add_post("/api/torrents", self.upload_torrent)
        app.router.add_delete("/api/torrents/{torrent_id}", self.delete_torrent)
        app.router.add_post("/api/downloads", self.start_download)
        app.router.add_get("/api/downloads", self.list_downloads)
        app.router.add_get("/api/downloads/{job_id}", self.get_download)
        app.router.add_get("/api/downloads/{job_id}/file", self.download_output_file)
        app.router.add_post("/api/downloads/{job_id}/stop", self.stop_download)
        app.on_shutdown.append(self.shutdown)
        return app

    def _load_existing_torrents(self) -> None:
        """Load previously uploaded torrent files from the local UI workspace."""

        self.torrents.clear()
        for path in sorted(self.upload_dir.glob("*.torrent")):
            torrent_id = path.name.split("-", 1)[0]
            if not torrent_id:
                continue
            try:
                metadata = torrent_metadata(path)
            except Exception:
                continue
            filename = path.name.split("-", 1)[1] if "-" in path.name else path.name
            self.torrents[torrent_id] = TorrentRecord(
                id=torrent_id,
                filename=filename,
                path=path,
                added_at=path.stat().st_mtime,
                metadata=metadata,
            )

    def _load_existing_jobs(self) -> None:
        """Load persisted job metadata from the local UI workspace.

        WhataBit 0.2 persists job metadata, not in-flight piece data. Jobs that
        were active when the app last exited are shown as stopped after restart.
        """

        self.jobs.clear()
        if not self.session_path.exists():
            return

        try:
            data = json.loads(self.session_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        for raw in data.get("jobs", []):
            if not isinstance(raw, dict):
                continue
            torrent_id = str(raw.get("torrent_id") or "")
            record = self.torrents.get(torrent_id)
            if record is None:
                continue

            settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
            output_dir = str(raw.get("output_dir") or settings.get("output_dir") or self.default_download_dir)
            manager = DownloadManager(
                torrent_path=str(record.path),
                output_dir=output_dir,
                max_peers=clamp_int(settings.get("max_peers"), default=50, minimum=1, maximum=500),
                max_connections=clamp_int(settings.get("max_connections"), default=20, minimum=1, maximum=100),
                port=clamp_int(settings.get("port"), default=6881, minimum=1, maximum=65535),
            )

            stats = raw.get("stats") if isinstance(raw.get("stats"), dict) else {}
            status = str(raw.get("status") or "stopped")
            error = str(raw.get("error") or "")
            if status in {"queued", "running", "stopping"}:
                status = "stopped"
                error = error or "Stopped because the Web UI restarted before this job completed."
                manager._set_status("stopped", error)
            elif status == "complete":
                manager.completed = bool(stats.get("is_complete", True))
                manager.output_written = bool(stats.get("output_written") or manager.get_stats().get("output_exists"))
                manager._set_status("complete", str(stats.get("status_message") or "Completed in a previous session"))
            elif status == "error":
                manager._set_status("error", error or "Failed in a previous session", error=error)
            else:
                manager._set_status("stopped", str(stats.get("status_message") or "Stopped in a previous session"))

            job = DownloadJob(
                id=str(raw.get("id") or uuid.uuid4().hex),
                torrent_id=torrent_id,
                torrent_name=str(raw.get("torrent_name") or record.metadata["name"]),
                output_dir=output_dir,
                manager=manager,
                status=status,
                error=error,
                created_at=float(raw.get("created_at") or time.time()),
                updated_at=float(raw.get("updated_at") or time.time()),
                progress={
                    "pieces_complete": int(stats.get("pieces_complete") or 0),
                    "pieces_total": int(stats.get("pieces_total") or record.metadata.get("pieces") or 0),
                    "downloaded": int(stats.get("downloaded") or 0),
                    "speed": 0,
                    "percent": float(stats.get("percent") or 0),
                },
                settings=settings,
            )
            self.jobs[job.id] = job

    def _save_session(self) -> None:
        """Persist durable Web UI job metadata."""

        self.app_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": time.time(),
            "jobs": [job.snapshot() for job in self.jobs.values()],
        }
        tmp_path = self.session_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.session_path)

    async def shutdown(self, _app: web.Application) -> None:
        for job in self.jobs.values():
            job.manager.stop()
            if job.task and not job.task.done():
                job.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await job.task
        self._save_session()

    async def index(self, _request: web.Request) -> web.Response:
        return web.Response(text=INDEX_HTML, content_type="text/html")

    async def list_torrents(self, _request: web.Request) -> web.Response:
        return web.json_response({"torrents": [record_to_json(t) for t in self.torrents.values()]})

    async def upload_torrent(self, request: web.Request) -> web.Response:
        reader = await request.multipart()
        field = await reader.next()
        if field is None or field.name != "torrent":
            raise web.HTTPBadRequest(text="Expected multipart field named 'torrent'")

        original_name = Path(field.filename or "upload.torrent").name
        if not original_name.lower().endswith(".torrent"):
            raise web.HTTPBadRequest(text="Please upload a .torrent file")

        torrent_id = uuid.uuid4().hex
        safe_name = safe_filename(original_name)
        target = self.upload_dir / f"{torrent_id}-{safe_name}"

        with target.open("wb") as output:
            while chunk := await field.read_chunk():
                output.write(chunk)

        try:
            metadata = torrent_metadata(target)
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise web.HTTPBadRequest(text=f"Invalid torrent file: {exc}") from exc

        record = TorrentRecord(
            id=torrent_id,
            filename=original_name,
            path=target,
            added_at=time.time(),
            metadata=metadata,
        )
        self.torrents[torrent_id] = record
        self._save_session()
        return web.json_response({"torrent": record_to_json(record)}, status=201)

    async def delete_torrent(self, request: web.Request) -> web.Response:
        torrent_id = request.match_info["torrent_id"]
        record = self.torrents.get(torrent_id)
        if record is None:
            raise web.HTTPNotFound(text="Torrent not found")

        active_statuses = {"queued", "running", "stopping"}
        if any(job.torrent_id == torrent_id and job.status in active_statuses for job in self.jobs.values()):
            raise web.HTTPConflict(text="Cannot delete a torrent while it has an active download")

        self.torrents.pop(torrent_id, None)
        record.path.unlink(missing_ok=True)
        self._save_session()
        return web.json_response({"ok": True})

    async def start_download(self, request: web.Request) -> web.Response:
        data = await request.json()
        torrent_id = str(data.get("torrent_id") or "")
        record = self.torrents.get(torrent_id)
        if record is None:
            raise web.HTTPNotFound(text="Torrent not found")

        output_dir = self._resolve_output_dir(str(data.get("output_dir") or DEFAULT_DOWNLOAD_DIR))
        max_peers = clamp_int(data.get("max_peers"), default=50, minimum=1, maximum=500)
        max_connections = clamp_int(data.get("max_connections"), default=20, minimum=1, maximum=100)
        port = clamp_int(data.get("port"), default=6881, minimum=1, maximum=65535)

        job_id = uuid.uuid4().hex

        def progress_callback(completed: int, total: int, downloaded: int, speed: float) -> None:
            job = self.jobs.get(job_id)
            if not job:
                return
            job.progress = {
                "pieces_complete": completed,
                "pieces_total": total,
                "downloaded": downloaded,
                "speed": speed,
                "percent": (completed / total * 100) if total else 0,
            }
            job.updated_at = time.time()
            self._save_session()

        manager = DownloadManager(
            torrent_path=str(record.path),
            output_dir=output_dir,
            max_peers=max_peers,
            max_connections=max_connections,
            port=port,
            progress_callback=progress_callback,
        )
        job = DownloadJob(
            id=job_id,
            torrent_id=torrent_id,
            torrent_name=record.metadata["name"],
            output_dir=output_dir,
            manager=manager,
            progress={
                "pieces_complete": 0,
                "pieces_total": record.metadata["pieces"],
                "downloaded": 0,
                "speed": 0,
                "percent": 0,
            },
            settings={
                "output_dir": output_dir,
                "max_peers": max_peers,
                "max_connections": max_connections,
                "port": port,
            },
        )
        self.jobs[job_id] = job
        self._save_session()
        job.task = asyncio.create_task(self._run_job(job))
        return web.json_response({"job": job.snapshot()}, status=201)

    def _resolve_output_dir(self, value: str) -> str:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.base_dir / path
        return str(path.resolve(strict=False))

    async def _run_job(self, job: DownloadJob) -> None:
        job.status = "running"
        job.updated_at = time.time()
        self._save_session()
        try:
            await job.manager.download()
            job.status = "complete" if job.manager.completed else "stopped"
        except asyncio.CancelledError:
            job.manager.stop()
            job.status = "stopped"
            raise
        except Exception as exc:
            job.status = "error"
            job.error = str(exc)
        finally:
            job.updated_at = time.time()
            self._save_session()

    async def list_downloads(self, _request: web.Request) -> web.Response:
        return web.json_response({"jobs": [job.snapshot() for job in self.jobs.values()]})

    async def get_download(self, request: web.Request) -> web.Response:
        job = self.jobs.get(request.match_info["job_id"])
        if job is None:
            raise web.HTTPNotFound(text="Download job not found")
        return web.json_response({"job": job.snapshot()})

    async def download_output_file(self, request: web.Request) -> web.StreamResponse:
        job = self.jobs.get(request.match_info["job_id"])
        if job is None:
            raise web.HTTPNotFound(text="Download job not found")

        output_path = Path(job.output_dir) / job.torrent_name
        if not output_path.exists() or not output_path.is_file():
            raise web.HTTPNotFound(text="Downloaded file is not available yet")
        return web.FileResponse(output_path)

    async def stop_download(self, request: web.Request) -> web.Response:
        job = self.jobs.get(request.match_info["job_id"])
        if job is None:
            raise web.HTTPNotFound(text="Download job not found")
        job.manager.stop()
        if job.task and not job.task.done():
            job.task.cancel()
        job.status = "stopping"
        job.updated_at = time.time()
        self._save_session()
        return web.json_response({"job": job.snapshot()})


def torrent_metadata(path: Path) -> dict[str, Any]:
    torrent = TorrentFile(str(path))
    return {
        "name": torrent.name,
        "announce": torrent.announce,
        "announce_list_count": sum(len(tier) for tier in torrent.announce_list or []),
        "info_hash": torrent.info_hash_hex,
        "piece_length": torrent.piece_length,
        "pieces": len(torrent.pieces),
        "total_length": torrent.total_length,
        "is_multi_file": torrent.is_multi_file,
        "files": torrent.files,
    }


def record_to_json(record: TorrentRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "filename": record.filename,
        "added_at": record.added_at,
        "metadata": record.metadata,
    }


def safe_filename(name: str) -> str:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- ")
    cleaned = "".join(ch for ch in name if ch in allowed).strip().replace(" ", "-")
    return cleaned or "upload.torrent"


def clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def run_webui(host: str = "127.0.0.1", port: int = 8080, base_dir: str = ".") -> None:
    """Run the WhataBit Web UI until interrupted."""

    web_app = WhataBitWebApp(base_dir=base_dir)
    app = web_app.create_app()
    web.run_app(app, host=host, port=port)


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>WhataBit</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0b1020;
      --panel: rgba(17, 24, 39, 0.78);
      --panel-strong: rgba(15, 23, 42, 0.94);
      --border: rgba(148, 163, 184, 0.22);
      --text: #e5e7eb;
      --muted: #94a3b8;
      --accent: #8b5cf6;
      --accent-2: #06b6d4;
      --danger: #ef4444;
      --good: #22c55e;
      --warn: #f59e0b;
      --shadow: 0 24px 80px rgba(0, 0, 0, 0.38);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      background:
        radial-gradient(circle at 15% 10%, rgba(139, 92, 246, 0.22), transparent 28rem),
        radial-gradient(circle at 85% 12%, rgba(6, 182, 212, 0.16), transparent 30rem),
        linear-gradient(135deg, #060913 0%, #111827 52%, #0b1020 100%);
    }
    .shell { width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 32px 0 48px; }
    header { display: flex; justify-content: space-between; gap: 24px; align-items: center; margin-bottom: 28px; }
    .brand { display: flex; align-items: center; gap: 14px; }
    .logo { width: 52px; height: 52px; border-radius: 18px; display: grid; place-items: center; background: linear-gradient(135deg, var(--accent), var(--accent-2)); box-shadow: var(--shadow); font-size: 28px; }
    h1 { margin: 0; font-size: clamp(2rem, 5vw, 4.2rem); letter-spacing: -0.06em; }
    .tagline { margin: 6px 0 0; color: var(--muted); }
    .badge { border: 1px solid var(--border); border-radius: 999px; padding: 8px 12px; color: var(--muted); background: rgba(255,255,255,0.04); white-space: nowrap; }
    .grid { display: grid; grid-template-columns: 0.95fr 1.05fr; gap: 18px; align-items: start; }
    .card { background: var(--panel); border: 1px solid var(--border); border-radius: 24px; box-shadow: var(--shadow); backdrop-filter: blur(18px); overflow: hidden; }
    .card h2 { margin: 0 0 6px; font-size: 1.05rem; }
    .card p { color: var(--muted); margin: 0; line-height: 1.5; }
    .card-body { padding: 22px; }
    .dropzone { margin-top: 18px; border: 1.5px dashed rgba(148,163,184,0.45); border-radius: 20px; padding: 28px; text-align: center; background: rgba(2,6,23,0.42); transition: 160ms ease; }
    .dropzone.drag { border-color: var(--accent-2); transform: translateY(-1px); background: rgba(6,182,212,0.09); }
    input[type="file"] { display: none; }
    button, .file-label { border: 0; border-radius: 14px; padding: 11px 16px; background: linear-gradient(135deg, var(--accent), #6366f1); color: white; font-weight: 700; cursor: pointer; display: inline-flex; gap: 8px; align-items: center; justify-content: center; }
    button.secondary { background: rgba(148,163,184,0.12); color: var(--text); border: 1px solid var(--border); }
    button.danger { background: rgba(239,68,68,0.16); color: #fecaca; border: 1px solid rgba(239,68,68,0.3); }
    button:disabled { opacity: 0.55; cursor: not-allowed; }
    .muted { color: var(--muted); }
    .stack { display: grid; gap: 14px; }
    .row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
    .meta { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 18px; }
    .stat { background: rgba(15,23,42,0.72); border: 1px solid var(--border); border-radius: 16px; padding: 14px; min-width: 0; }
    .stat .label { color: var(--muted); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.08em; }
    .stat .value { margin-top: 5px; font-size: 1rem; overflow-wrap: anywhere; }
    .settings { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 18px; }
    label.field { display: grid; gap: 7px; color: var(--muted); font-size: 0.85rem; }
    input[type="text"], input[type="number"] { width: 100%; border-radius: 12px; border: 1px solid var(--border); background: rgba(2,6,23,0.58); color: var(--text); padding: 11px 12px; outline: none; }
    input:focus { border-color: rgba(6,182,212,0.7); box-shadow: 0 0 0 3px rgba(6,182,212,0.12); }
    .job { border-top: 1px solid var(--border); padding: 18px 22px; background: rgba(2,6,23,0.25); }
    .job:first-child { border-top: 0; }
    .job-title { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; margin-bottom: 12px; }
    .pill { border-radius: 999px; padding: 5px 9px; font-size: 0.78rem; border: 1px solid var(--border); color: var(--muted); }
    .pill.running { color: #bae6fd; border-color: rgba(6,182,212,0.35); background: rgba(6,182,212,0.12); }
    .pill.complete { color: #bbf7d0; border-color: rgba(34,197,94,0.35); background: rgba(34,197,94,0.12); }
    .pill.error { color: #fecaca; border-color: rgba(239,68,68,0.35); background: rgba(239,68,68,0.12); }
    .pill.stopped, .pill.stopping, .pill.no_peers { color: #fde68a; border-color: rgba(245,158,11,0.35); background: rgba(245,158,11,0.12); }
    .bar { height: 12px; border-radius: 999px; background: rgba(148,163,184,0.16); overflow: hidden; border: 1px solid rgba(148,163,184,0.16); }
    .bar > div { height: 100%; width: 0%; border-radius: inherit; background: linear-gradient(90deg, var(--accent), var(--accent-2)); transition: width 250ms ease; }
    .empty { padding: 48px 22px; text-align: center; color: var(--muted); }
    .toast { position: fixed; right: 18px; bottom: 18px; max-width: 420px; background: var(--panel-strong); border: 1px solid var(--border); border-radius: 16px; padding: 14px 16px; box-shadow: var(--shadow); display: none; }
    .toast.show { display: block; }
    .file-list { max-height: 150px; overflow: auto; margin-top: 12px; border: 1px solid var(--border); border-radius: 14px; }
    .file-row { padding: 9px 12px; display: flex; justify-content: space-between; gap: 10px; border-top: 1px solid rgba(148,163,184,0.12); color: var(--muted); font-size: 0.86rem; }
    .file-row:first-child { border-top: 0; }
    .job-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-top: 12px; }
    .metric { border: 1px solid var(--border); border-radius: 14px; padding: 10px; background: rgba(15,23,42,0.48); }
    .metric .label { color: var(--muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em; }
    .metric .value { margin-top: 4px; font-size: 0.9rem; overflow-wrap: anywhere; }
    .torrent-list { display: grid; gap: 10px; margin-top: 16px; }
    .torrent-item { padding: 12px; border: 1px solid var(--border); border-radius: 16px; background: rgba(2,6,23,0.34); }
    .torrent-item.active { border-color: rgba(6,182,212,0.55); background: rgba(6,182,212,0.08); }
    .tiny { font-size: 0.78rem; }
    @media (max-width: 860px) { .grid, .meta, .settings, .job-metrics { grid-template-columns: 1fr; } header { align-items: flex-start; flex-direction: column; } }
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <div class="brand">
        <div class="logo">🧲</div>
        <div>
          <h1>WhataBit</h1>
          <p class="tagline">A small Python BitTorrent client with a local control panel.</p>
        </div>
      </div>
      <div class="badge">Local UI · Educational client</div>
    </header>

    <section class="grid">
      <div class="card">
        <div class="card-body">
          <h2>1. Add a torrent</h2>
          <p>Upload a legal `.torrent` file to inspect its metadata and start a controlled download.</p>
          <div id="dropzone" class="dropzone">
            <p style="font-size:2rem; margin-bottom:10px;">⬆️</p>
            <p>Drop a `.torrent` file here</p>
            <p class="muted" style="margin: 8px 0 16px;">or</p>
            <label class="file-label" for="torrentInput">Choose torrent</label>
            <input id="torrentInput" type="file" accept=".torrent,application/x-bittorrent" />
          </div>
          <div class="row" style="justify-content:space-between; margin-top:18px;">
            <h2 style="margin:0;">Uploaded torrents</h2>
            <button id="refreshTorrentsBtn" class="secondary" type="button">Refresh</button>
          </div>
          <p class="muted tiny" style="margin-top:6px;">Uploaded `.torrent` files are saved locally in <code>.whatabit/torrents/</code> so they are available after restarting the UI.</p>
          <div id="torrentList" class="torrent-list"><div class="empty" style="padding:20px;">No uploaded torrents yet.</div></div>
          <div id="torrentDetails" class="meta" style="display:none;"></div>
          <div id="fileList" class="file-list" style="display:none;"></div>
        </div>
      </div>

      <div class="card">
        <div class="card-body">
          <h2>2. Download settings</h2>
          <p>Start gently while testing. Downloads are saved on the machine running this Web UI.</p>
          <div class="settings">
            <label class="field">Output directory
              <input id="outputDir" type="text" value="downloads" />
            </label>
            <label class="field">Listening port
              <input id="port" type="number" min="1" max="65535" value="6881" />
            </label>
            <label class="field">Max peers
              <input id="maxPeers" type="number" min="1" max="500" value="50" />
            </label>
            <label class="field">Max connections
              <input id="maxConnections" type="number" min="1" max="100" value="20" />
            </label>
          </div>
          <div class="row" style="margin-top:18px;">
            <button id="startBtn" disabled>Start download</button>
            <button id="refreshBtn" class="secondary">Refresh jobs</button>
          </div>
        </div>
      </div>
    </section>

    <section class="card" style="margin-top:18px;">
      <div class="card-body">
        <h2>Downloads</h2>
        <p>Progress updates automatically while the page is open.</p>
      </div>
      <div id="jobs"><div class="empty">No downloads yet.</div></div>
    </section>
  </main>
  <div id="toast" class="toast"></div>

<script>
const state = { torrent: null, torrents: [], jobs: [] };
const $ = (id) => document.getElementById(id);
const fmtBytes = (bytes = 0) => {
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let n = Number(bytes) || 0;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
};
const fmtSpeed = (bytes = 0) => `${fmtBytes(bytes)}/s`;
const fmtDuration = (seconds = 0) => {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const mins = Math.floor(total / 60);
  const secs = total % 60;
  return mins ? `${mins}m ${secs}s` : `${secs}s`;
};
const toast = (message) => {
  const el = $('toast');
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove('show'), 4200);
};
async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(await res.text() || res.statusText);
  return res.json();
}
function renderTorrent(record) {
  state.torrent = record;
  $('startBtn').disabled = false;
  renderTorrentList(state.torrents);
  const m = record.metadata;
  $('torrentDetails').style.display = 'grid';
  $('torrentDetails').innerHTML = [
    ['Name', m.name],
    ['Size', fmtBytes(m.total_length)],
    ['Pieces', `${m.pieces} × ${fmtBytes(m.piece_length)}`],
    ['Info hash', m.info_hash],
    ['Tracker', m.announce || 'none'],
    ['Mode', m.is_multi_file ? 'Multi-file' : 'Single-file'],
  ].map(([label, value]) => `<div class="stat"><div class="label">${label}</div><div class="value">${escapeHtml(value)}</div></div>`).join('');
  if (m.files && m.files.length) {
    $('fileList').style.display = 'block';
    $('fileList').innerHTML = m.files.slice(0, 60).map(file =>
      `<div class="file-row"><span>${escapeHtml(file.path)}</span><span>${fmtBytes(file.length)}</span></div>`
    ).join('');
  } else {
    $('fileList').style.display = 'none';
  }
}

function renderTorrentList(torrents) {
  state.torrents = torrents;
  const root = $('torrentList');
  if (!torrents.length) {
    root.innerHTML = '<div class="empty" style="padding:20px;">No uploaded torrents yet.</div>';
    return;
  }
  root.innerHTML = torrents.map(record => {
    const active = state.torrent && state.torrent.id === record.id;
    const m = record.metadata || {};
    return `<div class="torrent-item ${active ? 'active' : ''}">
      <div class="row" style="justify-content:space-between; align-items:flex-start;">
        <div>
          <strong>${escapeHtml(m.name || record.filename)}</strong>
          <div class="muted tiny" style="margin-top:4px;">${fmtBytes(m.total_length)} · ${escapeHtml(record.filename)}</div>
        </div>
        <div class="row">
          <button class="secondary" type="button" onclick="selectTorrent('${record.id}')">${active ? 'Selected' : 'Select'}</button>
          <button class="danger" type="button" onclick="deleteTorrent('${record.id}')">Delete</button>
        </div>
      </div>
    </div>`;
  }).join('');
}
async function refreshTorrents() {
  const data = await api('/api/torrents');
  renderTorrentList(data.torrents || []);
  if (!state.torrent && state.torrents.length) renderTorrent(state.torrents[0]);
}
function selectTorrent(id) {
  const record = state.torrents.find(item => item.id === id);
  if (record) renderTorrent(record);
}
async function deleteTorrent(id) {
  await api(`/api/torrents/${id}`, { method: 'DELETE' });
  if (state.torrent && state.torrent.id === id) {
    state.torrent = null;
    $('startBtn').disabled = true;
    $('torrentDetails').style.display = 'none';
    $('fileList').style.display = 'none';
  }
  await refreshTorrents();
  toast('Uploaded torrent removed.');
}

function renderJobs(jobs) {
  state.jobs = jobs;
  const root = $('jobs');
  if (!jobs.length) {
    root.innerHTML = '<div class="empty">No downloads yet.</div>';
    return;
  }
  root.innerHTML = jobs.map(job => {
    const stats = job.stats || {};
    const total = stats.pieces_total || 0;
    const done = stats.pieces_complete || 0;
    const pct = total ? (done / total * 100) : (stats.percent || 0);
    const phase = stats.phase || job.status;
    const statusClass = ['running', 'complete', 'error', 'stopped', 'stopping', 'no_peers'].includes(job.status) ? job.status : phase;
    return `<article class="job">
      <div class="job-title">
        <div>
          <strong>${escapeHtml(job.torrent_name)}</strong>
          <div class="muted" style="margin-top:4px;">${escapeHtml(job.output_dir)}</div>
        </div>
        <span class="pill ${statusClass}">${escapeHtml(job.status)} · ${escapeHtml(phase)}</span>
      </div>
      <div class="bar"><div style="width:${Math.max(0, Math.min(100, pct))}%"></div></div>
      <div class="row muted" style="justify-content:space-between; margin-top:10px;">
        <span>${done}/${total} pieces · ${pct.toFixed(1)}%</span>
        <span>${fmtBytes(stats.downloaded)} · ${fmtSpeed(stats.speed)}</span>
      </div>
      <p class="muted" style="margin-top:10px;">${escapeHtml(stats.status_message || 'Waiting for status update')}</p>
      <div class="job-metrics">
        <div class="metric"><div class="label">Connected</div><div class="value">${stats.connected_peers || 0}</div></div>
        <div class="metric"><div class="label">Discovered</div><div class="value">${stats.peers_discovered || 0}</div></div>
        <div class="metric"><div class="label">Queued</div><div class="value">${stats.queued_peers || 0}</div></div>
        <div class="metric"><div class="label">Elapsed</div><div class="value">${fmtDuration(stats.elapsed || 0)}</div></div>
        <div class="metric"><div class="label">Output</div><div class="value">${escapeHtml(stats.output_exists ? 'Available' : 'Not written yet')}</div></div>
        <div class="metric"><div class="label">Path</div><div class="value">${escapeHtml(stats.output_path || job.output_dir)}</div></div>
      </div>
      ${(job.error || stats.last_error) ? `<p style="color:#fecaca; margin-top:10px;">${escapeHtml(job.error || stats.last_error)}</p>` : ''}
      <div class="row" style="margin-top:12px;">
        ${job.download_url ? `<a class="file-label" href="${job.download_url}">Download file</a>` : ''}
        ${['running','queued'].includes(job.status) ? `<button class="danger" onclick="stopJob('${job.id}')">Stop</button>` : ''}
      </div>
    </article>`;
  }).join('');
}
async function uploadTorrent(file) {
  const form = new FormData();
  form.append('torrent', file);
  toast('Uploading torrent…');
  const data = await api('/api/torrents', { method: 'POST', body: form });
  await refreshTorrents();
  renderTorrent(data.torrent);
  toast('Torrent loaded. Review details and start when ready.');
}
async function startDownload() {
  if (!state.torrent) return;
  const payload = {
    torrent_id: state.torrent.id,
    output_dir: $('outputDir').value || 'downloads',
    max_peers: $('maxPeers').value,
    max_connections: $('maxConnections').value,
    port: $('port').value,
  };
  const data = await api('/api/downloads', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  toast('Download started.');
  await refreshJobs();
}
async function refreshJobs() {
  const data = await api('/api/downloads');
  renderJobs(data.jobs || []);
}
async function stopJob(id) {
  await api(`/api/downloads/${id}/stop`, { method: 'POST' });
  toast('Stopping download…');
  await refreshJobs();
}
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
}
$('torrentInput').addEventListener('change', (event) => {
  const file = event.target.files[0];
  if (file) uploadTorrent(file).catch(err => toast(err.message));
});
$('startBtn').addEventListener('click', () => startDownload().catch(err => toast(err.message)));
$('refreshBtn').addEventListener('click', () => refreshJobs().catch(err => toast(err.message)));
$('refreshTorrentsBtn').addEventListener('click', () => refreshTorrents().catch(err => toast(err.message)));
const dropzone = $('dropzone');
for (const eventName of ['dragenter', 'dragover']) {
  dropzone.addEventListener(eventName, event => { event.preventDefault(); dropzone.classList.add('drag'); });
}
for (const eventName of ['dragleave', 'drop']) {
  dropzone.addEventListener(eventName, event => { event.preventDefault(); dropzone.classList.remove('drag'); });
}
dropzone.addEventListener('drop', event => {
  const file = event.dataTransfer.files[0];
  if (file) uploadTorrent(file).catch(err => toast(err.message));
});
setInterval(() => refreshJobs().catch(() => {}), 1500);
refreshTorrents().catch(() => {});
refreshJobs().catch(() => {});
</script>
</body>
</html>"""
