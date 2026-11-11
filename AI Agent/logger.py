"""
Event logging for the agent. Query events (including human SQL corrections) and
feedback events go to Postgres, schema `app_logs`, through an INSERT-only role.

  proposed_sql != final_sql -> high-value (prompt, bad, good) triple for evaluation
  or future fine-tuning.

Never raises: a logging failure must not interrupt the agent. If the database write
fails, the event is emitted to the process log as one JSON line instead (in a
container that goes to stdout / CloudWatch), so it is not silently lost.

Create the tables first: python -m setup_logging_db
"""
import json
import logging

from psycopg.types.json import Jsonb

from db import get_log_connection

_fallback = logging.getLogger("analytics_agent.events")
if not _fallback.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(message)s"))
    _fallback.addHandler(_h)
    _fallback.setLevel(logging.INFO)
    _fallback.propagate = False


def _emit_fallback(kind: str, record: dict, error: Exception) -> None:
    try:
        _fallback.warning(json.dumps({"event": kind, "log_write_failed": str(error), **record}, default=str))
    except Exception:
        pass


def log_query_event(
    question: str,
    proposed_sql: str,
    revision_history: list[dict],
    final_sql: str,
    db_errors: list[str],
    row_count: int,
) -> None:
    """
    Record one executed query. revision_history entries look like:
        {"type": "llm_revision",  "instruction": "...", "sql_after": "..."}
        {"type": "direct_edit",                          "sql_after": "..."}
    """
    was_corrected = proposed_sql.strip() != final_sql.strip()
    try:
        with get_log_connection() as conn:
            conn.execute(
                "INSERT INTO app_logs.query_events (question, proposed_sql, revision_history, final_sql, "
                "was_corrected, db_errors, row_count) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (question, proposed_sql, Jsonb(revision_history), final_sql,
                 was_corrected, Jsonb(db_errors), row_count),
            )
    except Exception as e:
        _emit_fallback("query_event", {
            "question": question, "proposed_sql": proposed_sql, "revision_history": revision_history,
            "final_sql": final_sql, "was_corrected": was_corrected, "db_errors": db_errors,
            "row_count": row_count}, e)


def log_feedback_event(question: str, sql: str, rating: str, comment: str | None) -> None:
    """Record a thumbs up/down on a result."""
    try:
        with get_log_connection() as conn:
            conn.execute(
                "INSERT INTO app_logs.feedback_events (question, sql, rating, comment) VALUES (%s,%s,%s,%s)",
                (question, sql, rating, comment),
            )
    except Exception as e:
        _emit_fallback("feedback_event", {"question": question, "sql": sql, "rating": rating, "comment": comment}, e)
