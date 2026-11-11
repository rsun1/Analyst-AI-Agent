"""
Execute SQL against the Olist Postgres database.

Safety (NFR-5): queries run as the SELECT-only `olist_reader` role, inside a
READ ONLY session with a statement timeout, so DDL/DML are rejected by the
database itself. A row cap is applied client-side as a last resort.
"""
import threading

import psycopg
from psycopg_pool import ConnectionPool

from config import DB_POOL_MAX, DB_POOL_MIN, PG_LOGGER_DSN, PG_READER_DSN

STATEMENT_TIMEOUT_MS = 15_000
MAX_ROWS = 50_000

# Errors a regenerated query could plausibly fix (bad column, syntax, type mismatch...).
RETRYABLE_ERRORS = (psycopg.ProgrammingError, psycopg.DataError)


_CONN_OPTIONS = (
    f"-c statement_timeout={STATEMENT_TIMEOUT_MS} "
    "-c default_transaction_read_only=on"
)
_pools: dict[str, ConnectionPool] = {}
_pool_lock = threading.Lock()


def _get_pool(name: str = "reader") -> ConnectionPool:
    """Create a shared pool on first use (not at import, so importing never needs a database)."""
    with _pool_lock:
        if name not in _pools:
            if name == "reader":
                _pools[name] = ConnectionPool(
                    PG_READER_DSN,
                    min_size=DB_POOL_MIN,
                    max_size=DB_POOL_MAX,
                    kwargs={"options": _CONN_OPTIONS},
                    check=ConnectionPool.check_connection,  # drop dead connections (e.g. after a DB restart)
                    open=True,
                )
            else:  # "logger": small pool, INSERT-only role, short timeout, not read-only
                _pools[name] = ConnectionPool(
                    PG_LOGGER_DSN,
                    min_size=0,
                    max_size=2,
                    kwargs={"options": "-c statement_timeout=5000"},
                    check=ConnectionPool.check_connection,
                    open=True,
                )
        return _pools[name]


def close_pool() -> None:
    with _pool_lock:
        for p in _pools.values():
            p.close()
        _pools.clear()


def get_connection(dsn: str | None = None):
    """
    Context manager yielding a read-only connection as the reader role.
    Default DSN: borrowed from the shared pool and returned on exit.
    Explicit DSN: a one-off connection, closed on exit.
    """
    if dsn:
        return psycopg.connect(dsn, options=_CONN_OPTIONS)
    return _get_pool("reader").connection()


def get_log_connection():
    """Pooled connection for writing log events (INSERT-only role)."""
    return _get_pool("logger").connection()


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
