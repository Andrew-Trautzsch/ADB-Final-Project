"""
IMDb Search System — Flask Web App

Run with:
  python app.py

Then open: http://localhost:5000
"""

from flask import Flask, render_template, request

from database import COLLECTION_FULL, COLLECTION_SAMPLE, get_collection, health_check
from search_service import (
    DEFAULT_SORT,
    GENRES,
    RESULT_LIMIT_DEFAULT,
    SORT_OPTIONS,
    TITLE_TYPES,
    SearchParams,
    search,
)

app = Flask(__name__)


def _parse_int(value: str) -> int | None:
    try:
        return int(value.strip()) if value and value.strip() else None
    except ValueError:
        return None


def _parse_float(value: str) -> float | None:
    try:
        return float(value.strip()) if value and value.strip() else None
    except ValueError:
        return None


def _collection_doc_count(name: str) -> int:
    try:
        return get_collection(name).count_documents({})
    except Exception:
        return 0


@app.route("/", methods=["GET", "POST"])
def index():
    db_ok = health_check()

    results = None
    error = None
    query_ms = None
    count = None
    query_filter = None

    # Preserve form state across POST
    form = {
        "collection": COLLECTION_SAMPLE,
        "keyword": "",
        "title_type": "",
        "genre": "",
        "start_year": "",
        "end_year": "",
        "min_rating": "",
        "min_votes": "",
        "limit": str(RESULT_LIMIT_DEFAULT),
        "sort_by": DEFAULT_SORT,
    }

    if request.method == "POST" and db_ok:
        form = {
            "collection": request.form.get("collection", COLLECTION_SAMPLE),
            "keyword": request.form.get("keyword", "").strip(),
            "title_type": request.form.get("title_type", ""),
            "genre": request.form.get("genre", ""),
            "start_year": request.form.get("start_year", "").strip(),
            "end_year": request.form.get("end_year", "").strip(),
            "min_rating": request.form.get("min_rating", "").strip(),
            "min_votes": request.form.get("min_votes", "").strip(),
            "limit": request.form.get("limit", str(RESULT_LIMIT_DEFAULT)),
            "sort_by": request.form.get("sort_by", DEFAULT_SORT),
        }

        # Optional filters: blank or zero means "do not apply" (same as plan for rating).
        min_rating = _parse_float(form["min_rating"])
        if min_rating is not None and min_rating <= 0:
            min_rating = None
        min_votes = _parse_int(form["min_votes"])
        if min_votes is not None and min_votes <= 0:
            min_votes = None

        params = SearchParams(
            collection_name=form["collection"],
            keyword=form["keyword"],
            title_type=form["title_type"],
            genre=form["genre"],
            start_year=_parse_int(form["start_year"]),
            end_year=_parse_int(form["end_year"]),
            min_rating=min_rating,
            min_votes=min_votes,
            limit=int(form["limit"]) if form["limit"] else RESULT_LIMIT_DEFAULT,
            sort_by=form["sort_by"],
        )

        result = search(params)

        if result.error:
            error = result.error
        else:
            results = result.documents
            count = result.count
            query_ms = result.query_ms
            query_filter = str(result.query_filter)

    sample_count = _collection_doc_count(COLLECTION_SAMPLE) if db_ok else 0
    full_count = _collection_doc_count(COLLECTION_FULL) if db_ok else 0

    return render_template(
        "index.html",
        db_ok=db_ok,
        sample_count=sample_count,
        full_count=full_count,
        form=form,
        results=results,
        count=count,
        query_ms=query_ms,
        query_filter=query_filter,
        error=error,
        collections=[COLLECTION_SAMPLE, COLLECTION_FULL],
        title_types=TITLE_TYPES,
        genres=GENRES,
        sort_options=list(SORT_OPTIONS.keys()),
        limit_options=[10, 20, 50, 100, 200],
        default_limit=RESULT_LIMIT_DEFAULT,
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
