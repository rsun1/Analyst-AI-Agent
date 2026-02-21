"""
Generate proposed SQL and short explanation from user question + RAG context.
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
