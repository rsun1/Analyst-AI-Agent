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
from schema_loader import render_live_schema_block

# ── Shared response format instructions ───────────────────────────────────────

_RESPONSE_FORMAT = """
Respond in this exact format, with no extra text before or after:

EXPLANATION:
<1-3 sentences>

SQL:
```sql
<single PostgreSQL query>
```"""

# ── Few-shot examples (injected into every system prompt) ─────────────────────
# These show the correct SQL pattern for the most common failure modes.
# Keep examples short — they are in addition to the schema catalog.

_FEW_SHOT_EXAMPLES = """
## WORKED EXAMPLES — follow these patterns exactly

### Example 1: Filter by English product category name
Question: "average price of products in the 'electronics' category"
Key pattern: product_category_name in products is PORTUGUESE — always JOIN the translation table.
```sql
SELECT AVG(oi.price)
FROM order_items oi
JOIN products p ON p.product_id = oi.product_id
JOIN product_category_name_translation pct
  ON pct.product_category_name = p.product_category_name
WHERE pct.product_category_name_english = 'electronics';
```

### Example 2: Revenue by category — use oi.price, not payment_value
Question: "top 5 product categories by total revenue"
Key pattern: SUM(order_items.price) for item-level revenue. Do NOT join order_payments for category revenue.
```sql
SELECT pct.product_category_name_english, SUM(oi.price) AS revenue
FROM order_items oi
JOIN products p ON p.product_id = oi.product_id
JOIN product_category_name_translation pct
  ON pct.product_category_name = p.product_category_name
GROUP BY pct.product_category_name_english
ORDER BY revenue DESC
LIMIT 5;
```

### Example 3: Delivery time from purchase (not delay)
Question: "average delivery time in days"
Key pattern: EXTRACT(EPOCH FROM (delivered - purchase_timestamp)) / 86400.0. Do NOT use order_estimated_delivery_date.
```sql
SELECT AVG(EXTRACT(EPOCH FROM (order_delivered_customer_date - order_purchase_timestamp)) / 86400.0)
FROM orders
WHERE order_delivered_customer_date IS NOT NULL;
```

### Example 4: Leads qualified in a period — do NOT filter won_date
Question: "conversion rate of leads qualified in Q4 2017"
Key pattern: filter ONLY on leads_qualified.first_contact_date; NEVER add AND lc.won_date BETWEEN ... — a lead qualified in Q4 can close in any future quarter.
```sql
SELECT
  COUNT(DISTINCT lq.mql_id) AS qualified,
  COUNT(DISTINCT lc.mql_id) AS closed,
  ROUND(1.0 * COUNT(DISTINCT lc.mql_id) / COUNT(DISTINCT lq.mql_id), 4) AS conversion_rate
FROM leads_qualified lq
LEFT JOIN leads_closed lc ON lc.mql_id = lq.mql_id
WHERE lq.first_contact_date BETWEEN '2017-10-01' AND '2017-12-31';
```

### Example 4b: Average time to close leads by channel — still filter first_contact_date only
Question: "average time from first contact to lead closed by origin channel in Q4 2017"
Key pattern: INNER JOIN (only leads that were closed), filter first_contact_date only, compute EXTRACT(EPOCH FROM (won_date - first_contact_date)) / 86400.0.
```sql
SELECT
  lq.origin,
  AVG(EXTRACT(EPOCH FROM (lc.won_date - lq.first_contact_date)) / 86400.0) AS avg_days_to_close
FROM leads_qualified lq
INNER JOIN leads_closed lc ON lc.mql_id = lq.mql_id
WHERE lq.first_contact_date BETWEEN '2017-10-01' AND '2017-12-31'
GROUP BY lq.origin
ORDER BY avg_days_to_close ASC;
```

### Example 5: Products with zero reviews
Question: "how many products have no reviews"
Key pattern: LEFT JOIN through order_items → orders → order_reviews, filter on NULL review_id.
```sql
SELECT COUNT(DISTINCT p.product_id)
FROM products p
LEFT JOIN order_items oi ON oi.product_id = p.product_id
LEFT JOIN orders o ON o.order_id = oi.order_id
LEFT JOIN order_reviews r ON r.order_id = o.order_id
WHERE r.review_id IS NULL;
```
"""

# ── System prompts ─────────────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    """Build the system prompt with the live schema and few-shot examples embedded."""
    schema_block = render_live_schema_block()
    return f"""You are a SQL expert for the Olist Brazilian e-commerce PostgreSQL database.

{schema_block}
{_FEW_SHOT_EXAMPLES}
Rules:
- Use ONLY the table and column names listed in the schema above. Never invent or guess names.
- Use ONLY the FK relationships listed above for JOIN conditions.
- Generate valid PostgreSQL SQL (date columns are TIMESTAMP: use TO_CHAR(col, 'YYYY-MM') or DATE_TRUNC for grouping, and EXTRACT(EPOCH FROM (a - b)) / 86400.0 for day differences). Round with ROUND(x::numeric, n).
- Output exactly one SQL query and a short explanation.
- If a CONVERSATION HISTORY block is present in the user message, use it to understand
  follow-up references (e.g. "break that down", "now filter by..."). If the current
  question is unrelated to the history, ignore the history and generate SQL from scratch.
{_RESPONSE_FORMAT}"""


def _build_revise_system_prompt() -> str:
    """System prompt for the SQL revision (edit-loop) path."""
    schema_block = render_live_schema_block()
    return f"""You are a SQL expert for the Olist Brazilian e-commerce PostgreSQL database.
The user has reviewed a proposed PostgreSQL query and requested a change.

{schema_block}

Rules:
- Apply the user's requested change to the existing SQL.
- Use ONLY the table and column names listed in the schema above. Never invent or guess names.
- Use ONLY the FK relationships listed above for JOIN conditions.
- Generate valid PostgreSQL SQL (date columns are TIMESTAMP: use TO_CHAR(col, 'YYYY-MM') or DATE_TRUNC for grouping, and EXTRACT(EPOCH FROM (a - b)) / 86400.0 for day differences). Round with ROUND(x::numeric, n).
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
    Generate a PostgreSQL query and explanation for the given question.

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

Provide one PostgreSQL query and a short explanation."""

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
