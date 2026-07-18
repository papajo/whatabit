#!/usr/bin/env python3
"""WhataBit - BitTorrent client CLI entry point.

Usage:
    python main.py <torrent_file>
    python main.py <torrent_file> -o /downloads --max-peers 100 --port 6881 -v
    python main.py --ui

Supports both UDP (BEP 0015) and HTTP (BEP 0003) trackers.
"""

import argparse
import asyncio
import logging
import sys
import os

# Ensure src/ is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.download import DownloadManager
from src.torrent import TorrentFile


def setup_logging(verbose: bool = False):
    """Configure logging with format and level."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%H:%M:%S"
    logging.basicConfig(level=level, format=fmt, datefmt=datefmt)


def print_torrent_info(torrent_path: str):
    """Print parsed torrent metadata."""
    try:
        t = TorrentFile(torrent_path)
        print("╔══════════════════════════════════════════╗")
        print("║         WhataBit - Torrent Info         ║")
        print("╚══════════════════════════════════════════╝")
        print(f"  Name:        {t.name}")
        print(f"  Size:        {t.total_length / (1024*1024):.1f} MiB")
        print(f"  Pieces:      {len(t.pieces)} ({t.piece_length / 1024:.0f} KiB each)")
        print(f"  Info Hash:   {t.info_hash_hex}")
        print(f"  Tracker:     {t.announce}")
        if t.is_multi_file:
            print(f"  Files:       {len(t.files)}")
            for f in t.files:
                print(f"               {f['path']} ({f['length'] / (1024*1024):.1f} MiB)")
        return t
    except Exception as e:
        print(f"Error parsing torrent: {e}")
        sys.exit(1)


def progress_callback(completed: int, total: int, downloaded: int, speed: float):
    """Simple CLI progress callback."""
    pct = (completed / total * 100) if total > 0 else 0
    speed_kb = speed / 1024
    downloaded_mb = downloaded / (1024 * 1024)
    bar_width = 30
    filled = int(bar_width * completed / total) if total > 0 else 0
    bar = "█" * filled + "░" * (bar_width - filled)
    
    print(f"\r  [{bar}] {completed}/{total} pieces ({pct:.1f}%)  {downloaded_mb:.1f} MiB  {speed_kb:.1f} KiB/s", end="", flush=True)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="WhataBit - A BitTorrent client in Python",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s ubuntu-24.04-desktop-amd64.iso.torrent
  %(prog)s file.torrent -o /downloads --max-peers 50 -v
  %(prog)s file.torrent --port 6881 --max-connections 10
        """,
    )
    parser.add_argument(
        "torrent",
        nargs="?",
        help="Path to the .torrent file",
    )
    parser.add_argument(
        "-o", "--output",
        default=".",
        help="Output directory (default: current directory)",
    )
    parser.add_argument(
        "--max-peers",
        type=int,
        default=50,
        help="Maximum peers to fetch from tracker (default: 50)",
    )
    parser.add_argument(
        "--max-connections",
        type=int,
        default=20,
        help="Max simultaneous peer connections (default: 20)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=6881,
        help="Listening port for incoming connections (default: 6881)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose debug logging",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="WhataBit 0.1.0",
        help="Show version and exit",
    )
    parser.add_argument(
        "--info",
        action="store_true",
        help="Just show torrent info and exit",
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="Start the local Web UI",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Web UI bind host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--ui-port",
        type=int,
        default=8080,
        help="Web UI port (default: 8080)",
    )
    return parser.parse_args()


async def run_download(args: argparse.Namespace):
    """Run the download process."""
    dm = DownloadManager(
        torrent_path=args.torrent,
        output_dir=args.output,
        max_peers=args.max_peers,
        max_connections=args.max_connections,
        port=args.port,
        progress_callback=progress_callback,
    )

    try:
        await dm.download()
    except KeyboardInterrupt:
        print()
        logging.getLogger("whatabit.download").info("Download interrupted by user")
        dm.stop()
    finally:
        # Print final stats
        stats = dm.get_stats()
        print("\n")
        print("╔══════════════════════════════════════════╗")
        print("║         Download Complete                ║")
        print("╚══════════════════════════════════════════╝")
        if stats["is_complete"]:
            print(f"  Status:      ✅ Completed")
        else:
            print(f"  Status:      ⏹️  Stopped")
        print(f"  Downloaded:  {stats['downloaded'] / (1024*1024):.1f} MiB")
        print(f"  Pieces:      {stats['pieces_complete']}/{stats['pieces_total']}")
        print(f"  Speed:       {stats['speed'] / 1024:.1f} KiB/s")
        print(f"  Elapsed:     {stats['elapsed']:.1f}s")
        print(f"  Output dir:  {args.output}")


def main():
    """Main entry point."""
    args = parse_args()
    setup_logging(verbose=args.verbose)

    if args.ui:
        from src.webui import run_webui

        print(f"Starting WhataBit Web UI at http://{args.host}:{args.ui_port}")
        run_webui(host=args.host, port=args.ui_port, base_dir=os.getcwd())
        return

    # Validate input
    if not args.torrent:
        print("Error: torrent file is required unless --ui is used")
        print("Run 'python main.py --help' for usage.")
        sys.exit(1)

    if not os.path.isfile(args.torrent):
        print(f"Error: Torrent file not found: {args.torrent}")
        sys.exit(1)

    # Show torrent info
    print_torrent_info(args.torrent)

    if args.info:
        return

    # Ensure output directory exists
    os.makedirs(args.output, exist_ok=True)

    print()
    print(f"  Connecting to tracker and peers...")
    print()

    # Run the async download
    asyncio.run(run_download(args))


if __name__ == "__main__":
    main()
