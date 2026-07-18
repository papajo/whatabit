# WhataBit AGENTS.md

## Purpose

A Python BitTorrent client built following the guide at:
https://allenkim67.github.io/programming/2016/05/04/how-to-make-your-own-bittorrent-client.html

Supports both UDP (BEP 0015) and HTTP (BEP 0003) trackers with full peer wire protocol implementation.

## Ownership

| Module | File | Owner |
|--------|------|-------|
| Bencode | `src/bencode.py` | Core encoding/decoding |
| Torrent Parser | `src/torrent.py` | .torrent metadata extraction |
| UDP Tracker | `src/tracker.py` | BEP 0015 connect/announce/scrape |
| HTTP Tracker | `src/http_tracker.py` | BEP 0003 HTTP announce |
| Peer Protocol | `src/peer.py` | Handshake, messages, PeerConnection |
| Download Manager | `src/download.py` | Full download orchestrator |
| CLI | `main.py` | Command-line entry point |

## Local Contracts

- All modules import from `src.*` package, never from relative sibling imports.
- Async download uses `asyncio` throughout; peer connections use `asyncio.StreamReader/Writer`.
- UDP tracker calls are synchronous (blocking socket); HTTP tracker is async (aiohttp).
- Run with `python main.py <torrent_file>`.
- Dependencies: aiohttp (HTTP tracker), all else stdlib.
- Info hash is always 20-byte SHA-1 of bencoded info dict.
- Peer ID format: `-WT0001-` + 12 hex digits (BEP 0020).

## Work Guidance

- To add DHT support, modify `peer.py` to handle MSG_PORT and implement Kademlia.
- To add magnet links, create a new `src/magnet.py` that parses xt=urn:btih: and contacts DHT/trackers.
- To add seeding support, track completed pieces and respond to peer requests in `download.py`.
- Keep `main.py` thin; complex logic belongs in `src/` modules.

## Verification

- Run `python -c "from src import *; print('OK')"` to verify imports.
- Run individual module tests with `python tests/<test_file>.py`.
- Test with `python main.py <test_torrent> --info` to verify parsing.
- For full download test: `python main.py <real_torrent> -o /tmp/dl -v`

## Child DOX Index

No child AGENTS.md files currently exist for this project.
