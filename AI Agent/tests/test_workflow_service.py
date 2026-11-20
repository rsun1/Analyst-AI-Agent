"""
End-to-end service tests (T1-T10 of PHASE_A_PLAN.md) on real Olist orders, with the mock
payment gateway. Needs local Postgres + Olist data + `python -m setup_workflow_db`.
"""
import threading
from datetime import datetime, timedelta
from decimal import Decimal

import psycopg
import pytest

import db
from config import PG_ADMIN_DSN
from workflow import rules, service
from workflow.facts import fetch_order_facts
from workflow.models import ReasonCategory
from workflow.payments_mock import MockPaymentGateway
from workflow.service import InvalidClaim, OrderNotFound, RefundNotAllowed
from workflow.states import Status
from workflow.store import ApprovalAlreadyDecided, get_dispute, list_approvals, list_audit

MARK = "__pytest_marker__"
S = Status


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
    _wipe()
    yield
    _wipe()


def _wipe():
    with psycopg.connect(PG_ADMIN_DSN) as c:
        ids = "SELECT id FROM workflow.disputes WHERE reason_text = %s"
        for t in ("audit_log", "approvals", "refund_attempts"):
            c.execute(f"DELETE FROM workflow.{t} WHERE dispute_id IN ({ids})", (MARK,))
        c.execute("DELETE FROM workflow.disputes WHERE reason_text = %s", (MARK,))


LATE_SMALL = "001d8f0e34a38c37f7dba2a37d4eba8b"    # T1:  53.54, late, 1 star   -> auto approve (R09)
HIGH = "0009c9a17f916a706d71784483a5d643"          # T2:  650.34                -> human (R07)
CANCELED = "00310b0c75bb13015ec4d82d341865a4"      # T5
OLD = "000e562887b1f2006d75e0be9558292e"           # T6:  delivered 2018-03-12
HAPPY = "00010242fe8c5a6d1ba2dd792cb16214"         # T7:  5 stars, on time
SMALL = "000e63d38ae8c00bbcb5a30573b99628"         # 56.78 on time              -> auto approve (R10)
NO_PAYMENT = "bfbd0f9bdef84302105ad712db648a6c"


def sub(order_id, amount, gw=None, *, as_of=None, **kw):
    """Submit with as_of = delivered + 5 days (inside the refund window) unless given."""
    if as_of is None:
        loaded = fetch_order_facts(order_id)
        if loaded and loaded[0].delivered_at:
            as_of = loaded[0].delivered_at + timedelta(days=5)
    return service.submit_dispute(order_id=order_id, amount=amount, reason_text=MARK,
                                  as_of=as_of, gateway=gw or MockPaymentGateway(), **kw)


def audit(dispute_id):
    with db.get_workflow_connection() as c:
        return list_audit(c, dispute_id)


def path(dispute_id):
    return [a["to_status"] for a in audit(dispute_id)]


def attempts(dispute_id):
    with psycopg.connect(PG_ADMIN_DSN) as c:
        return c.execute("SELECT idempotency_key, result FROM workflow.refund_attempts "
                         "WHERE dispute_id = %s ORDER BY id", (dispute_id,)).fetchall()


# ── T1: auto approve and pay ─────────────────────────────────────────────────

def test_t1_auto_approved_and_refunded():
    gw = MockPaymentGateway()
    r = sub(LATE_SMALL, "50.00", gw)
    assert r.status is S.REFUND_EXECUTED and r.decision.rule_id == rules.R09
    assert path(r.dispute_id) == ["submitted", "auto_approved", "refund_executed"]
    assert gw.calls == 1 and gw.refunded == [(LATE_SMALL, Decimal("50.00"))]
    assert attempts(r.dispute_id) == [(f"dispute-{r.dispute_id}-attempt-1", "success")]
    decision_row = audit(r.dispute_id)[1]
    assert decision_row["rule_id"] == rules.R09 and decision_row["actor_type"] == "agent"
    assert decision_row["details"]["facts"]["review_score"] == 1  # evidence is stored


# ── T2-T4: human review ──────────────────────────────────────────────────────

def test_t2_high_value_waits_for_a_human_and_pays_nothing():
    gw = MockPaymentGateway()
    r = sub(HIGH, "600", gw)
    assert r.status is S.PENDING_REVIEW and r.approval_id
    assert gw.calls == 0 and attempts(r.dispute_id) == []
    with db.get_workflow_connection() as c:
        assert r.approval_id in [a["id"] for a in list_approvals(c)]
        assert get_dispute(c, r.dispute_id)["review_reason_type"] == "policy"


def test_t3_human_approves_then_refund_runs():
    gw = MockPaymentGateway()
    r = sub(HIGH, "600", gw)
    status = service.decide(approval_id=r.approval_id, decision="approved", reviewer="alice",
                            comment="Checked with carrier", gateway=gw)
    assert status is S.REFUND_EXECUTED and gw.calls == 1
    assert path(r.dispute_id) == ["submitted", "pending_review", "approved", "refund_executed"]
    human = [a for a in audit(r.dispute_id) if a["actor_type"] == "human" and a["from_status"]]
    assert [(h["actor_id"], h["reasoning"]) for h in human] == [("alice", "Checked with carrier")]


def test_t4_human_rejects_and_it_is_final():
    gw = MockPaymentGateway()
    r = sub(HIGH, "600", gw)
    assert service.decide(approval_id=r.approval_id, decision="rejected", reviewer="bob", gateway=gw) is S.REJECTED
    assert gw.calls == 0
    with pytest.raises(ApprovalAlreadyDecided):
        service.decide(approval_id=r.approval_id, decision="approved", reviewer="bob", gateway=gw)
    with pytest.raises(RefundNotAllowed):
        service.retry_refund(r.dispute_id, gw)


# ── T5-T8: rejects and bad input ─────────────────────────────────────────────

def test_t5_canceled_order_is_rejected_with_a_trace():
    r = sub(CANCELED, "55")
    assert r.status is S.REJECTED and r.decision.rule_id == rules.R03
    assert path(r.dispute_id) == ["submitted", "rejected"]


def test_t6_outside_window_is_rejected():
    r = sub(OLD, "40", as_of=datetime(2018, 6, 1))
    assert (r.status, r.decision.rule_id) == (S.REJECTED, rules.R05)


def test_t7_contradiction_says_the_agent_cannot_judge():
    r = sub(HAPPY, "70", reason_category=ReasonCategory.NOT_RECEIVED)
    assert r.status is S.PENDING_REVIEW and r.decision.rule_id == rules.R06
    with db.get_workflow_connection() as c:
        assert get_dispute(c, r.dispute_id)["review_reason_type"] == "agent_uncertain"
    assert "cannot judge" in audit(r.dispute_id)[1]["reasoning"]


def test_t8_bad_input_stores_nothing():
    with pytest.raises(OrderNotFound):
        sub("does-not-exist", "10")
    for bad in ("-5", "0", "abc", "10.123", "nan"):
        with pytest.raises(InvalidClaim):
            sub(SMALL, bad)
    with pytest.raises(InvalidClaim):
        service.submit_dispute(order_id=SMALL, amount="10", reason_text="   ")
    with psycopg.connect(PG_ADMIN_DSN) as c:
        assert c.execute("SELECT count(*) FROM workflow.disputes WHERE reason_text = %s", (MARK,)).fetchone()[0] == 0


def test_t8_amount_over_paid_and_no_payment_are_rejected():
    assert sub(SMALL, "999").decision.rule_id == rules.R04      # paid 56.78
    assert sub(NO_PAYMENT, "10").decision.rule_id == rules.R04


def test_duplicate_open_dispute_is_rejected():
    first = sub(HIGH, "600")
    second = sub(HIGH, "600")
    assert first.status is S.PENDING_REVIEW
    assert (second.status, second.decision.rule_id) == (S.REJECTED, rules.R02)


def test_an_order_cannot_be_refunded_twice():
    gw = MockPaymentGateway()
    assert sub(SMALL, "50", gw).status is S.REFUND_EXECUTED       # paid 56.78, refunded 50
    again = sub(SMALL, "50", gw)                                  # only 6.78 left
    assert (again.status, again.decision.rule_id) == (S.REJECTED, rules.R04)
    assert "already refunded" in again.decision.reasoning
    assert gw.calls == 1


# ── T9: payment failures ─────────────────────────────────────────────────────

def test_t9_failed_refund_then_retry_succeeds_with_a_new_key_each_time():
    gw = MockPaymentGateway(fail_next=1)
    r = sub(SMALL, "50", gw)
    assert r.status is S.REFUND_FAILED
    assert service.retry_refund(r.dispute_id, gw) is S.REFUND_EXECUTED
    assert path(r.dispute_id) == ["submitted", "auto_approved", "refund_failed", "refund_executed"]
    assert attempts(r.dispute_id) == [(f"dispute-{r.dispute_id}-attempt-1", "failure"),
                                      (f"dispute-{r.dispute_id}-attempt-2", "success")]
    assert len(gw.refunded) == 1  # money moved once


def test_t9_repeated_failures_escalate_to_a_human():
    gw = MockPaymentGateway(fail_next=3)
    r = sub(SMALL, "50", gw)                                      # attempt 1 fails
    assert service.retry_refund(r.dispute_id, gw) is S.REFUND_FAILED   # attempt 2 fails
    assert service.retry_refund(r.dispute_id, gw) is S.REFUND_FAILED   # attempt 3 fails
    assert service.retry_refund(r.dispute_id, gw) is S.PENDING_REVIEW  # gives up, asks a human
    assert gw.calls == 3
    # status never looped, but all three failures are in the audit trail
    assert path(r.dispute_id).count("refund_failed") == 3
    with db.get_workflow_connection() as c:
        pending = [a for a in list_approvals(c) if a["dispute_id"] == r.dispute_id]
    assert len(pending) == 1
    assert service.decide(approval_id=pending[0]["id"], decision="approved", reviewer="alice", gateway=gw) \
        is S.REFUND_EXECUTED
    assert gw.refunded == [(SMALL, Decimal("50.00"))]


def test_gateway_exception_is_a_failed_attempt_not_a_lost_dispute():
    class Exploding:
        def refund(self, **kw):
            raise TimeoutError("provider timed out")
    r = sub(SMALL, "50", Exploding())
    assert r.status is S.REFUND_FAILED
    assert "timed out" in audit(r.dispute_id)[-1]["reasoning"]


def test_crash_after_payment_is_replayed_not_repeated():
    """Gateway paid, then the process died before COMMIT: the retry reuses the same key."""
    gw = MockPaymentGateway()
    r = sub(SMALL, "50", gw)
    key = f"dispute-{r.dispute_id}-attempt-1"
    replay = gw.refund(idempotency_key=key, order_id=SMALL, amount=Decimal("50"))
    assert replay.success and gw.calls == 1 and len(gw.refunded) == 1


def test_retry_only_from_refund_failed():
    r = sub(SMALL, "50")
    with pytest.raises(RefundNotAllowed):
        service.retry_refund(r.dispute_id)


# ── T10: concurrency ─────────────────────────────────────────────────────────

def _race(fn_a, fn_b):
    barrier, out = threading.Barrier(2), []

    def run(fn):
        try:
            barrier.wait(timeout=5)
            out.append(fn())
        except Exception as e:
            out.append(e)
    ts = [threading.Thread(target=run, args=(f,)) for f in (fn_a, fn_b)]
    [t.start() for t in ts]
    [t.join(timeout=20) for t in ts]
    return out


def test_t10_two_reviewers_at_once_pay_exactly_once():
    gw = MockPaymentGateway()
    r = sub(HIGH, "600", gw)
    out = _race(
        lambda: service.decide(approval_id=r.approval_id, decision="approved", reviewer="alice", gateway=gw),
        lambda: service.decide(approval_id=r.approval_id, decision="approved", reviewer="bob", gateway=gw),
    )
    assert sorted(type(o).__name__ for o in out) == ["ApprovalAlreadyDecided", "Status"]
    assert gw.calls == 1 and len(attempts(r.dispute_id)) == 1


def test_t10_two_simultaneous_submissions_for_one_order_make_one_review():
    out = _race(lambda: sub(HIGH, "600"), lambda: sub(HIGH, "600"))
    results = [o for o in out if not isinstance(o, Exception)]
    assert len(results) == 2, out
    assert sorted(r.status.value for r in results) == ["pending_review", "rejected"]
    assert sorted(r.decision.rule_id for r in results) == [rules.R02, rules.R07]
