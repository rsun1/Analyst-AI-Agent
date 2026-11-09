# UI Plan: Olist Data Analyst AI Agent — Chat Interface

**Version:** 2.0  
**Last updated:** February 2026  
**Status:** Draft

---

## 1. Overview

This document outlines the plan for building a browser-based chat interface that wraps the existing Python Analytics AI Agent. The UI replaces the current CLI (`agent.py`) with a conversational front end, while keeping all backend logic (RAG, SQL generation, execution, insights) unchanged.

### 1.1 Goals

- Give non-technical users a familiar chat experience with no CLI knowledge required.
- Surface every phase of the agent flow (propose → approve/edit → execute → insights) as natural chat messages and interactive UI components.
- Keep the human firmly in the driver's seat: support both LLM-mediated revisions and direct SQL editing.
- Silently collect every human correction as a proprietary fine-tuning dataset.
- Keep the entire stack in Python — no separate JavaScript build process required.

### 1.2 Constraints (inherited from PRD)

- Python only for all application logic.
- OpenAI API only; no LangChain, FAISS, or similar.
- Local SQLite database (`olist.sqlite`); connection opened read-only.

---

## 2. Technology Choice: Streamlit

**Recommended framework: [Streamlit](https://streamlit.io/)**

| Factor | Streamlit |
|--------|-----------|
| Language | Pure Python — zero JavaScript required |
| Chat primitives | `st.chat_message`, `st.chat_input` built-in |
| Tables | `st.dataframe` for results with sorting/scrolling |
| Code display | `st.code` with SQL syntax highlighting |
| State management | `st.session_state` for conversation history |
| Deployment | `streamlit run app.py` — single command |
| Install | `pip install streamlit` |

Streamlit is the natural fit because the entire project is Python-only and Streamlit ships first-class chat components that map directly to the agent's existing conversational loop.

---

## 3. Page Layout

```
┌─────────────────────────────────────────────────────────────────────┐
│  🔍  Olist Data Analyst                               [sidebar ▸]   │
├────────────────────────────┬────────────────────────────────────────┤
│                            │                                        │
│       SIDEBAR              │           CHAT AREA                    │
│                            │                                        │
│  About                     │  ┌─ assistant ──────────────────────┐  │
│  ─────                     │  │ Welcome! I'm your Olist data      │  │
│  This is a Data Analyst    │  │ analyst. Ask me anything about    │  │
│  AI Agent connected to     │  │ the Olist Brazilian e-commerce    │  │
│  the Olist Brazilian       │  │ database — orders, customers,     │  │
│  e-commerce database.      │  │ sellers, products, payments,      │  │
│                            │  │ reviews, and more.               │  │
│  Ask questions in plain    │  └──────────────────────────────────┘  │
│  English. The agent will   │                                        │
│  propose SQL, show you     │  ┌─ user ───────────────────────────┐  │
│  the reasoning, and ask    │  │ What was the GMV per month        │  │
│  for your approval before  │  │ in 2018?                         │  │
│  running anything.         │  └──────────────────────────────────┘  │
│                            │                                        │
│  ─────                     │  ┌─ assistant ──────────────────────┐  │
│  Tips                      │  │ Reason: Aggregates payment value  │  │
│  • "Top 10 sellers by GMV" │  │ by order month for 2018          │  │
│  • "Monthly order volume"  │  │                                  │  │
│  • "Avg review score by    │  │  [editable SQL textarea]         │  │
│     product category"      │  │                                  │  │
│                            │  │  [ ✅ Run this ]                 │  │
│  ─────                     │  │  [ 💬 Ask LLM to revise ]       │  │
│  ⚙️  Session               │  └──────────────────────────────────┘  │
│  Queries run: 3            │                                        │
│  Corrections made: 1       │  ┌──────────────────────── input ───┐  │
│  [ 🗑 Clear chat ]         │  │ Ask a question about Olist...  ↵ │  │
│                            │  └──────────────────────────────────┘  │
└────────────────────────────┴────────────────────────────────────────┘
```

---

## 4. UI Components

### 4.1 Header / Branding

- **Page title** (browser tab): `Olist Data Analyst AI`
- **Page icon**: a magnifying glass or chart emoji.
- **Main heading** (rendered at top of chat): `🔍 Olist Data Analyst AI Agent`
- **Subtitle / instruction banner** (rendered once as the first assistant message on a fresh session):

  > **Welcome!** I'm a Data Analyst AI Agent connected to the **Olist Brazilian e-commerce database** — covering orders, customers, sellers, products, payments, and reviews.
  >
  > Ask me anything in plain English. I'll propose a SQL query and explain my reasoning before running anything. You can edit the SQL directly, ask me to revise it, or approve it as-is at any step.

### 4.2 Sidebar

| Element | Content |
|---------|---------|
| **About** | Short description of the agent and the Olist dataset. |
| **Example questions** | 3–5 clickable chips that pre-fill the chat input. |
| **Session stats** | Queries run and corrections made this session. |
| **Clear chat** button | Resets `st.session_state` conversation history. |

### 4.3 Chat Messages

All messages use `st.chat_message(role)` where role is `"user"` or `"assistant"`.

| Message type | How it renders |
|---|---|
| User question | Plain text in a user bubble. |
| Agent thinking (spinner) | `st.spinner("Retrieving context and generating SQL…")` while the LLM call is in progress. |
| Proposed SQL | Reason text + `st.text_area` (editable) pre-populated with the generated SQL. |
| Action buttons | `st.button("✅ Run this")` and `st.button("💬 Ask LLM to revise")` rendered below the textarea. |
| LLM revision prompt | A `st.text_input` asking "What should change?" — appears only when the user chooses LLM revision. |
| Results table | `st.dataframe(df)` with row count shown below. Truncated to 200 rows with a note if larger. |
| Insights | Plain assistant message with the plain-English summary and bullet-point insights. |
| Error / warning | `st.warning(...)` or `st.error(...)` for SQL errors, retry notices, or "Too many attempts." |

### 4.4 SQL Editing: Two Complementary Paths

The proposed SQL is presented in an **editable `st.text_area`**, not a read-only code block. This gives analysts two editing paths depending on the nature of the fix:

| Path | When to use | How it works |
|------|------------|--------------|
| **Direct edit** | Surgical fixes — a wrong column name, a missing `WHERE`, swapping `LEFT JOIN` for `INNER JOIN`, fixing a typo. | User edits the textarea directly and clicks "Run this". No LLM call needed. Fast and precise. |
| **LLM revision** | Conceptual changes — "group by week instead of month", "include only completed orders", "add a ranking column". | User clicks "Ask LLM to revise" and types a natural-language instruction. `step_revise` is called; the result is loaded back into the textarea for a final review before running. |

Both paths converge on the same execution step. Safety is preserved not by restricting editing but by the read-only SQLite URI: even if a user hand-types `DROP TABLE`, the database engine rejects it at the connection level.

### 4.5 Chat Input

- `st.chat_input("Ask a question about the Olist data…")` pinned to the bottom of the page.
- Disabled while the agent is mid-flow (waiting for approval, an edit instruction, or execution), so the user cannot submit a new question before the current one resolves.

---

## 5. Conversation State Machine & The Streamlit Rerun Problem

### 5.1 Why Naive State Tracking Breaks

Streamlit is not a traditional reactive frontend. **Every widget interaction — clicking a button, submitting a text input — triggers a full top-to-bottom rerun of `app.py`.** If `st.session_state` only stores the current state node (e.g. `"AWAITING_APPROVAL"`), the entire chat history will flash and disappear on each rerun because Streamlit will not remember what was previously rendered.

### 5.2 The Append-Only Chat Log Pattern

The solution is to treat `st.session_state["messages"]` as an **append-only log** of every visual event that has ever occurred in the session. At the very top of `app.py`, before any new logic is processed, a **rendering loop** replays this log to reconstruct the full visual history:

```python
# --- TOP OF app.py: always reconstruct history first ---
for event in st.session_state.get("messages", []):
    render_event(event)   # draws the appropriate st.chat_message / st.code / st.dataframe

# --- THEN: process any new input or button clicks ---
handle_new_interaction()
```

Every event appended to `messages` is a serialisable dict describing both the data and the component type needed to display it:

```python
# Examples of event dicts stored in st.session_state["messages"]
{"type": "user_question",   "content": "What was GMV per month in 2018?"}
{"type": "proposed_sql",    "reason": "...", "sql": "SELECT ..."}
{"type": "user_edit",       "content": "Changed LEFT JOIN to INNER JOIN"}
{"type": "results",         "columns": [...], "rows": [...]}
{"type": "insights",        "content": "GMV peaked in November..."}
{"type": "error",           "content": "Too many attempts."}
```

The `render_event` function is a simple `match`/`if` dispatcher that calls the correct Streamlit component for each event type. Because this runs at the top of every rerun, the visual history is always fully reconstructed before any new interaction is handled.

### 5.3 State Machine

The flow state node is stored separately as `st.session_state["flow"]` and only controls which interactive widgets are rendered after the history replay:

```
IDLE
  │  user submits question
  ▼
GENERATING   ← spinner shown; chat input disabled
  │  SQL + explanation ready
  ▼
AWAITING_APPROVAL   ← editable textarea + "Run this" / "Ask LLM to revise" buttons shown
  ├── user edits textarea and clicks "Run this" ────────────────────► EXECUTING
  └── user clicks "Ask LLM to revise"
        │
        ▼
      AWAITING_REVISION_INSTRUCTION   ← text_input shown
          │  user submits instruction
          ▼
        REVISING   ← spinner; attempt counter incremented
          │  revised SQL ready → loaded back into textarea
          ▼
        AWAITING_APPROVAL   (loop, max 5 LLM attempts)
          │  LLM attempts exhausted
          ▼
        IDLE  (with "Too many attempts" message)

EXECUTING   ← spinner; self-correction retries happen silently
  │  rows returned
  ▼
EXPLAINING   ← spinner; insights LLM call
  │  insights ready
  ▼
IDLE   ← results + insights displayed; chat input re-enabled
```

Full session state shape:

```python
st.session_state["messages"] = []   # append-only event log (see §5.2)

st.session_state["flow"] = {
    "state": "IDLE",
    "question": "",          # original user question — never dropped
    "rag_context": "",
    "sql": "",               # current SQL in the textarea
    "explanation": "",
    "edit_attempts": 0,      # counts only LLM revision attempts
    "last_db_error": None,   # last sqlite3 error string, if any
}
```

---

## 6. Context Window Integrity in the Edit Loop

### 6.1 The Problem

Streamlit's stateless rerun model makes it easy to accidentally drop context. If `step_revise` only receives the instruction ("change to group by week"), the LLM loses the thread: it no longer knows the original question, the database schema context, or whether a prior SQL error occurred. The result is a revision that technically follows the instruction but no longer answers the user's actual question.

### 6.2 The Fix: Full Context Bundle

Every call to `step_revise` must receive the complete context bundle assembled from `st.session_state["flow"]`. The original question is stored at question-submission time and is **never overwritten** during the edit loop:

```python
def step_revise(
    original_question: str,   # from flow["question"] — set once, never mutated
    rag_context: str,          # from flow["rag_context"]
    current_sql: str,          # the SQL currently in the textarea (may be user-edited)
    current_explanation: str,
    revision_instruction: str, # the user's natural-language instruction
    last_db_error: str | None, # populated if the previous execution attempt failed
) -> tuple[str, str]:
    """
    Return (revised_sql, revised_explanation).
    The LLM prompt includes all six inputs simultaneously so no context is lost
    across Streamlit reruns.
    """
```

The LLM prompt built inside `step_revise` explicitly includes all six fields. If `last_db_error` is populated, the prompt leads with the error so the model understands the revision is also a correction.

### 6.3 Guard in `app.py`

Before calling `step_revise`, `app.py` reads all fields from `st.session_state["flow"]` — it never reconstructs them from widget state alone, since widget state is reset on each rerun. This is why storing the original question in `flow["question"]` at submission time (not derived from `st.chat_input`) is critical.

---

## 7. Silent Data Flywheel

### 7.1 Why This Matters

Every time a user corrects the agent's SQL — whether through the LLM edit loop or direct textarea editing — the delta between the agent's proposal and the final executed query is the most valuable data produced by the system. It marks the exact boundary where the semantic layer failed and human domain knowledge had to intervene. Collected at scale, this is a proprietary fine-tuning dataset.

### 7.2 What to Log

After every successful query execution, `app.py` silently appends one record to a local JSONL file (`query_log.jsonl`):

```json
{
  "timestamp": "2026-02-21T14:32:10Z",
  "question": "What was GMV per month in 2018?",
  "proposed_sql": "SELECT strftime('%Y-%m', o.order_purchase_timestamp) ...",
  "revision_history": [
    {
      "type": "llm_revision",
      "instruction": "group by week instead of month",
      "sql_after": "SELECT strftime('%Y-%W', o.order_purchase_timestamp) ..."
    },
    {
      "type": "direct_edit",
      "sql_after": "SELECT strftime('%Y-%W', o.order_purchase_timestamp) ... WHERE ..."
    }
  ],
  "final_sql": "SELECT strftime('%Y-%W', o.order_purchase_timestamp) ... WHERE ...",
  "was_corrected": true,
  "db_errors_encountered": [],
  "row_count": 52
}
```

### 7.3 Implementation

A single function in a new `logger.py` module handles all writes:

```python
def log_query_event(
    question: str,
    proposed_sql: str,
    revision_history: list[dict],
    final_sql: str,
    db_errors: list[str],
    row_count: int,
) -> None:
    """Append one record to query_log.jsonl. Silent — never raises."""
```

`logger.py` wraps all file I/O in a `try/except` so a logging failure never surfaces to the user or interrupts the agent flow.

### 7.4 What `was_corrected` Signals

`was_corrected = True` when `proposed_sql != final_sql`. Records where this field is `True` and the question type can be tagged are the highest-value rows for future fine-tuning: they are (prompt, bad_completion, good_completion) triples — exactly the format needed for supervised fine-tuning or DPO.

---

## 8. File Structure

```
AI Agent/
├── app.py                  ← NEW: Streamlit entry point + rendering loop
├── agent_core.py           ← NEW: pure-function wrappers for each agent step
│                               (extracted from agent.py — no CLI I/O)
├── logger.py               ← NEW: silent query event logger → query_log.jsonl
├── query_log.jsonl         ← NEW: append-only fine-tuning dataset (gitignored)
├── agent.py                ← existing CLI; refactored to import from agent_core
├── rag.py
├── sql_generator.py
├── sql_correction.py
├── db.py
├── insights.py
├── config.py
├── ingest.py
├── schema_loader.py
├── embeddings_store.json
├── olist_schema_queries.md
├── schema_catalog.yml
└── requirements.txt        ← add: streamlit
```

### 8.1 `agent_core.py` — Extracted API

The existing `agent.py` mixes business logic with `input()`/`print()` calls. To serve the Streamlit UI, `agent_core.py` exposes the same steps as pure functions with no side effects:

```python
def step_rag(question: str) -> str:
    """Return RAG context string."""

def step_generate(question: str, rag_context: str) -> tuple[str, str]:
    """Return (sql, explanation)."""

def step_revise(
    original_question: str,
    rag_context: str,
    current_sql: str,
    current_explanation: str,
    revision_instruction: str,
    last_db_error: str | None,
) -> tuple[str, str]:
    """Return (revised_sql, revised_explanation). May return UNCLEAR_INSTRUCTION sentinel."""

def step_execute(sql: str) -> tuple[list[str], list[tuple]]:
    """Execute SQL with self-correction. Return (columns, rows)."""

def step_explain(question: str, sql: str, columns: list, rows: list) -> str:
    """Return plain-English insights string."""
```

`agent.py` imports from `agent_core.py` so CLI and UI share identical logic.

---

## 9. Implementation Plan

### Phase 1 — Extract `agent_core.py`

- Pull all pure-logic functions out of `agent.py` into `agent_core.py`.
- Update `step_revise` signature to accept the full context bundle (§6.2).
- Update `agent.py` to import from `agent_core.py` and pass the full context bundle.
- Verify existing CLI still works end-to-end.

### Phase 2 — Build `app.py` skeleton with history replay

- Set up page config (title, icon, wide layout).
- Initialise `st.session_state["messages"]` and `st.session_state["flow"]`.
- Implement the `render_event` dispatcher and the rendering loop at the top of `app.py` (§5.2).
- Render sidebar (About, example chips, stats, clear button).
- Wire `st.chat_input` to append a `user_question` event and trigger `step_rag` + `step_generate`.
- Append a `proposed_sql` event; display as editable textarea.

### Phase 3 — Approve / Edit loop

- Render "Run this" and "Ask LLM to revise" buttons only when `flow["state"] == "AWAITING_APPROVAL"`.
- **Direct edit path**: when user clicks "Run this", read the textarea value as `final_sql`, call `step_execute`, then `step_explain`.
- **LLM revision path**: when user clicks "Ask LLM to revise", transition to `AWAITING_REVISION_INSTRUCTION`; show `st.text_input`; call `step_revise` with full context bundle; reload result into textarea.
- Track LLM attempt count separately from direct edits (direct edits do not consume attempt budget).
- Surface "Too many LLM revision attempts." message when limit is reached; direct editing remains available.

### Phase 4 — Logger

- Create `logger.py` with `log_query_event`.
- Call it from `app.py` immediately after every successful execution.
- Add `query_log.jsonl` to `.gitignore`.

### Phase 5 — Polish

- Add `st.spinner` messages for each async step.
- Update session stats counter in sidebar to show both queries run and corrections made.
- Style the results dataframe (column widths, row limit warning at 200 rows).
- Error handling: friendly messages for DB not found, API errors, empty result sets.

### Phase 6 — Dependency & launch

- Add `streamlit` to `requirements.txt`.
- Document launch command in `README.md`:
  ```bash
  streamlit run "AI Agent/app.py"
  ```

---

## 10. Non-Goals for This UI

- Authentication / login.
- Persistent chat history across browser sessions (in-memory only; `query_log.jsonl` is not a chat log).
- CSV download of results (note for v2).
- Mobile-optimised layout.
- Deployment to a cloud service.

---

## 11. Open Questions

| # | Question | Decision |
|---|----------|----------|
| 1 | Should the SQL be directly editable or LLM-only? | **Both.** Direct textarea for surgical fixes; LLM loop for conceptual changes. Direct edits do not consume the LLM attempt budget. |
| 2 | Should results > 200 rows offer a CSV download? | No for MVP; add `st.download_button` in v2. |
| 3 | Should the sidebar show recent questions from this session? | Yes — derive from `messages` log, no extra state needed. |
| 4 | Dark mode? | Streamlit default theme; no custom theme for MVP. |
| 5 | Should `query_log.jsonl` entries be viewable inside the app? | No for MVP; a separate script or notebook can read and analyse the log. |
