"""
Facts loader + rules against the real Olist fixtures in docs/refund-policy.md.
Needs the local Postgres with Olist data and `python -m setup_workflow_db`; skipped otherwise.
"""
from datetime import datetime, timedelta
from decimal import Decimal

import psycopg
import pytest

import db
from config import PG_ADMIN_DSN
from workflow import rules
from workflow.facts import fetch_order_facts, load_facts
from workflow.models import Claim, Outcome, ReasonCategory
from workflow.rules import evaluate
from workflow.store import create_dispute

MARK = "__pytest_marker__"


def _ready():
    try:
        with psycopg.connect(PG_ADMIN_DSN, connect_timeout=3) as c:
            return (c.execute("SELECT to_regclass('workflow.audit_log')").fetchone()[0] is not None
                    and c.execute("SELECT to_regclass('public.orders')").fetchone()[0] is not None)
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="Postgres, Olist data or workflow schema not available")


@pytest.fixture(autouse=True)
def cleanup():
    yield
    with psycopg.connect(PG_ADMIN_DSN) as c:
        ids = "SELECT id FROM workflow.disputes WHERE reason_text = %s"
        c.execute(f"DELETE FROM workflow.audit_log WHERE dispute_id IN ({ids})", (MARK,))
        c.execute("DELETE FROM workflow.disputes WHERE reason_text = %s", (MARK,))


T1 = "001d8f0e34a38c37f7dba2a37d4eba8b"
T1B = "0030d783f979fbc5981e75613b057344"
T2 = "0009c9a17f916a706d71784483a5d643"
T2B = "000576fe39319847cbb9d288c5617fa6"
T5 = "00310b0c75bb13015ec4d82d341865a4"
T6 = "000e562887b1f2006d75e0be9558292e"
T7 = "00010242fe8c5a6d1ba2dd792cb16214"
T_MID = "00018f77f2f0320c557190d7a144bdd3"
T_SMALL = "000e63d38ae8c00bbcb5a30573b99628"
T8A = "bfbd0f9bdef84302105ad712db648a6c"


def run(order_id, amount, *, as_of=None, category=ReasonCategory.OTHER):
    """Load real facts and evaluate. as_of defaults to delivered date + 5 days (inside the window)."""
    with db.get_workflow_connection() as wf:
        f = load_facts(wf, order_id)
    if as_of is None and f is not None and f.delivered_at is not None:
        as_of = f.delivered_at + timedelta(days=5)
    return f, evaluate(f, Claim(order_id, Decimal(str(amount)), as_of or datetime(2018, 1, 1), category))


def test_facts_shape_for_a_delivered_order():
    f, _ = run(T1, 50)
    assert (f.status, f.paid_total, f.review_score) == ("delivered", Decimal("53.54"), 1)
    assert f.delivered_at > f.estimated_delivery_at
    assert f.customer_unique_id


@pytest.mark.parametrize("order_id,amount,outcome,rule", [
    (T1, 50, Outcome.AUTO_APPROVE, rules.R09),
    (T1B, 70, Outcome.AUTO_APPROVE, rules.R09),
    (T2, 600, Outcome.NEEDS_REVIEW, rules.R07),
    (T2B, 800, Outcome.NEEDS_REVIEW, rules.R07),
    (T5, 55, Outcome.REJECT, rules.R03),
    (T_MID, 200, Outcome.NEEDS_REVIEW, rules.R99),
    (T_SMALL, 50, Outcome.AUTO_APPROVE, rules.R10),
    (T8A, 10, Outcome.REJECT, rules.R04),
])
def test_real_orders(order_id, amount, outcome, rule):
    _, d = run(order_id, amount)
    assert (d.outcome, d.rule_id) == (outcome, rule), d


def test_t6_outside_window():
    _, d = run(T6, 40, as_of=datetime(2018, 6, 1))
    assert (d.outcome, d.rule_id) == (Outcome.REJECT, rules.R05)


def test_t7_never_arrived_contradiction():
    _, d = run(T7, 70, category=ReasonCategory.NOT_RECEIVED)
    assert (d.outcome, d.rule_id) == (Outcome.NEEDS_REVIEW, rules.R06)
    assert "cannot judge" in d.reasoning


def test_unknown_order():
    f, d = run("does-not-exist", 10)
    assert f is None and d.rule_id == rules.R01


def test_open_dispute_is_seen_and_can_be_excluded():
    with db.get_workflow_connection() as wf:
        did = create_dispute(wf, order_id=T_SMALL, customer_id="x", reason_text=MARK, amount_requested=20)
    with db.get_workflow_connection() as wf:
        assert load_facts(wf, T_SMALL).has_open_dispute is True
        assert load_facts(wf, T_SMALL, exclude_dispute_id=did).has_open_dispute is False
    _, d = run(T_SMALL, 20)
    assert (d.outcome, d.rule_id) == (Outcome.REJECT, rules.R02)


def test_repeat_refunds_are_counted_across_all_customer_ids_of_one_person():
    f, siblings = fetch_order_facts(T_SMALL)
    with psycopg.connect(PG_ADMIN_DSN) as admin:  # fabricate two executed refunds for this person
        for i in range(2):
            admin.execute(
                "INSERT INTO workflow.disputes (order_id, customer_id, reason_text, amount_requested, status) "
                "VALUES (%s, %s, %s, 10, 'refund_executed')", (f"__other_{i}", siblings[0], MARK))
    _, d = run(T_SMALL, 20)
    assert (d.outcome, d.rule_id) == (Outcome.NEEDS_REVIEW, rules.R08)
