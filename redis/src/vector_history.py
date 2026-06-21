"""Optional Redis-powered historical memory using local vector similarity.

Embeddings are produced with sentence-transformers and stored as JSON in
plain Redis keys (history:usage:{id}). Similarity search reads the records
back and computes cosine similarity in Python, so RediSearch is NOT required
for the MVP.

Run from inside tracks/redis.
"""

import json

import numpy as np

from redis_client import get_redis
from schema import HISTORY_PREFIX

MODEL_NAME = "all-MiniLM-L6-v2"

SAMPLE_RECORDS = [
    {
        "text": "Cold front with respiratory spike caused 40% increase in IV Fluids and N95 Masks usage.",
        "item": "IV Fluids",
        "usage_increase_pct": 40,
    },
    {
        "text": "Heat wave increased saline and IV fluid consumption by 25%.",
        "item": "Saline",
        "usage_increase_pct": 25,
    },
    {
        "text": "Normal week with no illness spike had stable supply usage.",
        "item": "All",
        "usage_increase_pct": 0,
    },
    {
        "text": "Respiratory surge increased IV Fluids demand by 35% and N95 Masks by 50%.",
        "item": "N95 Masks",
        "usage_increase_pct": 50,
    },
]


def _load_model():
    """Load the sentence-transformers model, raising a clear error on failure."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is required for vector_history. "
            "Install it with: pip install sentence-transformers"
        ) from exc

    try:
        return SentenceTransformer(MODEL_NAME)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load embedding model '{MODEL_NAME}': {exc}"
        ) from exc


def _embed(model, text):
    """Return a plain Python list embedding for the given text."""
    vector = model.encode(text)
    return np.asarray(vector, dtype=float).tolist()


def _history_key(record_id):
    """Build the Redis key for a history record."""
    return f"{HISTORY_PREFIX}:{record_id}"


def seed_history():
    """Embed the sample records and store them in Redis. Returns the keys."""
    client = get_redis()
    model = _load_model()

    keys = []
    for idx, record in enumerate(SAMPLE_RECORDS, start=1):
        stored = {
            "text": record["text"],
            "item": record["item"],
            "usage_increase_pct": record["usage_increase_pct"],
            "embedding": _embed(model, record["text"]),
        }
        key = _history_key(idx)
        client.set(key, json.dumps(stored))
        keys.append(key)
        print(f"Seeded {key}: {record['text']}")

    return keys


def _cosine_similarity(a, b):
    """Compute cosine similarity between two vectors."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def find_similar_periods(query, top_k=3):
    """Return the top_k most similar historical records to the query text."""
    client = get_redis()
    model = _load_model()
    query_embedding = _embed(model, query)

    results = []
    for key in client.scan_iter(match=f"{HISTORY_PREFIX}:*"):
        raw = client.get(key)
        if raw is None:
            continue
        record = json.loads(raw)
        score = _cosine_similarity(query_embedding, record.get("embedding", []))
        results.append(
            {
                "key": key,
                "score": score,
                "text": record.get("text"),
                "item": record.get("item"),
                "usage_increase_pct": record.get("usage_increase_pct"),
            }
        )

    results.sort(key=lambda r: r["score"], reverse=True)
    top = results[:top_k]

    print(f"\nTop {top_k} similar periods for: {query!r}")
    for rank, result in enumerate(top, start=1):
        print(f"  {rank}. ({result['score']:.3f}) {result['text']}")

    return top
