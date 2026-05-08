"""
Text Search Comparison — Regex vs $text index

Runs the same keyword search two ways and compares:
  - query plan (COLLSCAN vs TEXT)
  - documents examined
  - execution time
  - result quality (relevance scoring with $text)

Usage:
  python text_search_comparison.py
  python text_search_comparison.py --keyword "star wars" --collection full
  python text_search_comparison.py --keyword batman --runs 3
"""

import argparse
import re
import sys
import time
from pathlib import Path

from database import COLLECTION_FULL, COLLECTION_SAMPLE, get_collection, health_check

DOCS_DIR = Path(__file__).parent / "docs"


# ---------------------------------------------------------------------------
# Core search functions
# ---------------------------------------------------------------------------

def run_regex_search(collection, keyword: str, runs: int = 1) -> dict:
    """
    Search using the current contains-style regex — mirrors search_service.py.
    """
    escaped = re.escape(keyword.strip())
    mongo_filter = {"primaryTitle": {"$regex": escaped, "$options": "i"}}

    timings = []
    docs = []
    for _ in range(runs):
        t0 = time.perf_counter()
        docs = list(collection.find(mongo_filter).limit(50))
        timings.append((time.perf_counter() - t0) * 1000)

    avg_ms = sum(timings) / len(timings)
    explain = _safe_explain(collection, mongo_filter)

    return {
        "method": "Regex ($regex)",
        "filter": str(mongo_filter),
        "avg_ms": round(avg_ms, 2),
        "docs_returned": len(docs),
        "docs_examined": _extract_docs_examined(explain),
        "stage": _extract_winning_stage(explain),
        "full_plan": _extract_full_plan(explain),
        "explain": explain,
        "docs": docs,
    }


def run_text_search(collection, keyword: str, runs: int = 1) -> dict:
    """
    Search using MongoDB $text operator — requires the text index to exist.
    Results sorted by relevance score.
    """
    mongo_filter = {"$text": {"$search": keyword.strip()}}
    projection = {"score": {"$meta": "textScore"}}
    sort = [("score", {"$meta": "textScore"})]

    timings = []
    docs = []
    for _ in range(runs):
        t0 = time.perf_counter()
        docs = list(
            collection.find(mongo_filter, projection)
            .sort(sort)
            .limit(50)
        )
        timings.append((time.perf_counter() - t0) * 1000)

    avg_ms = sum(timings) / len(timings)
    explain = _safe_explain(collection, mongo_filter)

    return {
        "method": "$text index",
        "filter": str(mongo_filter),
        "avg_ms": round(avg_ms, 2),
        "docs_returned": len(docs),
        "docs_examined": _extract_docs_examined(explain),
        "stage": _extract_winning_stage(explain),
        "full_plan": _extract_full_plan(explain),
        "explain": explain,
        "docs": docs,
    }


# ---------------------------------------------------------------------------
# Explain helpers — robust against different MongoDB version response shapes
# ---------------------------------------------------------------------------

def _safe_explain(collection, mongo_filter: dict) -> dict:
    """Run explain and return the raw dict, or {} on any error."""
    try:
        return collection.find(mongo_filter).explain()
    except Exception:
        return {}


def _walk_plan(plan: dict):
    """
    Generator that yields every stage node in a winning plan tree.
    Handles both inputStage (single) and inputStages (sharded/multi).
    """
    if not plan:
        return
    yield plan
    if "inputStage" in plan:
        yield from _walk_plan(plan["inputStage"])
    if "inputStages" in plan:
        for s in plan["inputStages"]:
            yield from _walk_plan(s)


def _extract_winning_stage(explain: dict) -> str:
    """
    Return the most informative stage name from the winning plan.
    Tries the standard queryPlanner path, then falls back to a
    recursive search of the whole explain dict.
    Prefers TEXT > IXSCAN > FETCH > COLLSCAN.
    """
    try:
        plan = (
            explain.get("queryPlanner", {}).get("winningPlan", {})
            or explain.get("queryPlanner", {}).get("queryPlan", {})
        )

        if not plan:
            return _recursive_find_key(explain, "stage") or "UNKNOWN"

        stages = [node.get("stage", "") for node in _walk_plan(plan)]
        for preferred in ("TEXT", "IXSCAN", "FETCH", "COLLSCAN"):
            if preferred in stages:
                return preferred
        return stages[0] if stages else "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def _extract_docs_examined(explain: dict) -> int:
    """Return totalDocsExamined from executionStats, with recursive fallback."""
    try:
        stats = explain.get("executionStats", {})
        if stats:
            val = stats.get("totalDocsExamined")
            if val is not None:
                return int(val)
        result = _recursive_find_key(explain, "totalDocsExamined")
        return int(result) if result is not None else -1
    except Exception:
        return -1


def _extract_full_plan(explain: dict) -> str:
    """Return a human-readable chain of stage names e.g. COLLSCAN → FETCH."""
    try:
        plan = (
            explain.get("queryPlanner", {}).get("winningPlan", {})
            or explain.get("queryPlanner", {}).get("queryPlan", {})
        )
        if not plan:
            stage = _recursive_find_key(explain, "stage")
            return stage if stage else "unavailable"

        stages = []
        for node in _walk_plan(plan):
            stage = node.get("stage", "")
            if not stage:
                continue
            extra = ""
            if stage == "TEXT":
                extra = f"(index: {node.get('indexName', 'text')})"
            elif stage == "IXSCAN":
                key = node.get("keyPattern", {})
                extra = f"(key: {key})"
            elif stage == "COLLSCAN":
                extra = "← no index used"
            stages.append(f"{stage} {extra}".strip())

        return " → ".join(reversed(stages)) if stages else "unavailable"
    except Exception:
        return "unavailable"


def _recursive_find_key(d, key, depth=0):
    """Recursively find the first value for a key anywhere in a nested dict."""
    if depth > 10 or not isinstance(d, dict):
        return None
    if key in d:
        return d[key]
    for v in d.values():
        result = _recursive_find_key(v, key, depth + 1)
        if result is not None:
            return result
    return None


# ---------------------------------------------------------------------------
# Text index check
# ---------------------------------------------------------------------------

def _text_index_exists(collection) -> bool:
    for idx in collection.list_indexes():
        if "text" in idx.get("key", {}).values():
            return True
    return False


# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------

def print_comparison(keyword: str, regex_result: dict, text_result: dict) -> None:
    W = 72
    print()
    print("=" * W)
    print(f"  TEXT SEARCH COMPARISON  —  keyword: \"{keyword}\"")
    print("=" * W)

    speedup = (
        f"{regex_result['avg_ms'] / text_result['avg_ms']:.1f}x faster"
        if text_result["avg_ms"] > 0 else "N/A"
    )

    rows = [
        ("Method",           regex_result["method"],             text_result["method"]),
        ("Query plan stage", regex_result["stage"],              text_result["stage"]),
        ("Avg time",         f"{regex_result['avg_ms']} ms",     f"{text_result['avg_ms']} ms  ({speedup})"),
        ("Docs examined",    str(regex_result["docs_examined"]), str(text_result["docs_examined"])),
        ("Docs returned",    str(regex_result["docs_returned"]), str(text_result["docs_returned"])),
    ]

    col_w = 22
    print(f"\n  {'Metric':<{col_w}}  {'Regex ($regex)':<26}  $text index")
    print(f"  {'-'*col_w}  {'-'*26}  {'-'*26}")
    for label, r_val, t_val in rows:
        print(f"  {label:<{col_w}}  {r_val:<26}  {t_val}")

    print(f"\n  Regex plan : {regex_result['full_plan']}")
    print(f"  $text plan : {text_result['full_plan']}")

    print(f"\n  {'─'*W}")
    print(f"  TOP RESULTS (first 8)")
    print(f"  {'─'*W}")
    regex_titles = [d.get("primaryTitle", "?") for d in regex_result["docs"][:8]]
    text_titles  = [d.get("primaryTitle", "?") for d in text_result["docs"][:8]]
    print(f"\n  {'Rank':<5}  {'Regex results':<36}  $text results (by relevance)")
    print(f"  {'----':<5}  {'-------------':<36}  ----------------------------")
    for i in range(max(len(regex_titles), len(text_titles))):
        r = regex_titles[i] if i < len(regex_titles) else "—"
        t = text_titles[i]  if i < len(text_titles)  else "—"
        flag = "  ← partial only" if (r not in text_titles and i < len(regex_titles)) else ""
        print(f"  {i+1:<5}  {r[:35]:<36}  {t[:35]}{flag}")

    print(f"\n  KEY TRADEOFFS")
    print(f"  {'─'*40}")
    print(f"  $text  → faster, index-backed, relevance scoring")
    print(f"         → BUT whole-word only ('bat' won't find 'Batman')")
    print(f"  $regex → flexible partial matching, no index benefit")
    print()
    print("=" * W)


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------

def save_markdown(keyword, collection_name, runs, regex_result, text_result) -> Path:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DOCS_DIR / "text_search_comparison.md"

    speedup = (
        f"{regex_result['avg_ms'] / text_result['avg_ms']:.1f}×"
        if text_result["avg_ms"] > 0 else "N/A"
    )

    regex_titles = [d.get("primaryTitle", "?") for d in regex_result["docs"][:10]]
    text_titles  = [d.get("primaryTitle", "?") for d in text_result["docs"][:10]]

    lines = [
        "# Text Search Comparison: `$regex` vs `$text` Index",
        "",
        f"**Keyword tested:** `{keyword}`  ",
        f"**Collection:** `{collection_name}`  ",
        f"**Runs per method:** {runs}  ",
        "",
        "## Results",
        "",
        "| Metric | Regex (`$regex`) | `$text` Index |",
        "|--------|-----------------|--------------|",
        f"| Query plan stage | `{regex_result['stage']}` | `{text_result['stage']}` |",
        f"| Avg execution time | {regex_result['avg_ms']} ms | {text_result['avg_ms']} ms |",
        f"| Speedup | — | **{speedup}** |",
        f"| Docs examined | {regex_result['docs_examined']} | {text_result['docs_examined']} |",
        f"| Docs returned | {regex_result['docs_returned']} | {text_result['docs_returned']} |",
        f"| Relevance scoring | ✗ | ✓ (`$meta: textScore`) |",
        f"| Partial/substring match | ✓ | ✗ (whole words only) |",
        "",
        "## Query Plans",
        "",
        f"**Regex plan:** `{regex_result['full_plan']}`  ",
        f"**$text plan:** `{text_result['full_plan']}`",
        "",
        "## Tradeoffs",
        "",
        "| | `$regex` | `$text` index |",
        "|---|---|---|",
        "| Speed | Slow (COLLSCAN) | Fast (index lookup) |",
        "| Partial matches | ✓ `bat` → Batman | ✗ whole words only |",
        "| Relevance ranking | ✗ | ✓ |",
        "| Case insensitive | ✓ | ✓ (built-in) |",
        "| Stemming | ✗ | ✓ (language-aware) |",
        "",
        "## Top Results Comparison",
        "",
        "| Rank | Regex results | `$text` results |",
        "|------|--------------|----------------|",
    ]
    for i in range(max(len(regex_titles), len(text_titles))):
        r = regex_titles[i] if i < len(regex_titles) else "—"
        t = text_titles[i]  if i < len(text_titles)  else "—"
        lines.append(f"| {i+1} | {r} | {t} |")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Public API — called by app.py /compare route
# ---------------------------------------------------------------------------

def compare(keyword: str, collection_name: str, runs: int = 1) -> dict:
    """
    Run both searches and return a structured dict for the web UI.
    """
    collection = get_collection(collection_name)

    regex_result = run_regex_search(collection, keyword, runs=runs)
    try:
        text_result = run_text_search(collection, keyword, runs=runs)
        text_error  = None
    except Exception as exc:
        text_result = None
        text_error  = str(exc)

    regex_top = [
        {
            "title":  d.get("primaryTitle"),
            "year":   d.get("startYear"),
            "rating": (d.get("rating") or {}).get("averageRating"),
            "type":   d.get("titleType"),
        }
        for d in regex_result["docs"][:10]
    ]
    text_top = []
    if text_result:
        text_top = [
            {
                "title":  d.get("primaryTitle"),
                "year":   d.get("startYear"),
                "rating": (d.get("rating") or {}).get("averageRating"),
                "type":   d.get("titleType"),
                "score":  round(d.get("score", 0), 3),
            }
            for d in text_result["docs"][:10]
        ]

    speedup = None
    if text_result and text_result["avg_ms"] > 0:
        speedup = round(regex_result["avg_ms"] / text_result["avg_ms"], 1)

    return {
        "keyword":            keyword,
        "collection":         collection_name,
        "text_index_exists":  _text_index_exists(collection),
        "regex": {
            "avg_ms":        regex_result["avg_ms"],
            "stage":         regex_result["stage"],
            "full_plan":     regex_result["full_plan"],
            "docs_examined": regex_result["docs_examined"],
            "docs_returned": regex_result["docs_returned"],
            "top":           regex_top,
        },
        "text": {
            "avg_ms":        text_result["avg_ms"]        if text_result else None,
            "stage":         text_result["stage"]         if text_result else None,
            "full_plan":     text_result["full_plan"]     if text_result else None,
            "docs_examined": text_result["docs_examined"] if text_result else None,
            "docs_returned": text_result["docs_returned"] if text_result else None,
            "top":           text_top,
            "error":         text_error,
        },
        "speedup": speedup,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Compare regex vs $text index keyword search."
    )
    parser.add_argument("--keyword",    default="batman")
    parser.add_argument("--collection", choices=["sample", "full"], default="sample")
    parser.add_argument("--runs",       type=int, default=1)
    args = parser.parse_args()

    if not health_check():
        sys.exit(1)

    col_name   = COLLECTION_SAMPLE if args.collection == "sample" else COLLECTION_FULL
    collection = get_collection(col_name)

    if collection.count_documents({}) == 0:
        print(f"[text_compare] '{col_name}' is empty. Run import_data.py first.")
        sys.exit(1)

    if not _text_index_exists(collection):
        print(f"\n[text_compare] WARNING: No $text index on '{col_name}'.")
        print("[text_compare] Run `python create_indexes.py` first.\n")

    print(f"\n[text_compare] Collection : {col_name}")
    print(f"[text_compare] Keyword    : \"{args.keyword}\"")
    print(f"[text_compare] Runs       : {args.runs}")

    print("\n[text_compare] Running regex search ...")
    regex_result = run_regex_search(collection, args.keyword, runs=args.runs)
    print(f"  → {regex_result['avg_ms']} ms  [{regex_result['stage']}]  "
          f"examined: {regex_result['docs_examined']}  returned: {regex_result['docs_returned']}")

    print("\n[text_compare] Running $text search ...")
    try:
        text_result = run_text_search(collection, args.keyword, runs=args.runs)
        print(f"  → {text_result['avg_ms']} ms  [{text_result['stage']}]  "
              f"examined: {text_result['docs_examined']}  returned: {text_result['docs_returned']}")
    except Exception as exc:
        print(f"  ERROR: {exc}")
        sys.exit(1)

    print_comparison(args.keyword, regex_result, text_result)

    out_path = save_markdown(
        args.keyword, col_name, args.runs, regex_result, text_result
    )
    print(f"[text_compare] Report saved to {out_path}")


if __name__ == "__main__":
    main()