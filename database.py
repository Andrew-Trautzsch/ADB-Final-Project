"""
MongoDB connection helper for the IMDb Search System.

Provides a single entry point for connecting to the local MongoDB instance
and selecting the appropriate collection (titles_sample or titles_full).
"""

import sys
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "imdb_search"

COLLECTION_SAMPLE = "titles_sample"
COLLECTION_FULL = "titles_full"

_client: MongoClient | None = None


def get_client() -> MongoClient:
    """Return a shared MongoClient, creating it on first call."""
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    return _client


def get_db():
    """Return the imdb_search database."""
    return get_client()[DB_NAME]


def get_collection(collection_name: str = COLLECTION_SAMPLE):
    """
    Return the requested collection from the imdb_search database.

    Args:
        collection_name: Either COLLECTION_SAMPLE or COLLECTION_FULL.
    """
    if collection_name not in (COLLECTION_SAMPLE, COLLECTION_FULL):
        raise ValueError(
            f"Unknown collection '{collection_name}'. "
            f"Use '{COLLECTION_SAMPLE}' or '{COLLECTION_FULL}'."
        )
    return get_db()[collection_name]


def health_check() -> bool:
    """
    Verify the MongoDB server is reachable.

    Returns True on success, prints an error and returns False otherwise.
    """
    try:
        get_client().admin.command("ping")
        print(f"[database] Connected to MongoDB at {MONGO_URI}")
        return True
    except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
        print(f"[database] Cannot reach MongoDB at {MONGO_URI}: {exc}", file=sys.stderr)
        return False


def close():
    """Close the shared MongoClient if it is open."""
    global _client
    if _client is not None:
        _client.close()
        _client = None


if __name__ == "__main__":
    ok = health_check()
    if ok:
        db = get_db()
        for name in (COLLECTION_SAMPLE, COLLECTION_FULL):
            col = db[name]
            count = col.count_documents({})
            print(f"  {name}: {count:,} documents")
    sys.exit(0 if ok else 1)
