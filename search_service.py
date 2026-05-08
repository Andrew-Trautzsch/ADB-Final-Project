"""
Search service for the IMDb search system.

Accepts filter parameters, builds a MongoDB query dynamically,
measures execution time, and returns results plus metadata.

The UI (app.py) and benchmark.py both call this module — it has no
knowledge of how results are displayed.
"""

import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from database import COLLECTION_SAMPLE, get_collection

# ---------------------------------------------------------------------------
# Sort options
# ---------------------------------------------------------------------------

SORT_OPTIONS: dict[str, list[tuple[str, int]]] = {
    "Rating (high to low)": [("rating.averageRating", -1)],
    "Votes (high to low)": [("rating.numVotes", -1)],
    "Year (newest first)": [("startYear", -1)],
    "Year (oldest first)": [("startYear", 1)],
    "Title (A–Z)": [("primaryTitle", 1)],
}

DEFAULT_SORT = "Rating (high to low)"

# ---------------------------------------------------------------------------
# Valid genres (matches the plan spec)
# ---------------------------------------------------------------------------

GENRES = [
    "Action", "Adventure", "Animation", "Comedy", "Crime",
    "Documentary", "Drama", "Family", "Fantasy", "Horror",
    "Mystery", "Romance", "Sci-Fi", "Thriller", "War", "Western",
]

TITLE_TYPES = [
    "movie", "short", "tvSeries", "tvMovie", "video",
    "tvMiniSeries", "tvEpisode",
]

RESULT_LIMIT_MIN = 10
RESULT_LIMIT_MAX = 200
RESULT_LIMIT_DEFAULT = 50


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SearchParams:
    collection_name: str = COLLECTION_SAMPLE
    keyword: str = ""
    title_type: str = ""          # empty string means "Any"
    genre: str = ""               # empty string means "Any"
    start_year: int | None = None
    end_year: int | None = None
    min_rating: float | None = None
    min_votes: int | None = None
    limit: int = RESULT_LIMIT_DEFAULT
    sort_by: str = DEFAULT_SORT


@dataclass
class SearchResult:
    documents: list[dict] = field(default_factory=list)
    count: int = 0
    query_ms: float = 0.0
    query_filter: dict = field(default_factory=dict)
    sort_spec: list = field(default_factory=list)
    limit_applied: int = RESULT_LIMIT_DEFAULT
    error: str = ""


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def _clamp_limit(limit: int) -> int:
    return max(RESULT_LIMIT_MIN, min(RESULT_LIMIT_MAX, limit))


def _validate_params(params: SearchParams) -> str:
    """Return an error string if params are invalid, else empty string."""
    if params.min_rating is not None and not (0.0 <= params.min_rating <= 10.0):
        return "Minimum rating must be between 0 and 10."
    if params.min_votes is not None and params.min_votes < 0:
        return "Minimum votes must be non-negative."
    if params.start_year is not None and not (1800 <= params.start_year <= 2200):
        return "Start year must be a valid year (1800–2200)."
    if params.end_year is not None and not (1800 <= params.end_year <= 2200):
        return "End year must be a valid year (1800–2200)."
    if (
        params.start_year is not None
        and params.end_year is not None
        and params.start_year > params.end_year
    ):
        return "Start year must not be greater than end year."
    if params.sort_by not in SORT_OPTIONS:
        return f"Unknown sort option '{params.sort_by}'."
    return ""


# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------

def _build_filter(params: SearchParams) -> dict[str, Any]:
    """Translate SearchParams into a MongoDB filter document."""
    mongo_filter: dict[str, Any] = {}

    # Keyword search on primaryTitle (case-insensitive, partial match)
    if params.keyword.strip():
        # Escape special regex chars, then wrap in a case-insensitive contains pattern
        escaped = re.escape(params.keyword.strip())
        mongo_filter["primaryTitle"] = {"$regex": escaped, "$options": "i"}

    # Title type equality
    if params.title_type:
        mongo_filter["titleType"] = params.title_type

    # Genre — array contains
    if params.genre:
        mongo_filter["genres"] = params.genre

    # Year range
    year_clause: dict[str, int] = {}
    if params.start_year is not None:
        year_clause["$gte"] = params.start_year
    if params.end_year is not None:
        year_clause["$lte"] = params.end_year
    if year_clause:
        mongo_filter["startYear"] = year_clause

    # Minimum rating (treat 0 as "no minimum" if it ever slips through)
    if params.min_rating is not None and params.min_rating > 0:
        mongo_filter["rating.averageRating"] = {"$gte": params.min_rating}

    # Minimum votes
    if params.min_votes is not None and params.min_votes > 0:
        mongo_filter["rating.numVotes"] = {"$gte": params.min_votes}

    return mongo_filter


# ---------------------------------------------------------------------------
# Main search function
# ---------------------------------------------------------------------------

def search(params: SearchParams) -> SearchResult:
    """
    Execute a search against MongoDB and return a SearchResult.

    Always applies a result limit (10–200) so the full collection is
    never accidentally returned.
    """
    result = SearchResult()

    # Validate
    error = _validate_params(params)
    if error:
        result.error = error
        return result

    safe_limit = _clamp_limit(params.limit)
    sort_spec = SORT_OPTIONS[params.sort_by]
    mongo_filter = _build_filter(params)

    result.query_filter = mongo_filter
    result.sort_spec = sort_spec
    result.limit_applied = safe_limit

    try:
        collection = get_collection(params.collection_name)

        t0 = time.perf_counter()
        cursor = (
            collection.find(mongo_filter)
            .sort(sort_spec)
            .limit(safe_limit)
        )
        docs = list(cursor)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        result.documents = docs
        result.count = len(docs)
        result.query_ms = round(elapsed_ms, 2)

    except Exception as exc:
        result.error = str(exc)

    return result


# ---------------------------------------------------------------------------
# Result summary for LLM context
# ---------------------------------------------------------------------------

def build_summary(params: SearchParams, result: SearchResult) -> dict:
    """
    Build a compact, token-efficient summary of search results for LLM context.

    Instead of sending all documents (~20k tokens for 200 results), this produces
    a ~200-token dict with: applied filters, aggregate stats, and the top 10 results.
    The LLM can call the search_movies tool for anything outside this window.
    """
    top_docs = []
    for doc in result.documents[:10]:
        rating = doc.get("rating") or {}
        top_docs.append({
            "title": doc.get("primaryTitle"),
            "year": doc.get("startYear"),
            "type": doc.get("titleType"),
            "genres": doc.get("genres") or [],
            "rating": rating.get("averageRating"),
            "votes": rating.get("numVotes"),
            "tconst": doc.get("tconst"),
        })

    genre_counts: Counter = Counter()
    for doc in result.documents:
        for g in doc.get("genres") or []:
            genre_counts[g] += 1

    raw_ratings = [
        (doc.get("rating") or {}).get("averageRating")
        for doc in result.documents
        if (doc.get("rating") or {}).get("averageRating") is not None
    ]
    avg_rating = round(sum(raw_ratings) / len(raw_ratings), 2) if raw_ratings else None

    filters: dict = {}
    if params.keyword:
        filters["keyword"] = params.keyword
    if params.title_type:
        filters["title_type"] = params.title_type
    if params.genre:
        filters["genre"] = params.genre
    if params.start_year is not None:
        filters["start_year"] = params.start_year
    if params.end_year is not None:
        filters["end_year"] = params.end_year
    if params.min_rating is not None:
        filters["min_rating"] = params.min_rating
    if params.min_votes is not None:
        filters["min_votes"] = params.min_votes

    return {
        "total_results": result.count,
        "limit_applied": result.limit_applied,
        "query_ms": result.query_ms,
        "collection": params.collection_name,
        "sort_by": params.sort_by,
        "filters_applied": filters,
        "top_10_results": top_docs,
        "genre_distribution": dict(genre_counts.most_common(5)),
        "average_rating_in_results": avg_rating,
    }


# ---------------------------------------------------------------------------
# Quick CLI smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    params = SearchParams(
        keyword="Batman",
        title_type="movie",
        min_rating=6.0,
        limit=10,
        sort_by="Rating (high to low)",
    )
    res = search(params)
    if res.error:
        print(f"Error: {res.error}")
    else:
        print(f"Found {res.count} results in {res.query_ms} ms")
        for doc in res.documents:
            rating = doc.get("rating", {})
            print(
                f"  {doc.get('primaryTitle')} "
                f"({doc.get('startYear')}) "
                f"— rating: {rating.get('averageRating')}"
            )
