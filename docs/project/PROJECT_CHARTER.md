# WhataBit 0.2 Project Charter

## Product Vision

WhataBit 0.2 will become a reliable, beginner-friendly local Web UI torrent downloader for legal `.torrent` files. It should preserve WhataBit's educational Python architecture while making the app practical enough for day-to-day local testing and controlled downloads.

## Current Milestone

**WhataBit 0.2: Reliable Local Web UI Torrent Downloader**

Primary outcome: a user can run `python main.py --ui`, upload/select a `.torrent` file, start a download into a chosen folder, monitor progress, stop/resume safely, and verify that completed output is correct.

## Target Users

- Beginner developers learning BitTorrent internals.
- Local hobby users testing legal torrents.
- Future contributors who need a clear, small Python codebase.

## 0.2 Scope

### In Scope

- Reliable `.torrent` metadata parsing and display.
- Persistent uploaded torrent library.
- Local-only Web UI workflow for add/select/start/stop/download status.
- Safer download engine behavior: piece/block retry, timeout handling, and correct final output.
- Basic session persistence for torrent list and download status.
- Clear user-facing errors and logs.
- Smoke-testable legal torrent workflow.

### Out of Scope for 0.2

- Magnet links.
- DHT.
- Seeding/upload mode.
- Remote access hardening.
- Native desktop packaging.
- Production-grade performance tuning.

## Success Metrics

- A real legal single-file torrent can complete through the Web UI.
- Stopping a download does not produce a misleading “complete” output.
- Restarting the UI preserves uploaded torrent metadata and planned/download state.
- The UI clearly distinguishes uploaded `.torrent` files from downloaded payload files.
- All changed code passes import/compile checks and relevant tests.

## Definition of Done

A 0.2 story is done when:

1. User behavior is implemented and documented.
2. Edge cases and failure states have clear UI or CLI feedback.
3. Generated files, downloaded payloads, torrent files, and secrets are not tracked by Git.
4. Relevant tests or smoke checks are documented in the story notes.
5. README/OKF/project docs are updated if behavior or priorities changed.

## Delivery Cadence

Use short one-week equivalent sprints. Each sprint should have a narrow outcome, a demo-able increment, and a small retro note before moving on.
