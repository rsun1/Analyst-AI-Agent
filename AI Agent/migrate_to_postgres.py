"""
One-off migration: copy olist.sqlite into Postgres.

Type mapping (SQLite -> Postgres):
  INTEGER -> BIGINT, REAL -> DOUBLE PRECISION, TEXT -> TEXT
  TEXT columns holding dates (names ending _date/_timestamp/_at) -> TIMESTAMP
  (empty strings become NULL). Real timestamps are the point of the migration:
  v1 stored dates as TEXT and needed strftime/julianday.

Also creates a read-only role (olist_reader) used by the agent for query
execution, so DDL/DML are rejected by the database itself (v1 used a
read-only SQLite URI for this).

Usage:
  python -m migrate_to_postgres            # migrate (drops and recreates tables)
"""
import io
import sqlite3
from pathlib import Path

import psycopg
from psycopg import sql

from config import (
    OLIST_DB_PATH,
    PG_ADMIN_DSN,
    PG_READER_PASSWORD,
    PG_READER_USER,
)

DATE_SUFFIXES = ("_date", "_timestamp", "_at")
TYPE_MAP = {"INTEGER": "BIGINT", "REAL": "DOUBLE PRECISION", "TEXT": "TEXT"}
BATCH = 50_000


def pg_type(col: str, sqlite_type: str) -> str:
    if sqlite_type.upper() == "TEXT" and col.endswith(DATE_SUFFIXES):
        return "TIMESTAMP"
    return TYPE_MAP.get(sqlite_type.upper(), "TEXT")


def _copy_value(v) -> str:
    """Encode a value for COPY text format. None -> \\N."""
    if v is None or v == "":
        return r"\N"
    s = str(v)
    return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def migrate(sqlite_path: str | Path = OLIST_DB_PATH) -> None:
    src = sqlite3.connect(f"file:{Path(sqlite_path).resolve()}?mode=ro", uri=True)
    tables = [
        r[0]
        for r in src.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]

    with psycopg.connect(PG_ADMIN_DSN, autocommit=False) as pg:
        for t in tables:
            cols = [(r[1], r[2]) for r in src.execute(f'PRAGMA table_info("{t}")')]
            ddl_cols = sql.SQL(", ").join(
                sql.SQL("{} {}").format(sql.Identifier(c), sql.SQL(pg_type(c, ty)))
                for c, ty in cols
            )
            pg.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(t)))
            pg.execute(sql.SQL("CREATE TABLE {} ({})").format(sql.Identifier(t), ddl_cols))

            col_idents = sql.SQL(", ").join(sql.Identifier(c) for c, _ in cols)
            copy_stmt = sql.SQL("COPY {} ({}) FROM STDIN").format(sql.Identifier(t), col_idents)
            cur = src.execute(f'SELECT * FROM "{t}"')
            total = 0
            with pg.cursor().copy(copy_stmt) as copy:
                while rows := cur.fetchmany(BATCH):
                    buf = io.StringIO()
                    for row in rows:
                        buf.write("\t".join(_copy_value(v) for v in row) + "\n")
                    copy.write(buf.getvalue())
                    total += len(rows)
            print(f"{t}: {total} rows")

        # Read-only role for agent query execution.
        pg.execute(
            sql.SQL(
                "DO $$ BEGIN "
                "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {name}) THEN "
                "CREATE ROLE {ident} LOGIN PASSWORD {pw}; END IF; END $$"
            ).format(
                name=sql.Literal(PG_READER_USER),
                ident=sql.Identifier(PG_READER_USER),
                pw=sql.Literal(PG_READER_PASSWORD),
            )
        )
        pg.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
            sql.Identifier(PG_READER_USER), sql.Literal(PG_READER_PASSWORD)))
        pg.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(PG_READER_USER)))
        for t in tables:
            pg.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(
                sql.Identifier(t), sql.Identifier(PG_READER_USER)))
        pg.commit()
    src.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
