# Choosing a database: SQLite vs MySQL vs PostgreSQL vs MongoDB vs a vector DB

Written while migrating this project from v1 (SQLite + a JSON file of embeddings) to v2 (PostgreSQL + pgvector). Where a point comes from something that happened in this migration, it says so. Where it is general knowledge I haven't tested here, it says that too.

## The short version

| Need | Reasonable pick | Why |
|---|---|---|
| Local prototype, single user, zero setup | SQLite | One file, no server |
| Relational app data, multiple users, access control | PostgreSQL or MySQL | Real server, roles, concurrent writers |
| Relational data **plus** embeddings in one system | PostgreSQL + pgvector | One database, one set of credentials, similarity search next to the data |
| Nested, variable-shape documents; event logs | MongoDB | The document model fits data that has no fixed schema |
| Very large vector collections, hybrid search, heavy filtering | Dedicated vector DB | Built and tuned for that one job |

This project picked **PostgreSQL + pgvector**: the data is relational, the agent needs a database-enforced read-only role, and vector search is small enough to live in the same system.

## SQLite

An embedded, file-based database. There is no server process.

- **Good for:** prototypes, local tools, single-user analytics, test fixtures.
- **Limits that mattered here:**
  - **No roles.** v1's "read-only" guarantee was a connection flag (`file:...?mode=ro`). Whoever controls the connection string can drop the flag. In Postgres the guarantee is a database role with `SELECT` only, which no connection string can bypass.
  - **Loose typing.** Dates were `TEXT`, so every date operation needed `strftime`/`julianday`, and a comparison like `BETWEEN '2017-01-01' AND '2017-12-31'` worked by string comparison.
  - **No network access.** An app on another machine can't reach it, which blocks any hosted deployment.

## PostgreSQL

A full relational server with a strong SQL dialect, roles and grants, real types, and an extension system.

- **Good for:** most application backends, analytics-style SQL, anything that needs both relational data and something extra (vectors, geospatial, JSON).
- **What the migration showed:**

| v1 (SQLite) | v2 (PostgreSQL) |
|---|---|
| Dates stored as `TEXT` | Real `TIMESTAMP` columns (converted in `migrate_to_postgres.py`) |
| `strftime('%Y-%m', col)` | `TO_CHAR(col, 'YYYY-MM')` |
| `julianday(a) - julianday(b)` | `EXTRACT(EPOCH FROM (a - b)) / 86400.0` |
| `ROUND(x, 2)` on a float | Fails on `double precision`; needs `ROUND(x::numeric, 2)` |
| Read-only via connection flag | `olist_reader` role with `SELECT` only; `DELETE`, `DROP`, `CREATE` rejected with `InsufficientPrivilege` |
| Cosine similarity in Python over a JSON file | `ORDER BY embedding <=> query` in SQL (pgvector) |

- **A bug the stricter types exposed:** with real timestamps, `BETWEEN '2017-07-01' AND '2017-07-31'` stops at midnight on July 31 and drops that day's orders. An eval's expected answer (3,729 instead of 3,872) had been wrong because of exactly this. The fix is a half-open range (`>= '2017-07-01' AND < '2017-08-01'`).

## MySQL

Another mainstream relational server. For a project like this one, MySQL and PostgreSQL are close enough that the deciding factors are usually outside the SQL itself:

- **What the team already runs.** A company standardised on MySQL usually shouldn't introduce Postgres for one service.
- **Ecosystem and extensions.** Postgres's extension system is why pgvector was a natural fit here. I haven't evaluated MySQL's current vector support, so check its documentation before relying on either claim.
- **Dialect differences** exist (quoting, date functions, some type behaviour), so SQL generated for one will need changes for the other. The same prompt-and-catalog work I did for the Postgres port would be needed again.

## MongoDB

A document database. Records are JSON-like documents, and the schema can differ per record.

- **Good for:** nested or variable-shape data, event and log streams, content that changes shape often.
- **Poor fit for this project's core data.** The Olist data is 11 tables linked by foreign keys, and the agent's value comes from joins across them (orders → items → sellers → leads). Text-to-SQL also leans on a fixed, introspectable schema. In Mongo, the same questions would become aggregation pipelines, and the schema catalog approach would have to be rebuilt.
- **Where it could fit:** semi-structured event data. This project's query log (each query, its SQL, any errors, any human corrections) is a natural document collection. I stored it in Postgres instead (`app_logs.query_events`, with the variable-shape parts in `JSONB` columns): one fewer system to run, and it can be joined or queried with the same tools. The choice was operational preference, not capability.

## Vector databases

A vector store indexes embeddings so you can find the nearest ones to a query vector.

- **What this project needs:** 35 chunks of schema text. At that size an exact scan is instant, and no special index is required.
- **pgvector (what we use):** keeps embeddings in the same Postgres database as everything else. One system to run and secure, joins and filters in ordinary SQL, transactional with the rest of the data. pgvector offers approximate indexes (HNSW, IVFFlat) for when exact scans become slow.
- **Dedicated vector databases (Pinecone, Qdrant, Weaviate and similar):** worth considering at very large scale, with heavy metadata filtering, hybrid keyword-plus-vector search, or when a managed service is preferred. The cost is another system to run and keep in sync with the source data.
- **What I haven't done:** benchmark any of this. At 35 vectors the comparison is meaningless, so the "when to switch" thresholds are general guidance, not measurements from this project.

## Choosing, in practice

1. **Start from the data shape.** Relational and join-heavy → a relational database. Variable documents → MongoDB.
2. **Then access control and deployment.** Multiple users, a hosted app, or a database-enforced read-only guarantee rules SQLite out.
3. **Then what else must live next to the data.** Embeddings small enough to share the database → pgvector. A very large vector workload is the point where a dedicated store earns its extra operational cost.
4. **Then the organisation.** Existing standards and team skills often decide between two technically similar options, such as Postgres and MySQL.

## Still open

- Connection handling: `db.py` opens a new connection per query, which is fine locally. A hosted API will need connection pooling, and RDS has its own connection limits. This is a Phase 2/3 topic.
- Whether to add an approximate (HNSW) index to `schema_chunks`. Not needed at 35 rows.
