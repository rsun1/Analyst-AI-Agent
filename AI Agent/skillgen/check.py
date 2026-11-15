"""
Quality gate for skills: validate the files, then run every SQL block against
Postgres (read-only role, read-only transaction, statement timeout) and apply
tie-out checks that catch silent double counting.

Usage (from "AI Agent/"):
    python -m skillgen.check              # all skills
    python -m skillgen.check monthly_gmv  # one skill
Exit code is non-zero if any check fails.
"""
from __future__ import annotations

import sys
from decimal import Decimal

import psycopg

from config import PG_READER_DSN
from skill_loader import load_skills

TIMEOUT_MS = 60_000
PREVIEW_ROWS = 50

# name -> (sql block label, result column to sum, "first" for the first cell, or "row0:<col>" for that column of the first row,
#          SQL returning the independently computed expected value, relative tolerance)
TIEOUTS: dict[str, tuple[str, str, str, float]] = {
    "monthly_gmv": ("main", "gmv", "SELECT SUM(payment_value) FROM order_payments", 1e-6),
    "seller_tpv_per_month": ("main", "seller_gmv", "SELECT SUM(price + freight_value) FROM order_items", 1e-6),
    "orders_per_month": ("main", "order_count", "SELECT COUNT(*) FROM orders", 0),
    "customer_order_count": ("main", "order_count", "SELECT COUNT(*) FROM orders", 0),
    "sales_by_customer_state": ("main", "total_sales", "SELECT SUM(payment_value) FROM order_payments", 1e-6),
    "payment_type_breakdown": ("main", "pct", "SELECT 100", 0.001),
    "reviews_with_comments": ("main", "first", """SELECT COUNT(*) FROM order_reviews
        WHERE COALESCE(trim(review_comment_title), '') <> '' OR COALESCE(trim(review_comment_message), '') <> ''""", 0),
    "monthly_average_review_score": ("main", "review_count",
                                      "SELECT COUNT(*) FROM order_reviews WHERE review_creation_date IS NOT NULL", 0),
    "conversion_rate_by_origin": ("main", "qualified_count", "SELECT COUNT(DISTINCT mql_id) FROM leads_qualified", 0),
    "revenue_by_product_category": ("main", "revenue", """SELECT SUM(oi.price) FROM order_items oi
        JOIN products p ON p.product_id = oi.product_id
        JOIN product_category_name_translation t ON t.product_category_name = p.product_category_name""", 1e-6),
    "aov": ("overall", "row0:aov", "SELECT ROUND((SUM(payment_value) / COUNT(DISTINCT order_id))::numeric, 2) FROM order_payments", 1e-9),
    "revenue_growth_mom": ("main", "gmv", "SELECT SUM(payment_value) FROM order_payments", 1e-6),
    "items_per_order": ("main", "row0:orders", "SELECT COUNT(DISTINCT order_id) FROM order_items", 0),
    "orders_by_status": ("main", "order_count", "SELECT COUNT(*) FROM orders", 0),
    "cancellation_rate": ("by_month", "canceled", "SELECT COUNT(*) FROM orders WHERE order_status = 'canceled'", 0),
    "orders_by_weekday_hour": ("by_weekday", "order_count", "SELECT COUNT(*) FROM orders", 0),
    "top_10_sellers_by_revenue": ("main", "row0:total_revenue", """SELECT SUM(price + freight_value) FROM order_items
        GROUP BY seller_id ORDER BY 1 DESC LIMIT 1""", 1e-6),
}


def _clean(sql: str) -> str:
    return sql.strip().rstrip(";")


def _connect() -> psycopg.Connection:
    return psycopg.connect(
        PG_READER_DSN, autocommit=False,
        options=f"-c statement_timeout={TIMEOUT_MS} -c default_transaction_read_only=on",
    )


def _num(v) -> float:
    return float(v) if isinstance(v, (int, float, Decimal)) else float(v or 0)


def check_skill(conn: psycopg.Connection, skill) -> list[str]:
    """Return a list of failure messages (empty = pass)."""
    fails: list[str] = []
    if skill.meta.route != "analyze":
        return fails
    for label, sql in skill.sql_blocks.items():
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM ({_clean(sql)}) AS t LIMIT {PREVIEW_ROWS}")
                rows = cur.fetchall()
            if not rows:
                fails.append(f"[{label}] returned no rows")
        except psycopg.Error as e:
            fails.append(f"[{label}] SQL error: {str(e).strip().splitlines()[0]}")
        finally:
            conn.rollback()
    tie = TIEOUTS.get(skill.name)
    if tie and not fails:
        label, col, expected_sql, tol = tie
        try:
            with conn.cursor() as cur:
                cur.execute(_clean(skill.sql_blocks[label]))
                names = [d.name for d in cur.description]
                rows = cur.fetchall()
                if col == "first":
                    got = _num(rows[0][0])
                elif col.startswith("row0:"):
                    got = _num(rows[0][names.index(col[5:])])
                else:
                    got = sum(_num(r[names.index(col)]) for r in rows)
                cur.execute(_clean(expected_sql))
                want = _num(cur.fetchone()[0])
            if abs(got - want) > tol * max(abs(want), 1):
                fails.append(f"tie-out: skill gives {got:,.2f}, independent total is {want:,.2f}")
        except (psycopg.Error, ValueError, KeyError) as e:
            fails.append(f"tie-out error: {str(e).strip().splitlines()[0]}")
        finally:
            conn.rollback()
    return fails


def main(argv: list[str]) -> int:
    try:
        skills = load_skills()
    except ValueError as e:
        print(e)
        return 1
    if argv:
        skills = [s for s in skills if s.name in argv]
        if not skills:
            print("no matching skills")
            return 1
    failed = 0
    with _connect() as conn:
        for s in skills:
            fails = check_skill(conn, s)
            tag = "FAIL" if fails else "ok  "
            tied = " +tie-out" if s.name in TIEOUTS and s.meta.route == "analyze" else ""
            print(f"{tag} {s.meta.domain}/{s.name}{tied}")
            for f in fails:
                print(f"       - {f}")
            failed += bool(fails)
    print(f"\n{len(skills) - failed}/{len(skills)} skills passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
