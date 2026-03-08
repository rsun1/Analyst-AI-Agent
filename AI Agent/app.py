"""
Streamlit chat interface for the Analytics AI Agent.
Replaces the CLI (agent.py) with a conversational front end.

Features:
  - Multi-turn conversation memory (follow-up questions work naturally)
  - Auto chart generation after each query result
  - Multi-query analysis for broad business questions
  - Append-only message log that survives Streamlit reruns
  - Silent query_log.jsonl logging (fine-tuning data flywheel)
"""
import streamlit as st

from agent_core import UNCLEAR_INSTRUCTION, step_explain, step_execute, step_generate, step_rag
from chart import detect_chart
from conversation import append_assistant_turn, append_user_turn
from logger import log_query_event
from multi_query import is_broad_question, run_multi_query_analysis
from schema_loader import validate_catalog_against_db

# ── Page config ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Olist Data Analyst AI",
    page_icon="🔍",
    layout="wide",
)

# ── Session state init ─────────────────────────────────────────────────────────

def _init_state() -> None:
    st.session_state.setdefault("messages", [])       # append-only event log
    st.session_state.setdefault("conv_history", [])   # multi-turn LLM history
    st.session_state.setdefault("queries_run", 0)
    st.session_state.setdefault("corrections_made", 0)
    st.session_state.setdefault("flow", {
        "state": "IDLE",
        "question": "",
        "rag_context": [],
        "sql": "",
        "explanation": "",
        "proposed_sql": "",      # original before any edits
        "edit_attempts": 0,
        "revision_history": [],
        "last_db_error": None,
    })
    st.session_state.setdefault("_schema_warnings", None)


_init_state()

# Run schema drift check once per session
if st.session_state["_schema_warnings"] is None:
    drift = validate_catalog_against_db()
    st.session_state["_schema_warnings"] = drift or []


# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🔍 Olist Data Analyst AI")
    st.markdown(
        "A Data Analyst AI Agent connected to the **Olist Brazilian e-commerce database** — "
        "covering orders, customers, sellers, products, payments, and reviews.\n\n"
        "Ask questions in plain English. The agent proposes SQL, shows its reasoning, "
        "and asks for approval before running anything."
    )

    st.divider()
    st.subheader("Example questions")
    examples = [
        "What was the GMV per month in 2018?",
        "Top 10 sellers by total revenue",
        "Average review score by product category",
        "Analyze our seller performance",
        "How do late deliveries affect customer review scores?",
    ]
    for ex in examples:
        if st.button(ex, key=f"ex_{ex[:20]}", use_container_width=True):
            st.session_state["_prefill"] = ex

    st.divider()
    st.subheader("⚙️ Session")
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
            "rag_context": [],
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


# ── Event renderer ─────────────────────────────────────────────────────────────

def render_event(event: dict) -> None:
    """Render a single event from the append-only message log."""
    t = event["type"]

    if t == "welcome":
        with st.chat_message("assistant"):
            st.markdown(event["content"])

    elif t == "user_question":
        with st.chat_message("user"):
            st.markdown(event["content"])

    elif t == "proposed_sql":
        with st.chat_message("assistant"):
            st.markdown(f"**Reason:** {event['reason']}")
            st.code(event["sql"], language="sql")

    elif t == "results":
        with st.chat_message("assistant"):
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
            with st.chat_message("assistant"):
                st.plotly_chart(fig, use_container_width=True)

    elif t == "insights":
        with st.chat_message("assistant"):
            st.markdown(event["content"])

    elif t == "user_edit":
        with st.chat_message("user"):
            st.markdown(f"✏️ *{event['content']}*")

    elif t == "error":
        with st.chat_message("assistant"):
            st.error(event["content"])

    elif t == "warning":
        with st.chat_message("assistant"):
            st.warning(event["content"])

    elif t == "multi_query_start":
        with st.chat_message("assistant"):
            st.markdown(f"Running a multi-part analysis for: **{event['question']}**")

    elif t == "multi_query_subresult":
        with st.chat_message("assistant"):
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
        with st.chat_message("assistant"):
            st.markdown(event["markdown"])


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

    with st.chat_message("assistant"):
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

    with st.chat_message("assistant"):
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
        from agent_core import step_revise
        new_sql, new_explanation = step_revise(
            original_question=flow["question"],
            rag_context=flow["rag_context"],
            current_sql=flow["sql"],
            current_explanation=flow["explanation"],
            revision_instruction=st.session_state["messages"][-1]["content"],
            last_db_error=flow["last_db_error"],
        )

    if new_sql == UNCLEAR_INSTRUCTION:
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
    with st.spinner("Executing query..."):
        try:
            columns, rows, db_errors = step_execute(flow["sql"])
            _set_flow(last_db_error=None)
        except Exception as exc:
            _append({"type": "error", "content": f"Query failed after retries: {exc}"})
            _set_flow(state="IDLE", last_db_error=str(exc))
            st.rerun()

    display_rows = rows[:200]
    _append({"type": "results", "columns": columns, "rows": display_rows})
    _append({"type": "chart",   "columns": columns, "rows": rows})
    _set_flow(state="EXPLAINING")

    # Log the event (silent)
    log_query_event(
        question=flow["question"],
        proposed_sql=flow["proposed_sql"],
        revision_history=flow["revision_history"],
        final_sql=flow["sql"],
        db_errors=db_errors,
        row_count=len(rows),
    )
    st.session_state["queries_run"] += 1
    st.rerun()


# ── EXPLAINING ───────────────────────────────────────────────────────────────

if _flow()["state"] == "EXPLAINING":
    flow = _flow()
    # Retrieve rows from the last results event
    results_event = next(
        (e for e in reversed(st.session_state["messages"]) if e["type"] == "results"),
        None,
    )
    if results_event:
        with st.spinner("Generating insights..."):
            try:
                summary = step_explain(
                    flow["question"],
                    flow["sql"],
                    results_event["columns"],
                    results_event["rows"],
                )
            except Exception as exc:
                summary = f"Could not generate insights: {exc}"

        _append({"type": "insights", "content": summary})

        # Update conversation history so follow-up questions have context
        append_assistant_turn(
            st.session_state["conv_history"],
            sql=flow["sql"],
            summary=summary,
        )

    _set_flow(state="IDLE")
    st.rerun()


# ── MULTI_QUERY_RUNNING ───────────────────────────────────────────────────────

if _flow()["state"] == "MULTI_QUERY_RUNNING":
    flow = _flow()
    progress_placeholder = st.empty()

    def _progress(msg: str) -> None:
        progress_placeholder.info(msg)

    with st.spinner("Running multi-part analysis..."):
        report = run_multi_query_analysis(
            broad_question=flow["question"],
            conversation_history=st.session_state["conv_history"],
            progress_callback=_progress,
        )

    progress_placeholder.empty()

    # Append sub-results as individual expandable events
    for i, sr in enumerate(report.sub_results, 1):
        _append({
            "type": "multi_query_subresult",
            "index": i,
            "sub_question": sr.sub_question,
            "sql": sr.sql,
            "columns": sr.columns,
            "rows": sr.rows[:200],
            "summary": sr.summary,
            "error": sr.error,
        })

    _append({"type": "multi_query_report", "markdown": report.report_markdown})

    # Update conversation history with executive summary (first paragraph of report)
    exec_summary = report.report_markdown.split("\n\n")[1] if "\n\n" in report.report_markdown else report.report_markdown[:500]
    append_assistant_turn(
        st.session_state["conv_history"],
        sql="(multi-query analysis)",
        summary=exec_summary,
    )

    st.session_state["queries_run"] += len(report.sub_results)
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
        rag_context=[],
        sql="",
        explanation="",
        proposed_sql="",
        edit_attempts=0,
        revision_history=[],
        last_db_error=None,
    )

    # Check if this is a broad analytical question
    with st.spinner("Analysing question scope..."):
        broad = is_broad_question(question)

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
            rag_context = step_rag(flow["question"])
            sql, explanation = step_generate(
                flow["question"],
                rag_context,
                conversation_history=st.session_state["conv_history"],
            )
        except Exception as exc:
            _append({"type": "error", "content": f"Could not generate SQL: {exc}"})
            _set_flow(state="IDLE")
            st.rerun()

    if not sql:
        _append({"type": "error", "content": "No SQL was generated. Please rephrase your question."})
        _set_flow(state="IDLE")
        st.rerun()

    _append({"type": "proposed_sql", "reason": explanation, "sql": sql})
    _set_flow(
        rag_context=rag_context,
        sql=sql,
        explanation=explanation,
        proposed_sql=sql,
        state="AWAITING_APPROVAL",
    )
    st.rerun()
