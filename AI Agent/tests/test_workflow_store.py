"""
Store tests: atomic transitions against the real local Postgres (skipped if the
`workflow` schema is not set up: python -m setup_workflow_db).
Test rows use a marker order_id and are deleted afterwards.
"""
import threading

import psycopg
import pytest

import db
from config import PG_ADMIN_DSN
from workflow.states import InvalidTransition, Status
from workflow.store import (
    DisputeNotFound, create_dispute, get_dispute, list_audit, transition,
)

MARK = "__pytest_marker__"
S = Status


def _ready():
    try:
        with psycopg.connect(PG_ADMIN_DSN, connect_timeout=3) as c:
            return c.execute("SELECT to_regclass('workflow.audit_log')").fetchone()[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="Postgres or workflow schema not available")


@pytest.fixture(autouse=True)
def cleanup():
    yield
    with psycopg.connect(PG_ADMIN_DSN) as c:
        ids = "SELECT id FROM workflow.disputes WHERE order_id = %s"
        c.execute(f"DELETE FROM workflow.audit_log WHERE dispute_id IN ({ids})", (MARK,))
        c.execute(f"DELETE FROM workflow.approvals WHERE dispute_id IN ({ids})", (MARK,))
        c.execute("DELETE FROM workflow.disputes WHERE order_id = %s", (MARK,))


def _new(conn) -> int:
    return create_dispute(conn, order_id=MARK, customer_id="c1", reason_text="late", amount_requested=50)


def _t(conn, did, to, **kw):
    kw.setdefault("action", "test")
    kw.setdefault("actor_type", "agent")
    kw.setdefault("actor_id", "pytest")
    kw.setdefault("reasoning", "because")
    return transition(conn, did, to, **kw)


def test_create_writes_dispute_and_first_audit():
    with db.get_workflow_connection() as conn:
        did = _new(conn)
    with db.get_workflow_connection() as conn:
        assert get_dispute(conn, did)["status"] == "submitted"
        audit = list_audit(conn, did)
    assert [(a["from_status"], a["to_status"]) for a in audit] == [(None, "submitted")]


def test_legal_transition_updates_status_and_appends_audit():
    with db.get_workflow_connection() as conn:
        did = _new(conn)
        res = _t(conn, did, S.AUTO_APPROVED, rule_id="R10_SMALL_AMOUNT", details={"k": 1})
    assert (res.from_status, res.to_status) == (S.SUBMITTED, S.AUTO_APPROVED)
    with db.get_workflow_connection() as conn:
        assert get_dispute(conn, did)["status"] == "auto_approved"
        last = list_audit(conn, did)[-1]
    assert (last["from_status"], last["to_status"], last["rule_id"]) == (
        "submitted", "auto_approved", "R10_SMALL_AMOUNT")
    assert last["details"] == {"k": 1}


def test_illegal_transition_raises_and_changes_nothing():
    with db.get_workflow_connection() as conn:
        did = _new(conn)
    with pytest.raises(InvalidTransition):
        with db.get_workflow_connection() as conn:
            _t(conn, did, S.REFUND_EXECUTED)  # submitted -> refund_executed is illegal
    with db.get_workflow_connection() as conn:
        assert get_dispute(conn, did)["status"] == "submitted"
        assert len(list_audit(conn, did)) == 1  # only the creation record


def test_failure_after_transition_rolls_back_status_and_audit():
    with db.get_workflow_connection() as conn:
        did = _new(conn)
    with pytest.raises(RuntimeError):
        with db.get_workflow_connection() as conn:
            _t(conn, did, S.REJECTED)
            raise RuntimeError("boom after the transition")
    with db.get_workflow_connection() as conn:
        assert get_dispute(conn, did)["status"] == "submitted"
        assert len(list_audit(conn, did)) == 1


def test_terminal_state_cannot_be_left():
    with db.get_workflow_connection() as conn:
        did = _new(conn)
        _t(conn, did, S.REJECTED)
    with pytest.raises(InvalidTransition):
        with db.get_workflow_connection() as conn:
            _t(conn, did, S.APPROVED)


def test_pending_review_requires_a_reason_type():
    with db.get_workflow_connection() as conn:
        did = _new(conn)
        with pytest.raises(ValueError):
            _t(conn, did, S.PENDING_REVIEW)
        _t(conn, did, S.PENDING_REVIEW, review_reason_type="policy")
        assert get_dispute(conn, did)["review_reason_type"] == "policy"


def test_unknown_dispute():
    with pytest.raises(DisputeNotFound):
        with db.get_workflow_connection() as conn:
            _t(conn, 2**60, S.REJECTED)


def test_concurrent_decisions_only_one_wins():
    """Two reviewers act on the same pending dispute at the same moment."""
    with db.get_workflow_connection() as conn:
        did = _new(conn)
        _t(conn, did, S.PENDING_REVIEW, review_reason_type="policy")

    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def reviewer(name: str, target: Status):
        try:
            with db.get_workflow_connection() as conn:
                barrier.wait(timeout=5)
                _t(conn, did, target, actor_type="human", actor_id=name)
            outcomes.append("ok")
        except InvalidTransition:
            outcomes.append("invalid")

    threads = [threading.Thread(target=reviewer, args=("alice", S.APPROVED)),
               threading.Thread(target=reviewer, args=("bob", S.REJECTED))]
    [t.start() for t in threads]
    [t.join(timeout=15) for t in threads]

    assert sorted(outcomes) == ["invalid", "ok"]
    with db.get_workflow_connection() as conn:
        humans = [a for a in list_audit(conn, did)
                  if a["actor_type"] == "human" and a["from_status"] == "pending_review"]
        assert len(humans) == 1  # exactly one reviewer decision was recorded
        assert get_dispute(conn, did)["status"] == humans[0]["to_status"]
