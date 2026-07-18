# WhataBit Product Backlog

## Priority Legend

- **P0**: Required for WhataBit 0.2 viability.
- **P1**: Important for 0.2 polish or reliability.
- **P2**: Nice-to-have after core reliability.
- **Later**: Explicitly deferred beyond 0.2.

## Epics

### E1 — Web UI Torrent Workflow

| ID | Priority | Story | Acceptance Criteria | Status |
| --- | --- | --- | --- | --- |
| UI-001 | P0 | As a user, I can start a local Web UI with `python main.py --ui`. | App starts on localhost, help text documents flags, README includes URL. | Done |
| UI-002 | P0 | As a user, I can upload a `.torrent` file and inspect metadata before downloading. | Name, size, tracker, pieces, hash, and file list render; invalid torrents show a clear error. | Done |
| UI-003 | P0 | As a user, I understand what happens to uploaded torrent files. | UI and README explain `.whatabit/torrents/`; Delete removes stored metadata file when safe. | Done |
| UI-004 | P0 | As a user, I can start and stop a download from the Web UI. | Start creates a job; stop cancels active work; status updates visibly. | In Progress |
| UI-005 | P1 | As a user, I can see reliable live progress. | UI shows pieces, percent, downloaded bytes, speed, connected peers, status, and errors. | In Progress |
| UI-006 | P1 | As a user, I can download/open completed output from the browser. | Completed single-file output exposes a safe download link. | Partial |
| UI-007 | P1 | As a user, I can resume a previous UI session. | Uploaded torrent library and job/session metadata reload after restart. | Partial |

### E2 — Reliable Download Engine

| ID | Priority | Story | Acceptance Criteria | Status |
| --- | --- | --- | --- | --- |
| DL-001 | P0 | As a downloader, incomplete downloads must not be marked or written as complete. | Stopped/incomplete jobs do not produce misleading zero-filled final output. | Todo |
| DL-002 | P0 | As a downloader, blocks have request timeouts and retries. | Missing blocks are retried; stalled peers do not stall the whole job indefinitely. | Todo |
| DL-003 | P0 | As a downloader, piece hashes are enforced. | Bad pieces are discarded/requeued and peers can be penalized. | Partial |
| DL-004 | P0 | As a downloader, pieces are written safely. | Single-file output is assembled correctly; multi-file behavior is defined and tested. | Todo |
| DL-005 | P1 | As a downloader, peer scheduling avoids duplicate waste. | Per-peer request windows and in-flight block tracking prevent uncontrolled duplicates. | Todo |
| DL-006 | P1 | As a downloader, tracker fallback is observable. | Tracker attempts and failures are visible in logs/status. | Todo |

### E3 — Persistence and State

| ID | Priority | Story | Acceptance Criteria | Status |
| --- | --- | --- | --- | --- |
| ST-001 | P0 | As a user, uploaded torrents survive UI restart. | Existing `.whatabit/torrents/*.torrent` files load at startup. | Done |
| ST-002 | P0 | As a user, active/completed job metadata survives restart. | Job list reloads with previous statuses and output paths. | Todo |
| ST-003 | P1 | As a user, partial progress can be resumed or rechecked. | Completed pieces are persisted or rechecked before continuing. | Todo |
| ST-004 | P1 | As a user, I can remove torrent metadata without deleting downloaded payloads. | Delete action is explicit and safe. | Done |

### E4 — Quality, Safety, and Documentation

| ID | Priority | Story | Acceptance Criteria | Status |
| --- | --- | --- | --- | --- |
| QA-001 | P0 | As a maintainer, I have project planning docs. | Charter, backlog, sprint plan, WBS, and risks exist and are updated. | Done |
| QA-002 | P0 | As a maintainer, generated/private files stay out of Git. | `.gitignore` covers `.whatabit/`, downloads, torrents, caches, env files. | Done |
| QA-003 | P1 | As a maintainer, I have focused tests for parser/tracker/peer primitives. | Unit tests cover bencode, torrent parser, tracker parsing, handshake/message helpers. | Todo |
| QA-004 | P1 | As a maintainer, I have a documented legal smoke-test workflow. | README describes safe test torrents and commands. | Partial |

## Deferred Beyond 0.2

| ID | Priority | Story | Reason Deferred |
| --- | --- | --- | --- |
| MAG-001 | Later | Magnet link input and metadata fetch. | Needs DHT and extension protocol foundation. |
| DHT-001 | Later | DHT peer discovery. | Major protocol subsystem. |
| SEED-001 | Later | Seeding/upload mode. | Requires upload/choking implementation. |
| PKG-001 | Later | Desktop packaging. | Valuable after core engine stability. |
