"""
Phase 1: Ingest olist_schema_queries.md, chunk by ### definition units,
generate embeddings via OpenAI, and store in a local JSON file.
"""
import json
from pathlib import Path

from openai import OpenAI

from config import (
    EMBEDDING_MODEL,
    EMBEDDINGS_STORE_PATH,
    OPENAI_API_KEY,
    SCHEMA_DOC_PATH,
)


def chunk_schema_document(text: str) -> list[str]:
    """
    Split the schema document into logical chunks using the mandated structure.
    Each chunk is either: (1) the intro before the first ### definition,
    or (2) one ### definition: <name> block including its ### SQL query and code.
    Uses native string operations only (no external chunking libs).
    """
    if not text or not text.strip():
        return []

    # Split on newline + "### definition:" so each part after the first
    # is the content of one definition block (name + description + SQL query).
    parts = text.split("\n### definition:")

    chunks = []
    for i, part in enumerate(parts):
        part = part.strip()
        if not part:
            continue
        if i == 0:
            # Intro: everything before the first ### definition (e.g. doc title, ## 1. Sales).
            chunks.append(part)
        else:
            # Re-attach the delimiter so the chunk is self-contained.
            chunks.append("### definition: " + part)

    return chunks


def get_embeddings(client: OpenAI, chunks: list[str]) -> list[list[float]]:
    """Call OpenAI Embeddings API for each chunk. Batched in one request if possible."""
    if not chunks:
        return []

    response = client.embeddings.create(model=EMBEDDING_MODEL, input=chunks)
    # Preserve order by index
    by_index = {item.index: item.embedding for item in response.data}
    return [by_index[i] for i in range(len(chunks))]


def run_ingest(
    schema_path: str | Path | None = None,
    store_path: str | Path | None = None,
) -> str:
    """
    Read schema doc, chunk, embed, and save to JSON store.
    Returns the path to the written store file.
    """
    schema_path = schema_path or SCHEMA_DOC_PATH
    store_path = store_path or EMBEDDINGS_STORE_PATH

    path = Path(schema_path)
    if not path.exists():
        raise FileNotFoundError(f"Schema document not found: {path}")

    text = path.read_text(encoding="utf-8")
    chunks = chunk_schema_document(text)
    if not chunks:
        raise ValueError("No chunks produced from schema document.")

    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not set in .env")

    client = OpenAI(api_key=OPENAI_API_KEY)
    embeddings = get_embeddings(client, chunks)

    store = {
        "chunks": [
            {"text": chunk, "embedding": emb}
            for chunk, emb in zip(chunks, embeddings, strict=True)
        ]
    }

    out = Path(store_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)

    return str(out)


if __name__ == "__main__":
    import sys

    schema = None
    store = None
    if len(sys.argv) > 1:
        schema = sys.argv[1]
    if len(sys.argv) > 2:
        store = sys.argv[2]

    out_path = run_ingest(schema_path=schema, store_path=store)
    print(f"Ingestion complete. Embeddings saved to: {out_path}")
