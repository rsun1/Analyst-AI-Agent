# Analytics AI Agent (agent folder)

The full documentation lives in the repository root: [`../README.md`](../README.md).

Quick start (from this folder):

```bash
docker compose up -d          # Postgres + pgvector
python -m migrate_to_postgres # one-time copy of olist.sqlite into Postgres
python -m ingest              # embed schema docs into pgvector
python -m agent               # CLI, or: streamlit run app.py
```
