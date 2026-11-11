"""
Create the log tables and the INSERT-only logger role. Safe to re-run; it never
drops anything (unlike migrate_to_postgres, which rebuilds the Olist tables).

The tables live in their own schema, `app_logs`, so they never show up in the schema
the SQL agent introspects, and the SELECT-only reader role cannot read them.

Usage:
  python -m setup_logging_db                      # create schema, tables, role
  python -m setup_logging_db --import-jsonl       # also load existing query_log.jsonl
"""
import json
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from config import BASE_DIR, PG_ADMIN_DSN, PG_LOGGER_PASSWORD, PG_LOGGER_USER

DDL = """
CREATE SCHEMA IF NOT EXISTS app_logs;

CREATE TABLE IF NOT EXISTS app_logs.query_events (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    question         TEXT NOT NULL,
    proposed_sql     TEXT NOT NULL,
    revision_history JSONB NOT NULL DEFAULT '[]',
    final_sql        TEXT NOT NULL,
    was_corrected    BOOLEAN NOT NULL,
    db_errors        JSONB NOT NULL DEFAULT '[]',
    row_count        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS query_events_created_idx ON app_logs.query_events (created_at DESC);

CREATE TABLE IF NOT EXISTS app_logs.feedback_events (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    question   TEXT NOT NULL,
    sql        TEXT NOT NULL,
    rating     TEXT NOT NULL CHECK (rating IN ('up', 'down')),
    comment    TEXT
);
CREATE INDEX IF NOT EXISTS feedback_events_created_idx ON app_logs.feedback_events (created_at DESC);
"""


def setup(import_jsonl: bool = False) -> None:
    with psycopg.connect(PG_ADMIN_DSN) as pg:
        pg.execute(DDL)
        pg.execute(sql.SQL(
            "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {name}) THEN "
            "CREATE ROLE {ident} LOGIN PASSWORD {pw}; END IF; END $$"
        ).format(name=sql.Literal(PG_LOGGER_USER), ident=sql.Identifier(PG_LOGGER_USER),
                 pw=sql.Literal(PG_LOGGER_PASSWORD)))
        pg.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
            sql.Identifier(PG_LOGGER_USER), sql.Literal(PG_LOGGER_PASSWORD)))
        who = sql.Identifier(PG_LOGGER_USER)
        pg.execute(sql.SQL("GRANT USAGE ON SCHEMA app_logs TO {}").format(who))
        # INSERT only: the API can append events but cannot read or change them.
        pg.execute(sql.SQL("GRANT INSERT ON app_logs.query_events, app_logs.feedback_events TO {}").format(who))

        if import_jsonl:
            path = Path(BASE_DIR) / "query_log.jsonl"
            n = 0
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                pg.execute(
                    "INSERT INTO app_logs.query_events (created_at, question, proposed_sql, revision_history, "
                    "final_sql, was_corrected, db_errors, row_count) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (r["timestamp"], r["question"], r["proposed_sql"], Jsonb(r["revision_history"]),
                     r["final_sql"], r["was_corrected"], Jsonb(r["db_errors_encountered"]), r["row_count"]))
                n += 1
            print(f"Imported {n} events from {path.name}")
        pg.commit()
    print("Logging schema ready.")


if __name__ == "__main__":
    setup(import_jsonl="--import-jsonl" in sys.argv)
