"""
Import IMDb data into local MongoDB.

Workflow:
  1. Read title.ratings.tsv.gz → build in-memory lookup keyed by tconst.
  2. Stream title.basics.tsv.gz row by row, clean values, attach ratings.
  3. Batch-insert documents into the selected MongoDB collection.

Usage:
  python import_data.py                   # import sample (10,000 movies)
  python import_data.py --mode full       # import full dataset (100,000+ records)
  python import_data.py --mode full --limit 200000
  python import_data.py --clear           # clear collection before importing

Options:
  --mode    sample | full   (default: sample)
  --limit   integer         (default: 10000 for sample, 100000 for full)
  --clear                   drop collection before inserting
"""

import argparse
import csv
import gzip
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from database import (
    COLLECTION_FULL,
    COLLECTION_SAMPLE,
    get_collection,
    health_check,
)

DATA_DIR = Path(__file__).parent / "data"
BASICS_FILE = DATA_DIR / "title.basics.tsv.gz"
RATINGS_FILE = DATA_DIR / "title.ratings.tsv.gz"

BATCH_SIZE = 1000

# Minimum quality bar for the sample collection
SAMPLE_FILTER_TITLE_TYPE = "movie"
SAMPLE_FILTER_MIN_VOTES = 1000

MISSING = r"\N"


# ---------------------------------------------------------------------------
# Cleaning helpers
# ---------------------------------------------------------------------------

def _int_or_none(raw: str) -> int | None:
    if raw == MISSING or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _float_or_none(raw: str) -> float | None:
    if raw == MISSING or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _genres_list(raw: str) -> list[str]:
    if raw == MISSING or raw == "":
        return []
    return [g.strip() for g in raw.split(",") if g.strip()]


def _bool_flag(raw: str) -> bool:
    return raw.strip() == "1"


def _str_or_none(raw: str) -> str | None:
    if raw == MISSING or raw == "":
        return None
    return raw


# ---------------------------------------------------------------------------
# Step 1: Load ratings lookup
# ---------------------------------------------------------------------------

def load_ratings(path: Path) -> dict:
    """
    Read title.ratings.tsv.gz and return a dict keyed by tconst.

    Each value is {"averageRating": float, "numVotes": int}.
    """
    if not path.exists():
        print(f"[import] ERROR: Ratings file not found: {path}", file=sys.stderr)
        print("[import] Run: python download_data.py", file=sys.stderr)
        sys.exit(1)

    ratings: dict = {}
    print(f"[import] Loading ratings from {path.name} ...", flush=True)
    t0 = time.perf_counter()

    with gzip.open(path, "rt", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            tconst = row["tconst"]
            avg = _float_or_none(row["averageRating"])
            votes = _int_or_none(row["numVotes"])
            if avg is not None and votes is not None:
                ratings[tconst] = {"averageRating": avg, "numVotes": votes}

    elapsed = time.perf_counter() - t0
    print(f"[import] Loaded {len(ratings):,} ratings in {elapsed:.1f}s")
    return ratings


# ---------------------------------------------------------------------------
# Step 2: Stream and clean title basics
# ---------------------------------------------------------------------------

def build_document(row: dict, ratings: dict, imported_at: datetime) -> dict | None:
    """
    Convert one TSV row into a MongoDB document.

    Returns None if the row is fatally malformed (missing tconst / primaryTitle).
    """
    tconst = row.get("tconst", "").strip()
    primary_title = _str_or_none(row.get("primaryTitle", ""))
    if not tconst or not primary_title:
        return None

    original_title = _str_or_none(row.get("originalTitle", ""))
    title_type = _str_or_none(row.get("titleType", ""))
    is_adult = _bool_flag(row.get("isAdult", "0"))
    start_year = _int_or_none(row.get("startYear", MISSING))
    end_year = _int_or_none(row.get("endYear", MISSING))
    runtime = _int_or_none(row.get("runtimeMinutes", MISSING))
    genres = _genres_list(row.get("genres", MISSING))

    rating_data = ratings.get(tconst)
    rating_embed = (
        {
            "averageRating": rating_data["averageRating"],
            "numVotes": rating_data["numVotes"],
        }
        if rating_data
        else {"averageRating": None, "numVotes": None}
    )

    return {
        "tconst": tconst,
        "titleType": title_type,
        "primaryTitle": primary_title,
        "originalTitle": original_title,
        "searchTitle": primary_title.lower(),
        "isAdult": is_adult,
        "startYear": start_year,
        "endYear": end_year,
        "runtimeMinutes": runtime,
        "genres": genres,
        "rating": rating_embed,
        "importedAt": imported_at,
    }


def passes_sample_filter(doc: dict) -> bool:
    """Return True when a document meets the minimum bar for titles_sample."""
    if doc.get("titleType") != SAMPLE_FILTER_TITLE_TYPE:
        return False
    if doc.get("startYear") is None:
        return False
    rating = doc.get("rating", {})
    if rating.get("averageRating") is None:
        return False
    if (rating.get("numVotes") or 0) < SAMPLE_FILTER_MIN_VOTES:
        return False
    return True


# ---------------------------------------------------------------------------
# Step 3: Import
# ---------------------------------------------------------------------------

def import_data(
    mode: str = "sample",
    limit: int | None = None,
    clear_first: bool = False,
) -> None:
    if not health_check():
        sys.exit(1)

    collection_name = COLLECTION_SAMPLE if mode == "sample" else COLLECTION_FULL
    default_limit = 10_000 if mode == "sample" else 100_000
    max_docs = limit if limit is not None else default_limit

    collection = get_collection(collection_name)

    print(f"\n[import] Mode        : {mode}")
    print(f"[import] Collection  : {collection_name}")
    print(f"[import] Record limit: {max_docs:,}")

    if clear_first:
        deleted = collection.delete_many({}).deleted_count
        print(f"[import] Cleared {deleted:,} existing documents.")

    ratings = load_ratings(RATINGS_FILE)

    if not BASICS_FILE.exists():
        print(f"[import] ERROR: Basics file not found: {BASICS_FILE}", file=sys.stderr)
        print("[import] Run: python download_data.py", file=sys.stderr)
        sys.exit(1)

    imported_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    rows_read = 0
    docs_inserted = 0
    docs_skipped = 0
    batch: list[dict] = []
    is_sample = mode == "sample"

    print(f"[import] Reading {BASICS_FILE.name} ...", flush=True)

    with gzip.open(BASICS_FILE, "rt", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")

        for row in reader:
            rows_read += 1

            doc = build_document(row, ratings, imported_at)
            if doc is None:
                docs_skipped += 1
                continue

            if is_sample and not passes_sample_filter(doc):
                docs_skipped += 1
                continue

            batch.append(doc)

            if len(batch) >= BATCH_SIZE:
                collection.insert_many(batch, ordered=False)
                docs_inserted += len(batch)
                batch = []

                if docs_inserted % 5_000 == 0:
                    elapsed = time.perf_counter() - t0
                    print(
                        f"[import]   {docs_inserted:,} inserted, "
                        f"{rows_read:,} rows read ({elapsed:.1f}s) ...",
                        flush=True,
                    )

            if docs_inserted + len(batch) >= max_docs:
                break

    # Insert remaining batch
    if batch:
        collection.insert_many(batch, ordered=False)
        docs_inserted += len(batch)

    elapsed = time.perf_counter() - t0

    print()
    print("=" * 50)
    print("  Import Summary")
    print("=" * 50)
    print(f"  Collection      : {collection_name}")
    print(f"  Rows read       : {rows_read:,}")
    print(f"  Ratings loaded  : {len(ratings):,}")
    print(f"  Docs inserted   : {docs_inserted:,}")
    print(f"  Docs skipped    : {docs_skipped:,}")
    print(f"  Duration        : {elapsed:.1f}s")
    print("=" * 50)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Import IMDb data into local MongoDB."
    )
    parser.add_argument(
        "--mode",
        choices=["sample", "full"],
        default="sample",
        help="Import mode: 'sample' (default, 10k movies) or 'full' (100k+ records).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of documents to insert (overrides mode default).",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear the target collection before importing.",
    )
    args = parser.parse_args()
    import_data(mode=args.mode, limit=args.limit, clear_first=args.clear)


if __name__ == "__main__":
    main()
