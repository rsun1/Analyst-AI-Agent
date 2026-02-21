"""
Send result set (and optional question/query) to OpenAI for a short plain-English
summary and 1-3 concrete business insights or recommendations.
"""
from openai import OpenAI

from config import CHAT_MODEL, OPENAI_API_KEY


def format_results_for_prompt(columns: list[str], rows: list[tuple], max_rows: int = 100) -> str:
    """Format column names and rows as readable text for the LLM (cap rows to avoid token overflow)."""
    head = " | ".join(columns)
    lines = [head, "-" * len(head)]
    for row in rows[:max_rows]:
        lines.append(" | ".join(str(c) for c in row))
    if len(rows) > max_rows:
        lines.append(f"... ({len(rows) - max_rows} more rows)")
    return "\n".join(lines)


def explain_results(
    question: str,
    sql: str,
    columns: list[str],
    rows: list[tuple],
) -> str:
    """
    Call OpenAI Chat to produce a short plain-English summary of what the numbers mean
    and 1-3 concrete business insights or recommendations.
    """
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not set in .env")

    results_text = format_results_for_prompt(columns, rows)

    user_content = f"""The user asked: "{question}"

We ran this SQL:
```sql
{sql}
```

Query results (first rows):
{results_text}

Provide:
1. A short plain-English summary of what these numbers mean.
2. One to three concrete business insights or recommendations.

Keep the response concise and actionable."""

    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {
                "role": "system",
                "content": "You are a business analyst. Summarize data in plain English and give 1-3 actionable insights or recommendations. Be concise.",
            },
            {"role": "user", "content": user_content},
        ],
    )
    return (response.choices[0].message.content or "").strip()
