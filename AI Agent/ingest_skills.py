"""
Embed the skills in skills/**/*.md and store them in Postgres (pgvector) for the router.

The table lives in its own schema (agent_meta) so schema introspection of the
`public` schema never shows it to the SQL-generating model.

Usage (from "AI Agent/"):  python -m ingest_skills
Re-run whenever a skill file changes; it replaces the table contents.
"""
import json

import psycopg
from openai import OpenAI
from pgvector.psycopg import register_vector

from config import EMBEDDING_MODEL, OPENAI_API_KEY, PG_ADMIN_DSN, PG_READER_USER
from ingest import get_embeddings
from skill_loader import load_skills


def run_ingest_skills() -> str:
    skills = load_skills()
    if not skills:
        raise ValueError("No skills found under skills/.")
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not set in .env")

    embeddings = get_embeddings(OpenAI(api_key=OPENAI_API_KEY), [s.embedding_text() for s in skills])

    with psycopg.connect(PG_ADMIN_DSN) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(conn)
        conn.execute("CREATE SCHEMA IF NOT EXISTS agent_meta")
        conn.execute("DROP TABLE IF EXISTS agent_meta.skills")
        conn.execute(
            f"CREATE TABLE agent_meta.skills ("
            f"name TEXT PRIMARY KEY, domain TEXT NOT NULL, route TEXT NOT NULL, title TEXT NOT NULL, "
            f"description TEXT NOT NULL, disambiguation TEXT NOT NULL DEFAULT '', meta JSONB NOT NULL, "
            f"embedding vector({len(embeddings[0])}) NOT NULL)"
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO agent_meta.skills (name, domain, route, title, description, disambiguation, meta, embedding) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)",
                [
                    (s.name, s.meta.domain, s.meta.route, s.meta.title, s.meta.description,
                     s.meta.disambiguation, json.dumps(s.meta.model_dump(mode="json")), emb)
                    for s, emb in zip(skills, embeddings, strict=True)
                ],
            )
        conn.execute(f'GRANT USAGE ON SCHEMA agent_meta TO "{PG_READER_USER}"')
        conn.execute(f'GRANT SELECT ON agent_meta.skills TO "{PG_READER_USER}"')
        conn.commit()
    return f"agent_meta.skills ({len(skills)} skills)"


if __name__ == "__main__":
    print(f"Skills ingested: {run_ingest_skills()}")
