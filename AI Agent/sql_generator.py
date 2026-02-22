"""
Generate proposed SQL and short explanation from user question + RAG context.
Also handles user-driven SQL revision (edit loop, FR-2.5/FR-2.6).
Uses OpenAI Chat only; no execution.
"""
from openai import OpenAI

from config import CHAT_MODEL, OPENAI_API_KEY

SYSTEM_PROMPT = """You are a SQL expert for the Olist Brazilian e-commerce SQLite database.
You must ONLY use the schema, table/column definitions, and sample SQL provided in the context below.
Generate valid SQLite SQL only (e.g. use strftime for dates, not date_trunc).
Output exactly one SQL query and a short explanation of why it answers the question.

Respond in this exact format, with no extra text before or after:

EXPLANATION:
<1-3 sentences>

SQL:
```sql
<single SQLite query>
```
"""


def generate_sql_and_explanation(question: str, rag_context: list[str]) -> tuple[str, str]:
    """
    Call OpenAI Chat with system prompt (restrict to context, SQLite, explanation)
    and user message (question + context). Returns (sql_query, explanation).
    """
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not set in .env")

    context_block = "\n\n---\n\n".join(rag_context) if rag_context else "(No schema context provided.)"

    user_content = f"""Schema and sample queries (use only this context):

{context_block}

---

User question: {question}

Provide one SQLite query and a short explanation."""

    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    )
    content = (response.choices[0].message.content or "").strip()

    # Parse EXPLANATION: ... SQL: ```sql ... ```
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
        # Fallback: try to extract first ```sql ... ``` block
        if "```sql" in content:
            sql = content.split("```sql", 1)[1].split("```", 1)[0].strip()
        explanation = content[:500] if content else "No explanation generated."

    return sql, explanation


# Sentinel returned when the user's edit instruction is too vague to act on (FR-2.6).
UNCLEAR_INSTRUCTION = "__UNCLEAR__"

REVISE_SYSTEM_PROMPT = """You are a SQL expert for the Olist Brazilian e-commerce SQLite database.
The user has reviewed a proposed SQLite query and requested a change.
Apply the user's requested change to the SQL and return the revised query with a short explanation.
Use ONLY table and column names that appear in the schema context provided.
Generate valid SQLite SQL only (e.g. use strftime for dates, not date_trunc).

If the user's instruction is too vague, contradictory, or does not clearly specify a SQL change,
respond with exactly the word UNCLEAR on a line by itself, and nothing else.

Otherwise respond in this exact format:

EXPLANATION:
<1-3 sentences describing what changed and why>

SQL:
```sql
<single revised SQLite query>
```
"""


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

    context_block = "\n\n---\n\n".join(rag_context) if rag_context else "(No schema context provided.)"

    user_content = f"""Schema and sample queries (use only these tables/columns):

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
            {"role": "system", "content": REVISE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    )
    content = (response.choices[0].message.content or "").strip()

    # Detect the explicit UNCLEAR sentinel
    if content.strip().upper() == "UNCLEAR" or content.strip().upper().startswith("UNCLEAR\n"):
        return UNCLEAR_INSTRUCTION, ""

    # Parse the normal EXPLANATION / SQL response (same format as generate_sql_and_explanation)
    new_explanation = ""
    new_sql = ""

    if "EXPLANATION:" in content:
        parts = content.split("EXPLANATION:", 1)[1]
        if "SQL:" in parts:
            expl_part, sql_part = parts.split("SQL:", 1)
            new_explanation = expl_part.strip()
            sql_part = sql_part.strip()
            if "```sql" in sql_part:
                new_sql = sql_part.split("```sql", 1)[1].split("```", 1)[0].strip()
            elif "```" in sql_part:
                new_sql = sql_part.split("```", 2)[1].strip()
            else:
                new_sql = sql_part
        else:
            new_explanation = parts.strip()
    else:
        if "```sql" in content:
            new_sql = content.split("```sql", 1)[1].split("```", 1)[0].strip()
        new_explanation = content[:500] if content else "No explanation generated."

    # If the model returned something but no parseable SQL, treat as unclear
    if not new_sql:
        return UNCLEAR_INSTRUCTION, ""

    return new_sql, new_explanation
