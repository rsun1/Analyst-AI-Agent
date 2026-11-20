"""
Load the facts the rules need. Two databases roles, on purpose:
  * Olist order data: the SELECT-only reader role (db.get_connection)
  * Dispute history: the workflow role, passed in as `wf_conn`
Neither role can see the other's tables.
"""
from decimal import Decimal

from psycopg import Connection

from db import get_connection
from workflow.models import OrderFacts
from workflow.store import dispute_history


def _money(x) -> Decimal | None:
    return None if x is None else Decimal(str(round(x, 2)))


def fetch_order_facts(order_id: str) -> tuple[OrderFacts, list[str]] | None:
    """
    Order facts from Olist, plus every customer_id of the same person (a person can
    appear under several customer_ids; history must be keyed on customer_unique_id).
    Returns None if the order does not exist.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT o.order_id, o.order_status, o.order_delivered_customer_date, "
            "       o.order_estimated_delivery_date, o.customer_id, c.customer_unique_id, "
            "       (SELECT SUM(payment_value) FROM order_payments p WHERE p.order_id = o.order_id), "
            "       (SELECT MIN(review_score) FROM order_reviews r WHERE r.order_id = o.order_id) "
            "FROM orders o JOIN customers c ON c.customer_id = o.customer_id "
            "WHERE o.order_id = %s",
            (order_id,),
        ).fetchone()
        if row is None:
            return None
        oid, status, delivered, estimated, customer_id, unique_id, paid, score = row
        siblings = [r[0] for r in conn.execute(
            "SELECT customer_id FROM customers WHERE customer_unique_id = %s", (unique_id,)
        ).fetchall()]
    facts = OrderFacts(
        order_id=oid, status=status, paid_total=_money(paid), delivered_at=delivered,
        estimated_delivery_at=estimated, review_score=score,
        customer_id=customer_id, customer_unique_id=unique_id,
    )
    return facts, siblings


def load_facts(wf_conn: Connection, order_id: str, exclude_dispute_id: int | None = None) -> OrderFacts | None:
    """Order facts plus dispute history, ready for rules.evaluate(). None = no such order."""
    loaded = fetch_order_facts(order_id)
    if loaded is None:
        return None
    facts, customer_ids = loaded
    has_open, refunds = dispute_history(wf_conn, order_id, customer_ids, exclude_dispute_id)
    return OrderFacts(**{**facts.__dict__, "has_open_dispute": has_open, "refunds_last_90d": refunds})
