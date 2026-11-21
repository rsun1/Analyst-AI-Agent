"""
Streamlit page for the refund / dispute workflow: submit a dispute, work the human review
queue, and read the decision trail. Everything goes through the API (api_client); the page
holds no business logic, so what you see is exactly what the API enforces.

Open with  ?page=disputes
"""
from __future__ import annotations

import streamlit as st

import api_client

# Real Olist orders (see docs/refund-policy.md). as_of is a few days after delivery so the order
# is inside the 30-day refund window; the Olist data is from 2016-2018, hence a simulated date.
SAMPLES = {
    "Late delivery, 1 star, small amount -> auto-approved and refunded":
        ("001d8f0e34a38c37f7dba2a37d4eba8b", "50.00", "2017-05-31", "The package arrived late."),
    "Small amount, on time -> auto-approved (R10)":
        ("000e63d38ae8c00bbcb5a30573b99628", "50.00", "2018-04-01", "Item not as described."),
    "High value (650 BRL) -> needs a human":
        ("0009c9a17f916a706d71784483a5d643", "600.00", "2018-05-05", "The item arrived damaged."),
    "Mid value (260 BRL) -> needs a human by default":
        ("00018f77f2f0320c557190d7a144bdd3", "200.00", "2017-05-17", "Wrong colour."),
    "Order was canceled -> rejected":
        ("00310b0c75bb13015ec4d82d341865a4", "55.00", "2018-08-30", "I want my money back."),
    "Outside the 30-day window -> rejected":
        ("000e562887b1f2006d75e0be9558292e", "40.00", "2018-06-01", "Changed my mind."),
    "More than was paid -> rejected":
        ("000e63d38ae8c00bbcb5a30573b99628", "500.00", "2018-04-01", "Refund everything."),
}
STATUS_ICON = {"refund_executed": "✅", "auto_approved": "✅", "approved": "✅", "pending_review": "🕒",
               "rejected": "⛔", "refund_failed": "⚠️", "submitted": "•"}


def disputes_enabled() -> bool:
    """Read at call time so a test (or a restart with new env) takes effect."""
    import config
    return config.ENABLE_DISPUTES_PAGE


def _status(s: str) -> str:
    return f"{STATUS_ICON.get(s, '•')} {s}"


def _load_sample() -> None:
    order_id, amount, as_of, reason = SAMPLES[st.session_state["dispute_sample"]]
    st.session_state.update(d_order=order_id, d_amount=amount, d_asof=as_of, d_reason=reason)


def _show_trail(dispute_id: int) -> None:
    """Story + consistency badge + the raw timeline, all from the API."""
    try:
        rep = api_client.dispute_replay(dispute_id)
        d = api_client.get_dispute(dispute_id)
        audit = api_client.dispute_audit(dispute_id)
    except api_client.ApiError as e:
        st.error(str(e))
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Status", _status(d["status"]))
    c2.metric("Amount requested", f"{float(d['amount_requested']):,.2f}")
    c3.metric("Audit check", "✅ consistent" if rep["audit_ok"] else "❌ problems")
    for p in rep["problems"]:
        st.error(p)
    s = rep["summary"]
    if s.get("decision"):
        st.info(f"**Decision:** `{s['decision']['rule_id']}`. {s['decision']['reason']}")
    st.markdown("**Decision trail** (rebuilt from the audit log only)")
    st.code(rep["narrative"], language="text")
    with st.expander("Raw audit rows"):
        st.dataframe(
            [{"time": a["ts"][:19].replace("T", " "), "actor": f"{a['actor_type']}:{a['actor_id']}",
              "action": a["action"], "from": a["from_status"], "to": a["to_status"], "rule": a["rule_id"]}
             for a in audit], use_container_width=True, hide_index=True)
    if d["status"] == "refund_failed":
        st.warning("The refund failed. After 3 failed attempts it escalates to a human.")
        if st.button("Retry the refund", key=f"retry_{dispute_id}"):
            try:
                st.success(f"Now: {api_client.retry_refund(dispute_id)['status']}")
                st.rerun()
            except api_client.ApiError as e:
                st.error(str(e))


def _tab_submit() -> None:
    st.caption("Pick a sample or fill in your own. The agent applies the written policy; "
               "anything it is unsure about goes to a human.")
    st.selectbox("Sample cases", list(SAMPLES), key="dispute_sample", index=None,
                 placeholder="Choose a sample to fill the form", on_change=_load_sample)
    with st.form("submit_dispute"):
        st.text_input("Order ID", key="d_order")
        c1, c2 = st.columns(2)
        c1.text_input("Amount to refund (BRL)", key="d_amount")
        c2.text_input("Simulated date (YYYY-MM-DD)", key="d_asof",
                      help="Olist orders are from 2016-2018. Leave empty to use today's date "
                           "(older orders will then be outside the refund window).")
        st.text_area("Reason", key="d_reason")
        go = st.form_submit_button("Submit dispute", type="primary")
    if go:
        as_of = st.session_state.get("d_asof", "").strip()
        try:
            r = api_client.submit_dispute(st.session_state.get("d_order", "").strip(),
                                          st.session_state.get("d_amount", "").strip(),
                                          st.session_state.get("d_reason", "").strip(),
                                          f"{as_of}T00:00:00" if as_of else None)
        except api_client.ApiError as e:
            st.error(str(e))
            return
        st.session_state.setdefault("d_mine", []).insert(0, r["dispute_id"])
        st.session_state["d_last"] = r
    r = st.session_state.get("d_last")
    if r:
        box = {"reject": st.error, "needs_review": st.warning, "auto_approve": st.success}[r["outcome"]]
        box(f"Dispute **#{r['dispute_id']}**: {_status(r['status'])} (rule `{r['rule_id']}`)\n\n{r['reasoning']}")
        if r["approval_id"]:
            st.caption(f"Waiting for a human in the **Review queue** (approval #{r['approval_id']}).")


def _tab_queue() -> None:
    reviewer = st.text_input("Your name (recorded in the audit trail)", key="d_reviewer")
    if st.button("Refresh queue"):
        st.rerun()
    try:
        queue = api_client.pending_approvals()
    except api_client.ApiError as e:
        st.error(str(e))
        return
    if not queue:
        st.success("Nothing is waiting for review.")
        return
    for a in queue:
        try:
            d = api_client.get_dispute(a["dispute_id"])
            audit = api_client.dispute_audit(a["dispute_id"])
        except api_client.ApiError as e:
            st.error(str(e))
            continue
        flag = "agent could not judge" if d["review_reason_type"] == "agent_uncertain" else "policy requires a human"
        with st.expander(f"#{d['id']}  ·  order {d['order_id'][:8]}…  ·  {float(d['amount_requested']):,.2f} BRL  ·  {flag}",
                         expanded=True):
            why = next((x for x in audit if x["action"] == "sent_to_human_review"), None)
            if why:
                st.markdown(f"**Why it is here:** `{why['rule_id']}`. {why['reasoning']}")
            brief = next((x for x in audit if x["action"] == "reviewer_brief_added"), None)
            if brief:
                st.markdown(f"**Brief:** {brief['reasoning']}")
            st.markdown(f"**Customer said:** {d['reason_text']}")
            comment = st.text_input("Comment", key=f"c_{a['id']}")
            b1, b2, _ = st.columns([1, 1, 3])
            for col, decision, label, kind in ((b1, "approved", "Approve & refund", "primary"),
                                               (b2, "rejected", "Reject", "secondary")):
                if col.button(label, key=f"{decision}_{a['id']}", type=kind):
                    if not reviewer.strip():
                        st.error("Enter your name first.")
                        continue
                    try:
                        res = api_client.decide_approval(a["id"], decision, reviewer.strip(), comment)
                        st.session_state["d_flash"] = f"Dispute #{d['id']} is now {res['status']}."
                        st.rerun()
                    except api_client.ApiError as e:
                        st.error(str(e))
    if flash := st.session_state.pop("d_flash", None):
        st.success(flash)


def _tab_trail() -> None:
    mine = st.session_state.get("d_mine", [])
    c1, c2 = st.columns([1, 2])
    pick = c2.selectbox("Disputes submitted in this session", mine, index=None, placeholder="none yet")
    typed = c1.number_input("Or a dispute id", min_value=0, step=1, value=0)
    dispute_id = pick or (int(typed) if typed else None)
    if dispute_id:
        _show_trail(dispute_id)


def render_disputes() -> None:
    st.title("🧾 Dispute desk")
    st.caption("Refund and dispute workflow: a written policy decides, a human handles the doubtful cases, "
               "every step is in an append-only audit trail.")
    submit, queue, trail = st.tabs(["Submit a dispute", "Review queue", "Decision trail"])
    with submit:
        _tab_submit()
    with queue:
        _tab_queue()
    with trail:
        _tab_trail()
