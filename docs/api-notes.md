# API design notes

Notes on how the HTTP API in `AI Agent/api.py` works and why it is shaped this way. Things I have not built or measured are marked as such.

## Request lifecycle (what happens on `POST /execute`)

1. **Client → server.** The client sends JSON over HTTP with the `X-API-Key` header.
2. **Middleware.** `access_log` assigns (or reuses) a request id and starts a timer. It writes one JSON log line per request when the response is done.
3. **Dependency: auth.** `require_api_key` compares the key in constant time. Missing or wrong → 401. Server has no key configured → 503.
4. **Validation.** Pydantic checks the body (types, lengths) before the function runs. Bad shape → 422 with a field-level message.
5. **Handler.** `validate_read_only_sql` allows one `SELECT`/`WITH` statement, then `agent_core.step_execute` runs it. Each call opens a database connection as the `olist_reader` role (read-only session, 15 s timeout).
6. **Explain.** `step_explain` calls OpenAI for the plain-English summary. If that fails, the rows are still returned and `insights_error` says why.
7. **Serialise.** Decimals and datetimes are converted to JSON-safe values, and rows are capped at `API_MAX_RESPONSE_ROWS` (the true count and a `truncated` flag are returned).
8. **Response.** Status code, JSON body and the `X-Request-ID` header go back to the client.

The status codes follow who can fix the problem: 400/422 the caller's input, 401 credentials, 403 the database refused the statement, 502 the model returned nothing usable, 503 the server is misconfigured or the database is down.

## Why stateless

The server stores no sessions. The client sends the history on `/ask` and the current SQL on `/revise` and `/execute`. That keeps the service trivially horizontally scalable and restartable (no state to lose or replicate), at the cost of the client holding more.

The one thing the client is **not** trusted to supply is retrieval context. `/revise` recomputes it from the question on the server; accepting it from the client would let the caller put arbitrary text into the model prompt.

## Why plain `def` endpoints

The agent steps make blocking calls (OpenAI, Postgres). FastAPI runs a plain `def` endpoint in a thread pool, so a slow request does not freeze the others. An `async def` endpoint calling these blocking functions would block the event loop and stall every request while it waited. If the steps were rewritten with async clients, `async def` would become the right choice.

## CORS

CORS is a **browser** rule: a page served from one origin may only call another origin's API if that API says so. It does not affect server-to-server calls, curl, or Streamlit's Python code calling the API.

So CORS only matters once a browser front end (for example a React page) calls the API directly. `CORS_ORIGINS` is empty by default, meaning no browser origin is allowed. Set it to the exact front-end origin when one exists. A wildcard would be a poor choice here, because the API key travels in a header.

## Streaming

Not implemented. `/execute` waits for the query and the OpenAI summary and returns once. The slow part is the model call, so a chat UI could feel faster if the summary were streamed (server-sent events or a chunked response) while the table is shown immediately. A cheap step in that direction is already available: `include_insights: false` returns the rows without waiting for the summary, and the client could then ask for the summary separately. Streaming would be worth building only after the front end exists and the latency is measured.

## Moving the Streamlit UI onto the API

`app.py` no longer imports the agent code; `api_client.py` makes the HTTP calls. What changed, and what it cost:

- **`/execute` now also explains.** Query, log entry and insights come back in one call, so the old separate "explaining" UI state is gone. The UI waits slightly longer for the first result than before; `include_insights: false` is available if that matters.
- **Multi-query analysis is one blocking call (`/analyze`).** The in-process version updated a progress message after each sub-query; over a single HTTP request that live progress is lost (about a minute of spinner). Streaming progress events would fix it and is not built.
- **`/analyze` skips the approval step.** Each sub-query runs without a human reviewing its SQL, exactly as before. It is still bound by the read-only role, but it is the one path where generated SQL is not human-approved.
- **Row limits.** The browser now receives at most `API_MAX_RESPONSE_ROWS` rows (default 1000); the UI says so when a result is truncated.
- **Auth.** The UI holds the API key server-side (it is a Python process, not browser code), so the key never reaches the user's browser. A React front end would need a different design: a browser cannot keep a shared key secret.

## Known gaps

- **Connections.** `db.py` opens a connection per query. Fine locally; a hosted API needs a connection pool and attention to the managed database's connection limit.
- **Rate limiting and per-user keys.** There is one shared key and no rate limit. Anyone with the key can spend OpenAI budget. Needed before a public URL.
- **Logs.** `query_log.jsonl` and `feedback_log.jsonl` are local files. In a container they disappear on restart; they should move to the database or a log service in the deployment phase.
- **The `/execute` validator is intentionally simple.** It rejects a `;` inside a string literal, and it would not catch every unsafe construct. It is a first guard only; the database role is the real boundary, and a test shows a write that passes the validator being refused by the database.
