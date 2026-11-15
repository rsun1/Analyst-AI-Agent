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


def overview_data(params: dict[str, str]) -> dict:
    """Monthly KPIs for the executive overview: GMV, orders, AOV, review score, late share."""
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
    months = [series[m] for m in sorted(series)]
    for row in months:
        row["aov"] = round(row["gmv"] / row["orders"], 2) if row.get("gmv") and row.get("orders") else None
    return {"title": "Executive overview", "months": months}


BUILDERS = {"executive_overview": overview_data}
