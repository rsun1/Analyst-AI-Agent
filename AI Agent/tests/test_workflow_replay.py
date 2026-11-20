"""
Audit replay tests. The first half feeds hand-made audit rows to the pure functions (no
database): every kind of broken trail must be detected. The second half runs real
lifecycles and checks that the story and the audit check hold.
"""
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient

import api
import db
from api import app
from config import PG_ADMIN_DSN
from workflow import replay as rp
from workflow import service
from workflow.facts import fetch_order_facts
from workflow.llm_assist import NullAssist, Opinion
from workflow.models import ReasonCategory
from workflow.payments_mock import MockPaymentGateway
from workflow.store import DisputeNotFound

T0 = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
EVIDENCE = {"facts": {"status": "delivered", "paid_total": "53.54", "delivered_at": "2017-05-26T00:00:00",
                      "estimated_delivery_at": "2017-05-24T00:00:00", "review_score": 1,
                      "has_open_dispute": False, "refunds_last_90d": 0, "refunded_total": "0"},
            "claim": {"amount": "50.00"}}


def e(n, action, frm, to, *, actor_type="agent", actor_id="refund-agent-v1", rule=None, why="because", details=None):
    return dict(ts=T0 + timedelta(seconds=n), action=action, actor_type=actor_type, actor_id=actor_id,
                from_status=frm, to_status=to, rule_id=rule, reasoning=why, details=details or {})


def good_trail():
    return [
        e(0, "dispute_submitted", None, "submitted", actor_type="human", actor_id="customer",
          details={"order_id": "o1", "amount_requested": "50.00"}),
        e(1, "policy_auto_approved", "submitted", "auto_approved", rule="R09", details=EVIDENCE),
        e(2, "refund_succeeded", "auto_approved", "refund_executed", actor_type="system", actor_id="mock-payments"),
    ]


def problems(entries, **kw):
    return rp.verify_entries(entries, **kw)


# ── pure: a good trail is clean, and every defect is caught ──────────────────

def test_a_good_trail_has_no_problems():
    assert problems(good_trail(), current_status="refund_executed", successful_attempts=1) == []


def test_empty_trail():
    assert problems([]) == ["the audit trail is empty"]


def test_missing_first_entry():
    assert any("does not start" in p for p in problems(good_trail()[1:]))


def test_broken_continuity_is_caught():
    t = good_trail()
    del t[1]  # the decision row is gone
    assert any("previous entry ended in submitted" in p for p in problems(t))


def test_illegal_move_is_caught():
    t = good_trail()
    t[2] = e(2, "refund_succeeded", "auto_approved", "pending_review", actor_type="system")
    assert any("illegal move" in p for p in problems(t))


def test_unknown_no_change_event_is_caught():
    t = good_trail() + [e(3, "mystery", "refund_executed", "refund_executed")]
    assert any("not a known event" in p for p in problems(t))


def test_known_event_rows_are_fine():
    t = good_trail()[:2] + [
        e(2, "refund_failed", "auto_approved", "refund_failed", actor_type="system"),
        e(3, "refund_failed", "refund_failed", "refund_failed", actor_type="system"),
        e(4, "refund_succeeded", "refund_failed", "refund_executed", actor_type="system")]
    assert problems(t) == []


def test_decision_without_rule_or_evidence_is_caught():
    t = good_trail()
    t[1] = e(1, "policy_auto_approved", "submitted", "auto_approved")
    found = problems(t)
    assert any("without a rule id" in p for p in found) and any("evidence snapshot" in p for p in found)


def test_missing_reasoning_is_caught():
    t = good_trail()
    t[1]["reasoning"] = "  "
    assert any("no reasoning" in p for p in problems(t))


def test_timestamps_going_backwards_are_caught():
    t = good_trail()
    t[2]["ts"] = T0 - timedelta(hours=1)
    assert any("backwards" in p for p in problems(t))


def test_money_before_approval_is_caught():
    t = [good_trail()[0],
         e(1, "policy_rejected", "submitted", "rejected", rule="R05", details=EVIDENCE)]
    t += [e(2, "refund_succeeded", "rejected", "refund_executed", actor_type="system")]
    found = problems(t)
    assert any("before any approval" in p for p in found) and any("illegal move" in p for p in found)


def test_double_payment_is_caught():
    t = good_trail() + [e(3, "refund_succeeded", "refund_executed", "refund_executed", actor_type="system")]
    assert any("more than once" in p for p in problems(t))


def test_human_decision_must_name_a_human_in_pending_review():
    t = [good_trail()[0],
         e(1, "sent_to_human_review", "submitted", "pending_review", rule="R07", details=EVIDENCE),
         e(2, "human_approved", "pending_review", "approved", actor_type="agent", actor_id="")]
    assert any("without a named human" in p for p in problems(t))
    t[2] = e(2, "human_approved", "approved", "approved", actor_type="human", actor_id="alice")
    assert any("outside pending_review" in p for p in problems(t))


def test_trail_must_agree_with_the_dispute_and_the_attempts_table():
    assert any("but the dispute is" in p for p in problems(good_trail(), current_status="rejected"))
    assert any("refund_attempts has 0" in p for p in problems(good_trail(), successful_attempts=0))


def test_narrative_reads_like_a_story():
    text = rp.narrate(rp.build_steps(good_trail()))
    assert "policy_auto_approved: submitted -> auto_approved [R09]" in text
    assert "delivered 2017-05-26 vs estimate 2017-05-24, review score=1" in text
    s = rp.summarize(rp.build_steps(good_trail()))
    assert (s["order_id"], s["final_status"], s["refunded"], s["rules_fired"]) == ("o1", "refund_executed", True, ["R09"])


# ── real lifecycles ──────────────────────────────────────────────────────────

MARK = "__pytest_marker__"
SMALL = "000e63d38ae8c00bbcb5a30573b99628"      # 56.78 on time -> auto approve (R10)
LATE = "001d8f0e34a38c37f7dba2a37d4eba8b"       # 53.54 late, 1 star -> auto approve (R09)
HIGH = "0009c9a17f916a706d71784483a5d643"       # 650.34 -> human review (R07)
CANCELED = "00310b0c75bb13015ec4d82d341865a4"
HAPPY = "00010242fe8c5a6d1ba2dd792cb16214"
KEY = "test-key"
H = {"X-API-Key": KEY}


def _ready():
    try:
        with psycopg.connect(PG_ADMIN_DSN, connect_timeout=3) as c:
            return (c.execute("SELECT to_regclass('workflow.audit_log')").fetchone()[0] is not None
                    and c.execute("SELECT to_regclass('public.orders')").fetchone()[0] is not None)
    except Exception:
        return False


db_only = pytest.mark.skipif(not _ready(), reason="Postgres, Olist data or workflow schema not available")


def _wipe():
    with psycopg.connect(PG_ADMIN_DSN) as c:
        ids = "SELECT id FROM workflow.disputes WHERE reason_text = %s"
        for t in ("audit_log", "approvals", "refund_attempts"):
            c.execute(f"DELETE FROM workflow.{t} WHERE dispute_id IN ({ids})", (MARK,))
        c.execute("DELETE FROM workflow.disputes WHERE reason_text = %s", (MARK,))


@pytest.fixture
def clean(monkeypatch):
    monkeypatch.setattr(api, "API_KEY", KEY)
    _wipe()
    yield
    _wipe()


def sub(order_id, amount, gw, assist=None, category=ReasonCategory.OTHER):
    f = fetch_order_facts(order_id)[0]
    return service.submit_dispute(order_id=order_id, amount=amount, reason_text=MARK, gateway=gw,
                                  as_of=f.delivered_at + timedelta(days=5) if f.delivered_at else None,
                                  assist=assist or NullAssist(), reason_category=category)


def pending_approval(dispute_id):
    with psycopg.connect(PG_ADMIN_DSN) as c:
        return c.execute("SELECT id FROM workflow.approvals WHERE dispute_id = %s AND status = 'pending'",
                         (dispute_id,)).fetchone()[0]


@db_only
def test_auto_approved_refund_replays_cleanly(clean):
    r = sub(LATE, "50.00", MockPaymentGateway())
    rep = rp.replay_audit(r.dispute_id)
    assert rep.ok, rep.problems
    assert rep.summary["decision"]["rule_id"] == "R09_LATE_DELIVERY_LOW_SCORE"
    assert rep.summary["refunded"] and rep.summary["final_status"] == "refund_executed"
    assert "review score=1" in rep.narrative and "Refund of 50.00 paid" in rep.narrative


@db_only
def test_human_approved_refund_names_the_reviewer(clean):
    gw = MockPaymentGateway()
    r = sub(HIGH, "600", gw)
    service.decide(approval_id=r.approval_id, decision="approved", reviewer="alice",
                   comment="Carrier confirmed the damage", gateway=gw)
    rep = rp.replay_audit(r.dispute_id)
    assert rep.ok, rep.problems
    assert rep.summary["human_reviewers"] == ["alice"]
    assert "Carrier confirmed the damage" in rep.narrative
    assert "R07_HIGH_VALUE" in rep.summary["rules_fired"]


@db_only
def test_rejections_replay_cleanly(clean):
    gw = MockPaymentGateway()
    canceled = sub(CANCELED, "55", gw)
    rep = rp.replay_audit(canceled.dispute_id)
    assert rep.ok and rep.summary["final_status"] == "rejected" and not rep.summary["refunded"]
    assert "never completed" in rep.narrative
    held = sub(HIGH, "600", gw)
    service.decide(approval_id=held.approval_id, decision="rejected", reviewer="bob", gateway=gw)
    rep = rp.replay_audit(held.dispute_id)
    assert rep.ok and rep.summary["human_reviewers"] == ["bob"] and not rep.summary["refunded"]


@db_only
def test_failures_escalation_and_final_refund_replay_cleanly(clean):
    gw = MockPaymentGateway(fail_next=3)
    r = sub(SMALL, "50", gw)
    for _ in range(3):
        service.retry_refund(r.dispute_id, gw)
    service.decide(approval_id=pending_approval(r.dispute_id), decision="approved", reviewer="alice", gateway=gw)
    rep = rp.replay_audit(r.dispute_id)
    assert rep.ok, rep.problems
    assert rep.summary["refund_attempts_logged"] == 4          # 3 failures + 1 success
    assert "REFUND_RETRIES_EXHAUSTED" in rep.narrative and rep.summary["refunded"]


@db_only
def test_llm_escalation_shows_what_the_rules_alone_would_have_done(clean):
    class Worried(NullAssist):
        def second_opinion(self, *a):
            return Opinion(True, "says late but it arrived on time")
    r = sub(SMALL, "50", MockPaymentGateway(), assist=Worried())
    rep = rp.replay_audit(r.dispute_id)
    assert rep.ok, rep.problems
    assert "the rules alone said auto_approve" in rep.narrative and "R90_LLM_ESCALATION" in rep.narrative


@db_only
def test_tampering_is_detected(clean):
    r = sub(LATE, "50.00", MockPaymentGateway())
    assert rp.replay_audit(r.dispute_id).ok
    with psycopg.connect(PG_ADMIN_DSN) as c:      # only the admin role can do this; the app role cannot
        c.execute("DELETE FROM workflow.audit_log WHERE dispute_id = %s AND action = 'policy_auto_approved'",
                  (r.dispute_id,))
    rep = rp.replay_audit(r.dispute_id)
    assert not rep.ok and any("previous entry ended" in p for p in rep.problems)


@db_only
def test_status_drift_is_detected(clean):
    r = sub(SMALL, "50", MockPaymentGateway())
    with psycopg.connect(PG_ADMIN_DSN) as c:
        c.execute("UPDATE workflow.disputes SET status = 'rejected' WHERE id = %s", (r.dispute_id,))
    assert any("but the dispute is rejected" in p for p in rp.replay_audit(r.dispute_id).problems)


@db_only
def test_unknown_dispute(clean):
    with pytest.raises(DisputeNotFound):
        rp.replay_audit(2**60)


@db_only
def test_cli_prints_the_story_and_exits_zero(clean, capsys):
    r = sub(LATE, "50.00", MockPaymentGateway())
    assert rp.main([str(r.dispute_id)]) == 0
    out = capsys.readouterr().out
    assert "R09_LATE_DELIVERY_LOW_SCORE" in out and "Audit check: OK" in out


@db_only
def test_replay_endpoint(clean, monkeypatch):
    gw = MockPaymentGateway()
    monkeypatch.setattr(service, "default_gateway", lambda: gw)
    monkeypatch.setattr(service, "default_assist", lambda: NullAssist())
    client = TestClient(app)
    r = sub(LATE, "50.00", gw)
    body = client.get(f"/disputes/{r.dispute_id}/replay", headers=H).json()
    assert body["audit_ok"] is True and body["problems"] == []
    assert body["summary"]["decision"]["rule_id"] == "R09_LATE_DELIVERY_LOW_SCORE"
    assert "refund_succeeded" in body["narrative"]
    assert client.get("/disputes/999999999/replay", headers=H).status_code == 404
    assert client.get(f"/disputes/{r.dispute_id}/replay").status_code == 401
