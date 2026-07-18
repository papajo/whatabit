# WhataBit

WhataBit is a small educational BitTorrent client written in Python. It can parse `.torrent` files, announce to HTTP and UDP trackers, connect to peers with the BitTorrent peer wire protocol, and download pieces while verifying SHA-1 piece hashes.

The project was originally generated inside an Agent Zero Docker environment and is now structured so it can be developed locally like a normal Python project.

## Features

- Bencode encoder/decoder for BitTorrent metadata
- `.torrent` metainfo parsing
- Info-hash calculation from the bencoded `info` dictionary
- HTTP tracker announce support
- UDP tracker announce support, including BEP 0015 connect/announce flow
- Peer wire protocol handshake and message parsing/building
- Async download manager built on `asyncio`
- CLI interface with info-only and download modes

## Project layout

```text
.
├── main.py              # CLI entry point
├── requirements.txt     # Python dependencies
├── src/
│   ├── bencode.py       # Bencode encode/decode helpers
│   ├── torrent.py       # Torrent metadata parser
│   ├── tracker.py       # UDP tracker client
│   ├── http_tracker.py  # HTTP tracker client
│   ├── peer.py          # Peer wire protocol helpers and connection class
│   └── download.py      # Download orchestration
└── tests/               # Test package placeholder / future tests
```

## Requirements

- Python 3.12+
- `aiohttp`

Install dependencies with:

```bash
python3 -m pip install -r requirements.txt
```

## Local setup

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Verify imports:

```bash
python -c "from src import *; print('OK modules load')"
```

Show CLI help:

```bash
python main.py --help
```


## Web UI

WhataBit also includes a lightweight local Web UI built with `aiohttp`. Start it with:

```bash
python main.py --ui
```

Then open:

```text
http://127.0.0.1:8080
```

You can upload a `.torrent` file, inspect metadata, choose download settings, start a download, stop active jobs, and watch progress from the browser. Uploaded `.torrent` files are stored under `.whatabit/torrents/` and are shown again after restarting the UI. Completed single-file downloads can be downloaded from the browser when the output file is available. Downloaded payloads default to `downloads/`; `.whatabit/` and `downloads/` are ignored by Git.

To use a different host or port:

```bash
python main.py --ui --host 0.0.0.0 --ui-port 8088
```

Only bind to `0.0.0.0` on networks you trust.

### What happens to uploaded torrent files?

The Web UI copies uploaded `.torrent` files into `.whatabit/torrents/` under the project directory. These small metadata files are kept locally so you can restart the UI and select them again. Use the Delete button in the Uploaded torrents list to remove one.

The actual downloaded content is separate and goes to the output directory you choose, defaulting to `downloads/`.

## Usage

### Show torrent metadata only

```bash
python main.py path/to/file.torrent --info
```

### Download a torrent

```bash
python main.py path/to/file.torrent -o downloads
```

### Download with fewer peers/connections while testing

```bash
python main.py path/to/file.torrent -o downloads --max-peers 10 --max-connections 5 -v
```

### CLI options

```text
-o, --output            Output directory, default: current directory
--max-peers             Maximum peers to request from tracker, default: 50
--max-connections       Maximum simultaneous peer connections, default: 20
--port                  Listening port for incoming connections, default: 6881
-v, --verbose           Enable debug logging
--info                  Print torrent metadata and exit
--version               Print WhataBit version
```

## Safe testing

Use only legal torrents, such as public Linux ISO torrents or your own test torrents. Avoid copyrighted content and avoid forwarding BitTorrent ports on your router until you understand the networking implications.

For a first local smoke test, prefer `--info` mode because it parses metadata without contacting peers:

```bash
python main.py example.torrent --info
```

Then try a small legal torrent with an explicit output directory:

```bash
mkdir -p downloads
python main.py example.torrent -o downloads --max-peers 10 --max-connections 5 -v
```

## Development notes

- Keep imports using the `src.*` package layout when adding new modules.
- The download path uses `asyncio`; avoid blocking calls in peer/download code where practical.
- UDP tracker calls are currently synchronous socket operations.
- HTTP tracker calls use `aiohttp`.
- Downloaded files, `.torrent` files, virtual environments, Python caches, and Agent Zero metadata are intentionally ignored by Git.

## Current limitations

WhataBit is an educational client and not a full production BitTorrent implementation. Some limitations may include:

- No DHT support
- No magnet link support
- No seeding mode
- Limited resume/persistence behavior
- Limited tracker retry/fallback behavior
- No advanced choking/peer strategy

## License

No license has been selected yet. Add a `LICENSE` file before publishing or sharing the project broadly.
