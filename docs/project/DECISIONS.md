# WhataBit Decision Log

## ADR-001 — 0.2 Focus: `.torrent` Web UI Downloader

**Date:** 2026-07-18  
**Status:** Accepted

### Context

WhataBit could evolve toward a full Vuze/uTorrent-like client, but that would require many major protocol and product subsystems.

### Decision

WhataBit 0.2 will focus on being a reliable local Web UI downloader for legal `.torrent` files before adding magnet links, DHT, or seeding.

### Consequences

- Reliability and safe output handling take priority over new protocol features.
- Magnet links, DHT, seeding, remote access, and packaging remain deferred.
- Project docs and backlog are organized around the 0.2 milestone.

## ADR-002 — Uploaded Torrent Storage

**Date:** 2026-07-18  
**Status:** Accepted

### Context

The Web UI needs uploaded torrent files after restart, but these files are metadata/runtime artifacts and should not be committed.

### Decision

Uploaded `.torrent` files are stored under `.whatabit/torrents/` and ignored by Git.

### Consequences

- UI can reload uploaded torrent metadata at startup.
- Users can delete uploaded torrents from the UI.
- Downloaded payloads remain separate under the chosen output directory, defaulting to `downloads/`.
