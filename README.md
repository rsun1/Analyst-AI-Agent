# Analytics AI Agent

Python application that lets stakeholders ask natural-language questions about the **Olist** Brazilian e-commerce dataset, stored in **PostgreSQL**. The agent retrieves relevant schema context via RAG (pgvector), proposes SQL with an explanation, and after approval runs the query and returns results plus plain-English business insights.

> **Versions.** `main` is **v2**: Postgres + pgvector. The original SQLite + JSON-vector-store implementation is preserved at the git tag `v1-constrained`.

# Why This Exists
Most enterprise analytics teams can't use vector databases, open-source models, or send raw data to external APIs. Security policies, compliance requirements, and IT governance make the standard RAG tutorial stack unavailable by default.

**v1** was built under those exact constraints. RAG implemented from scratch using cosine similarity on markdown files, no LangChain or FAISS, schema injected as structured YAML, all query execution local and read-only. Not because these are the best tools for the job in an ideal environment, but because they're what's actually available in most locked-down enterprise deployments.

The goal wasn't to build the most capable Text-to-SQL system. It was to build one that works reliably inside real constraints, and to measure that reliability rigorously enough to know when it's failing and why.

That second part turned out to be as hard as the first. The eval harness went through three iterations before the scoring was trustworthy. Factual accuracy moved from 27.8% to 70.6%, not by making the agent smarter, but by diagnosing specific failure modes: wrong column references, Portuguese category names, date filter logic, judge scoring tolerance. Each fix was grounded in evidence from the eval, not intuition.
The remaining failures are documented too. A system you can't honestly evaluate is a system you can't trust.

**v2** moves the same agent onto a production-shaped data layer: a real database server with a database-enforced read-only role, and vector search done inside Postgres with pgvector instead of in Python. The eval harness carried over unchanged, which is what made it possible to check the migration for regressions (see [Evaluation](#evaluation)).

---

## Requirements

- **Python 3.10+**
- **Docker** (runs Postgres + pgvector locally)
- **Dependencies:** `openai`, `python-dotenv`, `pyyaml`, `streamlit`, `psycopg`, `pgvector` (and others). Install with:
  ```bash
  pip install -r requirements.txt
  ```
- **`.env`** in the `AI Agent/` folder containing:
  ```
  OPENAI_API_KEY=your_key
  ```
- **`olist.sqlite`** in the `AI Agent/` folder. It is only the **source for the one-time migration** into Postgres; the agent never reads it at runtime.
  Download it from Kaggle:
  [E-commerce Dataset by Olist as an SQLite Database](https://www.kaggle.com/datasets/terencicp/e-commerce-dataset-by-olist-as-an-sqlite-database)

---

## Setup

Run these once (steps 3 again whenever `olist_schema_queries.md` or `schema_catalog.yml` change):

```bash
cd "AI Agent"

# 1. Start Postgres 16 + pgvector
docker compose up -d

# 2. Copy olist.sqlite into Postgres (drops and recreates tables; creates the read-only role)
python -m migrate_to_postgres

# 3. Chunk, embed and store the schema docs in the schema_chunks pgvector table
python -m ingest
```

**Migration details.** `INTEGER`→`BIGINT`, `REAL`→`DOUBLE PRECISION`, `TEXT`→`TEXT`. Columns whose names end in `_date`, `_timestamp` or `_at` become real `TIMESTAMP`s (empty strings become `NULL`). In v1 these were `TEXT` and needed `strftime`/`julianday`; in Postgres the agent uses `TO_CHAR`, `DATE_TRUNC` and `EXTRACT`.

**Ingest details.** Reads both source files, chunks them, calls the OpenAI Embeddings API, and stores everything in `schema_chunks` (35 chunks: metric definitions + one chunk per table + cross-domain paths + pitfalls).

---

## Running the agent

### CLI

```bash
cd "AI Agent"
python -m agent
# or pass the question directly:
python -m agent "What was our GMV per month last year?"
```

### Streamlit web UI

```bash
cd "AI Agent"
streamlit run app.py
```

---

## How it works (step by step)

1. **RAG retrieval** — The user's question is embedded and the top-5 most similar chunks (table descriptions, metric definitions, sample SQL) are fetched from the `schema_chunks` table with a pgvector cosine-distance query (`ORDER BY embedding <=> ...`).

2. **SQL generation** — The full schema catalog (`schema_catalog.yml`) is injected into the system prompt on every call as ground truth. The RAG context and few-shot worked examples are added to the user message. `gpt-4o-mini` generates a PostgreSQL query and a short explanation.

3. **Approve / edit** — The CLI shows the proposed SQL and explanation. You type `y` to approve, `n` to reject, or describe a change in plain English (edit loop, up to 5 attempts).

4. **Execute** — The SQL runs as the `olist_reader` role (`SELECT`-only) in a read-only session with a 15 s statement timeout and a row cap, so writes and DDL are rejected by the database itself. On a recoverable error (bad column, syntax, type mismatch), the agent auto-corrects and retries (up to 2 times). Permission errors are not retried.

5. **Insights** — Results are sent back to the model, which returns a plain-English summary and 1–3 business recommendations grounded in the data.

---

## Key source files

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Runs `pgvector/pgvector:pg16` locally |
| `migrate_to_postgres.py` | One-off copy of `olist.sqlite` into Postgres; converts date columns to `TIMESTAMP`; creates the read-only role |
| `ingest.py` | Chunks `olist_schema_queries.md` + `schema_catalog.yml`, generates embeddings, stores them in the `schema_chunks` pgvector table |
| `rag.py` | Embeds the question and retrieves top-k chunks by cosine distance inside Postgres |
| `db.py` | Read-only Postgres connection, live schema introspection (`information_schema`), query execution |
| `schema_catalog.yml` | Ground-truth schema: exact table/column names, types, FK relationships, cross-domain join paths, pitfalls |
| `olist_schema_queries.md` | Named metric definitions with correct sample SQL (used for RAG) |
| `sql_generator.py` | Builds SQL via OpenAI Chat; includes full schema + few-shot examples in system prompt |
| `sql_correction.py` | On SQL execution error, asks the model for a corrected query and retries |
| `sql_errors.py` | Classifies Postgres errors (missing column/table, syntax) into hints for the correction prompt |
| `agent_core.py` | Pure-function wrappers for each step (RAG, generate, execute, explain); used by both CLI and Streamlit |
| `agent.py` | CLI flow: question → propose → approve/edit → execute → explain |
| `app.py` | Streamlit web UI |
| `insights.py` | Generates plain-English summary + business insights from query results |
| `schema_loader.py` | Loads `schema_catalog.yml`, validates it against the live DB schema, renders the schema block for prompts |
| `evals/scorer.py` | Evaluation harness: runs all test questions through the agent, judges answers with LLM, saves results |
| `evals/test_dataset.json` | 20-question test dataset with expected SQL, expected answers, and eval type |

---

## Optional environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | (required) | OpenAI API key |
| `PG_HOST` / `PG_PORT` / `PG_DB` | `localhost` / `5432` / `olist` | Postgres location |
| `PG_ADMIN_USER` / `PG_ADMIN_PASSWORD` | `olist_admin` / `olist_admin_pw` | Owner role used by migration and ingest (**local-dev defaults, change for any shared deployment**) |
| `PG_READER_USER` / `PG_READER_PASSWORD` | `olist_reader` / `olist_reader_pw` | SELECT-only role used by the agent (**local-dev defaults**) |
| `PG_ADMIN_DSN` / `PG_READER_DSN` | built from the above | Full connection strings; override the individual settings |
| `OLIST_DB_PATH` | `./olist.sqlite` | SQLite source file for the migration only |
| `SCHEMA_DOC_PATH` | `./olist_schema_queries.md` | Metric definitions + sample queries |
| `SCHEMA_CATALOG_PATH` | `./schema_catalog.yml` | Structured schema catalog |

---

## Architecture notes

- **No LangChain, FAISS, or RAG frameworks** — embeddings come from `openai`, and similarity search runs in Postgres via the pgvector extension. (v1 did the cosine similarity in plain Python over a JSON file; the retrieval interface stayed the same.)
- **Database-enforced read-only access** — instead of v1's read-only SQLite URI, the agent connects as a `SELECT`-only role in a read-only session. `DELETE`, `DROP` and `CREATE` are rejected with `InsufficientPrivilege`.
- **Two-layer schema injection**: the full schema catalog is always in the system prompt (so the model can never hallucinate column names), while RAG provides query pattern examples as additional context in the user message.
- **Few-shot examples** are baked into every SQL generation call in `sql_generator.py`, covering the most common failure patterns (category name translation, revenue calculation, delivery time vs delay, leads date filtering).
- **Self-correction** handles Postgres programming/data errors at execution time; empty-result detection in the eval harness triggers a second generation attempt with a hint.
- **Models used**: `text-embedding-3-small` for embeddings, `gpt-4o-mini` for SQL generation, correction, and insights.

---

## Evaluation

### Running the eval

```bash
cd "AI Agent"
python evals/scorer.py
```

Results are written to `evals/eval_results_latest.json` by default; pass a path to choose another (`python evals/scorer.py evals/my_run.json`). `evals/eval_results.json` is the committed v1 baseline and is never overwritten by default; the v2 runs are saved as `evals/eval_results_pg*.json`. Each entry records the question, agent SQL, agent answer, expected answer, factual accuracy (PASS/FAIL/PARTIAL/DOCUMENTED), answer quality (1–3), and judge reasoning.

### Test dataset

20 questions covering:
- Simple aggregations (order counts, payment methods, top states)
- Revenue and category analysis (multi-table joins, Portuguese→English translation)
- Delivery time calculations
- Review score distributions
- Leads pipeline (qualified → closed, conversion rates, time-to-close by channel)
- Ambiguity handling (open-ended questions like "who are the best customers?")
- Known hard questions (complex correlated subqueries, multi-hop CTEs — marked `known_failure`)

### Eval types

| Type | Meaning |
|------|---------|
| `answer_verification` | LLM judge checks factual accuracy against an expected answer (within 1% tolerance for numeric questions) |
| `ambiguity_handling` | Agent should either ask a clarifying question or state its interpretation explicitly before proceeding |
| `known_failure` | Documented limitation — excluded from scoring (e.g. queries that exceed the 60s pipeline timeout) |

### Score progression (v1, SQLite)

| Iteration | Change | PASS rate |
|-----------|--------|-----------|
| Baseline | Original code | **27.8%** (5/18) |
| RAG fix + pitfalls | Ingested schema catalog as table chunks; added 6 pitfall rules; fixed bad sample query (`payment_value` → `oi.price`) | **55.6%** (10/18) |
| Few-shot examples + judge + retry | Added 6 worked SQL examples to system prompt; added empty-result retry; improved judge with tolerance enforcement and reasoning field; marked slow queries as `known_failure`; raised timeout 15s → 60s | **70.6%** (12/17) |

Average answer quality: **2.76 / 3.00**

### v2 (Postgres) regression check

The v1 eval was re-run on Postgres to check the migration. The first run scored lower (10/17), so the differences were investigated instead of accepted:

- Two ground-truth entries were wrong. **Q007**'s expected answer (7.24M) was the `payment_value` total, while its own SQL summed `price` (6.16M). **Q010**'s expected count (3,729) came from a `BETWEEN ... '2017-07-31'` range that dropped most of July 31 when compared against timestamps. Both were fixed and checked against Postgres.
- A single LLM-judged run turned out to be too noisy to trust, so the corrected dataset was run **three times** (`evals/eval_results_pg_clean_run{1,2,3}.json`):

| Run | PASS |
|-----|------|
| v1 (SQLite, one run) | 12/17 = 70.6% |
| v2 run 1 | 11/17 = 64.7% |
| v2 run 2 | 13/17 = 76.5% |
| v2 run 3 | 10/17 = 58.8% |
| **v2 mean** | **11.3/17 = 66.7%** |

**Findings**
- Run-to-run spread on the same code is about 18 points (58.8%–76.5%). The v1 figure is one run, so its own spread is unknown. The v2 mean is below v1's single number, but well inside the spread, so these runs give **no evidence of a regression** and are not enough to claim parity either.
- 13 of 17 questions gave the same result in all three runs, and all 13 match v1, including every date-handling question that had to be ported. The dialect change does not explain the variation.
- The instability comes from Q002, Q007, Q008 and Q011, which each flip in one run out of three.
- Q010 is genuinely ambiguous ("delivered in July" can mean purchase month or delivery month). The agent picked delivery month in an earlier batch and purchase month in all three clean runs. In one clean run it also re-created the `BETWEEN` end-of-month bug on its own (3,729). The dataset's expected answer used delivery month (3,455 vs 3,223), so the question failed whenever the agent chose the other reading. **Q010 has since been re-typed as `ambiguity_handling`** (the agent must ask, or state its interpretation and give numbers that match it), and a catalog pitfall now steers date ranges on timestamps to half-open ranges. These three clean runs were scored before that change, so they are not directly comparable with a new run.

### What was fixed and why (v1)

| Problem | Root cause | Fix |
|---------|-----------|-----|
| RAG retrieved wrong chunks for novel questions | Embeddings only covered metric names, not actual table/column content | Added `schema_catalog.yml` as table-level chunks to the embeddings store |
| Revenue by category was wrong (Q002, Q003) | Sample query in `.md` used `SUM(payment_value)` — order-level, double-counts across items | Rewrote `revenue_by_product_category` definition to use `SUM(order_items.price)` |
| English category names returned no results (Q009, Q011, Q012) | Model wrote `WHERE product_category_name = 'electronics'` — that column is in Portuguese | Added pitfall + few-shot example enforcing the translation table join |
| Delivery time used wrong column (Q012) | Model subtracted `order_estimated_delivery_date` instead of `order_purchase_timestamp` | Added pitfall + few-shot example distinguishing delivery time vs delay |
| Leads date filter was wrong (Q013, Q015) | Model added `AND lc.won_date BETWEEN ...` when only `first_contact_date` should be filtered | Added pitfall + two few-shot examples showing correct filter pattern |
| Products with zero reviews was wrong (Q016) | Model used `NOT IN (SELECT order_id FROM order_reviews)` — compared `product_id` to `order_id` | Added pitfall with correct LEFT JOIN path |
| Delivered order count off by 8 (Q001) | Missing `AND order_delivered_customer_date IS NOT NULL` filter | Added pitfall + few-shot example |
| Q017 blocked the entire eval run | 15s timeout too short for full pipeline; complex nested subquery exceeded it | Raised timeout to 60s; marked Q017 and Q003 as `known_failure` |
| Judge gave false FAILs (Q001, Q008, Q009) | Judge didn't enforce numeric tolerance; no reasoning to diagnose | Added explicit 1% tolerance instruction and `REASONING` field to judge prompt |

### Remaining failures

| ID | Issue | Classification |
|----|-------|---------------|
| Q002 | Fails in most runs because the judge wants exact revenue figures, not prose; passed once in three clean runs | Judge strictness / unstable |
| Q007 | Intermittently sums `payment_value` instead of `price` despite the catalog rule | Agent inconsistency |
| Q010 | "Delivered in July" is ambiguous. Now scored as `ambiguity_handling`. A spot check after adding the date-range pitfall (one sample) fixed the `BETWEEN` end-of-month bug, but the agent still mixed both readings in one query (grouped by purchase month, filtered by delivery date: 2,408 vs 3,114) without stating an interpretation | Ambiguity not surfaced (open) |
| Q013 | Model still writes `won_date` filter despite pitfall + two examples | `gpt-4o-mini` model ceiling |
| Q015 | SQL is now correct but insights only mention 2 of 7 channels | Insights presentation |
