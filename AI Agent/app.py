"""
Streamlit chat UI for the Olist Data Analyst AI Agent.

Design principles (UI.md §5):
  - st.session_state["messages"] is an append-only log of RESOLVED events.
    A rendering loop at the very top of the script replays this log on every
    Streamlit rerun so the chat history is never lost.
  - st.session_state["flow"] holds the current active state plus ALL context
    needed for the current in-progress question (original question, RAG context,
    current SQL, edit history, etc.).  It is never partially reset mid-flow.
  - The original question is stored in flow["question"] at submission time and
    NEVER overwritten during the edit loop — the LLM always receives it.
  - Interactive widgets (textarea, buttons) are rendered AFTER the history
    replay, reflecting only the current live state.
"""
import sqlite3

import pandas as pd
import streamlit as st

from agent_core import (
    UNCLEAR_INSTRUCTION,
    step_execute,
    step_explain,
    step_generate,
    step_rag,
    step_revise,
)
from config import MAX_EDIT_ATTEMPTS
from logger import log_query_event

# ── Page config (must be the very first Streamlit call) ──────────────────────

st.set_page_config(
    page_title="Olist Data Analyst AI",
    page_icon="🔍",
    layout="wide",
)

# ── Session state helpers ─────────────────────────────────────────────────────

EXAMPLE_QUESTIONS = [
    "What was the GMV per month in 2018?",
    "Who are the top 10 sellers by total revenue?",
    "What is the average review score by product category?",
    "How many orders were delivered late each month?",
    "What percentage of orders were delivered on time per state?",
]


def _reset_flow() -> dict:
    """Return a clean flow state dict."""
    return {
        # State machine node.
        "state": "IDLE",
        # Preserved across the entire edit loop — never overwritten.
        "question": "",
        "rag_context": [],
        # Current SQL shown in the approval textarea.
        "sql": "",
        "explanation": "",
        # Agent's very first proposal — kept for logging the delta.
        "proposed_sql": "",
        # Ordered list of revision steps for the data flywheel log.
        "revision_history": [],
        # Counts only LLM revision calls, not direct textarea edits.
        "llm_edit_attempts": 0,
        # Set when a DB error occurs during execution so step_revise can pass it on.
        "last_db_error": None,
        # True once llm_edit_attempts reaches MAX_EDIT_ATTEMPTS.
        "llm_revision_exhausted": False,
    }


if "messages" not in st.session_state:
    st.session_state.messages = []

if "flow" not in st.session_state:
    st.session_state.flow = _reset_flow()

if "stats" not in st.session_state:
    st.session_state.stats = {"queries_run": 0, "corrections_made": 0}

if "pending_question" not in st.session_state:
    st.session_state.pending_question = None

# ── Event renderer ────────────────────────────────────────────────────────────


def _render_event(event: dict) -> None:
    """Render a single resolved event as a static (non-interactive) component."""
    t = event["type"]

    if t == "user_question":
        with st.chat_message("user"):
            st.write(event["content"])

    elif t == "proposed_sql":
        with st.chat_message("assistant"):
            if event.get("reason"):
                st.markdown(f"**Reason:** {event['reason']}")
            st.code(event["sql"], language="sql")

    elif t == "llm_revision_request":
        with st.chat_message("user"):
            st.markdown(f"✏️ *Revision request:* {event['instruction']}")

    elif t == "direct_edit_note":
        with st.chat_message("user"):
            st.markdown("✏️ *SQL edited directly.*")

    elif t == "results":
        with st.chat_message("assistant"):
            columns = event["columns"]
            rows = event["rows"]
            total = event.get("total_rows", len(rows))
            if columns:
                df = pd.DataFrame(rows, columns=columns)
                st.dataframe(df, use_container_width=True)
                if total > len(rows):
                    st.caption(f"Showing first {len(rows)} of {total} rows.")
                else:
                    st.caption(f"{total} row{'s' if total != 1 else ''} returned.")
            else:
                st.info("Query returned no rows.")

    elif t == "insights":
        with st.chat_message("assistant"):
            st.markdown(event["content"])

    elif t == "error":
        with st.chat_message("assistant"):
            st.error(event["content"])

    elif t == "info":
        with st.chat_message("assistant"):
            st.info(event["content"])


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🔍 Olist Data Analyst")
    st.markdown("---")

    st.markdown("**About**")
    st.markdown(
        "This is a **Data Analyst AI Agent** connected to the "
        "**Olist Brazilian e-commerce database**, covering orders, customers, "
        "sellers, products, payments, and reviews.\n\n"
        "Ask questions in plain English. The agent proposes SQL, explains its "
        "reasoning, and waits for your approval before running anything. "
        "You can edit the SQL directly or ask the agent to revise it via natural language."
    )

    st.markdown("---")
    st.markdown("**Example questions**")
    for i, example in enumerate(EXAMPLE_QUESTIONS):
        if st.button(example, key=f"example_{i}", use_container_width=True):
            if st.session_state.flow["state"] == "IDLE":
                st.session_state.pending_question = example

    st.markdown("---")
    st.markdown("**Session stats**")
    st.metric("Queries run", st.session_state.stats["queries_run"])
    st.metric("Corrections made", st.session_state.stats["corrections_made"])

    st.markdown("")
    if st.button("🗑 Clear chat", use_container_width=True):
        st.session_state.messages = []
        st.session_state.flow = _reset_flow()
        st.session_state.stats = {"queries_run": 0, "corrections_made": 0}
        st.session_state.pending_question = None
        st.rerun()

# ── Welcome message (shown only on a fresh session) ──────────────────────────

if not st.session_state.messages and st.session_state.flow["state"] == "IDLE":
    with st.chat_message("assistant"):
        st.markdown(
            "**Welcome!** I'm a Data Analyst AI Agent connected to the "
            "**Olist Brazilian e-commerce database** — covering orders, customers, "
            "sellers, products, payments, and reviews.\n\n"
            "Ask me anything in plain English. I'll propose a SQL query and explain "
            "my reasoning before running anything.\n\n"
            "- **Edit the SQL directly** for quick fixes (wrong column, bad join, etc.)\n"
            "- **Ask me to revise it** for conceptual changes (different grouping, filters, etc.)\n"
            "- **Approve and run** when you're happy with the query"
        )

# ── History replay loop (UI.md §5.2) ─────────────────────────────────────────
# Reconstruct the full visual history on every rerun BEFORE processing any
# new interaction. This is the fix for Streamlit's full-rerun model.

for _event in st.session_state.messages:
    _render_event(_event)

# ── Current interactive state ─────────────────────────────────────────────────

flow = st.session_state.flow

# ── AWAITING_APPROVAL: show editable textarea + action buttons ────────────────

if flow["state"] == "AWAITING_APPROVAL":
    with st.chat_message("assistant"):
        st.markdown(f"**Reason:** {flow['explanation']}")
        st.markdown(
            "Review and edit the SQL below, then **run it** or ask me to **revise it**:"
        )

        # Use a key that changes with each LLM revision attempt so the textarea
        # always re-initialises with the freshly revised SQL.
        textarea_key = f"sql_textarea_{flow['llm_edit_attempts']}"
        if textarea_key not in st.session_state:
            st.session_state[textarea_key] = flow["sql"]

        st.text_area(
            "SQL Query",
            height=220,
            key=textarea_key,
            label_visibility="collapsed",
        )

        col1, col2, col3 = st.columns([2, 2, 4])
        with col1:
            run_clicked = st.button(
                "✅ Run this", type="primary", use_container_width=True
            )
        with col2:
            llm_revise_clicked = st.button(
                "💬 Ask LLM to revise",
                use_container_width=True,
                disabled=flow["llm_revision_exhausted"],
            )
        with col3:
            if flow["llm_revision_exhausted"]:
                st.caption(
                    f"LLM revision limit reached ({MAX_EDIT_ATTEMPTS}). "
                    "Edit the SQL directly and click Run."
                )
            elif flow["llm_edit_attempts"] > 0:
                remaining = MAX_EDIT_ATTEMPTS - flow["llm_edit_attempts"]
                st.caption(
                    f"{remaining} LLM revision attempt{'s' if remaining != 1 else ''} remaining."
                )

    # ── Handle "Run this" ──────────────────────────────────────────────────────
    if run_clicked:
        final_sql = st.session_state.get(textarea_key, flow["sql"]).strip()
        was_direct_edit = final_sql != flow["proposed_sql"].strip()

        if was_direct_edit:
            flow["revision_history"].append(
                {"type": "direct_edit", "sql_after": final_sql}
            )
            st.session_state.stats["corrections_made"] += 1

        # Log the proposed SQL as a resolved (static) event.
        st.session_state.messages.append(
            {"type": "proposed_sql", "reason": flow["explanation"], "sql": final_sql}
        )

        # Execute with silent self-correction.
        exec_error: str | None = None
        columns: list = []
        rows: list = []
        try:
            with st.spinner("Running query…"):
                columns, rows = step_execute(final_sql, flow["rag_context"])
        except (sqlite3.OperationalError, FileNotFoundError, Exception) as exc:
            exec_error = str(exc)

        if exec_error:
            st.session_state.messages.append(
                {"type": "error", "content": f"Execution failed: {exec_error}"}
            )
        else:
            display_rows = rows[:200]
            st.session_state.messages.append(
                {
                    "type": "results",
                    "columns": columns,
                    "rows": [list(r) for r in display_rows],
                    "total_rows": len(rows),
                }
            )

            # Generate insights.
            with st.spinner("Generating insights…"):
                try:
                    insights = step_explain(
                        flow["question"], final_sql, columns, rows
                    )
                    st.session_state.messages.append(
                        {"type": "insights", "content": insights}
                    )
                except Exception as exc:
                    st.session_state.messages.append(
                        {
                            "type": "error",
                            "content": f"Could not generate insights: {exc}",
                        }
                    )

            # Silently log for the data flywheel.
            log_query_event(
                question=flow["question"],
                proposed_sql=flow["proposed_sql"],
                revision_history=flow["revision_history"],
                final_sql=final_sql,
                db_errors=[],
                row_count=len(rows),
            )
            st.session_state.stats["queries_run"] += 1

        st.session_state.flow = _reset_flow()
        st.rerun()

    # ── Handle "Ask LLM to revise" ─────────────────────────────────────────────
    elif llm_revise_clicked:
        # Preserve any direct edits the user made before switching state.
        flow["sql"] = st.session_state.get(textarea_key, flow["sql"]).strip()
        flow["state"] = "AWAITING_REVISION_INSTRUCTION"
        st.rerun()

# ── AWAITING_REVISION_INSTRUCTION: show instruction input ─────────────────────

elif flow["state"] == "AWAITING_REVISION_INSTRUCTION":
    with st.chat_message("assistant"):
        attempts_used = flow["llm_edit_attempts"]
        remaining = MAX_EDIT_ATTEMPTS - attempts_used
        st.markdown(
            f"**Current SQL** "
            f"(LLM revision {attempts_used + 1} of {MAX_EDIT_ATTEMPTS}):"
        )
        st.code(flow["sql"], language="sql")
        st.markdown(
            f"What should change? "
            f"({remaining} attempt{'s' if remaining != 1 else ''} remaining)"
        )

        instruction = st.text_input(
            "Revision instruction",
            key="revision_instruction_input",
            placeholder=(
                "e.g. group by week instead of month, "
                "only include delivered orders, add a revenue ranking column…"
            ),
            label_visibility="collapsed",
        )

        col1, col2 = st.columns([2, 5])
        with col1:
            submit_clicked = st.button(
                "Submit", type="primary", use_container_width=True
            )
        with col2:
            cancel_clicked = st.button("← Back to SQL editor", use_container_width=True)

    if submit_clicked and instruction.strip():
        with st.spinner("Revising SQL…"):
            try:
                revised_sql, revised_explanation = step_revise(
                    original_question=flow["question"],
                    rag_context=flow["rag_context"],
                    current_sql=flow["sql"],
                    current_explanation=flow["explanation"],
                    revision_instruction=instruction.strip(),
                    last_db_error=flow["last_db_error"],
                )
            except Exception as exc:
                st.session_state.messages.append(
                    {"type": "error", "content": f"Revision failed: {exc}"}
                )
                flow["state"] = "AWAITING_APPROVAL"
                st.rerun()

        if revised_sql == UNCLEAR_INSTRUCTION:
            # FR-2.6: ambiguous instruction — do not count as an attempt.
            st.session_state.messages.append(
                {
                    "type": "info",
                    "content": (
                        "Unclear instruction — I couldn't determine a specific SQL change. "
                        "Please rephrase what you'd like to change."
                    ),
                }
            )
            # Stay in AWAITING_REVISION_INSTRUCTION; don't increment counter.
            st.rerun()
        else:
            # Successful revision: update flow and return to approval.
            flow["revision_history"].append(
                {
                    "type": "llm_revision",
                    "instruction": instruction.strip(),
                    "sql_after": revised_sql,
                }
            )
            flow["sql"] = revised_sql
            flow["explanation"] = revised_explanation
            flow["llm_edit_attempts"] += 1
            flow["last_db_error"] = None

            st.session_state.messages.append(
                {"type": "llm_revision_request", "instruction": instruction.strip()}
            )
            st.session_state.stats["corrections_made"] += 1

            if flow["llm_edit_attempts"] >= MAX_EDIT_ATTEMPTS:
                flow["llm_revision_exhausted"] = True
                st.session_state.messages.append(
                    {
                        "type": "info",
                        "content": (
                            f"LLM revision limit reached ({MAX_EDIT_ATTEMPTS} attempts). "
                            "You can still edit the SQL directly and run it."
                        ),
                    }
                )

            flow["state"] = "AWAITING_APPROVAL"
            st.rerun()

    elif cancel_clicked:
        flow["state"] = "AWAITING_APPROVAL"
        st.rerun()

# ── Chat input (pinned to bottom by Streamlit) ────────────────────────────────

is_mid_flow = flow["state"] != "IDLE"

user_input = st.chat_input(
    "Ask a question about the Olist data…",
    disabled=is_mid_flow,
)

# Consume a pending question set by a sidebar example button.
pending = st.session_state.pending_question
st.session_state.pending_question = None

new_question = user_input or (pending if not is_mid_flow else None)

if new_question and not is_mid_flow:
    new_question = new_question.strip()

    # Immediately append the user message so history replay shows it next rerun.
    st.session_state.messages.append(
        {"type": "user_question", "content": new_question}
    )

    # Phase 1: RAG retrieval.
    rag_context: list[str] = []
    try:
        with st.spinner("Retrieving schema context…"):
            rag_context = step_rag(new_question)
    except (FileNotFoundError, ValueError) as exc:
        st.session_state.messages.append({"type": "error", "content": str(exc)})
        st.rerun()

    # Phase 2: SQL + explanation generation.
    sql = ""
    explanation = ""
    try:
        with st.spinner("Generating SQL…"):
            sql, explanation = step_generate(new_question, rag_context)
    except Exception as exc:
        st.session_state.messages.append(
            {"type": "error", "content": f"Could not generate SQL: {exc}"}
        )
        st.rerun()

    if not sql:
        st.session_state.messages.append(
            {
                "type": "error",
                "content": "No SQL was generated. Please rephrase your question.",
            }
        )
        st.rerun()

    # Transition to approval state with full context frozen in flow.
    st.session_state.flow = {
        "state": "AWAITING_APPROVAL",
        "question": new_question,
        "rag_context": rag_context,
        "sql": sql,
        "explanation": explanation,
        "proposed_sql": sql,
        "revision_history": [],
        "llm_edit_attempts": 0,
        "last_db_error": None,
        "llm_revision_exhausted": False,
    }
    st.rerun()
