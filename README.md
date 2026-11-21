# Analytics AI Agent

Python application that lets stakeholders ask natural-language questions about the **Olist** Brazilian e-commerce dataset, stored in **PostgreSQL**. The agent retrieves relevant schema context via RAG (pgvector), proposes SQL with an explanation, and after approval runs the query and returns results plus plain-English business insights.

> **Versions.** `main` is **v2**: Postgres + pgvector. The original SQLite + JSON-vector-store implementation is preserved at the git tag `v1-constrained`.

## Live demo

**Try it: https://analyst-ai-agent-olist.streamlit.app/**

Ask a question about the Olist e-commerce data in plain English. The agent proposes SQL and an explanation; you approve or edit it, then it runs the query read-only and explains the result.

```
Streamlit Cloud (UI)  ->  HTTPS + API key  ->  FastAPI on Koyeb (AI Builder Space)  ->  Neon Postgres + pgvector
                                                      |
                                                      +-> OpenAI (SQL generation, embeddings, insights)
```

- **Free tiers, not always on.** Each layer sleeps when idle, so the first request after a quiet period can take several extra seconds (or need one retry). Hosting is free for 12 months from the first deploy (2026-11-13).
- **Read-only by design.** Generated SQL runs as a `SELECT`-only database role in a read-only session with a statement timeout and a row cap.
- **Logging.** Questions, SQL edits and feedback are stored in Postgres (an INSERT-only role) to track human corrections.
- **Also on AWS.** The same app runs on ECS Fargate with RDS; see [Deploying on AWS](#deploying-on-aws). The demo link for that deployment is shared on request.
- **Measured on the deployed stack.** The eval suite can target a live API (`EVAL_API_URL` / `EVAL_API_KEY`, see [Evaluation](#evaluation)): 12/17 PASS (70.6%), the same as the v1 baseline. Details of the deploy: [`docs/streamlit-cloud.md`](docs/streamlit-cloud.md).

## Deploying on AWS

The same two containers (API and UI) also run on AWS. This is the "production-shaped" deployment: a managed database, containers on Fargate behind a load balancer, secrets kept out of the image, and logs in CloudWatch. It is deployed with the AWS CLI and a few PowerShell scripts, not yet with infrastructure-as-code.

```
Browser ──HTTPS──> Application Load Balancer ──> ECS Fargate: UI (Streamlit)
                              │                          │  HTTPS + X-API-Key
                              └────────────────────────> ECS Fargate: API (FastAPI)
                                                              │            │
                                                     RDS PostgreSQL        └──> OpenAI
                                                     (+ pgvector)
        Secrets Manager: OPENAI_API_KEY, API_KEY, PG_READER_DSN, PG_LOGGER_DSN      CloudWatch Logs: /ecs/olist-api, /ecs/olist-ui
```

| Piece | What it is |
|---|---|
| Region | `us-east-2` |
| Images | Two ECR repositories, `olist-api` (`Dockerfile`) and `olist-ui` (`Dockerfile.ui`). The API image is about 64 MB and runs as a non-root user; the UI image ships only the modules the UI imports. |
| Compute | One ECS cluster, `olist`, with two **ECS Express Mode** services on Fargate: API (0.25 vCPU, 0.5 GB, 1 to 2 tasks) and UI (0.5 vCPU, 1 GB). Express Mode creates the load balancer, target groups, security groups and auto scaling. |
| Database | RDS for PostgreSQL 18, `db.t4g.micro`, 20 GB gp3, single AZ, pgvector 0.8. The master password is managed by RDS in Secrets Manager. |
| Secrets | One secret, `olist/app`, holds the four runtime values and is injected into the containers as environment variables. Nothing sensitive is in the image, the repo or the task definition. |
| IAM | `olist-ecs-execution-role` (pull images, write logs, read **only** `olist/app`) and `olist-ecs-infrastructure-role` (AWS managed policy for Express Mode). |
| Logs | CloudWatch log groups with 14 days of retention. The API writes one JSON line per request. |

**Security model.** The agent's SQL runs as a `SELECT`-only database role in a read-only session with a statement timeout; a separate `INSERT`-only role writes the logs. The database accepts connections only from the API service's security group (plus one admin IP while loading data). Every API endpoint except `/health` needs the `X-API-Key` header, and the key is held by the UI container, not the browser. The public URL has **no login**, so anyone with the link can use the UI; see the limits below.

### Deploy it yourself

The steps below are what produced the running deployment. You need an AWS account, the AWS CLI (logged in, for example `aws login --profile olist-aws`), Docker and PowerShell. More detail is in [`docs/aws-deploy.md`](docs/aws-deploy.md).

```powershell
# 1. Database: create an RDS PostgreSQL instance (console or CLI), then load the data.
#    Reads the master password from Secrets Manager and never prints it.
$env:PG_READER_PASSWORD = "<long letters and digits>"
$env:PG_LOGGER_PASSWORD = "<long letters and digits>"
.\scripts\aws\load_rds.ps1 -Endpoint <rds-endpoint> -SecretArn <master-secret-arn> [-WithSkills]

# 2. Runtime secrets (reads the OpenAI key from "AI Agent\.env"; generates API_KEY if unset)
.\scripts\aws\create_app_secret.ps1 -Endpoint <rds-endpoint>

# 3. Images: build both and push them to ECR (creates the repositories)
.\scripts\aws\push_images.ps1 -Region us-east-2 -Profile olist-aws -Tag v1

# 4. IAM roles, ECS cluster and log groups (the two roles are described in the table above)

# 5. Services: fill the placeholders in deploy/ecs/*.template.json, then
aws ecs create-express-gateway-service --cli-input-json file://deploy/ecs/api-service.template.json
aws ecs create-express-gateway-service --cli-input-json file://deploy/ecs/ui-service.template.json
```

After the API service is up, allow its security group to reach port 5432 on the database's security group, and put the API's URL in the UI service's `API_URL`. Express Mode returns each service's HTTPS URL when it is created.

**Releasing a new version.** Push images with a new tag, then run `aws ecs update-express-gateway-service` with the full container definition and the new image. A rollout takes about 7 minutes while old and new tasks overlap. Rolling back means pointing the service at the previous tag, so keep the last known-good tag in ECR.

**Switching the orchestrator on.** Run `python -m ingest_skills` against the database (the `-WithSkills` flag above does it) and set `USE_SKILLS=1` on the API service. The API then routes each question to a metric skill, to a dashboard link, or to a "not computable" explanation. Set `UI_BASE_URL` to the UI's address so dashboard links point to it.

### What it costs

These are estimates from AWS list prices, so check the Billing console. The load balancer and RDS are the main items, and Fargate is small at these sizes. Expect roughly USD 55 to 70 a month while everything runs; a new account's credits cover the first months. To stop paying, delete both Express services (`aws ecs delete-express-gateway-service`) and stop or delete the RDS instance. Set a budget alert before you start.

### Gotchas (so you don't have to hit them)

- **ECR login fails on Windows with `aws login` credentials.** The ECR token (about 4,000 bytes) is larger than the Windows Credential Manager accepts (`The stub received bad data`). `push_images.ps1` logs in with a temporary Docker config that stores the token in a plain file, deleted when the script ends. It also pipes the token inside `cmd.exe`, because PowerShell's pipe corrupts it.
- **`.dockerignore` and Markdown.** The skills are `.md` files, which an ignore rule excluded, so they were missing from the image until an explicit `!` exception was added.
- **A 503 right after creating a service.** The load balancer returns 503 until its health checks pass (a few minutes). The container logs show the health check hitting `/health`, which also proves the database connection.
- **A new account can lack the ECS service-linked role.** The first `create-express-gateway-service` call can fail with "Unable to assume the service linked role" for a moment after the roles are created; retrying works.
- **Connection pooling.** The app sets `statement_timeout` and a read-only transaction as connection options, and a transaction pooler rejects these, so connect to the database directly.

### Known limits and next steps

- **Public URL without a login.** Fine for a short demo with an OpenAI spending limit, not for anything longer. Next: restrict the load balancer to known IPs or put authentication in front of the UI.
- **Generated hostname.** A custom domain needs a certificate and changes made outside Express Mode; not done.
- **No infrastructure-as-code or CI/CD.** The steps above are scripts and CLI calls. Next: rebuild the same stack as plain ECS Fargate plus an ALB with Terraform or CDK, and write down what Express Mode hides.
- **RDS is still reachable from one admin IP** (used to load data). It should be switched to private once the data is loaded.
- **Observability stops at logs.** There are no alarms, no failure dashboard by type, and no evals in CI against the deployed endpoint yet.
- **Accuracy.** About 70% on the evaluation set (12 of 17 scored questions). The approval step exists so a person checks the SQL before it runs.

---

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
  pip install -r requirements-agent.txt
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
python -m setup_logging_db --import-jsonl   # log tables + INSERT-only role; imports the sample query_log.jsonl
python -m setup_workflow_db                 # only for the dispute workflow agent (see its section below)

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

The UI is a thin client: it talks to the HTTP API below (through `api_client.py`) and imports none of the agent code. Start the API first, then the UI, with the same `API_KEY` in both environments (or in `.env`):

```bash
cd "AI Agent"
uvicorn api:app --port 8000          # terminal 1
streamlit run app.py                 # terminal 2 (set API_URL if the API is not on localhost:8000)
```

The CLI (`python -m agent`) still calls the agent code directly.

### HTTP API

```bash
cd "AI Agent"
export API_KEY=choose-a-long-random-string     # PowerShell: $env:API_KEY = "..."
uvicorn api:app --reload                       # interactive docs at http://localhost:8000/docs
```

Stateless human-in-the-loop flow; the client carries the conversation and the current SQL. Every endpoint except `/health` needs an `X-API-Key` header, and if `API_KEY` is not set they return 503 instead of running open.

| Endpoint | Purpose |
|----------|---------|
| `POST /ask` | question (+ optional history) → proposed SQL + explanation. Nothing is executed. |
| `POST /revise` | current SQL + plain-English instruction → revised SQL (or `unclear: true`) |
| `POST /execute` | approved SQL → rows, auto-correction errors, plain-English insights |
| `POST /scope` | is this question broad enough to need a multi-part analysis? |
| `POST /analyze` | broad question → sub-analyses and a synthesised report (slow; runs its sub-queries without a per-query approval step) |
| `GET /schema/status` | live table count and any drift between the schema catalog and the database |
| `POST /feedback` | thumbs up/down on a result, stored in Postgres (`app_logs.feedback_events`) |
| `GET /health` | liveness plus a database round-trip (no key) |

`/execute` runs SQL sent by the client, so it has two guards: a cheap check that only a single `SELECT`/`WITH` statement is allowed, and, the one that actually matters, the database's own read-only role and session. A data-modifying CTE passes the first check and is stopped by the second (the API returns 403). Retrieval context is recomputed on the server and never accepted from the client. Design notes: [`docs/api-notes.md`](docs/api-notes.md).

Tests (agent steps mocked; the database tests use the local Postgres and are skipped if it is down):

```bash
cd "AI Agent"
python -m pytest tests -q
```

---

## How it works (step by step)

1. **RAG retrieval** — The user's question is embedded and the top-5 most similar chunks (table descriptions, metric definitions, sample SQL) are fetched from the `schema_chunks` table with a pgvector cosine-distance query (`ORDER BY embedding <=> ...`).

2. **SQL generation** — The full schema catalog (`schema_catalog.yml`) is injected into the system prompt on every call as ground truth. The RAG context and few-shot worked examples are added to the user message. `gpt-4o-mini` generates a PostgreSQL query and a short explanation.

3. **Approve / edit** — The CLI shows the proposed SQL and explanation. You type `y` to approve, `n` to reject, or describe a change in plain English (edit loop, up to 5 attempts).

4. **Execute** — The SQL runs as the `olist_reader` role (`SELECT`-only) in a read-only session with a 15 s statement timeout and a row cap, so writes and DDL are rejected by the database itself. On a recoverable error (bad column, syntax, type mismatch), the agent auto-corrects and retries (up to 2 times). Permission errors are not retried.

5. **Insights** — Results are sent back to the model, which returns a plain-English summary and 1–3 business recommendations grounded in the data.

---

## Dispute workflow agent (refund approvals)

The analytics agent above only reads. This second agent **acts**: a customer disputes an order, a written policy decides what happens, doubtful cases wait for a human, money moves through a (mock) payment API, and every step lands in an append-only audit trail. It reuses the same Postgres, the Olist tables as order history, the API and the UI.

```
POST /disputes ─▶ service.submit_dispute()  (one transaction, per-order advisory lock)
                    1. order facts          read-only role: status, amount paid, delivery dates, review score
                    2. rules.evaluate()     ordered, explainable rules; the first match decides
                    3. LLM assist (optional) can only escalate an auto-approval to a human, never approve
                    4. store.transition()   lock row -> check the state machine -> update status + write audit
                        │
        reject ─────────┼───────── needs_review ──────────── auto_approve
          ▼                              ▼                          ▼
      rejected               pending_review + approval row     auto_approved ─▶ refund
      (end)                  (persisted in Postgres)
                                         │  POST /approvals/{id}/decision  (X-Reviewer: name)
                                         ▼
                             approved ─▶ refund       rejected (end)

refund ─▶ refund_executed (end)  |  refund_failed ─▶ retry (new idempotency key) ─▶ after 3 failures: back to human review
```

### Policy (`workflow/rules.py`, written up in [`docs/refund-policy.md`](docs/refund-policy.md))

Rules run in priority order and the first match wins, so the order of the list is the policy. When unsure the answer is a human, never an automatic approval.

| Rule | Condition | Outcome |
|------|-----------|---------|
| R02 | another dispute on the order is still open | reject |
| R03 / R03B | order canceled or unavailable / not delivered yet | reject / human |
| R04 | nothing paid, or more than what is still refundable (paid minus already refunded) | reject |
| R05B / R05 | delivered but no delivery date / more than 30 days since delivery | human ("I cannot judge this") / reject |
| R06 | the claim contradicts the records (says "not received" but delivered and a 4-5 star review; says "late" but arrived on time) | human, `agent_uncertain` |
| R07 / R08 | amount at or above 450 BRL / 2+ refunds for this person in 90 days | human, `policy` |
| R09 / R10 | late delivery with a 1-2 star review and at most 100 BRL / any claim of at most 100 BRL inside the window | auto-approve |
| R99 | nothing else applies | human, `policy` |

The thresholds (100 and 450 BRL are about the median and the 95th percentile of order value) come from the Olist data, not from a business owner, so they are a starting point. Unknown orders and malformed amounts are rejected with a 400 and nothing is stored.

### What it is designed to guarantee

| Guarantee | How | Where it is tested |
|-----------|-----|--------------------|
| Only legal state changes | one table of allowed moves, one `transition()` entry point, plus a database `CHECK` on the status values | `test_workflow_states.py`, `test_workflow_store.py` |
| No half-finished disputes | every service call is one transaction; status and audit row commit together or not at all | rollback tests in `test_workflow_store.py` |
| Approvals survive a restart and cannot be doubled | approvals are rows in Postgres; row locks serialise two reviewers, so one wins and the other gets a 409 | `test_t10_*` in `test_workflow_service.py` |
| Money moves at most once | idempotency key `dispute-<id>-attempt-<n>`; a replayed key returns the first result; the refundable amount shrinks after each refund | `test_workflow_service.py` |
| The audit trail cannot be rewritten by the app | the `olist_workflow` role has INSERT and SELECT on `audit_log`, no UPDATE or DELETE; it also cannot read the Olist tables | `test_workflow_permissions.py` connects as that role and shows UPDATE, DELETE and TRUNCATE on `audit_log` are refused, nothing in the schema can be deleted, the schema cannot be altered and the Olist tables cannot be read (the tests were checked to fail when the role is granted UPDATE); `test_workflow_replay.py` shows that an edited trail is detected |
| The model cannot approve anything | `tighten()` is the only way an opinion changes a decision and it only turns an approval into a human review; customer text is treated as untrusted input; a model outage falls back to the pure rules | `test_workflow_llm.py` (including a sweep over every decision and opinion) |
| Any decision can be reconstructed | `python -m workflow.replay <id>` rebuilds who, when, which rule, which evidence and why from the audit log alone and checks that the trail is consistent | `test_workflow_replay.py` |

Example replay of a high-value dispute that a person approved:

```
sent_to_human_review: submitted -> pending_review [R07_HIGH_VALUE]
    why: Requested 600 is at or above the high-value limit (450).
    evidence: order status=delivered, paid=650.34, requested=600, delivered 2018-04-30 vs estimate 2018-05-09, review score=4, ...
human_approved: pending_review -> approved          (human:alice)
refund_succeeded: approved -> refund_executed       (system:mock-payments)
Audit check: OK
```

### Run it

```bash
cd "AI Agent"
python -m setup_workflow_db                   # workflow schema + the olist_workflow role (safe to re-run)
uvicorn api:app --port 8000                   # the workflow routes are part of the same API
streamlit run app.py                          # sidebar: "Dispute desk" (?page=disputes)
```

| Endpoint | Purpose |
|----------|---------|
| `POST /disputes` | submit order id, amount and reason; returns the decision (201). `as_of` simulates "today" because the Olist data is from 2016-2018 |
| `GET /disputes/{id}` · `/audit` · `/replay` | current state · raw audit rows · the decision story with a consistency check |
| `POST /disputes/{id}/retry-refund` | retry a failed refund |
| `GET /approvals?status=pending` | the review queue |
| `POST /approvals/{id}/decision` | a human approves or rejects (`X-Reviewer` header is required and recorded) |

Errors: 400 bad input or unknown order, 404 unknown id, 409 the dispute is not in a state that allows it (already decided, illegal move), 422 malformed body. A policy rejection is a normal 201 with the rule and reason.

Optional LLM assist (classify the free-text reason, a second opinion that can only escalate, a short brief for the reviewer) is **off** by default: set `USE_LLM_ASSIST=1` together with `OPENAI_API_KEY`.

### Known limits

- **Mock payments only.** The gateway is a fake with an idempotency key and injectable failures. It is called while the dispute's row lock is held, which is fine for a mock; a real provider needs an outbox or a two-phase flow.
- **Reviewer identity is a name, not a login.** `X-Reviewer` is trusted behind the shared API key, and nothing stops the person who submitted a dispute from approving it. Real use needs per-user authentication and separation of duties.
- **Not deployed yet.** The AWS deployment predates this agent: the production database has no `workflow` schema or role. The local default password for `olist_workflow` is for development only.
- **The LLM assist has only been tested with fakes.** The guardrails are tested; how good the model's classification and second opinion are has not been measured.
- **The dispute history uses the real clock** (90-day refund count) while the order timeline uses the simulated `as_of`.
- **No list or search endpoint** for disputes; the UI tracks the ones submitted in the session.
- Tests: 232 of the 315 tests in `tests/` cover this agent (`test_workflow_*.py`, `test_disputes_page.py`); the database ones skip when Postgres is down.

---

## Key source files

| File | Purpose |
|------|---------|
| `setup_workflow_db.py` | Creates the `workflow` schema (disputes, approvals, refund attempts, append-only audit log) and the `olist_workflow` role; never drops anything |
| `workflow/states.py` | Dispute states and the only allowed moves between them |
| `workflow/rules.py` · `models.py` · `facts.py` | The refund policy as ordered rules; the data types; loading order facts (read-only role) and dispute history |
| `workflow/store.py` | Database access; `transition()` is the single atomic way to change a status |
| `workflow/service.py` · `payments_mock.py` | Submit, decide and retry flows (one transaction each); the fake idempotent payment gateway |
| `workflow/llm_assist.py` | Optional model help that can only escalate to a human |
| `workflow/replay.py` · `workflow/api.py` | Decision replay and consistency check; the HTTP routes for the workflow |
| `disputes_page.py` | Streamlit "Dispute desk": submit, review queue, decision trail |

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Runs `pgvector/pgvector:pg16` locally |
| `setup_logging_db.py` | Creates the `app_logs` schema (query and feedback event tables) and the INSERT-only `olist_logger` role; never drops anything |
| `logger.py` | Writes query and feedback events to Postgres; falls back to a JSON line on stdout if the write fails |
| `migrate_to_postgres.py` | One-off copy of `olist.sqlite` into Postgres; converts date columns to `TIMESTAMP`; creates the read-only role |
| `ingest.py` | Chunks `olist_schema_queries.md` + `schema_catalog.yml`, generates embeddings, stores them in the `schema_chunks` pgvector table |
| `rag.py` | Embeds the question and retrieves top-k chunks by cosine distance inside Postgres |
| `db.py` | Read-only Postgres connection, live schema introspection (`information_schema`), query execution |
| `schema_catalog.yml` | Ground-truth schema: exact table/column names, types, FK relationships, cross-domain join paths, pitfalls |
| `olist_schema_queries.md` | Named metric definitions with correct sample SQL (used for RAG) |
| `sql_generator.py` | Builds SQL via OpenAI Chat; includes full schema + few-shot examples in system prompt |
| `sql_correction.py` | On SQL execution error, asks the model for a corrected query and retries |
| `sql_errors.py` | Classifies Postgres errors (missing column/table, syntax) into hints for the correction prompt |
| `agent_core.py` | Pure-function wrappers for each step (RAG, generate, execute, explain); used by the CLI and the API |
| `agent.py` | CLI flow: question → propose → approve/edit → execute → explain |
| `api.py` | FastAPI service: `/ask`, `/revise`, `/execute`, `/scope`, `/analyze`, `/schema/status`, `/feedback`, `/health` |
| `api_client.py` | HTTP client the Streamlit UI uses to call the API |
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
| `PG_LOGGER_USER` / `PG_LOGGER_PASSWORD` | `olist_logger` / `olist_logger_pw` | INSERT-only role the API uses to write log events (**local-dev defaults**) |
| `PG_READER_USER` / `PG_READER_PASSWORD` | `olist_reader` / `olist_reader_pw` | SELECT-only role used by the agent (**local-dev defaults**) |
| `PG_WORKFLOW_USER` / `PG_WORKFLOW_PASSWORD` / `PG_WORKFLOW_DSN` | `olist_workflow` / `olist_workflow_pw` / built from the above | Role for the dispute workflow: read/write on the `workflow` schema, INSERT-only on its audit log (**local-dev defaults; set a strong password anywhere shared**) |
| `USE_LLM_ASSIST` / `LLM_ASSIST_TIMEOUT_S` | `0` / `8` | Turns on the optional model help in the dispute workflow, and its per-call timeout in seconds |
| `PG_ADMIN_DSN` / `PG_READER_DSN` | built from the above | Full connection strings; override the individual settings |
| `API_KEY` | (none) | Key required in the `X-API-Key` header; unset means the API refuses requests (503) |
| `DB_POOL_MIN` / `DB_POOL_MAX` | `1` / `10` | Size of the API's database connection pool (per process) |
| `API_URL` | `http://localhost:8000` | Where the Streamlit UI finds the API |
| `CORS_ORIGINS` | (empty) | Comma-separated browser origins allowed to call the API; empty means none |
| `API_MAX_RESPONSE_ROWS` | `1000` | Rows returned per `/execute`; the full count is still reported |
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

To run the same eval against a deployed API instead of the local agent code (retries gateway blips so infrastructure hiccups are not scored as wrong answers):

```bash
EVAL_API_URL=https://<your-api-host> EVAL_API_KEY=<api key> python evals/scorer.py evals/eval_results_live.json
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
