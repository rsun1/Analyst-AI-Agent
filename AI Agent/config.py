"""
Configuration for the Analytics AI Agent.
Loads from .env and provides paths and settings.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Base directory: where this package lives (AI Agent folder)
BASE_DIR = Path(__file__).resolve().parent

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMBEDDING_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4o-mini"

# Database: read-only SQLite (path configurable via .env)
OLIST_DB_PATH = os.getenv("OLIST_DB_PATH") or str(BASE_DIR / "olist.sqlite")

# Postgres (v2). Admin role owns tables/migrations; reader role is SELECT-only
# and is the one the agent uses to execute generated SQL.
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = os.getenv("PG_PORT", "5432")
PG_DB = os.getenv("PG_DB", "olist")
PG_ADMIN_USER = os.getenv("PG_ADMIN_USER", "olist_admin")
PG_ADMIN_PASSWORD = os.getenv("PG_ADMIN_PASSWORD", "olist_admin_pw")
PG_READER_USER = os.getenv("PG_READER_USER", "olist_reader")
PG_READER_PASSWORD = os.getenv("PG_READER_PASSWORD", "olist_reader_pw")
PG_ADMIN_DSN = os.getenv("PG_ADMIN_DSN") or (
    f"postgresql://{PG_ADMIN_USER}:{PG_ADMIN_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"
)
PG_READER_DSN = os.getenv("PG_READER_DSN") or (
    f"postgresql://{PG_READER_USER}:{PG_READER_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"
)

# Schema & RAG
SCHEMA_DOC_PATH = os.getenv("SCHEMA_DOC_PATH") or str(BASE_DIR / "olist_schema_queries.md")
EMBEDDINGS_STORE_PATH = os.getenv("EMBEDDINGS_STORE_PATH") or str(BASE_DIR / "embeddings_store.json")

# Structured schema catalog (always injected into SQL prompts – not via RAG)
SCHEMA_CATALOG_PATH = os.getenv("SCHEMA_CATALOG_PATH") or str(BASE_DIR / "schema_catalog.yml")

# Retrieval: top-k chunks (PRD: k <= 5)
TOP_K_CHUNKS = 5

# Self-correction: max retries for SQL execution
MAX_SQL_RETRIES = 2

# Edit loop: max times user can decline and request changes before the agent stops (FR-2.5)
MAX_EDIT_ATTEMPTS = 5

# Multi-turn conversation: max history entries passed to LLM (3 user + 3 assistant = 6)
MAX_HISTORY_TURNS = 6

# Multi-query analysis
MULTI_QUERY_DOMAIN_CONTEXT = (
    "Olist Brazilian e-commerce database covering orders, customers, sellers, "
    "products, payments, reviews, and marketing leads"
)
MAX_SUB_QUESTIONS = 5
MIN_SUB_QUESTIONS = 2

# HTTP API (api.py)
# API_KEY: required for every endpoint except /health. If unset, protected endpoints
# return 503 (fail closed) instead of running unauthenticated.
API_KEY = os.getenv("API_KEY")
# Comma-separated list of browser origins allowed by CORS (empty = none).
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
# Rows returned to the client per query; the full count is still reported.
API_MAX_RESPONSE_ROWS = int(os.getenv("API_MAX_RESPONSE_ROWS", "1000"))

# Streamlit front end -> API (api_client.py). The UI talks to the API over HTTP only.
API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
