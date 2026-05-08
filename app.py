"""
IMDb Search System — Flask Web App

Run with:
  python app.py

Then open: http://localhost:5000

Set GEMINI_API_KEY in a .env file to enable the AI chat feature.
"""

import json
import os

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from database import COLLECTION_FULL, COLLECTION_SAMPLE, get_collection, health_check
from search_service import (
    DEFAULT_SORT,
    GENRES,
    RESULT_LIMIT_DEFAULT,
    SORT_OPTIONS,
    TITLE_TYPES,
    SearchParams,
    build_summary,
    search,
)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "imdb-search-dev-key")

GEMINI_MODEL = "gemini-2.5-flash"

# ---------------------------------------------------------------------------
# Gemini tool definition
# ---------------------------------------------------------------------------

def _make_search_tool():
    from google.genai import types

    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="search_movies",
                description=(
                    "Search the local IMDb MongoDB database with optional filters. "
                    "Use when the user asks about titles not in the current results, "
                    "or wants to explore a different filter combination."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "keyword": types.Schema(
                            type=types.Type.STRING,
                            description="Partial case-insensitive title keyword",
                        ),
                        "title_type": types.Schema(
                            type=types.Type.STRING,
                            enum=TITLE_TYPES,
                            description="Filter by title type (movie, tvSeries, …)",
                        ),
                        "genre": types.Schema(
                            type=types.Type.STRING,
                            enum=GENRES,
                            description="Filter by genre",
                        ),
                        "start_year": types.Schema(
                            type=types.Type.INTEGER,
                            description="Earliest release year",
                        ),
                        "end_year": types.Schema(
                            type=types.Type.INTEGER,
                            description="Latest release year",
                        ),
                        "min_rating": types.Schema(
                            type=types.Type.NUMBER,
                            description="Minimum IMDb average rating (0–10)",
                        ),
                        "min_votes": types.Schema(
                            type=types.Type.INTEGER,
                            description="Minimum number of IMDb votes",
                        ),
                        "limit": types.Schema(
                            type=types.Type.INTEGER,
                            description="Max results to return (10–50 recommended)",
                        ),
                    },
                ),
            )
        ]
    )


def _execute_search_tool(args: dict) -> dict:
    """Run search_service.search() from tool-call args and return a plain dict."""
    params = SearchParams(
        keyword=args.get("keyword", ""),
        title_type=args.get("title_type", ""),
        genre=args.get("genre", ""),
        start_year=args.get("start_year"),
        end_year=args.get("end_year"),
        min_rating=args.get("min_rating"),
        min_votes=args.get("min_votes"),
        limit=min(int(args.get("limit", 10)), 50),
    )
    result = search(params)
    if result.error:
        return {"error": result.error}

    docs = []
    for doc in result.documents:
        rating = doc.get("rating") or {}
        docs.append({
            "title": doc.get("primaryTitle"),
            "year": doc.get("startYear"),
            "type": doc.get("titleType"),
            "genres": doc.get("genres") or [],
            "rating": rating.get("averageRating"),
            "votes": rating.get("numVotes"),
        })
    return {"count": result.count, "query_ms": result.query_ms, "results": docs}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET", "POST"])
def index():
    db_ok = health_check()

    results = None
    error = None
    query_ms = None
    count = None
    query_filter = None
    query_summary = None

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
            if count and count > 0:
                query_summary = build_summary(params, result)

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
        query_summary=query_summary,
        error=error,
        collections=[COLLECTION_SAMPLE, COLLECTION_FULL],
        title_types=TITLE_TYPES,
        genres=GENRES,
        sort_options=list(SORT_OPTIONS.keys()),
        limit_options=[10, 20, 50, 100, 200],
        default_limit=RESULT_LIMIT_DEFAULT,
        gemini_configured=bool(os.environ.get("GEMINI_API_KEY")),
    )


@app.route("/chat", methods=["POST"])
def chat():
    """
    Chat endpoint — stateless.

    Expects JSON:  { "messages": [...], "query_summary": {...} | null }

    Uses Gemini 2.5 Flash with function calling. The compact query_summary
    (~200 tokens) is passed as system context; the search_movies tool lets
    the model fetch additional data on-demand.
    """
    if not request.is_json:
        return jsonify({"error": "JSON required"}), 400

    data = request.get_json()
    user_messages: list = data.get("messages", [])
    query_summary: dict | None = data.get("query_summary")

    if not user_messages:
        return jsonify({"error": "No messages provided"}), 400

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return jsonify({"error": "GEMINI_API_KEY is not set on the server."}), 503

    # Build system prompt with compact summary
    system_parts = [
        "You are a movie-obsessed best friend — enthusiastic, opinionated, and genuinely excited "
        "to help someone find the perfect thing to watch. You know the IMDb database inside out. "
        "Your tone is warm, casual, and conversational — like texting a friend who has seen everything. "
        "Give concrete recommendations with a short reason why they'll love it. "
        "If someone is unsure, ask one quick follow-up question to narrow it down (mood, genre, who they're watching with). "
        "Never just list titles — sell them on it. Keep replies short and punchy unless they ask for detail. "
        "Use the search_movies tool whenever you need fresh data from the database."
    ]

    if query_summary:
        filters = query_summary.get("filters_applied") or {}
        filter_str = ", ".join(f"{k}={v}" for k, v in filters.items()) or "none (all titles)"
        system_parts.append(
            f"\n\n## Current Search Results\n"
            f"- {query_summary.get('total_results')} results from `{query_summary.get('collection')}`\n"
            f"- Filters: {filter_str}\n"
            f"- Sort: {query_summary.get('sort_by')}\n"
            f"- Avg rating: {query_summary.get('average_rating_in_results')}\n"
            f"- Top genres: {json.dumps(query_summary.get('genre_distribution') or {})}\n"
            f"\n### Top 10 Results\n"
        )
        for i, doc in enumerate(query_summary.get("top_10_results") or [], 1):
            genres_str = ", ".join(doc.get("genres") or []) or "—"
            system_parts.append(
                f"{i}. {doc.get('title')} ({doc.get('year')}) "
                f"| {doc.get('type')} | rating: {doc.get('rating')} "
                f"| {genres_str}\n"
            )
        system_parts.append(
            "\nCall search_movies for anything outside this list."
        )

    system_content = "".join(system_parts)

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        search_tool = _make_search_tool()

        # Convert OpenAI-style messages [{role, content}] → Gemini contents
        contents = []
        for msg in user_messages:
            role = "model" if msg["role"] == "assistant" else "user"
            contents.append(
                types.Content(role=role, parts=[types.Part.from_text(text=msg["content"])])
            )

        # Agentic tool-call loop (max 5 rounds)
        for _ in range(5):
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_content,
                    tools=[search_tool],
                ),
            )

            candidate = response.candidates[0]
            function_calls = [
                p for p in candidate.content.parts
                if p.function_call is not None
            ]

            if function_calls:
                # Append model turn (with function calls)
                contents.append(candidate.content)

                # Execute all function calls and collect responses
                response_parts = []
                for part in function_calls:
                    args = dict(part.function_call.args)
                    tool_result = _execute_search_tool(args)
                    response_parts.append(
                        types.Part.from_function_response(
                            name=part.function_call.name,
                            response=tool_result,
                        )
                    )

                contents.append(types.Content(role="user", parts=response_parts))
            else:
                return jsonify({"reply": response.text})

        return jsonify({
            "reply": "I needed too many database queries to answer that. Try a more specific question."
        })

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
