"""
Create the `workflow` schema, its tables, and the `olist_workflow` role.
Safe to re-run: uses IF NOT EXISTS and never drops anything.
Modelled on setup_logging_db.py.

The role can read/write disputes, approvals and refund_attempts, but audit_log is
append-only: it gets SELECT + INSERT and no UPDATE/DELETE, so the database itself
guarantees audit records cannot be altered by the application.

Usage:
  python -m setup_workflow_db
"""
import psycopg
from psycopg import sql

from config import PG_ADMIN_DSN, PG_WORKFLOW_PASSWORD, PG_WORKFLOW_USER
from workflow.states import Status

# Generated from the enum so the CHECK constraint can never drift from states.py.
STATUS_VALUES = ", ".join(f"'{s.value}'" for s in Status)

DDL = f"""
CREATE SCHEMA IF NOT EXISTS workflow;

CREATE TABLE IF NOT EXISTS workflow.disputes (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id           TEXT NOT NULL,
    customer_id        TEXT NOT NULL,
    reason_text        TEXT NOT NULL,
    reason_category    TEXT,
    amount_requested   NUMERIC(12,2) NOT NULL CHECK (amount_requested > 0),
    status             TEXT NOT NULL CHECK (status IN ({STATUS_VALUES})),
    review_reason_type TEXT CHECK (review_reason_type IN ('policy', 'agent_uncertain')),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS disputes_order_idx ON workflow.disputes (order_id);
CREATE INDEX IF NOT EXISTS disputes_status_idx ON workflow.disputes (status);

-- Persistent human approvals (survive restarts, unlike an in-memory Future).
CREATE TABLE IF NOT EXISTS workflow.approvals (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dispute_id   BIGINT NOT NULL REFERENCES workflow.disputes (id),
    status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at   TIMESTAMPTZ,
    decided_by   TEXT,
    comment      TEXT,
    -- a decision must carry who and when; a pending row must not
    CHECK ((status = 'pending') = (decided_at IS NULL AND decided_by IS NULL))
);
-- One dispute can have at most ONE pending approval at a time.
CREATE UNIQUE INDEX IF NOT EXISTS approvals_one_pending_idx
    ON workflow.approvals (dispute_id) WHERE status = 'pending';

-- Every payment attempt; the idempotency key makes a double-click/retry harmless.
CREATE TABLE IF NOT EXISTS workflow.refund_attempts (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dispute_id      BIGINT NOT NULL REFERENCES workflow.disputes (id),
    idempotency_key TEXT NOT NULL UNIQUE,
    amount          NUMERIC(12,2) NOT NULL,
    result          TEXT NOT NULL CHECK (result IN ('success', 'failure')),
    response        JSONB NOT NULL DEFAULT jsonb_build_object(),
    attempted_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only audit trail: every state change and every agent decision.
CREATE TABLE IF NOT EXISTS workflow.audit_log (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    dispute_id  BIGINT NOT NULL REFERENCES workflow.disputes (id),
    action      TEXT NOT NULL,
    actor_type  TEXT NOT NULL CHECK (actor_type IN ('agent', 'human', 'system')),
    actor_id    TEXT NOT NULL,
    from_status TEXT CHECK (from_status IN ({STATUS_VALUES})),
    to_status   TEXT NOT NULL CHECK (to_status IN ({STATUS_VALUES})),
    rule_id     TEXT,
    reasoning   TEXT NOT NULL,
    details     JSONB NOT NULL DEFAULT jsonb_build_object()
);
CREATE INDEX IF NOT EXISTS audit_dispute_idx ON workflow.audit_log (dispute_id, ts);
"""


def setup() -> None:
    with psycopg.connect(PG_ADMIN_DSN) as pg:
        pg.execute(DDL)

        pg.execute(sql.SQL(
            "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {name}) THEN "
            "CREATE ROLE {ident} LOGIN PASSWORD {pw}; END IF; END $$"
        ).format(name=sql.Literal(PG_WORKFLOW_USER), ident=sql.Identifier(PG_WORKFLOW_USER),
                 pw=sql.Literal(PG_WORKFLOW_PASSWORD)))
        pg.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
            sql.Identifier(PG_WORKFLOW_USER), sql.Literal(PG_WORKFLOW_PASSWORD)))

        who = sql.Identifier(PG_WORKFLOW_USER)
        pg.execute(sql.SQL("GRANT USAGE ON SCHEMA workflow TO {}").format(who))
        pg.execute(sql.SQL(
            "GRANT SELECT, INSERT, UPDATE ON workflow.disputes, workflow.approvals, "
            "workflow.refund_attempts TO {}").format(who))
        # Append-only: no UPDATE, no DELETE.
        pg.execute(sql.SQL("GRANT SELECT, INSERT ON workflow.audit_log TO {}").format(who))
        # GENERATED ... AS IDENTITY columns need sequence access for INSERT.
        pg.execute(sql.SQL("GRANT USAGE ON ALL SEQUENCES IN SCHEMA workflow TO {}").format(who))

        pg.commit()
    print("Workflow schema ready.")


if __name__ == "__main__":
    setup()
