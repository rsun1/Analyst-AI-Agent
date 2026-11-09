"""
Pure Python exception handling for PostgreSQL execution errors.
Classifies error type (e.g. missing column) and structures retry hints for the model.
"""
from dataclasses import dataclass
from typing import Literal

# Postgres raises UndefinedColumn / UndefinedTable (psycopg.ProgrammingError subclasses),
# e.g. 'column "x" does not exist', 'relation "x" does not exist'.
# We classify so the retry prompt can be tailored (e.g. strict "use only schema columns").
ErrorKind = Literal["missing_column", "missing_table", "syntax", "other"]


@dataclass(frozen=True)
class ClassifiedError:
    """Result of classifying a PostgreSQL error."""
    kind: ErrorKind
    message: str
    hint_for_model: str


# Substrings that indicate a missing column (Postgres message text).
_MISSING_COLUMN_MARKERS = (
    "no such column",
    "has no column named",
    "unknown column",
)

# Substrings that indicate a missing table.
_MISSING_TABLE_MARKERS = (
    "no such table",
    "unknown table",
)


def classify_db_error(exc: BaseException) -> ClassifiedError:
    """
    Classify a database error (or any exception) into a known kind
    and build a structured hint for the correction model.
    Pure Python; no DB or OpenAI calls.
    """
    message = str(exc).strip()
    lower = message.lower()

    missing_col = "column" in lower and "does not exist" in lower
    missing_tbl = "relation" in lower and "does not exist" in lower

    if missing_col or any(m in lower for m in _MISSING_COLUMN_MARKERS):
        return ClassifiedError(
            kind="missing_column",
            message=message,
            hint_for_model=(
                "MISSING COLUMN ERROR: The database reported that a column does not exist. "
                "You MUST use ONLY table and column names that appear in the schema context below. "
                "Do NOT invent or guess column names. Copy exact names from the schema and sample queries. "
                f"Exact error: {message}"
            ),
        )

    if missing_tbl or any(m in lower for m in _MISSING_TABLE_MARKERS):
        return ClassifiedError(
            kind="missing_table",
            message=message,
            hint_for_model=(
                "MISSING TABLE ERROR: The database reported that a table does not exist. "
                "You MUST use ONLY table names that appear in the schema context below. "
                "Do NOT invent table names. "
                f"Exact error: {message}"
            ),
        )

    # Syntax / other (e.g. 'syntax error at or near "foo"')
    return ClassifiedError(
        kind="syntax" if "syntax" in message.lower() else "other",
        message=message,
        hint_for_model=f"PostgreSQL error: {message}",
    )


def is_missing_column_error(exc: BaseException) -> bool:
    """Return True if the exception is a missing-column style error."""
    try:
        return classify_db_error(exc).kind == "missing_column"
    except Exception:
        return False


# Backwards-compatible alias (v1 name).
classify_sqlite_error = classify_db_error
