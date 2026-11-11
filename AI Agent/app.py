"""
Streamlit chat interface for the Analytics AI Agent.
Replaces the CLI (agent.py) with a conversational front end.

Features:
  - Multi-turn conversation memory (follow-up questions work naturally)
  - Auto chart generation after each query result
  - Multi-query analysis for broad business questions
  - Append-only message log that survives Streamlit reruns
  - Query/correction logging happens in the API (Postgres app_logs schema)
"""
import streamlit as st

import api_client
from chart import detect_chart
from conversation import append_assistant_turn, append_user_turn

# ── Page config ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Olist Data Analyst AI",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom styling ─────────────────────────────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

/* ── Global font & reset ── */
html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
}

/* ── Restore Material icon fonts (global override breaks them) ── */
.material-symbols-rounded,
.material-symbols-outlined,
.material-icons,
span[class*="material-symbols"],
span[class*="material-icons"] {
    font-family: 'Material Symbols Rounded', 'Material Symbols Outlined', 'Material Icons' !important;
}

/* ── Hide Streamlit chrome ── */
#MainMenu { visibility: hidden; }
footer    { visibility: hidden; }

/* ── Main container ── */
.main .block-container {
    padding-top: 0.5rem !important;
    padding-bottom: 3rem !important;
    max-width: 880px !important;
}

/* ── Sidebar ── */
section[data-testid="stSidebar"] {
    border-right: 1px solid #D0D5ED !important;
}
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] .stMarkdown h1 {
    font-size: 1.1rem !important;
    font-weight: 700 !important;
    color: #4F6EF7 !important;
    letter-spacing: -0.01em !important;
}

/* ── Chat message bubbles ── */
div[data-testid="stChatMessage"] {
    border-radius: 14px !important;
    padding: 1rem 1.25rem !important;
    margin-bottom: 0.625rem !important;
    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06) !important;
    border: 1px solid rgba(208, 213, 237, 0.6) !important;
    background: #ffffff !important;
}

/* ── Code blocks ── */
pre, code {
    border-radius: 10px !important;
}
.stCode > div, [data-testid="stCode"] > div {
    background-color: #1a1d2e !important;
    border-radius: 10px !important;
}
[data-testid="stCode"] pre {
    background-color: #1a1d2e !important;
    color: #cdd6f4 !important;
    font-size: 0.83rem !important;
    line-height: 1.6 !important;
}

/* ── Buttons ── */
.stButton > button {
    border-radius: 10px !important;
    font-weight: 500 !important;
    font-size: 0.875rem !important;
    padding: 0.45rem 1.1rem !important;
    transition: all 0.15s ease !important;
}
.stButton > button:hover {
    box-shadow: 0 4px 14px rgba(79, 110, 247, 0.3) !important;
    transform: translateY(-1px) !important;
}
.stButton > button[kind="primary"] {
    background-color: #4F6EF7 !important;
    border-color: #4F6EF7 !important;
}

/* ── Text areas & inputs ── */
.stTextArea > div > textarea,
.stTextInput > div > div > input {
    border-radius: 10px !important;
    border: 1.5px solid #C5CAEC !important;
    font-size: 0.9rem !important;
    transition: border-color 0.15s ease, box-shadow 0.15s ease !important;
}
.stTextArea > div > textarea:focus,
.stTextInput > div > div > input:focus {
    border-color: #4F6EF7 !important;
    box-shadow: 0 0 0 3px rgba(79, 110, 247, 0.15) !important;
}

/* ── Dataframe / table ── */
.stDataFrame, [data-testid="stDataFrame"] {
    border-radius: 10px !important;
    overflow: hidden !important;
    border: 1px solid #D8DCF0 !important;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05) !important;
}

/* ── Expanders ── */
[data-testid="stExpander"] {
    border: 1px solid #D8DCF0 !important;
    border-radius: 12px !important;
    margin-bottom: 0.5rem !important;
    overflow: hidden !important;
}
.streamlit-expanderHeader {
    font-weight: 600 !important;
    background: #F0F3FF !important;
}

/* ── Alert boxes ── */
[data-testid="stAlert"], div[role="alert"] {
    border-radius: 10px !important;
}

/* ── Chat input bar ── */
[data-testid="stChatInputTextArea"] textarea {
    border-radius: 12px !important;
    font-size: 0.9rem !important;
}
</style>
""", unsafe_allow_html=True)

# ── Session state init ─────────────────────────────────────────────────────────

def _init_state() -> None:
    st.session_state.setdefault("messages", [])       # append-only event log
    st.session_state.setdefault("conv_history", [])   # multi-turn LLM history
    st.session_state.setdefault("queries_run", 0)
    st.session_state.setdefault("corrections_made", 0)
    st.session_state.setdefault("flow", {
        "state": "IDLE",
        "question": "",
        "sql": "",
        "explanation": "",
        "proposed_sql": "",      # original before any edits
        "edit_attempts": 0,
        "revision_history": [],
        "last_db_error": None,
    })
    st.session_state.setdefault("_schema_warnings", None)
    st.session_state.setdefault("_live_schema_tables", None)


_init_state()

# Ask the API for the live schema and any catalog drift, once per session.
if st.session_state["_live_schema_tables"] is None:
    try:
        status = api_client.schema_status()
        st.session_state["_live_schema_tables"] = status["live_tables"]
        st.session_state["_schema_warnings"] = status["drift_warnings"]
    except api_client.ApiError:
        st.session_state["_live_schema_tables"] = 0
        st.session_state["_schema_warnings"] = []


# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🔍 Olist Data Analyst AI")
    st.markdown(
        "A Data Analyst AI Agent connected to the **Olist Brazilian 2017-2018 e-commerce database** — "
        "covering orders, customers, sellers, products, payments, and reviews.\n\n"
        "Ask questions in plain English. The agent proposes SQL, shows its reasoning, "
        "and asks for approval before running anything."
    )

    st.divider()
    st.subheader("Try an example")
    examples = [
        ("📈 GMV by month", "What was the GMV per month in 2018?"),
        ("🏆 Top sellers", "Top 10 sellers by total revenue"),
        ("⭐ Review scores", "Average review score by product category"),
        ("🔍 Seller perf.", "Analyze our seller performance"),
        ("🚚 Late deliveries", "How do late deliveries affect customer review scores?"),
    ]
    for i, (label, question) in enumerate(examples):
        if st.button(label, key=f"ex_{i}", help=question, use_container_width=True):
            st.session_state["_prefill"] = question

    st.divider()
    st.subheader("⚙️ Session")
    n_tables = st.session_state.get("_live_schema_tables") or 0
    if n_tables:
        st.caption(f"✅ Live schema: {n_tables} tables loaded from DB")
    else:
        st.caption("⚠️ Live schema unavailable")
    st.write(f"Queries run: **{st.session_state['queries_run']}**")
    st.write(f"Corrections made: **{st.session_state['corrections_made']}**")
    if st.button("🗑 Clear chat", use_container_width=True):
        st.session_state["messages"] = []
        st.session_state["conv_history"] = []
        st.session_state["queries_run"] = 0
        st.session_state["corrections_made"] = 0
        st.session_state["flow"] = {
            "state": "IDLE",
            "question": "",
            "sql": "",
            "explanation": "",
            "proposed_sql": "",
            "edit_attempts": 0,
            "revision_history": [],
            "last_db_error": None,
        }
        st.rerun()

    if st.session_state["_schema_warnings"]:
        st.divider()
        st.warning("Schema drift detected:\n" + "\n".join(st.session_state["_schema_warnings"]))


ASSISTANT_AVATAR = "🔍"
USER_AVATAR = "👤"

# ── Event renderer ─────────────────────────────────────────────────────────────

def render_event(event: dict) -> None:
    """Render a single event from the append-only message log."""
    t = event["type"]

    if t == "welcome":
        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            st.markdown(event["content"])

    elif t == "user_question":
        with st.chat_message("user", avatar=USER_AVATAR):
            st.markdown(event["content"])

    elif t == "proposed_sql":
        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            st.markdown(f"**Reason:** {event['reason']}")
            st.code(event["sql"], language="sql")

    elif t == "results":
        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            import pandas as pd
            df = pd.DataFrame(event["rows"], columns=event["columns"])
            st.dataframe(df, use_container_width=True)
            n = len(event["rows"])
            if n >= 200:
                st.caption(f"Showing first 200 of {n} rows.")
            else:
                st.caption(f"{n} row(s) returned.")

    elif t == "chart":
        fig = detect_chart(event["columns"], event["rows"])
        if fig is not None:
            with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
                st.plotly_chart(fig, use_container_width=True)

    elif t == "insights":
        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            st.markdown(event["content"])

    elif t == "user_edit":
        with st.chat_message("user", avatar=USER_AVATAR):
            st.markdown(f"✏️ *{event['content']}*")

    elif t == "error":
        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            st.error(event["content"])

    elif t == "warning":
        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            st.warning(event["content"])

    elif t == "multi_query_start":
        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            st.markdown(f"Running a multi-part analysis for: **{event['question']}**")

    elif t == "multi_query_subresult":
        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            label = f"{'✅' if not event.get('error') else '❌'} Sub-analysis {event['index']}: {event['sub_question']}"
            with st.expander(label, expanded=False):
                if event.get("error"):
                    st.error(f"Error: {event['error']}")
                else:
                    st.markdown(event["summary"])
                    if event.get("sql"):
                        st.code(event["sql"], language="sql")
                    if event.get("columns") and event.get("rows"):
                        import pandas as pd
                        df = pd.DataFrame(event["rows"][:200], columns=event["columns"])
                        st.dataframe(df, use_container_width=True)
                        fig = detect_chart(event["columns"], event["rows"])
                        if fig is not None:
                            st.plotly_chart(fig, use_container_width=True)

    elif t == "multi_query_report":
        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            st.markdown(event["markdown"])


# ── Page header banner ─────────────────────────────────────────────────────────

st.markdown("""
<div style="
    background: linear-gradient(135deg, #4F6EF7 0%, #7B96FF 100%);
    border-radius: 16px;
    padding: 1.25rem 1.75rem;
    margin-bottom: 1.25rem;
    display: flex;
    align-items: center;
    gap: 1rem;
">
    <span style="font-size: 2rem; line-height: 1;">🔍</span>
    <div>
        <div style="color: white; font-size: 1.25rem; font-weight: 700; letter-spacing: -0.02em; margin: 0;">
            Olist Data Analyst
        </div>
        <div style="color: rgba(255,255,255,0.8); font-size: 0.85rem; margin-top: 2px;">
            Ask questions in plain English &mdash; get SQL, charts, and insights
        </div>
    </div>
</div>
""", unsafe_allow_html=True)


# ── Reconstruct history at the top of every rerun ─────────────────────────────

# Show welcome message on first run
if not st.session_state["messages"]:
    welcome = {
        "type": "welcome",
        "content": (
            "**Welcome!** I'm a Data Analyst AI Agent connected to the "
            "**Olist Brazilian e-commerce database** — covering orders, customers, "
            "sellers, products, payments, and reviews.\n\n"
            "Ask me anything in plain English. I'll propose a SQL query and explain my "
            "reasoning before running anything. You can edit the SQL directly, ask me to "
            "revise it, or approve it as-is.\n\n"
            "For broad questions like *'Analyze seller performance'*, I'll automatically "
            "decompose the question and run multiple analyses."
        ),
    }
    st.session_state["messages"].append(welcome)

for event in st.session_state["messages"]:
    render_event(event)


# ── Flow helpers ───────────────────────────────────────────────────────────────

def _flow() -> dict:
    return st.session_state["flow"]


def _set_flow(**kwargs) -> None:
    st.session_state["flow"].update(kwargs)


def _append(event: dict) -> None:
    st.session_state["messages"].append(event)
    render_event(event)


# ── AWAITING_APPROVAL interactive widgets ──────────────────────────────────────

if _flow()["state"] == "AWAITING_APPROVAL":
    flow = _flow()

    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        edited_sql = st.text_area(
            "Edit SQL if needed, then click Run:",
            value=flow["sql"],
            height=180,
            key="sql_textarea",
        )
        col1, col2 = st.columns(2)

        run_clicked = col1.button("✅ Run this", use_container_width=True)
        revise_clicked = col2.button("💬 Ask LLM to revise", use_container_width=True)

    if run_clicked:
        # Direct edit path — does not consume edit_attempts budget
        final_sql = edited_sql.strip()
        if final_sql != flow["proposed_sql"]:
            st.session_state["corrections_made"] += 1
            flow["revision_history"].append({
                "type": "direct_edit",
                "sql_after": final_sql,
            })
        _set_flow(sql=final_sql, state="EXECUTING")
        st.rerun()

    if revise_clicked:
        _set_flow(state="AWAITING_REVISION_INSTRUCTION")
        st.rerun()


# ── AWAITING_REVISION_INSTRUCTION ─────────────────────────────────────────────

if _flow()["state"] == "AWAITING_REVISION_INSTRUCTION":
    flow = _flow()
    max_attempts = 5

    if flow["edit_attempts"] >= max_attempts:
        _append({"type": "warning", "content": f"Too many LLM revision attempts ({max_attempts}). You can still edit the SQL directly above."})
        _set_flow(state="AWAITING_APPROVAL")
        st.rerun()

    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        instruction = st.text_input(
            "What should change?",
            key="revision_input",
            placeholder="e.g. 'group by week instead of month'",
        )
        submit_revision = st.button("Submit revision", key="submit_revision")

    if submit_revision and instruction.strip():
        _append({"type": "user_edit", "content": instruction.strip()})
        _set_flow(state="REVISING", edit_attempts=flow["edit_attempts"] + 1)
        st.rerun()


# ── REVISING ──────────────────────────────────────────────────────────────────

if _flow()["state"] == "REVISING":
    flow = _flow()
    with st.spinner("Revising SQL..."):
        try:
            rev = api_client.revise(
                question=flow["question"],
                sql=flow["sql"],
                explanation=flow["explanation"],
                instruction=st.session_state["messages"][-1]["content"],
                last_db_error=flow["last_db_error"],
            )
        except api_client.ApiError as exc:
            _append({"type": "error", "content": f"Could not revise the SQL: {exc}"})
            _set_flow(state="AWAITING_APPROVAL", edit_attempts=flow["edit_attempts"] - 1)
            st.rerun()
    new_sql, new_explanation = rev["sql"], rev["explanation"]

    if rev["unclear"]:
        _append({"type": "warning", "content": "Unclear instruction. Can you please rephrase?"})
        _set_flow(state="AWAITING_REVISION_INSTRUCTION", edit_attempts=flow["edit_attempts"] - 1)
    else:
        _append({"type": "proposed_sql", "reason": new_explanation, "sql": new_sql})
        flow["revision_history"].append({
            "type": "llm_revision",
            "instruction": st.session_state["messages"][-2]["content"],
            "sql_after": new_sql,
        })
        _set_flow(sql=new_sql, explanation=new_explanation, state="AWAITING_APPROVAL")

    st.rerun()


# ── EXECUTING ────────────────────────────────────────────────────────────────

if _flow()["state"] == "EXECUTING":
    flow = _flow()
    with st.spinner("Executing query and generating insights..."):
        try:
            # The API runs the query read-only, logs the correction record, and explains the result.
            result = api_client.execute(
                question=flow["question"],
                sql=flow["sql"],
                proposed_sql=flow["proposed_sql"],
                revision_history=flow["revision_history"],
            )
            _set_flow(last_db_error=None)
        except api_client.ApiError as exc:
            _append({"type": "error", "content": f"Query failed: {exc}"})
            _set_flow(state="IDLE", last_db_error=str(exc))
            st.rerun()

    columns, rows = result["columns"], result["rows"]
    _append({"type": "results", "columns": columns, "rows": rows[:200]})
    _append({"type": "chart",   "columns": columns, "rows": rows})
    if result["truncated"]:
        _append({"type": "warning", "content": f"The query returned {result['row_count']:,} rows; only the first {len(rows):,} were sent to the browser."})

    summary = result["insights"] or result["insights_error"] or "No insights were generated."
    _append({"type": "insights", "content": summary})

    # Update conversation history so follow-up questions have context
    append_assistant_turn(st.session_state["conv_history"], sql=flow["sql"], summary=summary)

    st.session_state["queries_run"] += 1
    _set_flow(state="IDLE")
    st.rerun()


# ── MULTI_QUERY_RUNNING ───────────────────────────────────────────────────────

if _flow()["state"] == "MULTI_QUERY_RUNNING":
    flow = _flow()
    # No live per-step progress: /analyze is one blocking HTTP call (see docs/api-notes.md).
    with st.spinner("Running multi-part analysis (this can take a minute)..."):
        try:
            report = api_client.analyze(flow["question"], st.session_state["conv_history"])
        except api_client.ApiError as exc:
            _append({"type": "error", "content": f"Analysis failed: {exc}"})
            _set_flow(state="IDLE")
            st.rerun()

    # Append sub-results as individual expandable events
    for i, sr in enumerate(report["sub_results"], 1):
        _append({
            "type": "multi_query_subresult",
            "index": i,
            "sub_question": sr["sub_question"],
            "sql": sr["sql"],
            "columns": sr["columns"],
            "rows": sr["rows"],
            "summary": sr["summary"],
            "error": sr["error"],
        })

    report_md = report["report_markdown"]
    _append({"type": "multi_query_report", "markdown": report_md})

    # Update conversation history with executive summary (first paragraph of report)
    exec_summary = report_md.split("\n\n")[1] if "\n\n" in report_md else report_md[:500]
    append_assistant_turn(
        st.session_state["conv_history"],
        sql="(multi-query analysis)",
        summary=exec_summary,
    )

    st.session_state["queries_run"] += len(report["sub_results"])
    _set_flow(state="IDLE")
    st.rerun()


# ── Chat input ────────────────────────────────────────────────────────────────

# Handle example question prefill
prefill = st.session_state.pop("_prefill", None)

is_busy = _flow()["state"] != "IDLE"

user_input = st.chat_input(
    "Ask a question about the Olist data...",
    disabled=is_busy,
    key="chat_input",
)

question = prefill or user_input

if question and not is_busy:
    question = question.strip()
    _append({"type": "user_question", "content": question})
    append_user_turn(st.session_state["conv_history"], question)
    _set_flow(
        question=question,
        sql="",
        explanation="",
        proposed_sql="",
        edit_attempts=0,
        revision_history=[],
        last_db_error=None,
    )

    # Check if this is a broad analytical question
    with st.spinner("Analysing question scope..."):
        try:
            broad = api_client.scope(question)
        except api_client.ApiError:
            broad = False  # fall through to the normal path, which will surface any API error

    if broad:
        _append({"type": "multi_query_start", "question": question})
        _set_flow(state="MULTI_QUERY_RUNNING")
        st.rerun()
    else:
        # Normal single-query path
        _set_flow(state="GENERATING")
        st.rerun()


# ── GENERATING ───────────────────────────────────────────────────────────────

if _flow()["state"] == "GENERATING":
    flow = _flow()
    with st.spinner("Retrieving context and generating SQL..."):
        try:
            # The current question is already the last entry in conv_history; the API expects
            # prior turns only, so send everything before it.
            proposal = api_client.ask(flow["question"], st.session_state["conv_history"][:-1])
        except api_client.ApiError as exc:
            _append({"type": "error", "content": f"Could not generate SQL: {exc}"})
            _set_flow(state="IDLE")
            st.rerun()

    sql, explanation = proposal["sql"], proposal["explanation"]
    _append({"type": "proposed_sql", "reason": explanation, "sql": sql})
    _set_flow(
        sql=sql,
        explanation=explanation,
        proposed_sql=sql,
        state="AWAITING_APPROVAL",
    )
    st.rerun()
