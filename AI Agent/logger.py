"""
Silent query event logger — the data flywheel (UI.md §7).

After every successful query execution, one JSONL record is appended to
query_log.jsonl capturing:
  - original user question
  - agent's initially proposed SQL
  - full revision history (LLM edits and direct textarea edits)
  - final executed SQL
  - any DB errors encountered during self-correction
  - result row count
  - was_corrected flag (True when proposed_sql != final_sql)

Records where was_corrected=True are (prompt, bad_completion, good_completion)
triples — the highest-value rows for future fine-tuning.

This module NEVER raises. Logging failures are silently swallowed so they
cannot interrupt the agent flow.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from config import BASE_DIR

LOG_PATH: Path = BASE_DIR / "query_log.jsonl"


def log_query_event(
    question: str,
    proposed_sql: str,
    revision_history: list[dict],
    final_sql: str,
    db_errors: list[str],
    row_count: int,
) -> None:
    """
    Append one structured record to query_log.jsonl.

    Args:
        question:          The original natural-language question.
        proposed_sql:      The SQL the agent first proposed (before any edits).
        revision_history:  List of revision steps, each a dict with keys:
                             type ("llm_revision" | "direct_edit"),
                             instruction (str, LLM revisions only),
                             sql_after (str).
        final_sql:         The SQL that was actually executed.
        db_errors:         List of sqlite3 error strings from self-correction retries.
        row_count:         Number of rows returned by the final query.
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "question": question,
        "proposed_sql": proposed_sql,
        "revision_history": revision_history,
        "final_sql": final_sql,
        "was_corrected": proposed_sql.strip() != final_sql.strip(),
        "db_errors_encountered": db_errors,
        "row_count": row_count,
    }
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass
