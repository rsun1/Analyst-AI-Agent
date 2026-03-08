"""
Generate proposed SQL and short explanation from user question + RAG context.
Also handles user-driven SQL revision (edit loop, FR-2.5/FR-2.6).
Uses OpenAI Chat only; no execution.

SQL accuracy strategy:
  - The SYSTEM prompt always contains the full schema catalog (exact table/column
    names, FK relationships, enums, pitfalls) loaded from schema_catalog.yml.
  - The USER message contains the RAG-retrieved metric definitions and sample queries.
  - This two-layer approach ensures the model can never guess column or table names:
    the ground-truth schema is always present regardless of what RAG returns.
"""
from openai import OpenAI

from config import CHAT_MODEL, OPENAI_API_KEY
from schema_loader import render_schema_block

# ── Shared response format instructions ───────────────────────────────────────

_RESPONSE_FORMAT = """
Respond in this exact format, with no extra text before or after:

EXPLANATION:
<1-3 sentences>

SQL:
```sql
<single SQLite query>
```"""

# ── System prompts ─────────────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    """Build the system prompt with the always-on schema catalog embedded."""
    schema_block = render_schema_block()
    return f"""You are a SQL expert for the Olist Brazilian e-commerce SQLite database.

{schema_block}

Rules:
- Use ONLY the table and column names listed in the schema above. Never invent or guess names.
- Use ONLY the FK relationships listed above for JOIN conditions.
- Generate valid SQLite SQL (strftime for date grouping, julianday for date differences).
- Output exactly one SQL query and a short explanation.
- If a CONVERSATION HISTORY block is present in the user message, use it to understand
  follow-up references (e.g. "break that down", "now filter by..."). If the current
  question is unrelated to the history, ignore the history and generate SQL from scratch.
{_RESPONSE_FORMAT}"""


def _build_revise_system_prompt() -> str:
    """System prompt for the SQL revision (edit-loop) path."""
    schema_block = render_schema_block()
    return f"""You are a SQL expert for the Olist Brazilian e-commerce SQLite database.
The user has reviewed a proposed SQLite query and requested a change.

{schema_block}

Rules:
- Apply the user's requested change to the existing SQL.
- Use ONLY the table and column names listed in the schema above. Never invent or guess names.
- Use ONLY the FK relationships listed above for JOIN conditions.
- Generate valid SQLite SQL (strftime for date grouping, julianday for date differences).
- If the user's instruction is too vague, contradictory, or does not clearly specify a SQL
  change, respond with exactly the word UNCLEAR on a line by itself, and nothing else.
- Otherwise respond in the format below.
{_RESPONSE_FORMAT}"""


# ── Response parser (shared) ───────────────────────────────────────────────────

def _parse_response(content: str) -> tuple[str, str]:
    """Parse EXPLANATION: / SQL: response into (sql, explanation). Returns ("", "") on failure."""
    explanation = ""
    sql = ""

    if "EXPLANATION:" in content:
        parts = content.split("EXPLANATION:", 1)[1]
        if "SQL:" in parts:
            expl_part, sql_part = parts.split("SQL:", 1)
            explanation = expl_part.strip()
            sql_part = sql_part.strip()
            if "```sql" in sql_part:
                sql = sql_part.split("```sql", 1)[1].split("```", 1)[0].strip()
            elif "```" in sql_part:
                sql = sql_part.split("```", 2)[1].strip()
            else:
                sql = sql_part
        else:
            explanation = parts.strip()
    else:
        if "```sql" in content:
            sql = content.split("```sql", 1)[1].split("```", 1)[0].strip()
        explanation = content[:500] if content else ""

    return sql, explanation


# ── Public API ─────────────────────────────────────────────────────────────────

def generate_sql_and_explanation(
    question: str,
    rag_context: list[str],
    conversation_history: list[dict] | None = None,
) -> tuple[str, str]:
    """
    Generate a SQLite query and explanation for the given question.

    The system prompt always contains the full schema catalog (ground truth).
    The user message adds RAG-retrieved metric definitions and query patterns.
    When conversation_history is provided, prior turns are prepended so the
    model understands follow-up references.
    Returns (sql_query, explanation).
    """
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not set in .env")

    from conversation import build_history_block, trim_history  # local import avoids circular deps

    context_block = "\n\n---\n\n".join(rag_context) if rag_context else "(No sample queries retrieved.)"

    history_block = ""
    if conversation_history:
        history_block = build_history_block(trim_history(conversation_history))

    history_prefix = f"{history_block}\n\n" if history_block else ""

    user_content = f"""{history_prefix}Relevant metric definitions and sample queries (for query pattern guidance):

{context_block}

---

User question: {question}

Provide one SQLite query and a short explanation."""

    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": _build_system_prompt()},
            {"role": "user", "content": user_content},
        ],
    )
    content = (response.choices[0].message.content or "").strip()
    sql, explanation = _parse_response(content)
    return sql, explanation


# ── Edit loop (FR-2.5 / FR-2.6) ───────────────────────────────────────────────

# Sentinel returned when the user's edit instruction is too vague to act on.
UNCLEAR_INSTRUCTION = "__UNCLEAR__"


def revise_sql(
    current_sql: str,
    explanation: str,
    user_instruction: str,
    rag_context: list[str],
) -> tuple[str, str]:
    """
    Revise the current SQL according to the user's natural-language instruction.

    Returns:
        (revised_sql, revised_explanation) on success.
        (UNCLEAR_INSTRUCTION, "") when the instruction is too vague to act on (FR-2.6).
    """
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not set in .env")

    context_block = "\n\n---\n\n".join(rag_context) if rag_context else "(No sample queries retrieved.)"

    user_content = f"""Relevant metric definitions and sample queries (for query pattern guidance):

{context_block}

---

Current SQL:
```sql
{current_sql}
```

Current explanation: {explanation}

User's requested change: {user_instruction}

Apply the change and return the revised SQL, or respond with UNCLEAR if the instruction is too vague."""

    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": _build_revise_system_prompt()},
            {"role": "user", "content": user_content},
        ],
    )
    content = (response.choices[0].message.content or "").strip()

    # Detect the explicit UNCLEAR sentinel
    if content.strip().upper() == "UNCLEAR" or content.strip().upper().startswith("UNCLEAR\n"):
        return UNCLEAR_INSTRUCTION, ""

    new_sql, new_explanation = _parse_response(content)

    # If the model returned something but no parseable SQL, treat as unclear
    if not new_sql:
        return UNCLEAR_INSTRUCTION, ""

    return new_sql, new_explanation
