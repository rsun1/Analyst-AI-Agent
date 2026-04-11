"""
Pure-function wrappers for each agent step.
No CLI I/O here — usable by both app.py (Streamlit) and agent.py (CLI).
"""
import sqlite3

from config import MAX_SQL_RETRIES
from db import execute_query
from insights import explain_results
from rag import retrieve
from schema_loader import render_live_schema_block
from sql_correction import get_corrected_sql
from sql_errors import classify_sqlite_error
from sql_generator import UNCLEAR_INSTRUCTION, generate_sql_and_explanation, revise_sql


def step_introspect_schema(db_path: str | None = None) -> str:
    """
    Introspect the live database and return a schema block for LLM injection.

    Queries SQLite directly (via PRAGMA table_info) to get the ground-truth
    table and column names, then enriches them with notes, enums, FK
    relationships, cross-domain paths, and pitfalls from schema_catalog.yml.

    The result is cached after the first call — SQLite schema is static at
    runtime, so there is no cost to calling this repeatedly.
    """
    return render_live_schema_block(db_path)


def step_rag(question: str) -> list[str]:
    """Retrieve RAG context chunks for the question."""
    return retrieve(question)


def step_generate(
    question: str,
    rag_context: list[str],
    conversation_history: list[dict] | None = None,
) -> tuple[str, str]:
    """Return (sql, explanation). Passes conversation history for follow-up awareness."""
    return generate_sql_and_explanation(question, rag_context, conversation_history)


def step_revise(
    original_question: str,
    rag_context: list[str],
    current_sql: str,
    current_explanation: str,
    revision_instruction: str,
    last_db_error: str | None = None,
) -> tuple[str, str]:
    """
    Revise SQL based on user instruction.
    Returns (UNCLEAR_INSTRUCTION, '') if the instruction is too vague.
    """
    return revise_sql(current_sql, current_explanation, revision_instruction, rag_context)


def step_execute(sql: str) -> tuple[list[str], list[tuple], list[str]]:
    """
    Execute SQL with automatic self-correction on failure.
    Returns (columns, rows, db_errors_encountered).
    Raises on unrecoverable failure.
    """
    rag_context: list[str] = []  # correction uses schema catalog, RAG is supplementary
    db_errors: list[str] = []
    current_sql = sql

    for attempt in range(MAX_SQL_RETRIES + 1):
        try:
            columns, rows = execute_query(current_sql)
            return columns, rows, db_errors
        except sqlite3.OperationalError as e:
            last_error = str(e)
            db_errors.append(last_error)
            classified = classify_sqlite_error(e)
            if attempt < MAX_SQL_RETRIES:
                current_sql = get_corrected_sql(
                    current_sql, last_error, rag_context, classified=classified
                )
            else:
                raise

    raise RuntimeError("Execution failed after all retries")


def step_explain(
    question: str,
    sql: str,
    columns: list[str],
    rows: list[tuple],
) -> str:
    """Return plain-English summary and insights."""
    return explain_results(question, sql, columns, rows)
