# IMDb Search and Query Optimization Using Local MongoDB

A local MongoDB-based IMDb search system built for an Advanced Database Systems course.  
Demonstrates document modeling, data cleaning, indexing, query optimization, and benchmarking with real-world IMDb data.

---

## Project Overview

| Feature | Details |
|---|---|
| Database | Local MongoDB (`imdb_search`) |
| Collections | `titles_sample` (10k docs) · `titles_full` (100k+ docs) |
| Dataset | IMDb non-commercial TSV files |
| UI | Flask web app |
| Search filters | Keyword, title type, genre, year range, rating, votes |
| Search modes | Regex (partial match) · $text index (fast, relevance-ranked) |
| Index types | Single-field, compound, multikey (array), text |

---

## Requirements

- Python 3.11+
- MongoDB 6.0+ running locally on `localhost:27017`
- pip packages (see `requirements.txt`)

### Install Python packages

```bash
pip install -r requirements.txt
```

---

## Setup Steps

### 1. Start MongoDB

Make sure your local MongoDB server is running before any other step.

```bash
# macOS with Homebrew
brew services start mongodb-community

# Linux (systemd)
sudo systemctl start mongod

# Verify
mongosh --eval "db.adminCommand('ping')"
```

### 2. Download IMDb data files

```bash
python download_data.py
```

Downloads `title.basics.tsv.gz` and `title.ratings.tsv.gz` into `data/`.  
Skip this step if you already have the files in `data/`.

### 3. Import data (sample — recommended first)

```bash
python import_data.py --mode sample --clear
```

Imports up to 10,000 quality movie records into `titles_sample`.

To import the larger dataset:

```bash
python import_data.py --mode full --clear
```

Imports up to 100,000 records into `titles_full`.

### 4. Create indexes

```bash
python create_indexes.py
```

Creates all required single-field, compound, and text indexes on both collections (only operates on non-empty ones).

### 5. Run the UI

```bash
python app.py
```

Opens at `http://localhost:5000` in your browser.

---

## Running the Benchmark

The benchmark compares query performance **before** and **after** indexes.

```bash
# Benchmark titles_sample
python benchmark.py

# Benchmark titles_full (more visible improvement)
python benchmark.py --collection full

# Run each query 3 times for averaged results
python benchmark.py --collection full --runs 3
```

Results are printed to the terminal and saved to `docs/benchmark_results.md`.

---

## Text Search Comparison

Compare the current `$regex` keyword search against the `$text` index directly from the UI at `http://localhost:5000/compare`, or from the command line:

```bash
python text_search_comparison.py --keyword batman --collection full --runs 3
```

Results are saved to `docs/text_search_comparison.md`.

---

## Resetting for a Clean Re-import

```bash
# Drop both collections (with confirmation prompt)
python reset_database.py

# Drop only indexes, keep data
python reset_database.py --indexes-only

# Drop a single collection without prompt (for scripting)
python reset_database.py --collection sample --yes
```

---

## File Reference

| File | Purpose |
|---|---|
| `database.py` | MongoDB connection helper, collection selector |
| `download_data.py` | Download IMDb dataset files |
| `import_data.py` | Clean, merge, and import title + rating data |
| `create_indexes.py` | Create single-field, compound, and text indexes |
| `search_service.py` | Dynamic query builder with execution timing; supports regex and $text modes |
| `text_search_comparison.py` | Side-by-side regex vs $text index performance comparison |
| `app.py` | Flask search UI with search mode toggle and comparison page |
| `benchmark.py` | Before/after index performance comparison |
| `reset_database.py` | Drop collections or indexes for re-import |
| `templates/index.html` | Main search page |
| `templates/compare.html` | Regex vs $text comparison page |
| `data/` | IMDb `.tsv.gz` files (not committed to git) |
| `docs/` | Report, benchmark results, screenshots |

---

## MongoDB Document Structure

Each document represents one IMDb title with an embedded rating:

```json
{
  "tconst": "tt0111161",
  "titleType": "movie",
  "primaryTitle": "The Shawshank Redemption",
  "originalTitle": "The Shawshank Redemption",
  "searchTitle": "the shawshank redemption",
  "isAdult": false,
  "startYear": 1994,
  "endYear": null,
  "runtimeMinutes": 142,
  "genres": ["Drama"],
  "rating": {
    "averageRating": 9.3,
    "numVotes": 2950000
  },
  "importedAt": "2024-01-01T00:00:00Z"
}
```

---

## Indexes Created

### Single-field

| Field | Purpose |
|---|---|
| `tconst` | Unique ID lookup |
| `primaryTitle` | Keyword search |
| `searchTitle` | Lowercase keyword search |
| `titleType` | Title type equality filter |
| `startYear` | Year range queries |
| `genres` | Genre array membership (multikey) |
| `rating.averageRating` | Rating filter and sort |
| `rating.numVotes` | Votes filter and sort |

### Compound

| Index | Purpose |
|---|---|
| `(titleType, startYear)` | Filter movies by year range |
| `(genres, startYear, rating.averageRating)` | Genre + year + rating combined |
| `(titleType, genres, startYear, rating.averageRating)` | Full combined search |
| `(titleType, rating.numVotes)` | Popular titles by type |

### Text

| Index | Purpose |
|---|---|
| `primaryTitle` (text) | Fast whole-word keyword search with relevance scoring via `$text` |

---

## Search Filters Available

| Filter | Description |
|---|---|
| Keyword | Partial, case-insensitive title search |
| Search Mode | **Regex** — flexible partial match (e.g. "bat" finds "Batman") · **$text** — fast index lookup, whole-word, relevance-ranked |
| Title Type | movie, tvSeries, short, tvMovie, video, etc. |
| Genre | Action, Drama, Comedy, Horror, etc. |
| Year Range | From/to start year |
| Minimum Rating | 0.0–10.0 |
| Minimum Votes | Positive integer |
| Result Limit | 10, 20, 50, 100, or 200 |
| Sort | Rating, votes, year, or title |

---

## Sample Searches

**Top Action movies since 2000:**
- Title Type: `movie` · Genre: `Action` · Year from: `2000` · Min Rating: `7.0` · Limit: `50`

**Highly rated popular films:**
- Title Type: `movie` · Min Rating: `8.0` · Min Votes: `50000` · Limit: `100`

**Find Batman titles:**
- Keyword: `Batman` · Sort: Rating high to low · Limit: `50`

**Find Batman titles with relevance ranking:**
- Keyword: `Batman` · Search Mode: `$text` · Sort: Rating high to low · Limit: `50`

---

## Limitations

- Only `title.basics` and `title.ratings` files are used; actor/director search is not included.
- Regex keyword search uses a contains-style pattern which does not benefit from a normal index (prefix-style regex is more index-friendly).
- `$text` search matches whole words only — searching "bat" will not find "Batman".
- Designed for local MongoDB only — not Atlas or cloud deployment.

---

## Future Work

- Add cast/crew search using `title.principals.tsv.gz`
- Add pagination for large result sets
- Add charts (rating distribution, genres over time)
- Add Atlas Search for full-text capabilities
- Add a user watchlist feature

---

## Data Source

IMDb Non-Commercial Datasets — https://developer.imdb.com/non-commercial-datasets/

> IMDb data is used for personal and non-commercial purposes only, under IMDb's stated license terms.