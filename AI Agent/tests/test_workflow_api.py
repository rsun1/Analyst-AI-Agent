"""
Workflow API tests (FastAPI TestClient, real local Postgres + Olist data, mock gateway).
Skipped if the database or the workflow schema is not available.
"""
import psycopg
import pytest
from fastapi.testclient import TestClient

import api
from api import app
from config import PG_ADMIN_DSN
from workflow import service
from workflow.payments_mock import MockPaymentGateway

KEY = "test-key"
H = {"X-API-Key": KEY}
MARK = "__pytest_marker__"
client = TestClient(app)

HIGH = "0009c9a17f916a706d71784483a5d643"           # 650.34, delivered 2018-04-30 -> human review
SMALL = "000e63d38ae8c00bbcb5a30573b99628"          # 56.78, delivered 2018-03-27 -> auto approve
AS_OF = "2018-04-02T00:00:00"                       # inside the window for SMALL
AS_OF_HIGH = "2018-05-03T00:00:00"


def _ready():
    try:
        with psycopg.connect(PG_ADMIN_DSN, connect_timeout=3) as c:
            return (c.execute("SELECT to_regclass('workflow.audit_log')").fetchone()[0] is not None
                    and c.execute("SELECT to_regclass('public.orders')").fetchone()[0] is not None)
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="Postgres, Olist data or workflow schema not available")


def _wipe():
    with psycopg.connect(PG_ADMIN_DSN) as c:
        ids = "SELECT id FROM workflow.disputes WHERE reason_text = %s"
        for t in ("audit_log", "approvals", "refund_attempts"):
            c.execute(f"DELETE FROM workflow.{t} WHERE dispute_id IN ({ids})", (MARK,))
        c.execute("DELETE FROM workflow.disputes WHERE reason_text = %s", (MARK,))


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(api, "API_KEY", KEY)
    gw = MockPaymentGateway()
    monkeypatch.setattr(service, "default_gateway", lambda: gw)
    _wipe()
    yield gw
    _wipe()


def submit(order_id=SMALL, amount="50.00", as_of=AS_OF, **kw):
    body = {"order_id": order_id, "amount": amount, "reason_text": MARK, "as_of": as_of, **kw}
    return client.post("/disputes", json=body, headers=H)


# ── auth ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ("post", "/disputes"), ("get", "/disputes/1"), ("get", "/disputes/1/audit"),
    ("post", "/disputes/1/retry-refund"), ("get", "/approvals"), ("post", "/approvals/1/decision"),
])
def test_every_workflow_route_needs_the_api_key(method, path):
    assert getattr(client, method)(path).status_code == 401


def test_routes_are_in_the_openapi_schema():
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/disputes", "/disputes/{dispute_id}/audit", "/approvals",
            "/approvals/{approval_id}/decision"} <= set(paths)


# ── submit ───────────────────────────────────────────────────────────────────

def test_small_claim_is_approved_and_refunded(setup):
    r = submit()
    assert r.status_code == 201
    body = r.json()
    assert (body["status"], body["outcome"], body["rule_id"]) == ("refund_executed", "auto_approve", "R10_SMALL_AMOUNT")
    assert body["approval_id"] is None and setup.calls == 1


def test_large_claim_waits_for_review(setup):
    r = submit(HIGH, "600", as_of=AS_OF_HIGH)
    assert r.status_code == 201 and r.json()["status"] == "pending_review"
    assert setup.calls == 0
    d = client.get(f"/disputes/{r.json()['dispute_id']}", headers=H).json()
    assert d["status"] == "pending_review" and d["pending_approval_id"] == r.json()["approval_id"]
    assert d["review_reason_type"] == "policy"


def test_rejected_claim_is_a_201_with_the_reason():
    r = submit(amount="500")  # more than the 56.78 paid
    assert r.status_code == 201
    assert (r.json()["status"], r.json()["rule_id"]) == ("rejected", "R04_AMOUNT_NOT_COVERED")
    assert "exceeds" in r.json()["reasoning"]


@pytest.mark.parametrize("amount", ["-5", "0", "10.123"])
def test_invalid_amount_is_400(amount):
    assert submit(amount=amount).status_code == 400


def test_unknown_order_is_400():
    r = submit("does-not-exist")
    assert r.status_code == 400 and "does not exist" in r.json()["detail"]


def test_malformed_body_is_422():
    assert client.post("/disputes", json={"order_id": SMALL, "amount": "abc", "reason_text": "x"},
                       headers=H).status_code == 422
    assert client.post("/disputes", json={"order_id": SMALL}, headers=H).status_code == 422


# ── review queue and decisions ───────────────────────────────────────────────

def test_review_queue_and_approval_flow(setup):
    did = submit(HIGH, "600", as_of=AS_OF_HIGH).json()
    queue = client.get("/approvals", headers=H).json()
    assert did["approval_id"] in [a["id"] for a in queue]

    r = client.post(f"/approvals/{did['approval_id']}/decision", headers={**H, "X-Reviewer": "alice"},
                    json={"decision": "approved", "comment": "carrier confirmed"})
    assert r.status_code == 200 and r.json() == {"status": "refund_executed"}
    assert setup.calls == 1
    assert did["approval_id"] not in [a["id"] for a in client.get("/approvals", headers=H).json()]

    trail = client.get(f"/disputes/{did['dispute_id']}/audit", headers=H).json()
    assert [(a["from_status"], a["to_status"]) for a in trail] == [
        (None, "submitted"), ("submitted", "pending_review"),
        ("pending_review", "approved"), ("approved", "refund_executed")]
    human = trail[2]
    assert (human["actor_type"], human["actor_id"], human["reasoning"]) == ("human", "alice", "carrier confirmed")
    assert trail[1]["rule_id"] == "R07_HIGH_VALUE" and trail[1]["details"]["facts"]["status"] == "delivered"


def test_deciding_twice_is_409(setup):
    did = submit(HIGH, "600", as_of=AS_OF_HIGH).json()
    url = f"/approvals/{did['approval_id']}/decision"
    hdr = {**H, "X-Reviewer": "bob"}
    assert client.post(url, headers=hdr, json={"decision": "rejected"}).status_code == 200
    again = client.post(url, headers=hdr, json={"decision": "approved"})
    assert again.status_code == 409 and setup.calls == 0


def test_decision_needs_a_reviewer_header():
    did = submit(HIGH, "600", as_of=AS_OF_HIGH).json()
    r = client.post(f"/approvals/{did['approval_id']}/decision", headers=H, json={"decision": "approved"})
    assert r.status_code == 400


def test_bad_decision_value_is_422():
    r = client.post("/approvals/1/decision", headers={**H, "X-Reviewer": "a"}, json={"decision": "maybe"})
    assert r.status_code == 422


def test_unknown_ids_are_404():
    assert client.get("/disputes/999999999", headers=H).status_code == 404
    assert client.get("/disputes/999999999/audit", headers=H).status_code == 404
    assert client.post("/approvals/999999999/decision", headers={**H, "X-Reviewer": "a"},
                       json={"decision": "approved"}).status_code == 404
    assert client.post("/disputes/999999999/retry-refund", headers=H).status_code == 404


# ── refund retries ───────────────────────────────────────────────────────────

def test_failed_refund_can_be_retried_over_http(setup):
    setup.fail_next = 1
    did = submit().json()
    assert did["status"] == "refund_failed"
    r = client.post(f"/disputes/{did['dispute_id']}/retry-refund", headers=H)
    assert r.status_code == 200 and r.json() == {"status": "refund_executed"}
    assert len(setup.refunded) == 1


def test_retry_on_a_dispute_that_is_not_failed_is_409():
    did = submit().json()  # already refunded
    assert client.post(f"/disputes/{did['dispute_id']}/retry-refund", headers=H).status_code == 409
