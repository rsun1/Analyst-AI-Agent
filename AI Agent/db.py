"""
Execute SQL against the Olist Postgres database.

Safety (NFR-5): queries run as the SELECT-only `olist_reader` role, inside a
READ ONLY session with a statement timeout, so DDL/DML are rejected by the
database itself. A row cap is applied client-side as a last resort.
"""
import psycopg

from config import PG_READER_DSN

STATEMENT_TIMEOUT_MS = 15_000
MAX_ROWS = 50_000

# Errors a regenerated query could plausibly fix (bad column, syntax, type mismatch...).
RETRYABLE_ERRORS = (psycopg.ProgrammingError, psycopg.DataError)


def get_connection(dsn: str | None = None) -> psycopg.Connection:
    """Open a read-only connection as the reader role."""
    conn = psycopg.connect(
        dsn or PG_READER_DSN,
        options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS} "
                "-c default_transaction_read_only=on",
    )
    return conn


def get_live_schema(dsn: str | None = None) -> dict[str, list[dict]]:
    """
    Query information_schema for all public tables and their columns.
    Returns {table_name: [{"name": col_name, "type": col_type}, ...]}
    ordered as the DB defines them (excludes internal tables like schema_chunks).
    """
    with get_connection(dsn) as conn:
        rows = conn.execute(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name <> 'schema_chunks' "
            "ORDER BY table_name, ordinal_position"
        ).fetchall()
    schema: dict[str, list[dict]] = {}
    for table, col, typ in rows:
        schema.setdefault(table, []).append({"name": col, "type": typ.upper()})
    return schema


def execute_query(sql: str, dsn: str | None = None) -> tuple[list[str], list[tuple]]:
    """
    Execute the given SQL (read-only) and return (column_names, rows).
    Raises a psycopg.Error subclass on failure.
    """
    with get_connection(dsn) as conn:
        cur = conn.execute(sql)
        columns = [d.name for d in cur.description] if cur.description else []
        rows = cur.fetchmany(MAX_ROWS) if cur.description else []
        return columns, rows
