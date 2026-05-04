"""
Reset utility — drop collections and/or indexes for a clean re-import.

Usage:
  python reset_database.py                        # drop both collections
  python reset_database.py --collection sample    # drop titles_sample only
  python reset_database.py --collection full      # drop titles_full only
  python reset_database.py --indexes-only         # drop indexes, keep data
  python reset_database.py --collection sample --indexes-only
"""

import argparse
import sys

from database import (
    COLLECTION_FULL,
    COLLECTION_SAMPLE,
    get_collection,
    health_check,
)


def drop_collection(collection_name: str) -> None:
    col = get_collection(collection_name)
    count = col.count_documents({})
    col.drop()
    print(f"[reset] Dropped collection '{collection_name}' ({count:,} documents removed).")


def drop_indexes(collection_name: str) -> None:
    col = get_collection(collection_name)
    doc_count = col.count_documents({})

    if doc_count == 0:
        print(f"[reset] '{collection_name}' is empty — nothing to index-drop.")
        return

    indexes = list(col.list_indexes())
    dropped = 0
    for idx in indexes:
        if idx["name"] == "_id_":
            continue
        col.drop_index(idx["name"])
        print(f"[reset]   Dropped index '{idx['name']}' from '{collection_name}'.")
        dropped += 1

    if dropped == 0:
        print(f"[reset] No non-default indexes found on '{collection_name}'.")
    else:
        print(f"[reset] Dropped {dropped} index(es) from '{collection_name}'.")


def confirm(prompt: str) -> bool:
    """Ask for y/n confirmation; return True if user types y/yes."""
    answer = input(f"{prompt} [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def main():
    parser = argparse.ArgumentParser(
        description="Reset IMDb MongoDB collections or indexes."
    )
    parser.add_argument(
        "--collection",
        choices=["sample", "full", "both"],
        default="both",
        help="Which collection to operate on (default: both).",
    )
    parser.add_argument(
        "--indexes-only",
        action="store_true",
        help="Drop non-default indexes but keep the document data.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt (for scripted use).",
    )
    args = parser.parse_args()

    if not health_check():
        sys.exit(1)

    targets = []
    if args.collection in ("sample", "both"):
        targets.append(COLLECTION_SAMPLE)
    if args.collection in ("full", "both"):
        targets.append(COLLECTION_FULL)

    action = "drop indexes on" if args.indexes_only else "DROP"
    target_str = " and ".join(f"'{t}'" for t in targets)

    if not args.yes:
        ok = confirm(f"About to {action} {target_str}. Continue?")
        if not ok:
            print("[reset] Aborted.")
            sys.exit(0)

    for col_name in targets:
        if args.indexes_only:
            drop_indexes(col_name)
        else:
            drop_collection(col_name)

    print("\n[reset] Done.")


if __name__ == "__main__":
    main()
