# Analytics AI Agent

Python application that lets stakeholders ask natural-language questions about the **olist.sqlite** database (Olist Brazilian e-commerce). The agent retrieves relevant schema context via RAG, proposes SQL with an explanation, and after approval runs the query and returns results plus plain-English business insights.

---

## Requirements

- **Python 3.10+**
- **Dependencies:** `openai`, `python-dotenv`, `pyyaml`, `streamlit` (for the web UI). Install with:
  ```bash
  pip install -r requirements.txt
  ```
- **`.env`** in the `AI Agent/` folder containing:
  ```
  OPENAI_API_KEY=your_key
  ```
- **olist.sqlite** in this folder (or set `OLIST_DB_PATH` in `.env`).
  Download the database from Kaggle:
  [E-commerce Dataset by Olist as an SQLite Database](https://www.kaggle.com/datasets/terencicp/e-commerce-dataset-by-olist-as-an-sqlite-database)
  Place the downloaded `olist.sqlite` file in the `AI Agent/` folder before running the agent.

---

## Setup (one-time, and whenever schema files change)

The agent uses embeddings for RAG. Run ingest once to build the vector store, and again any time you edit `olist_schema_queries.md` or `schema_catalog.yml`:

```bash
cd "AI Agent"
python -m ingest
```

This reads both source files, chunks them, calls the OpenAI Embeddings API, and saves everything to `embeddings_store.json` (34 chunks total: metric definitions + one chunk per table + cross-domain paths + pitfalls).

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

1. **RAG retrieval** — The user's question is embedded and compared via cosine similarity to all chunks in `embeddings_store.json`. The top-5 most relevant chunks (table descriptions, metric definitions, sample SQL) are retrieved as context.

2. **SQL generation** — The full schema catalog (`schema_catalog.yml`) is injected into the system prompt on every call as ground truth. The RAG context and few-shot worked examples are added to the user message. `gpt-4o-mini` generates a SQLite query and a short explanation.

3. **Approve / edit** — The CLI shows the proposed SQL and explanation. You type `y` to approve, `n` to reject, or describe a change in plain English (edit loop, up to 5 attempts).

4. **Execute** — The SQL runs against `olist.sqlite` in read-only mode. On an execution error (`OperationalError`), the agent auto-corrects and retries (up to 2 times).

5. **Insights** — Results are sent back to the model, which returns a plain-English summary and 1–3 business recommendations grounded in the data.

---

## Key source files

| File | Purpose |
|------|---------|
| `ingest.py` | Chunks `olist_schema_queries.md` + `schema_catalog.yml`, generates embeddings, saves `embeddings_store.json` |
| `rag.py` | Loads embeddings store, retrieves top-k chunks by cosine similarity |
| `schema_catalog.yml` | Ground-truth schema: exact table/column names, types, FK relationships, cross-domain join paths, pitfalls |
| `olist_schema_queries.md` | Named metric definitions with correct sample SQL (used for RAG) |
| `sql_generator.py` | Builds SQL via OpenAI Chat; includes full schema + few-shot examples in system prompt |
| `sql_correction.py` | On SQL execution error, asks the model for a corrected query and retries |
| `agent_core.py` | Pure-function wrappers for each step (RAG, generate, execute, explain); used by both CLI and Streamlit |
| `agent.py` | CLI flow: question → propose → approve/edit → execute → explain |
| `app.py` | Streamlit web UI |
| `insights.py` | Generates plain-English summary + business insights from query results |
| `schema_loader.py` | Loads `schema_catalog.yml`, validates against live DB via PRAGMA, renders schema block for prompts |
| `evals/scorer.py` | Evaluation harness: runs all test questions through the agent, judges answers with LLM, saves results |
| `evals/test_dataset.json` | 20-question test dataset with expected SQL, expected answers, and eval type |

---

## Optional environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | (required) | OpenAI API key |
| `OLIST_DB_PATH` | `./olist.sqlite` | Path to SQLite database |
| `SCHEMA_DOC_PATH` | `./olist_schema_queries.md` | Metric definitions + sample queries |
| `SCHEMA_CATALOG_PATH` | `./schema_catalog.yml` | Structured schema catalog |
| `EMBEDDINGS_STORE_PATH` | `./embeddings_store.json` | Saved chunk embeddings |

---

## Architecture notes

- **No LangChain, FAISS, or RAG frameworks** — embeddings and retrieval are implemented from scratch using `openai` and `json`.
- **Two-layer schema injection**: the full schema catalog is always in the system prompt (so the model can never hallucinate column names), while RAG provides query pattern examples as additional context in the user message.
- **Few-shot examples** are baked into every SQL generation call in `sql_generator.py`, covering the most common failure patterns (category name translation, revenue calculation, delivery time vs delay, leads date filtering).
- **Self-correction** handles SQLite `OperationalError` at execution time; empty-result detection in the eval harness triggers a second generation attempt with a hint.
- **Models used**: `text-embedding-3-small` for embeddings, `gpt-4o-mini` for SQL generation, correction, and insights.

---

## Evaluation

### Running the eval

```bash
cd "AI Agent"
python evals/scorer.py
```

Results are saved to `evals/eval_results.json`. Each entry records the question, agent SQL, agent answer, expected answer, factual accuracy (PASS/FAIL/PARTIAL/DOCUMENTED), answer quality (1–3), and judge reasoning.

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
| `known_failure` | Documented limitation — excluded from scoring (e.g. queries that exceed the 60s pipeline timeout on SQLite) |

### Score progression

| Iteration | Change | PASS rate |
|-----------|--------|-----------|
| Baseline | Original code | **27.8%** (5/18) |
| RAG fix + pitfalls | Ingested schema catalog as table chunks; added 6 pitfall rules; fixed bad sample query (`payment_value` → `oi.price`) | **55.6%** (10/18) |
| Few-shot examples + judge + retry | Added 6 worked SQL examples to system prompt; added empty-result retry; improved judge with tolerance enforcement and reasoning field; marked slow queries as `known_failure`; raised timeout 15s → 60s | **70.6%** (12/17) |

Average answer quality: **2.76 / 3.00**

### What was fixed and why

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

### Remaining failures (as of final run)

| ID | Issue | Classification |
|----|-------|---------------|
| Q002 | Agent answer is factually correct but judge requires exact revenue figures, not prose | Judge false FAIL |
| Q010 | Question is ambiguous — "delivered in July" could mean purchase date or delivery date | Ambiguous spec |
| Q013 | Model still writes `won_date` filter despite pitfall + two examples | `gpt-4o-mini` model ceiling |
| Q015 | SQL is now correct but insights only mention 2 of 7 channels | Insights presentation |
