"""
UI tests for the dispute desk, using Streamlit's AppTest with a fake API client (no server,
no database). They check what the page sends to the API and what it shows back.
"""
import pytest
from streamlit.testing.v1 import AppTest

import api_client
import disputes_page


def script():
    import disputes_page as page
    page.render_disputes()


@pytest.fixture
def backend(monkeypatch):
    calls = {"submit": [], "decide": [], "retry": []}
    state = {"queue": [{"id": 7, "dispute_id": 42, "status": "pending"}], "status": "refund_failed"}
    dispute = {"id": 42, "order_id": "0009c9a17f916a706d71784483a5d643", "customer_id": "c", "amount_requested": "600.00",
               "reason_text": "The item arrived damaged.", "review_reason_type": "policy", "status": "pending_review"}
    audit = [
        {"ts": "2026-11-20T10:00:00+00:00", "actor_type": "agent", "actor_id": "refund-agent-v1", "action": "sent_to_human_review",
         "from_status": "submitted", "to_status": "pending_review", "rule_id": "R07_HIGH_VALUE",
         "reasoning": "Requested 600 is at or above the high-value limit (450).", "details": {}},
        {"ts": "2026-11-20T10:00:01+00:00", "actor_type": "agent", "actor_id": "llm-assist", "action": "reviewer_brief_added",
         "from_status": "pending_review", "to_status": "pending_review", "rule_id": None,
         "reasoning": "Check the carrier scan.", "details": {}}]

    def submit(order_id, amount, reason_text, as_of=None):
        calls["submit"].append((order_id, amount, reason_text, as_of))
        if order_id == "bad":
            raise api_client.ApiError("Order 'bad' does not exist.", 400)
        return {"dispute_id": 42, "status": "pending_review", "outcome": "needs_review", "rule_id": "R07_HIGH_VALUE",
                "reasoning": "Requested 600 is at or above the high-value limit (450).", "approval_id": 7}

    def decide(approval_id, decision, reviewer, comment=None):
        calls["decide"].append((approval_id, decision, reviewer, comment))
        state["queue"] = []
        return {"status": "refund_executed" if decision == "approved" else "rejected"}

    monkeypatch.setattr(api_client, "submit_dispute", submit)
    monkeypatch.setattr(api_client, "pending_approvals", lambda: state["queue"])
    monkeypatch.setattr(api_client, "get_dispute", lambda i: {**dispute, "status": state["status"]})
    monkeypatch.setattr(api_client, "dispute_audit", lambda i: audit)
    monkeypatch.setattr(api_client, "decide_approval", decide)
    monkeypatch.setattr(api_client, "retry_refund", lambda i: calls["retry"].append(i) or {"status": "refund_executed"})
    monkeypatch.setattr(api_client, "dispute_replay", lambda i: {
        "dispute_id": i, "audit_ok": True, "problems": [], "narrative": "STORY-TEXT",
        "summary": {"decision": {"rule_id": "R07_HIGH_VALUE", "reason": "big"}}})
    return calls, state


def app() -> AppTest:
    return AppTest.from_function(script, default_timeout=10).run()


def btn(at, label):
    return next(b for b in at.button if b.label == label)


def test_renders_three_tabs_without_errors(backend):
    at = app()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Submit a dispute", "Review queue", "Decision trail"]


def test_a_sample_fills_the_form_and_submit_sends_it_to_the_api(backend):
    calls, _ = backend
    at = app()
    label = "High value (650 BRL) -> needs a human"
    at.selectbox(key="dispute_sample").select(label).run()
    order, amount, as_of, reason = disputes_page.SAMPLES[label]
    assert (at.session_state["d_order"], at.session_state["d_amount"], at.session_state["d_asof"]) == (order, amount, as_of)
    btn(at, "Submit dispute").click().run()
    assert calls["submit"] == [(order, amount, reason, f"{as_of}T00:00:00")]
    assert any("#42" in w.value and "R07_HIGH_VALUE" in w.value for w in at.warning)  # needs_review is a warning box
    assert at.session_state["d_mine"] == [42]


def test_api_errors_are_shown_not_raised(backend):
    at = app()
    at.text_input(key="d_order").set_value("bad")
    at.text_input(key="d_amount").set_value("10")
    at.text_area(key="d_reason").set_value("x")
    btn(at, "Submit dispute").click().run()
    assert not at.exception
    assert any("does not exist" in e.value for e in at.error)


def test_the_dispute_desk_is_off_by_default(monkeypatch):
    """Evaluate the same expression config.py uses, with the variable unset."""
    import config
    monkeypatch.delenv("ENABLE_DISPUTES_PAGE", raising=False)
    src = open(config.__file__, encoding="utf-8").read()
    assert 'os.getenv("ENABLE_DISPUTES_PAGE", "0")' in src          # the default is "0"
    assert config.os.getenv("ENABLE_DISPUTES_PAGE", "0").lower() not in ("1", "true", "yes")


def _full_app(monkeypatch, enabled: bool, page: str | None):
    import config
    monkeypatch.setattr(config, "ENABLE_DISPUTES_PAGE", enabled)
    monkeypatch.setattr(api_client, "schema_status", lambda: {"live_tables": 11, "drift_warnings": []})
    at = AppTest.from_file("app.py", default_timeout=20)
    if page:
        at.query_params["page"] = page
    return at.run()


def test_switched_off_there_is_no_sidebar_button_and_the_page_says_so(monkeypatch):
    at = _full_app(monkeypatch, False, None)
    assert not at.exception and not any("Dispute desk" in b.label for b in at.sidebar.button)
    at = _full_app(monkeypatch, False, "disputes")
    assert not at.exception
    assert any("switched off" in i.value for i in at.info) and not at.tabs   # no queue, no approve buttons


def test_switched_on_the_button_and_the_page_appear(monkeypatch, backend):
    assert any("Dispute desk" in b.label for b in _full_app(monkeypatch, True, None).sidebar.button)
    assert [t.label for t in _full_app(monkeypatch, True, "disputes").tabs] == \
        ["Submit a dispute", "Review queue", "Decision trail"]


def test_every_sample_uses_a_real_looking_order_and_a_parsable_date():
    import datetime
    for order_id, amount, as_of, reason in disputes_page.SAMPLES.values():
        assert len(order_id) == 32 and float(amount) > 0 and reason
        datetime.date.fromisoformat(as_of)


def test_queue_shows_why_the_dispute_is_there_and_the_brief(backend):
    at = app()
    text = " ".join(m.value for m in at.markdown)
    assert "R07_HIGH_VALUE" in text and "Check the carrier scan." in text and "The item arrived damaged." in text


def test_reviewer_must_give_a_name_before_deciding(backend):
    calls, _ = backend
    at = app()
    btn(at, "Approve & refund").click().run()
    assert calls["decide"] == [] and any("Enter your name" in e.value for e in at.error)


def test_approving_sends_the_reviewer_and_comment(backend):
    calls, _ = backend
    at = app()
    at.text_input(key="d_reviewer").set_value("alice")
    at.text_input(key="c_7").set_value("Carrier confirmed")
    btn(at, "Approve & refund").click().run()
    assert calls["decide"] == [(7, "approved", "alice", "Carrier confirmed")]
    assert not at.exception


def test_empty_queue_message(backend):
    _, state = backend
    state["queue"] = []
    at = app()
    assert any("Nothing is waiting" in s.value for s in at.success)


def test_trail_tab_shows_story_and_offers_a_retry_for_failed_refunds(backend):
    calls, state = backend
    at = app()
    at.session_state["d_mine"] = [42]
    at.run()
    at.selectbox[-1].select(42).run()
    assert any("STORY-TEXT" in c.value for c in at.code)
    assert any("refund failed" in w.value.lower() for w in at.warning)
    btn(at, "Retry the refund").click().run()
    assert calls["retry"] == [42]
