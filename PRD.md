# Product Requirements Document: Analytics AI Agent

**Version:** 1.0  
**Last updated:** February 2026  
**Status:** Draft

---

## 1. Overview

### 1.1 Purpose

The **Analytics AI Agent** is a Python application that lets stakeholders (e.g. Product Managers, executives) ask questions in natural language about the **olist.sqlite** database (Olist Brazilian e‑commerce). The agent answers by generating and running SQL, then explaining results in plain English with clear insights.

### 1.2 Goals

- Enable non-technical users to query the database without writing SQL.
- Ensure SQL is grounded in documented schema and sample queries via RAG.
- Provide transparency: show the proposed query and reasoning before execution.
- Deliver not only numbers but also interpretation and business insights.

### 1.3 Out of Scope / Constraints

- **No open-source LLM or embedding models.** All language and embedding capabilities must use the **OpenAI API** only.
- **No third-party RAG/vector frameworks** such as LangChain, FAISS, Vanna.ai, LlamaIndex, etc. Implement RAG and retrieval using OpenAI APIs and custom Python code only.
- **Single API key:** One OpenAI API key, stored in `.env`; no other LLM providers required for core flow.
- **Implementation language:** Python only for all scripts and application logic.
- **Data source:** Local SQLite database only (`olist.sqlite`); no live external DBs or data pipelines in scope.

---

## 2. Data & Inputs

### 2.1 Database

- **File:** `olist.sqlite`
- **Content:** Olist Brazilian e‑commerce dataset (orders, customers, sellers, products, payments, reviews, etc.).
- **Location:** Local filesystem; path may be configurable (e.g. via `.env` or config).

### 2.2 Schema & Query Knowledge Base

- **File:** `olist_schema_queries.md`
- **Content:** Human-written document that includes:
  - Schema context and table/column definitions
  - Named metric definitions (e.g. `seller_tpv_per_month`, `monthly_gmv`, `revenue_by_product_category`)
  - Sample SQL queries for SQLite (e.g. using `strftime` for dates)
- **Role:** Sole source of truth for RAG: chunks from this file are embedded and used to retrieve relevant context when generating SQL.

### 2.3 Environment

- **`.env`** must contain:
  - `OPENAI_API_KEY`: API key for OpenAI (embeddings and chat/completion).
- No other secrets are required for the core flow.

---

## 3. System Architecture (High Level)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Analytics AI Agent (Python)                       │
├─────────────────────────────────────────────────────────────────────────┤
│  1. Offline / setup                                                     │
│     olist_schema_queries.md ──► Chunk ──► OpenAI Embeddings ──► Store   │
│                                                                         │
│  2. User question                                                        │
│     User question ──► Embed ──► Retrieve similar chunks ──► RAG context  │
│     RAG context + question ──► OpenAI Chat ──► Proposed SQL + reason    │
│                                                                         │
│  3. After user approval                                                  │
│     Execute SQL on olist.sqlite ──► Result set                           │
│     Result set + context ──► OpenAI Chat ──► Plain-English summary +     │
│                                               insights                   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Functional Requirements

### 4.1 Phase 1: Embedding and Storage (One-time / Periodic)

| ID | Requirement | Details |
|----|-------------|---------|
| FR-1.1 | **Ingest schema doc** | Read `olist_schema_queries.md` and split into chunks (e.g. by section, by definition + query block). Chunk size/strategy is an implementation choice but must preserve logical units (e.g. one definition + its SQL together). |
| FR-1.2 | **Generate embeddings** | For each chunk, call the **OpenAI Embeddings API** (e.g. `text-embedding-3-small` or current recommended model) to obtain a vector. No open-source or local embedding models. |
| FR-1.3 | **Store for retrieval** | Persist chunk text and corresponding embedding so that later steps can run similarity search. Storage must be implementable without FAISS/LangChain/Vanna (e.g. in-memory structures, JSON, or a simple SQLite table with vector representation and cosine similarity in Python). |
| FR-1.4 | **Refresh path** | Provide a way to re-run ingestion (e.g. script or CLI) when `olist_schema_queries.md` is updated. |
| FR-1.5 | **Cross-section context for RAG** | Embedding and retrieval must be designed so the agent can understand and use context **fluently across examples**. A single user question may require combining knowledge from **multiple sections** of `olist_schema_queries.md` (e.g. Sales, Customers & orders, and another section) to produce one correct SQL query—i.e. the needed knowledge must be **connected together**, not isolated to one example. The agent must **not** be limited to answering only by matching one question to one example in the file; it must be able to retrieve and synthesize relevant content from **all** applicable sections and use that combined context to generate accurate, composite queries. Chunking and retrieval (e.g. top-k and context window usage) should support returning multiple relevant chunks from different parts of the document when a question spans several concepts. |

### 4.2 Phase 2: Question → Proposed SQL (RAG + Explanation)

| ID | Requirement | Details |
|----|-------------|---------|
| FR-2.1 | **Accept user question** | Accept a natural-language question from the user (e.g. via CLI prompt, or later via a simple UI). |
| FR-2.2 | **Retrieve relevant context** | Embed the user question with the same OpenAI embedding model; retrieve the top-k most similar chunks from the store (e.g. by cosine similarity). Use only this retrieved context plus the question for SQL generation. |
| FR-2.3 | **Generate SQL and reasoning** | Call the **OpenAI Chat/Completion API** with a system prompt that: (a) restricts the agent to the provided schema/query context, (b) requires valid SQLite SQL, (c) requires a short explanation of why this query answers the question. Output: one proposed SQL query + explanation. No execution in this step. |
| FR-2.4 | **Present to user** | Show the user the proposed SQL and the explanation. Ask for explicit confirmation before executing (e.g. “Proceed? (y/n)” or “Edit/Approve/Cancel”). |

### 4.3 Phase 3: Execution and Results + Insights

| ID | Requirement | Details |
|----|-------------|---------|
| FR-3.1 | **Execute only after approval** | Run the approved SQL against `olist.sqlite` only when the user agrees to proceed. Use Python’s built-in `sqlite3` (or equivalent) to execute; no external query services. |
| FR-3.2 | **Return raw results** | Return the result set to the user in a clear format (e.g. table in console, or structured data for a future UI). |
| FR-3.3 | **Explain in plain English** | Send the result set (and optionally the query and question) to the OpenAI Chat API with a prompt that asks for: a short plain-English summary of what the numbers mean and 1–3 concrete business insights or recommendations. |
| FR-3.4 | **Handle errors** | If SQL execution fails (syntax error, missing table, etc.), surface the error to the user and do not call the “explain results” step. Optionally offer to regenerate SQL. |

---

## 5. Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NFR-1 | **Python only** – All application and script logic must be implemented in Python. |
| NFR-2 | **OpenAI only for ML** – Embeddings and text generation must use OpenAI APIs only; no LangChain, FAISS, Vanna.ai, or other open-source RAG/vector frameworks. |
| NFR-3 | **Single API key** – One `OPENAI_API_KEY` in `.env` suffices for the described flow. |
| NFR-4 | **Local-first** – Database and embedding store are local; no requirement for cloud DB or external vector DB for MVP. |
| NFR-5 | **Determinism / safety** – Only `SELECT` statements should be executed; reject or strip any DDL/DML if present (implementation detail). |

---

## 6. User Flow (End-to-End)

1. **Setup (once or when doc changes)**  
   Run script to read `olist_schema_queries.md` → chunk → embed via OpenAI → store embeddings.

2. **Ask**  
   User asks, e.g. “What was our GMV per month last year?”

3. **Propose**  
   Agent embeds question, retrieves relevant chunks, calls OpenAI to generate SQL + explanation, then shows:  
   - “I will run the following query: …”  
   - “Reason: …”  
   - “Proceed? (y/n)”

4. **Approve**  
   User confirms.

5. **Execute**  
   Agent runs the SQL on `olist.sqlite` and returns the result table.

6. **Explain**  
   Agent sends results (and context) to OpenAI and returns a plain-English summary plus insights (e.g. “GMV peaked in November; consider promotions in Q4 next year.”).

---

## 7. Dependencies

- **Python 3.x** (version to be specified in project README or `requirements.txt`).
- **OpenAI** – Official `openai` Python package for Embeddings and Chat APIs.
- **python-dotenv** – Load `OPENAI_API_KEY` from `.env`.
- **sqlite3** – Standard library for connecting to `olist.sqlite` and executing SQL.
- No LangChain, FAISS, Vanna.ai, or similar; no open-source embedding or LLM libraries for the core RAG/SQL flow.

---

## 8. Success Criteria

- A stakeholder can ask a natural-language question about Olist data and receive a proposed SQL query with a clear explanation.
- After approval, the agent returns correct query results and a concise plain-English explanation with at least one actionable insight.
- All embeddings and text generation use only the OpenAI API; schema knowledge comes only from `olist_schema_queries.md` via the custom RAG pipeline.

---

## 9. Future Considerations (Out of Scope for This PRD)

- Web or GUI interface.
- Multiple databases or live connections.
- Caching of embeddings or query results.
- Role-based access or audit logging.
- Use of other LLM or embedding providers.

---

## 10. Document History

| Version | Date       | Author | Changes |
|---------|------------|--------|---------|
| 1.0     | Feb 2026   | —      | Initial PRD for Analytics AI Agent. |
