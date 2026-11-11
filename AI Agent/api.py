"""
HTTP API for the Analytics AI Agent (FastAPI).

Stateless by design: the client carries the conversation history and the current
SQL; the server holds no session. Human-in-the-loop flow:

    POST /ask      question (+ history)            -> proposed SQL + explanation
    POST /revise   sql + plain-English instruction -> revised SQL (repeat as needed)
    POST /execute  approved SQL                    -> rows + plain-English insights
    POST /scope    question                        -> is it broad (multi-query)?
    POST /analyze  broad question (+ history)      -> sub-analyses + synthesised report
    GET  /schema/status                            -> live table count + catalog drift
    POST /feedback thumbs up/down on a result      -> logged for later evaluation
    GET  /health   liveness + database check (no auth)

Security model
  * Every endpoint except /health needs the `X-API-Key` header. If API_KEY is not
    configured the protected endpoints return 503 (fail closed).
  * /execute runs SQL sent by the client. The first guard (validate_read_only_sql)
    is a cheap sanity check; the real guard is the database: queries run as the
    SELECT-only `olist_reader` role in a read-only session with a statement timeout.
  * Retrieval context is recomputed server-side, never accepted from the client.

Endpoints are plain `def` (not `async def`) on purpose: the agent steps make
blocking OpenAI and database calls, and FastAPI runs sync endpoints in a thread
pool so one slow request does not stall the others.

Run:  uvicorn api:app --reload        (docs at /docs, schema at /openapi.json)
"""
import contextlib
import json
import logging
import re
import secrets
import time
import uuid
from typing import Literal

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import agent_core
from config import API_KEY, API_MAX_RESPONSE_ROWS, CORS_ORIGINS
from db import close_pool, get_connection
from logger import log_feedback_event, log_query_event
from multi_query import is_broad_question, run_multi_query_analysis
from schema_loader import validate_catalog_against_db
from sql_generator import UNCLEAR_INSTRUCTION

log = logging.getLogger("analytics_agent.api")
if not log.handlers:  # uvicorn only configures its own loggers; emit our access log as plain JSON lines
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False

@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    close_pool()  # release pooled database connections on shutdown


app = FastAPI(
    lifespan=lifespan,
    title="Analytics AI Agent API",
    version="2.0.0",
    description="Natural-language questions about the Olist database: propose SQL, "
    "let a human revise and approve it, execute read-only, explain the results.",
)

if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["X-API-Key", "Content-Type"],
    )


@app.middleware("http")
async def access_log(request: Request, call_next):
    """One JSON log line per request (method, path, status, duration, request id)."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        log.info(json.dumps({
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": status,
            "ms": round((time.perf_counter() - start) * 1000),
        }))


# ── Auth ──────────────────────────────────────────────────────────────────────

def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if not API_KEY:
        raise HTTPException(503, "Server is not configured: API_KEY is not set.")
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(401, "Missing or invalid API key.")


# ── SQL validation (first guard; the database role is the real one) ───────────

MAX_SQL_CHARS = 10_000
_LEADING_COMMENTS = re.compile(r"^(\s+|--[^\n]*\n|/\*.*?\*/)*", re.DOTALL)


def validate_read_only_sql(sql: str) -> str:
    """
    Return the cleaned SQL, or raise ValueError. Allows a single SELECT/WITH
    statement. Rejects a ';' anywhere except as the final character, so a
    semicolon inside a string literal is (knowingly) rejected too.
    """
    cleaned = sql.strip()
    if not cleaned:
        raise ValueError("SQL is empty.")
    if len(cleaned) > MAX_SQL_CHARS:
        raise ValueError(f"SQL is longer than {MAX_SQL_CHARS} characters.")
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].rstrip()
    if ";" in cleaned:
        raise ValueError("Only a single SQL statement is allowed.")
    body = _LEADING_COMMENTS.sub("", cleaned, count=1)
    if not re.match(r"(select|with)\b", body, re.IGNORECASE):
        raise ValueError("Only SELECT queries (optionally starting with WITH) are allowed.")
    return cleaned


# ── Schemas ───────────────────────────────────────────────────────────────────

class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)
    sql: str | None = Field(default=None, max_length=MAX_SQL_CHARS)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=12,
                                       description="Prior turns, oldest first, for follow-up questions.")


class ProposedSQL(BaseModel):
    sql: str
    explanation: str


class ReviseRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    sql: str = Field(min_length=1, max_length=MAX_SQL_CHARS)
    explanation: str = Field(default="", max_length=4000)
    instruction: str = Field(min_length=1, max_length=1000,
                             description="Plain-English change, e.g. 'group by week instead of month'.")
    last_db_error: str | None = Field(default=None, max_length=4000,
                                      description="Database error from the previous run, if any.")


class ReviseResponse(BaseModel):
    unclear: bool = Field(description="True if the instruction was too vague; sql is then unchanged.")
    sql: str
    explanation: str


class RevisionStep(BaseModel):
    type: Literal["llm_revision", "direct_edit"]
    instruction: str | None = None
    sql_after: str


class ExecuteRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    sql: str = Field(min_length=1, max_length=MAX_SQL_CHARS, description="The approved SQL.")
    proposed_sql: str | None = Field(default=None, max_length=MAX_SQL_CHARS,
                                     description="The SQL originally proposed, for the correction log.")
    revision_history: list[RevisionStep] = Field(default_factory=list, max_length=20)
    include_insights: bool = True


class ExecuteResponse(BaseModel):
    columns: list[str]
    rows: list[list]
    row_count: int = Field(description="Total rows the query returned.")
    truncated: bool = Field(description="True if rows was cut to the response row limit.")
    db_errors: list[str] = Field(description="Errors hit (and auto-corrected) before success.")
    insights: str | None = None
    insights_error: str | None = None


class ScopeRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class ScopeResponse(BaseModel):
    broad: bool = Field(description="True if the question needs several sub-analyses.")


class AnalyzeRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=12)


class SubResult(BaseModel):
    sub_question: str
    sql: str
    columns: list[str]
    rows: list[list]
    summary: str
    error: str | None = None


class AnalyzeResponse(BaseModel):
    sub_results: list[SubResult]
    report_markdown: str
    failed_count: int


class SchemaStatus(BaseModel):
    live_tables: int
    drift_warnings: list[str]


class FeedbackRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    sql: str = Field(max_length=MAX_SQL_CHARS)
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=2000)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health", tags=["ops"])
def health():
    """Liveness plus a database round-trip. No auth, so load balancers can call it."""
    try:
        with get_connection() as conn:
            conn.execute("SELECT 1")
    except Exception:
        raise HTTPException(503, "Database unreachable.")
    return {"status": "ok"}


@app.post("/ask", response_model=ProposedSQL, tags=["agent"], dependencies=[Depends(require_api_key)])
def ask(req: AskRequest):
    """Retrieve context and propose SQL with a short explanation. Nothing is executed."""
    history = [t.model_dump(exclude_none=True) for t in req.history]
    rag_context = agent_core.step_rag(req.question)
    sql, explanation = agent_core.step_generate(req.question, rag_context, history or None)
    if not sql:
        raise HTTPException(502, "The model did not return a usable SQL query. Try rephrasing.")
    return ProposedSQL(sql=sql, explanation=explanation)


@app.post("/revise", response_model=ReviseResponse, tags=["agent"], dependencies=[Depends(require_api_key)])
def revise(req: ReviseRequest):
    """Apply a plain-English change to the proposed SQL. Context is re-retrieved server-side."""
    rag_context = agent_core.step_rag(req.question)
    sql, explanation = agent_core.step_revise(
        original_question=req.question,
        rag_context=rag_context,
        current_sql=req.sql,
        current_explanation=req.explanation,
        revision_instruction=req.instruction,
        last_db_error=req.last_db_error,
    )
    if sql == UNCLEAR_INSTRUCTION:
        return ReviseResponse(unclear=True, sql=req.sql, explanation=req.explanation)
    if not sql:
        raise HTTPException(502, "The model did not return a usable SQL query. Try rephrasing.")
    return ReviseResponse(unclear=False, sql=sql, explanation=explanation)


@app.post("/execute", response_model=ExecuteResponse, tags=["agent"], dependencies=[Depends(require_api_key)])
def execute(req: ExecuteRequest):
    """Run the approved SQL read-only (auto-correcting recoverable errors), then explain the results."""
    try:
        sql = validate_read_only_sql(req.sql)
    except ValueError as e:
        raise HTTPException(400, str(e))

    try:
        columns, rows, db_errors = agent_core.step_execute(sql)
    except (psycopg.errors.InsufficientPrivilege, psycopg.errors.ReadOnlySqlTransaction):
        raise HTTPException(403, "The database does not allow this statement (read-only access).")
    except psycopg.Error as e:
        raise HTTPException(422, f"Query failed after retries: {str(e).strip()}")

    log_query_event(
        question=req.question,
        proposed_sql=req.proposed_sql or sql,
        revision_history=[s.model_dump(exclude_none=True) for s in req.revision_history],
        final_sql=sql,
        db_errors=db_errors,
        row_count=len(rows),
    )

    insights = insights_error = None
    if req.include_insights:
        try:
            insights = agent_core.step_explain(req.question, sql, columns, rows)
        except Exception as e:  # results are still useful without the summary
            insights_error = f"Could not generate insights: {e}"

    shown = rows[:API_MAX_RESPONSE_ROWS]
    return ExecuteResponse(
        columns=columns,
        rows=jsonable_encoder([list(r) for r in shown]),
        row_count=len(rows),
        truncated=len(rows) > len(shown),
        db_errors=db_errors,
        insights=insights,
        insights_error=insights_error,
    )


@app.post("/scope", response_model=ScopeResponse, tags=["agent"], dependencies=[Depends(require_api_key)])
def scope(req: ScopeRequest):
    """Classify a question as single-query or broad (needs a multi-part analysis)."""
    return ScopeResponse(broad=is_broad_question(req.question))


@app.post("/analyze", response_model=AnalyzeResponse, tags=["agent"], dependencies=[Depends(require_api_key)])
def analyze(req: AnalyzeRequest):
    """
    Multi-query analysis for a broad question: decompose, run each sub-query, synthesise.
    Unlike /ask this executes SQL without a human approval step (every sub-query still runs
    through the read-only role). It makes many model calls, so it is slow (tens of seconds).
    """
    history = [t.model_dump(exclude_none=True) for t in req.history]
    try:
        report = run_multi_query_analysis(req.question, history or None)
    except Exception as e:
        raise HTTPException(502, f"Analysis failed: {e}")
    return AnalyzeResponse(
        sub_results=[
            SubResult(
                sub_question=r.sub_question, sql=r.sql, columns=r.columns,
                rows=jsonable_encoder([list(row) for row in r.rows[:200]]),
                summary=r.summary, error=r.error,
            )
            for r in report.sub_results
        ],
        report_markdown=report.report_markdown,
        failed_count=report.failed_count,
    )


@app.get("/schema/status", response_model=SchemaStatus, tags=["ops"], dependencies=[Depends(require_api_key)])
def schema_status():
    """Live table count and any drift between the schema catalog and the database."""
    try:
        block = agent_core.step_introspect_schema()
        drift = validate_catalog_against_db() or []
    except Exception as e:
        raise HTTPException(503, f"Could not read the schema: {e}")
    return SchemaStatus(live_tables=block.count(chr(10) + "TABLE: "), drift_warnings=list(drift))


@app.post("/feedback", status_code=204, tags=["agent"], dependencies=[Depends(require_api_key)])
def feedback(req: FeedbackRequest):
    """Record a thumbs up/down on a result. Used later to find weak spots in the agent."""
    log_feedback_event(req.question, req.sql, req.rating, req.comment)
