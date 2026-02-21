# Analytics AI Agent

Python application that lets stakeholders ask natural-language questions about the **olist.sqlite** database (Olist Brazilian e-commerce). The agent uses RAG over `olist_schema_queries.md`, proposes SQL with an explanation, and after approval runs the query and returns results plus plain-English insights.

## Requirements

- **Python 3.10+**
- **Dependencies (core):** `openai`, `python-dotenv`. Install with:  
  `pip install openai python-dotenv`  
  (The project `requirements.txt` may include additional packages; the agent only needs these for the described flow.)
- **`.env`** in this folder with `OPENAI_API_KEY=your_key`.
- **olist.sqlite** in this folder (or set `OLIST_DB_PATH` in `.env`).
- **olist_schema_queries.md** in this folder (or set `SCHEMA_DOC_PATH` in `.env`).

## Setup (one-time or when schema doc changes)

Ingest the schema document, chunk it, generate embeddings via OpenAI, and save to `embeddings_store.json`:

```bash
cd "AI Agent"
python -m ingest
```

Optional: pass custom paths:

```bash
python -m ingest path/to/olist_schema_queries.md path/to/embeddings_store.json
```

## Run the agent

```bash
cd "AI Agent"
python -m agent
```

You’ll be prompted for a question. Example: *"What was our GMV per month last year?"*

Alternatively, pass the question on the command line:

```bash
python -m agent "What was our GMV per month last year?"
```

Flow:

1. **Propose** – The agent retrieves relevant schema chunks (RAG), generates SQL and a short explanation, and shows them.
2. **Approve** – You confirm with `y` (or decline with `n`).
3. **Execute** – The SQL runs against `olist.sqlite` (read-only). On failure, the agent can auto-correct and retry (see PRD FR-3.4).
4. **Explain** – You get the result table and a plain-English summary with 1–3 business insights.

## Optional env vars

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | (required) | OpenAI API key. |
| `OLIST_DB_PATH` | `./olist.sqlite` | Path to SQLite DB. |
| `SCHEMA_DOC_PATH` | `./olist_schema_queries.md` | Schema + sample queries markdown. |
| `EMBEDDINGS_STORE_PATH` | `./embeddings_store.json` | Path to stored embeddings. |

## Architecture (summary)

- **ingest** – Chunks `olist_schema_queries.md` on `### definition:` / `### SQL query` units, embeds via OpenAI, saves chunks + vectors to JSON.
- **rag** – Loads store, embeds the user question, returns top-5 chunks by cosine similarity.
- **sql_generator** – Builds proposed SQL and explanation from question + RAG context (OpenAI Chat).
- **db** – Read-only SQLite connection (`file:...?mode=ro`); executes approved SQL.
- **sql_correction** – On execution error, asks the model for corrected SQL and retries.
- **insights** – Sends results + context to the model for summary and 1–3 insights.
- **agent** – CLI that ties the flow together: question → propose → approve → execute → explain.

All ML uses **OpenAI only** (embeddings + chat). No LangChain, FAISS, or other RAG/vector frameworks.
