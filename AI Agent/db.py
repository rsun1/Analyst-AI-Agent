"""
Execute SQL against olist.sqlite using a read-only connection.
NFR-5: Database safety (read-only URI) so DDL/DML are rejected by the engine.
"""
import sqlite3
from pathlib import Path

from config import OLIST_DB_PATH


def get_connection(db_path: str | Path | None = None):
    """
    Open a read-only SQLite connection using URI mode.
    file:path?mode=ro ensures the engine rejects writes.
    """
    path = Path(db_path or OLIST_DB_PATH).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    uri = f"file:{path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def get_live_schema(db_path: str | Path | None = None) -> dict[str, list[dict]]:
    """
    Query the live SQLite database for all tables and their columns.
    Returns {table_name: [{"name": col_name, "type": col_type}, ...]}
    ordered as the DB defines them.
    """
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        tables = [row[0] for row in cur.fetchall()]
        schema: dict[str, list[dict]] = {}
        for table in tables:
            cur.execute(f'PRAGMA table_info("{table}")')
            schema[table] = [{"name": row[1], "type": row[2]} for row in cur.fetchall()]
        return schema
    finally:
        conn.close()


def execute_query(sql: str, db_path: str | Path | None = None) -> tuple[list[str], list[tuple]]:
    """
    Execute the given SQL (read-only) and return (column_names, rows).
    Raises sqlite3.OperationalError (or other sqlite3.Error) on failure.
    """
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        columns = [d[0] for d in cur.description] if cur.description else []
        return columns, rows
    finally:
        conn.close()
