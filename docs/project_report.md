# IMDb Search and Query Optimization Using Local MongoDB

**Course:** Advanced Database Systems  
**Project Title:** IMDb Search and Query Optimization Using Local MongoDB

---

## 1. Introduction

IMDb publishes large-scale title and rating datasets as downloadable TSV files.
Searching this data efficiently requires thoughtful data modeling, proper type
handling, indexing, and query design.

This project builds a local MongoDB-based IMDb search system to demonstrate:

- Document-oriented data modeling
- Real-world dataset import and cleaning
- Multi-condition search queries
- Index creation and the impact of indexes on query performance
- Before/after benchmarking using MongoDB's `explain()` output

The system allows a user to search by keyword, title type, genre, year range,
minimum rating, and minimum vote count through a Streamlit web interface.

---

## 2. Dataset

### Source Files

| File | Purpose |
|---|---|
| `title.basics.tsv.gz` | Title metadata: type, name, year, runtime, genres |
| `title.ratings.tsv.gz` | IMDb rating: average score and vote count |

Both files are connected via `tconst` (unique IMDb title ID).

### Fields Used

**From `title.basics`:**
`tconst`, `titleType`, `primaryTitle`, `originalTitle`, `isAdult`,
`startYear`, `endYear`, `runtimeMinutes`, `genres`

**From `title.ratings`:**
`tconst`, `averageRating`, `numVotes`

### Missing Value Handling

IMDb uses the literal string `\N` for missing values.
The import script converts these to `null` (or `[]` for genre arrays).
No field in the database contains the raw `\N` string.

### Data Cleaning Rules

| Raw value | Stored as |
|---|---|
| `\N` | `null` |
| `"1994"` | `1994` (integer) |
| `"8.2"` | `8.2` (float) |
| `"Action,Drama"` | `["Action", "Drama"]` (array) |
| `"0"` for isAdult | `false` (boolean) |

---

## 3. System Architecture

```
IMDb .tsv.gz Files
        │
        ▼
 import_data.py          ← reads, cleans, merges, batch-inserts
        │
        ▼
 Local MongoDB (imdb_search)
   ├── titles_sample     ← ~10,000 quality movies (dev/testing)
   └── titles_full       ← ~100,000+ records (benchmarking)
        │
        ├──▶ search_service.py   ← query builder + timing
        │           │
        │           ▼
        │       app.py (Streamlit UI)
        │
        └──▶ benchmark.py        ← before/after index comparison
```

---

## 4. MongoDB Document Design

Each document represents one IMDb title.
Rating data is **embedded** inside the title document rather than stored
in a separate collection, because the UI always displays title and rating
together and MongoDB does not support joins natively.

### Example Document

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

### Design Decisions

**`searchTitle`** — a lowercase copy of `primaryTitle` stored to support
case-insensitive prefix lookups without a regex option flag.

**`genres` as array** — the raw `"Action,Crime,Drama"` string is split into
`["Action", "Crime", "Drama"]` so that MongoDB can use a multikey index and
simple equality filters (`{ genres: "Action" }`).

**Embedded rating** — avoids a join-like `$lookup` on every search query.

---

## 5. Data Import Process

The import script (`import_data.py`) follows this sequence:

1. Load all of `title.ratings.tsv.gz` into a Python dict keyed by `tconst`.
2. Stream `title.basics.tsv.gz` row by row.
3. For each row: clean types, convert genres to array, attach rating object.
4. For `titles_sample`: apply quality filter (`titleType=movie`, rating exists,
   `startYear` not null, `numVotes ≥ 1000`).
5. Stop after the configured limit (10,000 for sample, 100,000 for full).
6. Insert in batches of 1,000 documents.
7. Print import summary.

Indexes are created **after** bulk import to avoid the overhead of updating
indexes on every insert.

---

## 6. Search Interface

The Streamlit UI (`app.py`) provides these filters:

| Filter | MongoDB Operator |
|---|---|
| Keyword | `{ primaryTitle: { $regex: "...", $options: "i" } }` |
| Title Type | `{ titleType: "movie" }` |
| Genre | `{ genres: "Action" }` |
| Year Range | `{ startYear: { $gte: 2000, $lte: 2024 } }` |
| Min Rating | `{ "rating.averageRating": { $gte: 7.5 } }` |
| Min Votes | `{ "rating.numVotes": { $gte: 10000 } }` |

All queries apply a result limit (10–200) and a user-selected sort.
Every search reports execution time in milliseconds.

### Regex Search Note

MongoDB can use a normal index for prefix-style regex (e.g. `^Batman`).
A contains-style regex (e.g. `Batman` anywhere) requires a collection scan
unless a text index is used. The UI uses contains-style regex for flexibility;
this limitation is noted in the benchmark section.

---

## 7. Index Design

Indexes follow the MongoDB ESR guideline:
**Equality** fields first, then **Sort** fields, then **Range** fields.

### Single-field Indexes

| Field | Type |
|---|---|
| `tconst` | Ascending |
| `primaryTitle` | Ascending |
| `searchTitle` | Ascending |
| `titleType` | Ascending |
| `startYear` | Ascending |
| `genres` | Ascending (multikey — array field) |
| `rating.averageRating` | Descending |
| `rating.numVotes` | Descending |

### Compound Indexes

| Index | Rationale |
|---|---|
| `(titleType, startYear)` | Filter by type then narrow by year |
| `(genres, startYear, rating.averageRating)` | Genre + year range + rating sort |
| `(titleType, genres, startYear, rating.averageRating)` | Full combined search — most common query pattern |
| `(titleType, rating.numVotes)` | Popular titles by type |

---

## 8. Benchmark Results

*(Run `python benchmark.py` to populate this section.)*

After running the benchmark, results are written to `docs/benchmark_results.md`.

Expected pattern: before indexes, queries perform a **COLLSCAN** (full
collection scan). After indexes, they switch to **IXSCAN** or **FETCH**,
with significantly fewer documents examined and lower execution time.

---

## 9. Limitations

- Only two IMDb files are used; actor and director search is not available.
- Keyword search (contains-style regex) may not benefit strongly from a
  normal index. Genre, year, rating, and combined filters benefit most.
- Full-text search (`$text` index) is not implemented.
- The system runs on local MongoDB only; no cloud or Atlas deployment.
- Adult content is present in the full dataset but filtered out in
  `titles_sample` by the quality filter.

---

## 10. Future Work

- Import `title.principals.tsv.gz` to add actor and director search.
- Add a MongoDB text index on `primaryTitle` for better keyword performance.
- Add pagination (cursor-based) for large result sets.
- Visualize rating distributions and genre trends with charts.
- Explore MongoDB Atlas Search for full-text capabilities.
- Add a user watchlist stored as a separate MongoDB collection.
