"""
RAG: Load embeddings store, embed user question, retrieve top-k chunks by cosine similarity.
All in-memory; no external vector DB.
"""
import json
from pathlib import Path

from openai import OpenAI

from config import (
    EMBEDDING_MODEL,
    EMBEDDINGS_STORE_PATH,
    OPENAI_API_KEY,
    TOP_K_CHUNKS,
)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def load_store(store_path: str | Path | None = None) -> list[dict]:
    """Load the embeddings store JSON and return list of {text, embedding}."""
    path = Path(store_path or EMBEDDINGS_STORE_PATH)
    if not path.exists():
        raise FileNotFoundError(
            f"Embeddings store not found: {path}. Run ingest first (e.g. python -m ingest)."
        )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("chunks", [])


def retrieve(
    question: str,
    store_path: str | Path | None = None,
    top_k: int | None = None,
) -> list[str]:
    """
    Embed the question, compute cosine similarity with all stored chunks,
    return the text of the top-k most similar chunks (at most 5 per PRD).
    """
    top_k = top_k if top_k is not None else TOP_K_CHUNKS
    top_k = min(top_k, TOP_K_CHUNKS)

    chunks = load_store(store_path)
    if not chunks:
        return []

    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not set in .env")

    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=[question])
    query_embedding = response.data[0].embedding

    scored = [
        (cosine_similarity(query_embedding, c["embedding"]), c["text"])
        for c in chunks
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [text for _, text in scored[:top_k]]
