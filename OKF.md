# WhataBit OKF

## Objective

Build and maintain WhataBit as a small, understandable Python BitTorrent client that is useful for learning BitTorrent internals and experimenting with tracker, peer, and download-manager code locally.

## Current Milestone

**WhataBit 0.2:** reliable local Web UI torrent downloader for legal `.torrent` files.

Planning docs live under `docs/project/` and should be updated as priorities, scope, or sprint status changes.

## Key Results

1. **Reliable torrent metadata parsing**
   - Parse common single-file and multi-file `.torrent` files.
   - Preserve correct BitTorrent `info_hash` calculation from bencoded metadata.
   - Provide a clear `--info` CLI mode for safe inspection without downloading.

2. **Tracker compatibility**
   - Support HTTP/HTTPS tracker announces.
   - Support UDP tracker connect and announce flows.
   - Return usable compact peer lists for the download manager.

3. **Peer protocol implementation**
   - Perform BitTorrent handshakes correctly.
   - Encode/decode common peer messages.
   - Request and receive blocks while respecting piece boundaries.

4. **Download orchestration**
   - Download pieces asynchronously from multiple peers.
   - Verify completed pieces with SHA-1 hashes before writing data.
   - Provide visible CLI progress and final stats.

5. **Local developer friendliness**
   - Keep setup simple: Python virtual environment plus `requirements.txt`.
   - Keep generated/runtime files out of Git.
   - Maintain clear README usage and safety instructions.
   - Web UI torrent library should make uploaded `.torrent` file storage and deletion clear.

## Focus Areas

- Correctness before performance.
- Clear module boundaries under `src/`.
- Safe legal testing with public-domain or open-source torrents.
- Beginner-friendly commands and documentation.

## Non-Goals for Now

- Full production-grade BitTorrent client behavior.
- DHT support.
- Magnet link support.
- Seeding mode.
- Production-grade GUI, native app, or remotely exposed WebUI.
- NAT traversal or router port automation.

## Repository Hygiene

Keep these out of commits:

- `.a0proj/`
- `.venv/`
- `__pycache__/`
- `*.pyc`
- `downloads/`
- `*.torrent`
- real secrets, API keys, tokens, private paths, or personal data

## Verification Checklist

Before committing meaningful changes:

```bash
python -c "from src import *; print('OK modules load')"
python main.py --help
python main.py path/to/legal-test.torrent --info
python main.py --ui
```

For Web UI work, manually check upload, metadata display, start, progress refresh, stop, and mobile-width layout.

When tests exist:

```bash
python -m pytest
```

For download-related changes, smoke-test with a small legal torrent and an explicit output directory:

```bash
mkdir -p downloads
python main.py path/to/legal-test.torrent -o downloads --max-peers 10 --max-connections 5 -v
```

## Safety Notes

BitTorrent clients may expose your IP address to peers and may download or upload data. Only test with legal content, keep downloads in a known directory, and avoid opening router/firewall ports unless you understand the consequences.
