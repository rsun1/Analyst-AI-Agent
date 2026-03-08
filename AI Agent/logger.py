"""
Silent query event logger. Appends one JSON record per executed query to
query_log.jsonl. Never raises — a logging failure must never interrupt the agent flow.

Each record is a proprietary fine-tuning dataset entry:
  proposed_sql != final_sql → high-value (prompt, bad, good) triple for future fine-tuning.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from config import BASE_DIR

_LOG_PATH = BASE_DIR / "query_log.jsonl"


def log_query_event(
    question: str,
    proposed_sql: str,
    revision_history: list[dict],
    final_sql: str,
    db_errors: list[str],
    row_count: int,
) -> None:
    """
    Append one record to query_log.jsonl.
    Silent — never raises.

    revision_history entries look like:
        {"type": "llm_revision",  "instruction": "...", "sql_after": "..."}
        {"type": "direct_edit",                          "sql_after": "..."}
    """
    try:
        record = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "question": question,
            "proposed_sql": proposed_sql,
            "revision_history": revision_history,
            "final_sql": final_sql,
            "was_corrected": proposed_sql.strip() != final_sql.strip(),
            "db_errors_encountered": db_errors,
            "row_count": row_count,
        }
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # logging must never surface errors to the user
