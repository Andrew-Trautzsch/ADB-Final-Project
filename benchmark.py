"""
Benchmark script — compares query performance before and after indexes.

Workflow:
  1. Drop all non-default indexes from the target collection.
  2. Run five predefined benchmark queries and record timing + explain output.
  3. Create all project indexes (calls create_indexes.py logic).
  4. Run the same queries again.
  5. Print a comparison table and save results to docs/benchmark_results.md.

Usage:
  python benchmark.py                       # benchmark titles_sample
  python benchmark.py --collection full     # benchmark titles_full
  python benchmark.py --collection sample --runs 3
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from pymongo import ASCENDING, DESCENDING

from create_indexes import COMPOUND_INDEXES, SINGLE_FIELD_INDEXES, create_indexes_on
from database import (
    COLLECTION_FULL,
    COLLECTION_SAMPLE,
    get_collection,
    health_check,
)

DOCS_DIR = Path(__file__).parent / "docs"

# ---------------------------------------------------------------------------
# Benchmark query definitions
# ---------------------------------------------------------------------------

BENCHMARK_QUERIES = [
    {
        "name": "Q1 — Genre: Action",
        "filter": {"genres": "Action"},
        "sort": [("rating.averageRating", DESCENDING)],
        "limit": 100,
    },
    {
        "name": "Q2 — Year range 2000–2020",
        "filter": {"startYear": {"$gte": 2000, "$lte": 2020}},
        "sort": [("startYear", DESCENDING)],
        "limit": 100,
    },
    {
        "name": "Q3 — Rating ≥ 8.0",
        "filter": {"rating.averageRating": {"$gte": 8.0}},
        "sort": [("rating.averageRating", DESCENDING)],
        "limit": 100,
    },
    {
        "name": "Q4 — Votes ≥ 10,000",
        "filter": {"rating.numVotes": {"$gte": 10_000}},
        "sort": [("rating.numVotes", DESCENDING)],
        "limit": 100,
    },
    {
        "name": "Q5 — Combined (movie, Drama, 2000–2024, rating≥7.5, votes≥10k)",
        "filter": {
            "titleType": "movie",
            "genres": "Drama",
            "startYear": {"$gte": 2000, "$lte": 2024},
            "rating.averageRating": {"$gte": 7.5},
            "rating.numVotes": {"$gte": 10_000},
        },
        "sort": [("rating.averageRating", DESCENDING)],
        "limit": 100,
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_query(collection, q: dict, runs: int = 1) -> dict:
    """
    Execute a benchmark query *runs* times and return timing + explain data.
    """
    timings_ms = []
    last_explain = None

    for _ in range(runs):
        t0 = time.perf_counter()
        docs = list(
            collection.find(q["filter"])
            .sort(q["sort"])
            .limit(q["limit"])
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        timings_ms.append(elapsed_ms)

    # Capture explain on last run
    try:
        last_explain = collection.find(q["filter"]).sort(q["sort"]).explain()
    except Exception:
        last_explain = {}

    avg_ms = sum(timings_ms) / len(timings_ms)

    # Extract stage and examine-count from explain
    stage = _extract_stage(last_explain)
    docs_examined = _extract_docs_examined(last_explain)
    docs_returned = len(docs)

    return {
        "avg_ms": round(avg_ms, 2),
        "runs": runs,
        "docs_returned": docs_returned,
        "docs_examined": docs_examined,
        "stage": stage,
        "explain": last_explain,
    }


def _extract_stage(explain: dict) -> str:
    """Walk explain output to find the innermost winning plan stage."""
    try:
        plan = explain.get("queryPlanner", {}).get("winningPlan", {})
        stage = plan.get("stage", "UNKNOWN")
        # Drill into input stages if present
        while "inputStage" in plan:
            plan = plan["inputStage"]
            stage = plan.get("stage", stage)
        return stage
    except Exception:
        return "UNKNOWN"


def _extract_docs_examined(explain: dict) -> int:
    try:
        stats = explain.get("executionStats", {})
        return stats.get("totalDocsExamined", -1)
    except Exception:
        return -1


def _drop_non_default_indexes(collection) -> None:
    """Drop all indexes except the default _id index."""
    indexes = list(collection.list_indexes())
    dropped = 0
    for idx in indexes:
        if idx["name"] == "_id_":
            continue
        collection.drop_index(idx["name"])
        dropped += 1
    print(f"[benchmark] Dropped {dropped} non-default index(es) from '{collection.name}'.")


def _format_table_row(
    q_name: str,
    before: dict | None,
    after: dict | None,
) -> str:
    if before is None or after is None:
        return ""

    improvement = (
        f"{before['avg_ms'] / after['avg_ms']:.1f}x"
        if after["avg_ms"] > 0
        else "N/A"
    )
    return (
        f"| {q_name:<52} "
        f"| {before['avg_ms']:>10.1f} ms "
        f"| {after['avg_ms']:>9.1f} ms "
        f"| {improvement:>11} "
        f"| {before['stage']:<9} "
        f"| {after['stage']:<9} "
        f"| {before['docs_examined']:>8} "
        f"| {after['docs_examined']:>7} |"
    )


# ---------------------------------------------------------------------------
# Main benchmark runner
# ---------------------------------------------------------------------------

def run_benchmark(collection_name: str = COLLECTION_SAMPLE, runs: int = 1) -> None:
    if not health_check():
        sys.exit(1)

    collection = get_collection(collection_name)
    doc_count = collection.count_documents({})
    if doc_count == 0:
        print(
            f"[benchmark] Collection '{collection_name}' is empty. "
            "Run import_data.py first.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"\n[benchmark] Collection  : {collection_name} ({doc_count:,} documents)")
    print(f"[benchmark] Runs/query  : {runs}")

    # ------------------------------------------------------------------ #
    # Phase 1: before indexes
    # ------------------------------------------------------------------ #

    print("\n[benchmark] === Phase 1: Before indexes ===")
    _drop_non_default_indexes(collection)
    time.sleep(0.3)  # brief pause after dropping

    before_results: list[dict] = []
    for q in BENCHMARK_QUERIES:
        print(f"  Running {q['name']} ...", end=" ", flush=True)
        res = _run_query(collection, q, runs=runs)
        before_results.append(res)
        print(f"{res['avg_ms']:.1f} ms  [{res['stage']}]  docs examined: {res['docs_examined']}")

    # ------------------------------------------------------------------ #
    # Phase 2: create indexes
    # ------------------------------------------------------------------ #

    print("\n[benchmark] === Creating indexes ===")
    create_indexes_on(collection)
    time.sleep(0.3)

    # ------------------------------------------------------------------ #
    # Phase 3: after indexes
    # ------------------------------------------------------------------ #

    print("\n[benchmark] === Phase 2: After indexes ===")
    after_results: list[dict] = []
    for q in BENCHMARK_QUERIES:
        print(f"  Running {q['name']} ...", end=" ", flush=True)
        res = _run_query(collection, q, runs=runs)
        after_results.append(res)
        print(f"{res['avg_ms']:.1f} ms  [{res['stage']}]  docs examined: {res['docs_examined']}")

    # ------------------------------------------------------------------ #
    # Print comparison table
    # ------------------------------------------------------------------ #

    header = (
        f"\n{'Query':<54} | {'Before':>12} | {'After':>11} | {'Improvement':>11} "
        f"| {'Plan-B':<9} | {'Plan-A':<9} | {'ExamB':>8} | {'ExamA':>7} |"
    )
    sep = "-" * len(header)

    print(f"\n{'='*80}")
    print("  BENCHMARK RESULTS")
    print(f"{'='*80}")
    print(header)
    print(sep)

    for i, q in enumerate(BENCHMARK_QUERIES):
        row = _format_table_row(q["name"], before_results[i], after_results[i])
        print(row)

    print(sep)

    # ------------------------------------------------------------------ #
    # Save to markdown
    # ------------------------------------------------------------------ #

    _save_markdown(collection_name, runs, BENCHMARK_QUERIES, before_results, after_results)


# ---------------------------------------------------------------------------
# Markdown output
# ---------------------------------------------------------------------------

def _save_markdown(
    collection_name: str,
    runs: int,
    queries: list[dict],
    before: list[dict],
    after: list[dict],
) -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DOCS_DIR / "benchmark_results.md"

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# Benchmark Results",
        "",
        f"**Collection:** `{collection_name}`  ",
        f"**Runs per query:** {runs}  ",
        f"**Generated:** {ts}  ",
        "",
        "| Query | Before Index | After Index | Improvement | Plan Before | Plan After | Docs Examined Before | Docs Examined After |",
        "|-------|------------:|------------:|------------:|-------------|------------|---------------------:|--------------------:|",
    ]

    for i, q in enumerate(queries):
        b = before[i]
        a = after[i]
        imp = f"{b['avg_ms'] / a['avg_ms']:.1f}×" if a["avg_ms"] > 0 else "N/A"
        lines.append(
            f"| {q['name']} "
            f"| {b['avg_ms']:.1f} ms "
            f"| {a['avg_ms']:.1f} ms "
            f"| {imp} "
            f"| {b['stage']} "
            f"| {a['stage']} "
            f"| {b['docs_examined']} "
            f"| {a['docs_examined']} |"
        )

    lines += [
        "",
        "## Notes",
        "",
        "- **COLLSCAN**: MongoDB scanned every document (no index used).",
        "- **IXSCAN**: MongoDB used an index to narrow the search.",
        "- **FETCH**: MongoDB fetched matching documents after an index scan.",
        "- Improvement = Before time ÷ After time.",
        "- Results vary by dataset size, hardware, and MongoDB cache state.",
        "",
        "## Query Definitions",
        "",
    ]

    for q in queries:
        lines.append(f"### {q['name']}")
        lines.append(f"```json\n{json.dumps(q['filter'], indent=2)}\n```")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[benchmark] Results saved to {out_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Benchmark IMDb MongoDB queries before and after index creation."
    )
    parser.add_argument(
        "--collection",
        choices=["sample", "full"],
        default="sample",
        help="Collection to benchmark (default: sample).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Number of times to run each query for averaging (default: 1).",
    )
    args = parser.parse_args()

    col_name = COLLECTION_SAMPLE if args.collection == "sample" else COLLECTION_FULL
    run_benchmark(collection_name=col_name, runs=args.runs)


if __name__ == "__main__":
    main()
