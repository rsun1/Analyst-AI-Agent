"""
API tests. Agent steps that call OpenAI are mocked; the integration tests at the
bottom hit the real local Postgres (no OpenAI) and are skipped if it is down.
"""
import datetime
import decimal

import pytest
from fastapi.testclient import TestClient

import api
from api import app, validate_read_only_sql

KEY = "test-key"
H = {"X-API-Key": KEY}


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(api, "API_KEY", KEY)
    # Keep tests from writing to the real logs.
    monkeypatch.setattr(api, "log_query_event", lambda **kw: None)
    monkeypatch.setattr(api, "log_feedback_event", lambda *a, **k: None)


client = TestClient(app)


# -- auth ---------------------------------------------------------------------

def test_missing_key_is_401():
    assert client.post("/ask", json={"question": "hi"}).status_code == 401


def test_wrong_key_is_401():
    assert client.post("/ask", json={"question": "hi"}, headers={"X-API-Key": "nope"}).status_code == 401


def test_unconfigured_server_fails_closed(monkeypatch):
    monkeypatch.setattr(api, "API_KEY", None)
    assert client.post("/ask", json={"question": "hi"}, headers=H).status_code == 503


def test_health_needs_no_key():
    # Hits the real DB; if it is down this is 503, which is itself the correct behaviour.
    assert client.get("/health").status_code in (200, 503)


# -- SQL validation -----------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select 1;",
    "  WITH x AS (SELECT 1) SELECT * FROM x",
    "-- a comment\nSELECT 1",
    "/* c */ SELECT 1",
])
def test_validator_accepts(sql):
    validate_read_only_sql(sql)


@pytest.mark.parametrize("sql", [
    "",
    "   ",
    "DELETE FROM orders",
    "DROP TABLE orders",
    "SELECT 1; DROP TABLE orders",
    "UPDATE orders SET order_status = 'x'",
    "SELECT 1;;",
    "x" * (api.MAX_SQL_CHARS + 1),
])
def test_validator_rejects(sql):
    with pytest.raises(ValueError):
        validate_read_only_sql(sql)


def test_execute_rejects_write_with_400():
    r = client.post("/execute", json={"question": "q", "sql": "DELETE FROM orders"}, headers=H)
    assert r.status_code == 400


# -- /ask and /revise (mocked agent steps) ------------------------------------

def test_ask_returns_proposal(monkeypatch):
    seen = {}
    monkeypatch.setattr(api.agent_core, "step_rag", lambda q: ["chunk"])

    def gen(q, ctx, hist):
        seen["hist"] = hist
        return "SELECT 1", "because"
    monkeypatch.setattr(api.agent_core, "step_generate", gen)
    r = client.post("/ask", json={"question": "q", "history": [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "answer", "sql": "SELECT 2"},
    ]}, headers=H)
    assert r.status_code == 200 and r.json() == {"sql": "SELECT 1", "explanation": "because"}
    assert seen["hist"][1] == {"role": "assistant", "content": "answer", "sql": "SELECT 2"}


def test_ask_empty_sql_is_502(monkeypatch):
    monkeypatch.setattr(api.agent_core, "step_rag", lambda q: [])
    monkeypatch.setattr(api.agent_core, "step_generate", lambda *a: ("", ""))
    assert client.post("/ask", json={"question": "q"}, headers=H).status_code == 502


def test_ask_validates_input():
    assert client.post("/ask", json={"question": ""}, headers=H).status_code == 422
    too_long = [{"role": "user", "content": "x"}] * 13
    assert client.post("/ask", json={"question": "q", "history": too_long}, headers=H).status_code == 422


def test_revise_unclear(monkeypatch):
    monkeypatch.setattr(api.agent_core, "step_rag", lambda q: [])
    monkeypatch.setattr(api.agent_core, "step_revise", lambda **kw: (api.UNCLEAR_INSTRUCTION, ""))
    r = client.post("/revise", json={"question": "q", "sql": "SELECT 1", "explanation": "e",
                                      "instruction": "make it better"}, headers=H)
    assert r.status_code == 200
    assert r.json() == {"unclear": True, "sql": "SELECT 1", "explanation": "e"}


def test_revise_ok(monkeypatch):
    monkeypatch.setattr(api.agent_core, "step_rag", lambda q: [])
    monkeypatch.setattr(api.agent_core, "step_revise", lambda **kw: ("SELECT 2", "weekly"))
    r = client.post("/revise", json={"question": "q", "sql": "SELECT 1", "instruction": "weekly"}, headers=H)
    assert r.json() == {"unclear": False, "sql": "SELECT 2", "explanation": "weekly"}


# -- /execute (mocked) --------------------------------------------------------

def test_execute_serialises_rows_truncates_and_explains(monkeypatch):
    rows = [(decimal.Decimal("1.50"), datetime.datetime(2017, 7, 31, 12, 0), "x")] * 5
    monkeypatch.setattr(api.agent_core, "step_execute", lambda sql: (["a", "b", "c"], rows, ["fixed once"]))
    monkeypatch.setattr(api.agent_core, "step_explain", lambda *a: "summary")
    monkeypatch.setattr(api, "API_MAX_RESPONSE_ROWS", 2)
    r = client.post("/execute", json={"question": "q", "sql": "SELECT 1"}, headers=H)
    body = r.json()
    assert r.status_code == 200
    assert body["row_count"] == 5 and len(body["rows"]) == 2 and body["truncated"] is True
    assert body["rows"][0] == [1.5, "2017-07-31T12:00:00", "x"]
    assert body["db_errors"] == ["fixed once"] and body["insights"] == "summary"


def test_execute_survives_insights_failure(monkeypatch):
    monkeypatch.setattr(api.agent_core, "step_execute", lambda sql: (["a"], [(1,)], []))

    def boom(*a):
        raise RuntimeError("openai down")
    monkeypatch.setattr(api.agent_core, "step_explain", boom)
    body = client.post("/execute", json={"question": "q", "sql": "SELECT 1"}, headers=H).json()
    assert body["rows"] == [[1]] and body["insights"] is None and "openai down" in body["insights_error"]


def test_execute_can_skip_insights(monkeypatch):
    monkeypatch.setattr(api.agent_core, "step_execute", lambda sql: (["a"], [(1,)], []))
    monkeypatch.setattr(api.agent_core, "step_explain", lambda *a: pytest.fail("should not be called"))
    r = client.post("/execute", json={"question": "q", "sql": "SELECT 1", "include_insights": False}, headers=H)
    assert r.status_code == 200 and r.json()["insights"] is None


def test_feedback_is_204_and_logged(monkeypatch):
    got = []
    monkeypatch.setattr(api, "log_feedback_event", lambda *a: got.append(a))
    r = client.post("/feedback", json={"question": "q", "sql": "SELECT 1", "rating": "down", "comment": "wrong"}, headers=H)
    assert r.status_code == 204 and got == [("q", "SELECT 1", "down", "wrong")]
    assert client.post("/feedback", json={"question": "q", "sql": "s", "rating": "meh"}, headers=H).status_code == 422


# -- integration: real Postgres, no OpenAI ------------------------------------

def _db_up():
    try:
        from db import get_connection
        with get_connection() as c:
            c.execute("SELECT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_up(), reason="local Postgres not running")


@needs_db
def test_execute_real_query():
    r = client.post("/execute", headers=H, json={
        "question": "delivered orders", "include_insights": False,
        "sql": "SELECT COUNT(*) AS n FROM orders WHERE order_status = 'delivered' "
               "AND order_delivered_customer_date IS NOT NULL"})
    assert r.status_code == 200 and r.json()["rows"] == [[96470]]


@needs_db
def test_database_blocks_write_that_passes_the_validator():
    """A data-modifying CTE starts with WITH, so the API-layer check lets it through;
    the SELECT-only database role is what stops it."""
    sql = "WITH d AS (DELETE FROM orders WHERE false RETURNING *) SELECT * FROM d"
    validate_read_only_sql(sql)  # passes the first guard on purpose
    r = client.post("/execute", headers=H, json={"question": "q", "sql": sql, "include_insights": False})
    assert r.status_code == 403


@needs_db
def test_health_ok_with_db():
    assert client.get("/health").json() == {"status": "ok"}
