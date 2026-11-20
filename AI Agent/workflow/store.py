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

from workflow.states import TERMINAL_STATES, InvalidTransition, Status, assert_transition


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
    reason_category: str | None = None,
) -> int:
    """Insert a dispute in `submitted` and write the first audit record. Returns its id."""
    dispute_id = conn.execute(
        "INSERT INTO workflow.disputes (order_id, customer_id, reason_text, reason_category, "
        "amount_requested, status) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (order_id, customer_id, reason_text, reason_category, amount_requested, Status.SUBMITTED.value),
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


class ApprovalNotFound(Exception):
    pass


class ApprovalAlreadyDecided(Exception):
    def __init__(self, approval_id: int, status: str):
        self.approval_id, self.status = approval_id, status
        super().__init__(f"Approval {approval_id} was already decided: {status}")


def lock_dispute(conn: Connection, dispute_id: int) -> dict:
    """Read a dispute and hold its row lock until the transaction ends."""
    row = conn.cursor(row_factory=dict_row).execute(
        "SELECT * FROM workflow.disputes WHERE id = %s FOR UPDATE", (dispute_id,)
    ).fetchone()
    if row is None:
        raise DisputeNotFound(dispute_id)
    return row


def create_approval(conn: Connection, dispute_id: int) -> int:
    """Open a pending approval. The partial unique index allows only one pending per dispute."""
    return conn.execute(
        "INSERT INTO workflow.approvals (dispute_id) VALUES (%s) RETURNING id", (dispute_id,)
    ).fetchone()[0]


def decide_approval(conn: Connection, approval_id: int, decision: str, decided_by: str,
                    comment: str | None) -> int:
    """
    Record a human decision on a pending approval and return its dispute_id.
    The approval row is locked first, so two reviewers acting at once serialize and
    the second gets ApprovalAlreadyDecided.
    """
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be 'approved' or 'rejected'")
    row = conn.execute(
        "SELECT dispute_id, status FROM workflow.approvals WHERE id = %s FOR UPDATE", (approval_id,)
    ).fetchone()
    if row is None:
        raise ApprovalNotFound(approval_id)
    dispute_id, status = row
    if status != "pending":
        raise ApprovalAlreadyDecided(approval_id, status)
    conn.execute(
        "UPDATE workflow.approvals SET status = %s, decided_at = now(), decided_by = %s, comment = %s "
        "WHERE id = %s", (decision, decided_by, comment, approval_id))
    return dispute_id


def list_approvals(conn: Connection, status: str | None = "pending") -> list[dict]:
    cur = conn.cursor(row_factory=dict_row)
    if status is None:
        return cur.execute("SELECT * FROM workflow.approvals ORDER BY id").fetchall()
    return cur.execute("SELECT * FROM workflow.approvals WHERE status = %s ORDER BY id", (status,)).fetchall()


def count_refund_attempts(conn: Connection, dispute_id: int) -> int:
    return conn.execute(
        "SELECT count(*) FROM workflow.refund_attempts WHERE dispute_id = %s", (dispute_id,)
    ).fetchone()[0]


def record_refund_attempt(conn: Connection, dispute_id: int, idempotency_key: str,
                          amount: Decimal, success: bool, response: dict) -> int:
    return conn.execute(
        "INSERT INTO workflow.refund_attempts (dispute_id, idempotency_key, amount, result, response) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (dispute_id, idempotency_key, amount, "success" if success else "failure", Jsonb(response)),
    ).fetchone()[0]


def record_event(
    conn: Connection, dispute_id: int, *, action: str, actor_type: str, actor_id: str,
    reasoning: str, rule_id: str | None = None, details: dict | None = None,
) -> int:
    """
    Append an audit record WITHOUT changing the status (e.g. a retry that failed again
    while the dispute stays in refund_failed). The caller must hold the dispute's lock.
    """
    current = Status(conn.execute(
        "SELECT status FROM workflow.disputes WHERE id = %s", (dispute_id,)).fetchone()[0])
    return _write_audit(conn, dispute_id, action=action, actor_type=actor_type, actor_id=actor_id,
                        from_status=current, to_status=current, rule_id=rule_id,
                        reasoning=reasoning, details=details)


def get_dispute(conn: Connection, dispute_id: int) -> dict:
    row = conn.cursor(row_factory=dict_row).execute(
        "SELECT * FROM workflow.disputes WHERE id = %s", (dispute_id,)
    ).fetchone()
    if row is None:
        raise DisputeNotFound(dispute_id)
    return row


def dispute_history(
    conn: Connection, order_id: str, customer_ids: list[str], exclude_dispute_id: int | None = None
) -> tuple[bool, int, Decimal]:
    """
    (has_open_dispute_on_order, executed_refunds_in_last_90_days_for_these_customer_ids,
     total_already_refunded_on_this_order).
    Pass `exclude_dispute_id` when the dispute being evaluated already exists, so it
    does not count against itself. The 90 days run on the real clock: this is our own
    table, only the Olist order timeline uses the injectable `as_of`.
    """
    terminal = [s.value for s in TERMINAL_STATES]
    has_open = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM workflow.disputes WHERE order_id = %s "
        "AND NOT (status = ANY(%s)) AND id IS DISTINCT FROM %s::bigint)",
        (order_id, terminal, exclude_dispute_id),
    ).fetchone()[0]
    refunds = conn.execute(
        "SELECT count(*) FROM workflow.disputes WHERE customer_id = ANY(%s) AND status = %s "
        "AND updated_at >= now() - interval '90 days' AND id IS DISTINCT FROM %s::bigint",
        (customer_ids, Status.REFUND_EXECUTED.value, exclude_dispute_id),
    ).fetchone()[0]
    refunded_total = conn.execute(
        "SELECT COALESCE(SUM(amount_requested), 0) FROM workflow.disputes "
        "WHERE order_id = %s AND status = %s AND id IS DISTINCT FROM %s::bigint",
        (order_id, Status.REFUND_EXECUTED.value, exclude_dispute_id),
    ).fetchone()[0]
    return has_open, refunds, refunded_total


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
