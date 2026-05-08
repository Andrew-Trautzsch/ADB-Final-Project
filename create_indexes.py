"""
Create MongoDB indexes for the IMDb search collections.

Creates the indexes described in the project spec on both
titles_sample and titles_full (whichever already have data).

Usage:
  python create_indexes.py                        # both collections
  python create_indexes.py --collection sample    # titles_sample only
  python create_indexes.py --collection full      # titles_full only
"""

import argparse
import sys

from pymongo import ASCENDING, DESCENDING, TEXT
from pymongo.collection import Collection

from database import (
    COLLECTION_FULL,
    COLLECTION_SAMPLE,
    get_collection,
    health_check,
)

# ---------------------------------------------------------------------------
# Index definitions
# ---------------------------------------------------------------------------

SINGLE_FIELD_INDEXES = [
    ("tconst", ASCENDING),
    ("primaryTitle", ASCENDING),
    ("searchTitle", ASCENDING),
    ("titleType", ASCENDING),
    ("startYear", ASCENDING),
    ("genres", ASCENDING),
    ("rating.averageRating", DESCENDING),
    ("rating.numVotes", DESCENDING),
]

COMPOUND_INDEXES = [
    [("titleType", ASCENDING), ("startYear", ASCENDING)],
    [("genres", ASCENDING), ("startYear", ASCENDING), ("rating.averageRating", DESCENDING)],
    [("titleType", ASCENDING), ("genres", ASCENDING), ("startYear", ASCENDING), ("rating.averageRating", DESCENDING)],
    [("titleType", ASCENDING), ("rating.numVotes", DESCENDING)],
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _index_key_str(key_list: list) -> str:
    parts = []
    for field, direction in key_list:
        if direction == TEXT:
            arrow = "T"
        elif direction == ASCENDING:
            arrow = "▲"
        else:
            arrow = "▼"
        parts.append(f"{field} {arrow}")
    return " | ".join(parts)


def create_indexes_on(collection: Collection) -> None:
    name = collection.name
    print(f"\n[indexes] Creating indexes on '{name}' ...")

    created = 0
    skipped = 0

    # Single-field indexes
    for field, direction in SINGLE_FIELD_INDEXES:
        key = [(field, direction)]
        try:
            collection.create_index(key)
            print(f"  [OK] Single  : {field}")
            created += 1
        except Exception as exc:
            if "already exists" in str(exc).lower() or "IndexOptionsConflict" in str(exc):
                print(f"  [--] Single  : {field}  (already exists)")
                skipped += 1
            else:
                print(f"  [ERR] Single : {field}  — {exc}", file=sys.stderr)

    # Compound indexes
    for key_list in COMPOUND_INDEXES:
        try:
            collection.create_index(key_list)
            print(f"  [OK] Compound: {_index_key_str(key_list)}")
            created += 1
        except Exception as exc:
            if "already exists" in str(exc).lower() or "IndexOptionsConflict" in str(exc):
                print(f"  [--] Compound: {_index_key_str(key_list)}  (already exists)")
                skipped += 1
            else:
                print(f"  [ERR] Compound: {_index_key_str(key_list)}  — {exc}", file=sys.stderr)

    # $text index on primaryTitle
    # MongoDB allows only one text index per collection.
    # This enables fast $text keyword search as an alternative to $regex.
    try:
        collection.create_index([("primaryTitle", TEXT)], name="primaryTitle_text")
        print(f"  [OK] Text    : primaryTitle  (enables $text keyword search)")
        created += 1
    except Exception as exc:
        if "already exists" in str(exc).lower() or "IndexOptionsConflict" in str(exc):
            print(f"  [--] Text    : primaryTitle  (already exists)")
            skipped += 1
        else:
            print(f"  [ERR] Text   : primaryTitle  — {exc}", file=sys.stderr)

    print(f"\n[indexes] Done for '{name}': {created} created, {skipped} already existed.")


def list_indexes(collection: Collection) -> None:
    print(f"\n[indexes] Current indexes on '{collection.name}':")
    for idx in collection.list_indexes():
        key_str = ", ".join(f"{k}: {v}" for k, v in idx["key"].items())
        print(f"  {idx['name']:45s}  {{{key_str}}}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Create MongoDB indexes for IMDb collections."
    )
    parser.add_argument(
        "--collection",
        choices=["sample", "full", "both"],
        default="both",
        help="Which collection to index (default: both).",
    )
    args = parser.parse_args()

    if not health_check():
        sys.exit(1)

    targets = []
    if args.collection in ("sample", "both"):
        targets.append(COLLECTION_SAMPLE)
    if args.collection in ("full", "both"):
        targets.append(COLLECTION_FULL)

    for col_name in targets:
        col = get_collection(col_name)
        count = col.count_documents({})
        if count == 0:
            print(f"\n[indexes] '{col_name}' is empty — skipping.")
            continue
        create_indexes_on(col)
        list_indexes(col)

    print("\n[indexes] All done.")


if __name__ == "__main__":
    main()