"""
Dashboard deep links for redirect skills.

A redirect skill names a dashboard (provider + page) and the parameters a link may
carry. Parameters extracted from the user's question are validated here against
that allowlist before they are put in a URL, so a model-supplied value can never
smuggle in anything unexpected.
"""
from __future__ import annotations

import re
from urllib.parse import urlencode

from config import UI_BASE_URL
from skill_loader import Dashboard

BR_STATES = frozenset(
    "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
)
_DATE_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


def clean_params(dash: Dashboard, raw: dict | None) -> dict[str, str]:
    """Keep only allowlisted params whose values pass their type check."""
    out: dict[str, str] = {}
    for key, value in (raw or {}).items():
        kind = dash.params.get(key)
        if kind is None or value is None:
            continue
        v = str(value).strip()
        if kind == "state" and v.upper() in BR_STATES:
            out[key] = v.upper()
        elif kind == "date" and _DATE_RE.match(v):
            out[key] = v
        elif kind == "text" and 0 < len(v) <= 60 and re.match(r"^[\w .,'-]+$", v):
            out[key] = v
    return out


def build_link(dash: Dashboard, raw_params: dict | None = None) -> str:
    """Deep link for the dashboard. External providers get their URL unchanged."""
    if dash.provider != "streamlit":
        return dash.page
    q = {"page": dash.page, **clean_params(dash, raw_params)}
    return f"{UI_BASE_URL}/?{urlencode(q)}"


# ── Data for dashboard pages ─────────────────────────────────────────────────
# Dashboards reuse the SQL of the analyze skills (single source of truth), so the
# numbers always match what the agent answers.

_LATE_BY_MONTH_SQL = """SELECT TO_CHAR(order_purchase_timestamp, 'YYYY-MM') AS month,
  ROUND((100.0 * AVG((order_delivered_customer_date > order_estimated_delivery_date)::int))::numeric, 2) AS late_pct
FROM orders
WHERE order_delivered_customer_date IS NOT NULL AND order_estimated_delivery_date IS NOT NULL
GROUP BY month ORDER BY month"""


def _skill_sql(name: str, label: str = "main") -> str:
    from skill_loader import skills_by_name
    return skills_by_name()[name].sql_blocks[label].strip().rstrip(";")


def _in_range(month: str, date_from: str | None, date_to: str | None) -> bool:
    if date_from and month < date_from[:7]:
        return False
    if date_to and month > date_to[:7]:
        return False
    return True


_CATEGORY_BY_MONTH_SQL = """SELECT TO_CHAR(o.order_purchase_timestamp, 'YYYY-MM') AS month,
  COALESCE(pct.product_category_name_english, 'unknown') AS segment, SUM(oi.price) AS value
FROM orders o
JOIN order_items oi ON oi.order_id = o.order_id
JOIN products p ON p.product_id = oi.product_id
LEFT JOIN product_category_name_translation pct ON pct.product_category_name = p.product_category_name
GROUP BY month, segment"""

_PAYMENT_BY_MONTH_SQL = """SELECT TO_CHAR(o.order_purchase_timestamp, 'YYYY-MM') AS month,
  op.payment_type AS segment, SUM(op.payment_value) AS value
FROM orders o
JOIN order_payments op ON op.order_id = o.order_id
GROUP BY month, segment"""

_DELIVERY_BY_MONTH_SQL = """SELECT TO_CHAR(order_purchase_timestamp, 'YYYY-MM') AS month,
  CASE WHEN order_delivered_customer_date > order_estimated_delivery_date THEN 'late' ELSE 'on_time' END AS segment,
  COUNT(*) AS value
FROM orders
WHERE order_delivered_customer_date IS NOT NULL AND order_estimated_delivery_date IS NOT NULL
GROUP BY month, segment"""


def top_n_with_other(rows: list[dict], n: int = 6, other: str = "other") -> list[dict]:
    """Keep the n segments with the largest total; fold the rest into one 'other' segment per month."""
    totals: dict[str, float] = {}
    for r in rows:
        totals[r["segment"]] = totals.get(r["segment"], 0.0) + r["value"]
    keep = {k for k, _ in sorted(totals.items(), key=lambda kv: -kv[1])[:n]}
    folded: dict[tuple[str, str], float] = {}
    for r in rows:
        seg = r["segment"] if r["segment"] in keep else other
        key = (r["month"], seg)
        folded[key] = folded.get(key, 0.0) + r["value"]
    return [{"month": m, "segment": sg, "value": v} for (m, sg), v in sorted(folded.items())]


def _segment_rows(sql: str, lo: str | None, hi: str | None) -> list[dict]:
    from db import execute_query

    cols, rows = execute_query(sql)
    mi, si, vi = cols.index("month"), cols.index("segment"), cols.index("value")
    return [{"month": r[mi], "segment": str(r[si]), "value": float(r[vi] or 0)}
            for r in rows if r[mi] and _in_range(r[mi], lo, hi)]


def overview_data(params: dict[str, str]) -> dict:
    """
    Executive overview: monthly KPIs plus segmentations (product category, payment type,
    delivery performance), all from the same SQL the metric skills use.
    """
    from db import execute_query

    lo, hi = params.get("date_from"), params.get("date_to")
    series: dict[str, dict] = {}

    def add(sql: str, col: str, month_col: str, out: str) -> None:
        cols, rows = execute_query(sql)
        mi, vi = cols.index(month_col), cols.index(col)
        for r in rows:
            m = r[mi]
            if m and _in_range(m, lo, hi):
                series.setdefault(m, {"month": m})[out] = None if r[vi] is None else float(r[vi])

    add(_skill_sql("monthly_gmv"), "gmv", "order_month", "gmv")
    add(_skill_sql("orders_per_month"), "order_count", "order_month", "orders")
    add(_skill_sql("monthly_average_review_score"), "avg_score", "review_month", "avg_review_score")
    add(_LATE_BY_MONTH_SQL, "late_pct", "month", "late_pct")
    # Reviews are dated by review creation, so a few months have reviews but no orders: keep order months only.
    months = [series[m] for m in sorted(series) if "gmv" in series[m] and "orders" in series[m]]
    for row in months:
        row["aov"] = round(row["gmv"] / row["orders"], 2) if row.get("gmv") and row.get("orders") else None
    return {
        "title": "Executive overview",
        "months": months,
        "segments": {
            "category": top_n_with_other(_segment_rows(_CATEGORY_BY_MONTH_SQL, lo, hi), n=6),
            "payment": top_n_with_other(_segment_rows(_PAYMENT_BY_MONTH_SQL, lo, hi), n=4),
            "delivery": _segment_rows(_DELIVERY_BY_MONTH_SQL, lo, hi),
        },
    }


BUILDERS = {"executive_overview": overview_data}
