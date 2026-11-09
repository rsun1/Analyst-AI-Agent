"""
RAG: embed the user question and retrieve the top-k most similar schema chunks
from Postgres using pgvector (cosine distance, `<=>`).
"""
import psycopg
from openai import OpenAI
from pgvector.psycopg import register_vector

from config import (
    EMBEDDING_MODEL,
    OPENAI_API_KEY,
    PG_READER_DSN,
    TOP_K_CHUNKS,
)


def retrieve(question: str, top_k: int | None = None) -> list[str]:
    """
    Embed the question and return the text of the top-k most similar chunks
    (at most TOP_K_CHUNKS per PRD). Similarity is computed inside Postgres.
    """
    top_k = top_k if top_k is not None else TOP_K_CHUNKS
    top_k = min(top_k, TOP_K_CHUNKS)

    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not set in .env")

    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=[question])
    query_embedding = response.data[0].embedding

    with psycopg.connect(PG_READER_DSN) as conn:
        register_vector(conn)
        rows = conn.execute(
            "SELECT text FROM schema_chunks ORDER BY embedding <=> %s::vector LIMIT %s",
            (query_embedding, top_k),
        ).fetchall()
    return [r[0] for r in rows]
