"""
Database access for disputes. The ONLY place that changes dispute status.

Every function takes an open connection (`conn`) instead of opening its own, so a
caller can put several steps in ONE transaction:

    with get_workflow_connection() as conn:      # commit on success, rollback on error
        did = create_dispute(conn, ...)
        transition(conn, did, Status.PENDING_REVIEW, ...)

transition() is atomic: lock the row, check the state machine, update the status and
append the audit record. If any step raises, nothing is saved.
"""
from dataclasses import dataclass
from decimal import Decimal

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from workflow.states import InvalidTransition, Status, assert_transition


class DisputeNotFound(Exception):
    pass


@dataclass(frozen=True)
class TransitionResult:
    dispute_id: int
    from_status: Status
    to_status: Status
    audit_id: int


def create_dispute(
    conn: Connection,
    *,
    order_id: str,
    customer_id: str,
    reason_text: str,
    amount_requested: Decimal | float | str,
    actor_id: str = "customer",
) -> int:
    """Insert a dispute in `submitted` and write the first audit record. Returns its id."""
    dispute_id = conn.execute(
        "INSERT INTO workflow.disputes (order_id, customer_id, reason_text, amount_requested, status) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (order_id, customer_id, reason_text, amount_requested, Status.SUBMITTED.value),
    ).fetchone()[0]
    _write_audit(
        conn, dispute_id, action="dispute_submitted", actor_type="human", actor_id=actor_id,
        from_status=None, to_status=Status.SUBMITTED, rule_id=None,
        reasoning=f"Dispute submitted: {reason_text}",
        details={"order_id": order_id, "amount_requested": str(amount_requested)},
    )
    return dispute_id


def transition(
    conn: Connection,
    dispute_id: int,
    to_status: Status,
    *,
    action: str,
    actor_type: str,
    actor_id: str,
    reasoning: str,
    rule_id: str | None = None,
    details: dict | None = None,
    review_reason_type: str | None = None,
) -> TransitionResult:
    """
    Move a dispute to `to_status`, atomically.

    1. SELECT ... FOR UPDATE locks the row, so concurrent callers queue up and each
       one sees the status the previous one left behind (no double approval).
    2. assert_transition() enforces the state machine -> InvalidTransition.
    3. UPDATE the status, then INSERT the audit record, in the caller's transaction.

    Raises DisputeNotFound / InvalidTransition; the caller's `with` block rolls back.
    """
    if to_status is Status.PENDING_REVIEW and review_reason_type is None:
        raise ValueError("review_reason_type is required when moving to pending_review")

    row = conn.execute(
        "SELECT status FROM workflow.disputes WHERE id = %s FOR UPDATE", (dispute_id,)
    ).fetchone()
    if row is None:
        raise DisputeNotFound(dispute_id)
    from_status = Status(row[0])

    assert_transition(from_status, to_status)  # raises InvalidTransition

    conn.execute(
        "UPDATE workflow.disputes SET status = %s, updated_at = now(), "
        "review_reason_type = COALESCE(%s, review_reason_type) WHERE id = %s",
        (to_status.value, review_reason_type, dispute_id),
    )
    audit_id = _write_audit(
        conn, dispute_id, action=action, actor_type=actor_type, actor_id=actor_id,
        from_status=from_status, to_status=to_status, rule_id=rule_id,
        reasoning=reasoning, details=details,
    )
    return TransitionResult(dispute_id, from_status, to_status, audit_id)


def get_dispute(conn: Connection, dispute_id: int) -> dict:
    row = conn.cursor(row_factory=dict_row).execute(
        "SELECT * FROM workflow.disputes WHERE id = %s", (dispute_id,)
    ).fetchone()
    if row is None:
        raise DisputeNotFound(dispute_id)
    return row


def list_audit(conn: Connection, dispute_id: int) -> list[dict]:
    """Audit records for one dispute, oldest first."""
    return conn.cursor(row_factory=dict_row).execute(
        "SELECT * FROM workflow.audit_log WHERE dispute_id = %s ORDER BY ts, id", (dispute_id,)
    ).fetchall()


def _write_audit(
    conn: Connection, dispute_id: int, *, action: str, actor_type: str, actor_id: str,
    from_status: Status | None, to_status: Status, rule_id: str | None,
    reasoning: str, details: dict | None,
) -> int:
    return conn.execute(
        "INSERT INTO workflow.audit_log (dispute_id, action, actor_type, actor_id, from_status, "
        "to_status, rule_id, reasoning, details) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (dispute_id, action, actor_type, actor_id,
         from_status.value if from_status else None, to_status.value, rule_id,
         reasoning, Jsonb(details or {})),
    ).fetchone()[0]
