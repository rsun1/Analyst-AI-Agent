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

# Schema & RAG
SCHEMA_DOC_PATH = os.getenv("SCHEMA_DOC_PATH") or str(BASE_DIR / "olist_schema_queries.md")
EMBEDDINGS_STORE_PATH = os.getenv("EMBEDDINGS_STORE_PATH") or str(BASE_DIR / "embeddings_store.json")

# Retrieval: top-k chunks (PRD: k <= 5)
TOP_K_CHUNKS = 5

# Self-correction: max retries for SQL execution
MAX_SQL_RETRIES = 2
