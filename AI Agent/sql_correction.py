"""
On SQL execution failure, send the failed query and error message to OpenAI
to get a corrected SQL query for retry (FR-3.4).
The schema catalog is always injected so the model uses only exact column/table names.
"""
from openai import OpenAI

from config import CHAT_MODEL, OPENAI_API_KEY
from schema_loader import render_live_schema_block
from sql_errors import ClassifiedError, classify_db_error

CORRECTION_PROMPT = """The following PostgreSQL query failed.

Failed query:
```sql
{sql}
```

{error_section}

Using ONLY the table and column names in the schema above, provide a corrected PostgreSQL query.
Output nothing but the SQL, optionally inside a ```sql code block. Do not explain."""


def _build_correction_system_prompt(emphasize_schema: bool = False) -> str:
    schema_block = render_live_schema_block()
    base = (
        "You fix PostgreSQL errors. "
        + ("The database reported a missing column or table. " if emphasize_schema else "")
        + "You MUST use ONLY table and column names listed in the schema below. "
        "Do NOT invent or guess any names. Reply with only the corrected SQL."
    )
    return f"{base}\n\n{schema_block}"


def get_corrected_sql(
    failed_sql: str,
    error_message: str,
    rag_context: list[str],
    *,
    classified: ClassifiedError | None = None,
) -> str:
    """
    Ask the model for a corrected SQL given the failed query and DB error.
    The full schema catalog is injected in the system prompt (always-on).
    RAG context is still included for query-pattern guidance.
    classified (from classify_db_error) provides a structured error hint.
    """
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not set in .env")

    if classified is None:
        classified = classify_db_error(Exception(error_message))

    is_schema_error = classified.kind in ("missing_column", "missing_table")
    system = _build_correction_system_prompt(emphasize_schema=is_schema_error)

    num_chunks = 5 if is_schema_error else 3
    context_block = "\n\n".join(rag_context[:num_chunks]) if rag_context else ""

    error_section = f"**Critical:** {classified.hint_for_model}"

    user_content = f"""Relevant sample queries (for query-pattern guidance only):

{context_block}

---

{CORRECTION_PROMPT.format(sql=failed_sql, error_section=error_section)}"""

    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
    )
    content = (response.choices[0].message.content or "").strip()
    if "```sql" in content:
        content = content.split("```sql", 1)[1].split("```", 1)[0].strip()
    elif "```" in content:
        content = content.split("```", 2)[1].strip()
    return content
