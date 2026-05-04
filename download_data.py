"""
Download IMDb non-commercial dataset files into the data/ directory.

Files downloaded:
  - title.basics.tsv.gz
  - title.ratings.tsv.gz

IMDb non-commercial datasets:
  https://developer.imdb.com/non-commercial-datasets/

Usage:
  python download_data.py
"""

import os
import sys
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

IMDB_FILES = {
    "title.basics.tsv.gz": "https://datasets.imdbws.com/title.basics.tsv.gz",
    "title.ratings.tsv.gz": "https://datasets.imdbws.com/title.ratings.tsv.gz",
}


def _progress_hook(filename: str):
    """Return a urllib reporthook that prints a single-line progress indicator."""
    last_pct = [-1]

    def hook(block_num: int, block_size: int, total_size: int):
        if total_size <= 0:
            return
        downloaded = block_num * block_size
        pct = min(100, int(downloaded * 100 / total_size))
        if pct != last_pct[0]:
            mb_done = downloaded / 1_048_576
            mb_total = total_size / 1_048_576
            print(
                f"\r  {filename}: {pct:3d}%  ({mb_done:.1f} / {mb_total:.1f} MB)",
                end="",
                flush=True,
            )
            last_pct[0] = pct
        if pct == 100:
            print()

    return hook


def download_file(filename: str, url: str, dest_dir: Path) -> bool:
    """
    Download *url* to *dest_dir/filename*.

    Skips the download if the file already exists.
    Returns True on success.
    """
    dest = dest_dir / filename
    if dest.exists():
        size_mb = dest.stat().st_size / 1_048_576
        print(f"  {filename} already exists ({size_mb:.1f} MB) — skipping.")
        return True

    print(f"  Downloading {filename} from {url} ...")
    try:
        urllib.request.urlretrieve(url, dest, reporthook=_progress_hook(filename))
        size_mb = dest.stat().st_size / 1_048_576
        print(f"  Saved {filename} ({size_mb:.1f} MB).")
        return True
    except Exception as exc:
        print(f"\n  ERROR downloading {filename}: {exc}", file=sys.stderr)
        if dest.exists():
            dest.unlink()
        return False


def main():
    print("=== IMDb Dataset Downloader ===\n")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Destination: {DATA_DIR.resolve()}\n")

    success = True
    for filename, url in IMDB_FILES.items():
        ok = download_file(filename, url, DATA_DIR)
        success = success and ok

    print()
    if success:
        print("All files are ready.")
    else:
        print("One or more downloads failed. Check the error messages above.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
