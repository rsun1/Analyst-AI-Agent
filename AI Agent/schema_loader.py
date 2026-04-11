"""
Load schema_catalog.yml and render it as a compact, LLM-readable text block
for injection into SQL generation prompts.

Optionally validates the YAML catalog against the live SQLite database using
PRAGMA table_info() so that catalog drift is detected early at startup.
"""
import sqlite3
import warnings
from functools import lru_cache
from pathlib import Path

import yaml

from config import OLIST_DB_PATH, SCHEMA_CATALOG_PATH


# ── Load & render ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def load_catalog(path: str | None = None) -> dict:
    """Load and cache the YAML schema catalog."""
    p = Path(path or SCHEMA_CATALOG_PATH)
    if not p.exists():
        raise FileNotFoundError(
            f"Schema catalog not found: {p}. "
            "Create schema_catalog.yml or set SCHEMA_CATALOG_PATH in .env."
        )
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def render_schema_block(path: str | None = None) -> str:
    """
    Return a compact, structured text block (YAML-style) suitable for
    direct injection into an LLM prompt.

    Includes: table descriptions, exact column names with types and notes,
    enum values, FK relationships, and common pitfalls.
    """
    catalog = load_catalog(path)
    lines: list[str] = []

    lines.append("=== DATABASE SCHEMA (ground truth – use ONLY these exact names) ===")
    lines.append("")

    tables: dict = catalog.get("tables", {})
    for table_name, tdef in tables.items():
        desc = tdef.get("description", "")
        lines.append(f"TABLE: {table_name}")
        if desc:
            lines.append(f"  # {desc}")
        columns: dict = tdef.get("columns", {})
        for col_name, cdef in columns.items():
            if isinstance(cdef, dict):
                col_type = cdef.get("type", "")
                note = cdef.get("note", "")
                enum = cdef.get("enum")
                parts = [f"  {col_name}  [{col_type}]"]
                if note:
                    parts.append(f"-- {note}")
                if enum:
                    parts.append(f"-- enum: {enum}")
                lines.append("  ".join(parts))
            else:
                lines.append(f"  {col_name}  {cdef}")
        lines.append("")

    relationships: list = catalog.get("relationships", [])
    if relationships:
        lines.append("FOREIGN KEYS (exact join columns – do not substitute):")
        for r in relationships:
            lines.append(f"  {r['from']}  →  {r['to']}")
        lines.append("")

    cross_domain: list = catalog.get("cross_domain_paths", [])
    if cross_domain:
        lines.append("CROSS-DOMAIN JOIN PATHS (multi-hop paths between business domains):")
        lines.append("  WARNING: Never join orders/customers directly to leads tables. Always use the seller bridge.")
        lines.append("")
        for cdp in cross_domain:
            lines.append(f"  PATH: {cdp.get('name', '')}")
            desc = str(cdp.get("description", "")).strip().replace("\n", " ")
            lines.append(f"    Description: {desc}")
            lines.append(f"    Traversal:   {cdp.get('path', '')}")
            example = str(cdp.get("example_use", "")).strip().replace("\n", " ")
            if example:
                lines.append(f"    Example use: {example}")
            lines.append("")

    pitfalls: list = catalog.get("pitfalls", [])
    if pitfalls:
        lines.append("COMMON PITFALLS (avoid these mistakes):")
        for p in pitfalls:
            lines.append(f"  • {p}")
        lines.append("")

    lines.append("=== END SCHEMA ===")
    return "\n".join(lines)


@lru_cache(maxsize=1)
def render_live_schema_block(
    db_path: str | None = None,
    catalog_path: str | None = None,
) -> str:
    """
    Build a schema block using **live DB introspection** for exact table/column
    names, enriched with notes, enums, FK relationships, cross-domain paths, and
    pitfalls from schema_catalog.yml.

    This is the primary schema source injected into every LLM prompt.
    Using the live DB (via PRAGMA table_info) ensures the LLM always sees the
    real column names and cannot hallucinate based on a stale YAML file.

    Cached after first call — SQLite schema is static at runtime.
    """
    from db import get_live_schema  # local import avoids top-level circular-import risk

    live = get_live_schema(db_path)
    catalog = load_catalog(catalog_path)
    catalog_tables: dict = catalog.get("tables", {})

    lines: list[str] = []
    lines.append("=== DATABASE SCHEMA (live – use ONLY these exact names) ===")
    lines.append("")

    for table_name, columns in live.items():
        table_meta = catalog_tables.get(table_name, {})
        desc = table_meta.get("description", "") if isinstance(table_meta, dict) else ""
        lines.append(f"TABLE: {table_name}")
        if desc:
            lines.append(f"  # {desc}")
        catalog_cols: dict = table_meta.get("columns", {}) if isinstance(table_meta, dict) else {}
        for col in columns:
            col_name = col["name"]
            col_type = col["type"]
            col_meta = catalog_cols.get(col_name, {})
            note = col_meta.get("note", "") if isinstance(col_meta, dict) else ""
            enum = col_meta.get("enum") if isinstance(col_meta, dict) else None
            parts = [f"  {col_name}  [{col_type}]"]
            if note:
                parts.append(f"-- {note}")
            if enum:
                parts.append(f"-- enum: {enum}")
            lines.append("  ".join(parts))
        lines.append("")

    relationships: list = catalog.get("relationships", [])
    if relationships:
        lines.append("FOREIGN KEYS (exact join columns – do not substitute):")
        for r in relationships:
            lines.append(f"  {r['from']}  →  {r['to']}")
        lines.append("")

    cross_domain: list = catalog.get("cross_domain_paths", [])
    if cross_domain:
        lines.append("CROSS-DOMAIN JOIN PATHS (multi-hop paths between business domains):")
        lines.append("  WARNING: Never join orders/customers directly to leads tables. Always use the seller bridge.")
        lines.append("")
        for cdp in cross_domain:
            lines.append(f"  PATH: {cdp.get('name', '')}")
            desc_str = str(cdp.get("description", "")).strip().replace("\n", " ")
            lines.append(f"    Description: {desc_str}")
            lines.append(f"    Traversal:   {cdp.get('path', '')}")
            example = str(cdp.get("example_use", "")).strip().replace("\n", " ")
            if example:
                lines.append(f"    Example use: {example}")
            lines.append("")

    pitfalls: list = catalog.get("pitfalls", [])
    if pitfalls:
        lines.append("COMMON PITFALLS (avoid these mistakes):")
        for p in pitfalls:
            lines.append(f"  • {p}")
        lines.append("")

    lines.append("=== END SCHEMA ===")
    return "\n".join(lines)


# ── PRAGMA drift validation ───────────────────────────────────────────────────

def validate_catalog_against_db(
    db_path: str | None = None,
    catalog_path: str | None = None,
) -> list[str]:
    """
    Cross-check the YAML catalog against the live database using PRAGMA table_info().
    Returns a list of drift warning strings (empty list = no drift detected).
    Emits Python warnings for each discrepancy so they surface at startup without crashing.
    """
    catalog = load_catalog(catalog_path)
    tables_in_catalog: dict = catalog.get("tables", {})

    path = Path(db_path or OLIST_DB_PATH).resolve()
    if not path.exists():
        return [f"Database not found for drift check: {path}"]

    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        db_tables = {row[0] for row in cur.fetchall()}

        drift: list[str] = []

        for table_name, tdef in tables_in_catalog.items():
            if table_name not in db_tables:
                msg = f"[schema drift] Table '{table_name}' is in catalog but not in the database."
                drift.append(msg)
                warnings.warn(msg, stacklevel=2)
                continue

            cur.execute(f'PRAGMA table_info("{table_name}")')
            db_columns = {row[1] for row in cur.fetchall()}
            catalog_columns = set(tdef.get("columns", {}).keys())

            for col in catalog_columns - db_columns:
                msg = f"[schema drift] Column '{table_name}.{col}' is in catalog but not in the database."
                drift.append(msg)
                warnings.warn(msg, stacklevel=2)

            for col in db_columns - catalog_columns:
                msg = f"[schema drift] Column '{table_name}.{col}' exists in database but is missing from catalog."
                drift.append(msg)
                warnings.warn(msg, stacklevel=2)

        for tbl in db_tables - set(tables_in_catalog.keys()):
            msg = f"[schema drift] Table '{tbl}' exists in database but is missing from catalog."
            drift.append(msg)
            warnings.warn(msg, stacklevel=2)

        return drift
    finally:
        conn.close()
