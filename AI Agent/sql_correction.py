"""
On SQL execution failure, send the failed query and error message to OpenAI
to get a corrected SQL query for retry (FR-3.4).
Supports structured hints (e.g. for missing-column errors) so the model is
instructed to use only schema columns and avoid hallucinating names.
"""
from openai import OpenAI

from config import CHAT_MODEL, OPENAI_API_KEY

from sql_errors import ClassifiedError, classify_sqlite_error

CORRECTION_PROMPT = """The following SQLite query failed.

Failed query:
```sql
{sql}
```

{error_section}

Provide a corrected SQLite query only. Output nothing but the SQL, optionally inside a ```sql code block. Do not explain."""

# System prompt when we know it's a column/table error: stress schema-only names.
SYSTEM_MISSING_COLUMN = (
    "You fix SQLite errors. The database reported a missing column or table. "
    "You MUST use ONLY table and column names that appear in the schema context provided. "
    "Do NOT invent or guess any names. Reply with only the corrected SQL."
)
SYSTEM_DEFAULT = "You fix SQLite syntax/semantic errors. Reply with only the corrected SQL."


def get_corrected_sql(
    failed_sql: str,
    error_message: str,
    rag_context: list[str],
    *,
    classified: ClassifiedError | None = None,
) -> str:
    """
    Ask the model for a corrected SQL given the failed query and DB error.
    rag_context is used so the model stays schema-grounded.
    If classified is provided (e.g. from classify_sqlite_error), the retry
    prompt uses the structured hint to reduce column/table hallucination.
    """
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not set in .env")

    if classified is None:
        classified = classify_sqlite_error(Exception(error_message))

    # Use full RAG context for column/table errors so the model sees all valid names.
    num_chunks = 5 if classified.kind in ("missing_column", "missing_table") else 3
    context_block = "\n\n".join(rag_context[:num_chunks]) if rag_context else ""

    error_section = f"**Critical:** {classified.hint_for_model}"

    user_content = f"""Schema and sample queries — use ONLY these tables and columns (exact names):

{context_block}

---

{CORRECTION_PROMPT.format(sql=failed_sql, error_section=error_section)}"""

    system = SYSTEM_MISSING_COLUMN if classified.kind in ("missing_column", "missing_table") else SYSTEM_DEFAULT

    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
    )
    content = (response.choices[0].message.content or "").strip()
    # Extract SQL from markdown block if present
    if "```sql" in content:
        content = content.split("```sql", 1)[1].split("```", 1)[0].strip()
    elif "```" in content:
        content = content.split("```", 2)[1].strip()
    return content
