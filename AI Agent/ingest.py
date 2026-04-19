"""
Phase 1: Ingest olist_schema_queries.md (metric definitions) AND schema_catalog.yml
(table-level schema chunks), generate embeddings via OpenAI, and store in a local JSON file.

Two chunk sources are merged:
  1. olist_schema_queries.md  — pre-defined metric definitions + sample SQL queries
  2. schema_catalog.yml       — one chunk per table (columns/notes), plus cross-domain
                                join paths and pitfalls

This ensures retrieval works for arbitrary questions (matching relevant tables/columns)
not just questions that happen to be semantically close to a pre-defined metric name.
"""
import json
from pathlib import Path

import yaml
from openai import OpenAI

from config import (
    EMBEDDING_MODEL,
    EMBEDDINGS_STORE_PATH,
    OPENAI_API_KEY,
    SCHEMA_CATALOG_PATH,
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


def chunk_catalog(catalog_path: str | Path | None = None) -> list[str]:
    """
    Parse schema_catalog.yml and produce one text chunk per table plus
    additional chunks for cross-domain join paths and pitfalls.

    Table chunks contain the table description and every column with its type,
    notes, and enum values — the same information a human analyst would look up
    to write a query. This lets cosine similarity match a user question
    ("average delivery delay") to the tables that actually have the relevant
    columns (orders.order_delivered_customer_date, etc.).
    """
    path = Path(catalog_path or SCHEMA_CATALOG_PATH)
    if not path.exists():
        return []

    with open(path, encoding="utf-8") as f:
        catalog = yaml.safe_load(f)

    chunks: list[str] = []

    # One chunk per table
    for table_name, tdef in catalog.get("tables", {}).items():
        lines = [f"TABLE: {table_name}"]
        desc = tdef.get("description", "")
        if desc:
            lines.append(f"Description: {desc}")
        lines.append("Columns:")
        for col_name, cdef in tdef.get("columns", {}).items():
            if isinstance(cdef, dict):
                col_type = cdef.get("type", "")
                note = cdef.get("note", "")
                enum = cdef.get("enum")
                col_line = f"  {col_name} [{col_type}]"
                if note:
                    col_line += f" — {note}"
                if enum:
                    col_line += f" — enum: {enum}"
                lines.append(col_line)
            else:
                lines.append(f"  {col_name}: {cdef}")
        chunks.append("\n".join(lines))

    # Cross-domain join paths as a single chunk
    cross_domain = catalog.get("cross_domain_paths", [])
    if cross_domain:
        lines = ["CROSS-DOMAIN JOIN PATHS:"]
        for cdp in cross_domain:
            lines.append(f"\nPATH: {cdp.get('name', '')}")
            desc = str(cdp.get("description", "")).strip().replace("\n", " ")
            lines.append(f"  Description: {desc}")
            lines.append(f"  Traversal: {cdp.get('path', '')}")
            example = str(cdp.get("example_use", "")).strip().replace("\n", " ")
            if example:
                lines.append(f"  Example: {example}")
        chunks.append("\n".join(lines))

    # Pitfalls as a single chunk
    pitfalls = catalog.get("pitfalls", [])
    if pitfalls:
        lines = ["COMMON PITFALLS (avoid these mistakes):"]
        for p in pitfalls:
            lines.append(f"  • {p}")
        chunks.append("\n".join(lines))

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
    catalog_path: str | Path | None = None,
) -> str:
    """
    Read metric definitions doc + schema catalog, chunk both, embed, and save to JSON store.
    Returns the path to the written store file.
    """
    schema_path = schema_path or SCHEMA_DOC_PATH
    store_path = store_path or EMBEDDINGS_STORE_PATH

    path = Path(schema_path)
    if not path.exists():
        raise FileNotFoundError(f"Schema document not found: {path}")

    text = path.read_text(encoding="utf-8")
    metric_chunks = chunk_schema_document(text)
    catalog_chunks = chunk_catalog(catalog_path)
    chunks = metric_chunks + catalog_chunks
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
