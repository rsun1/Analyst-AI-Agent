"""
Pure-function wrappers for each agent pipeline step.

No CLI I/O, no print statements. Used by both app.py (Streamlit UI) and
agent.py (CLI) so both share identical business logic.

Step signatures follow UI.md §8.1.
"""
import sqlite3

from config import MAX_SQL_RETRIES
from db import execute_query
from insights import explain_results as _explain_results
from rag import retrieve
from sql_correction import get_corrected_sql
from sql_errors import classify_sqlite_error
from sql_generator import (
    UNCLEAR_INSTRUCTION,
    generate_sql_and_explanation,
    revise_sql as _revise_sql,
)

# Re-export so callers only need to import from agent_core.
__all__ = [
    "UNCLEAR_INSTRUCTION",
    "step_rag",
    "step_generate",
    "step_revise",
    "step_execute",
    "step_explain",
]


def step_rag(question: str) -> list[str]:
    """Embed the question and return top-k RAG context chunks."""
    return retrieve(question)


def step_generate(question: str, rag_context: list[str]) -> tuple[str, str]:
    """
    Generate a proposed (sql, explanation) pair for the given question.
    Calls the OpenAI Chat API via sql_generator.
    """
    return generate_sql_and_explanation(question, rag_context)


def step_revise(
    original_question: str,
    rag_context: list[str],
    current_sql: str,
    current_explanation: str,
    revision_instruction: str,
    last_db_error: str | None = None,
) -> tuple[str, str]:
    """
    Revise current_sql per the user's revision_instruction.

    Builds an enriched instruction that carries the original question and any
    prior DB error so the LLM never loses context across Streamlit reruns
    (UI.md §6.2).

    Returns (revised_sql, revised_explanation) on success, or
    (UNCLEAR_INSTRUCTION, "") when the instruction is too vague to act on.
    """
    parts: list[str] = []
    if original_question:
        parts.append(f"[Original user question: {original_question}]")
    if last_db_error:
        parts.append(f"[Previous execution error: {last_db_error}]")
    parts.append(revision_instruction)
    enriched_instruction = "\n\n".join(parts)

    return _revise_sql(current_sql, current_explanation, enriched_instruction, rag_context)


def step_execute(
    sql: str,
    rag_context: list[str],
) -> tuple[list[str], list[tuple]]:
    """
    Execute sql against olist.sqlite with automatic self-correction (FR-3.4).

    Retries up to MAX_SQL_RETRIES times: on each OperationalError the model is
    asked for a corrected query using the error message and RAG context.

    Returns (column_names, rows) on success.
    Raises on total failure (after all retries exhausted).
    """
    current_sql = sql
    last_error: str | None = None

    for attempt in range(MAX_SQL_RETRIES + 1):
        try:
            return execute_query(current_sql)
        except sqlite3.OperationalError as exc:
            last_error = str(exc)
            classified = classify_sqlite_error(exc)
            if attempt < MAX_SQL_RETRIES:
                current_sql = get_corrected_sql(
                    current_sql, last_error, rag_context, classified=classified
                )
            else:
                raise sqlite3.OperationalError(
                    f"Query failed after {MAX_SQL_RETRIES + 1} attempt(s): {last_error}"
                ) from exc

    # Unreachable, but satisfies type checkers.
    raise RuntimeError(f"step_execute: unexpected exit. Last error: {last_error}")


def step_explain(
    question: str,
    sql: str,
    columns: list[str],
    rows: list[tuple],
) -> str:
    """Return a plain-English summary and 1-3 business insights for the result set."""
    return _explain_results(question, sql, columns, rows)
