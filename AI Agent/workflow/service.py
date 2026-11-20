"""
Orchestration: submit a dispute, apply the policy decision, run the human approval,
execute the (mock) refund, retry or escalate failures.

Each public function is ONE database transaction (commit on success, roll back on any
exception), so a dispute never ends up half-processed. The audit trail is written by
store.transition() in the same transaction as every status change.

Design notes
  * Rules decide; this module only carries the decision out.
  * submit_dispute takes a per-order advisory lock, so two simultaneous submissions for
    the same order cannot both pass the "no open dispute" check.
  * Refund idempotency key = "dispute-<id>-attempt-<n>", n = committed attempts + 1.
    If the process dies after the gateway succeeded but before COMMIT, the retry
    computes the same key and the gateway replays the success instead of paying twice.
  * Trade-off: execute_refund calls the gateway while holding the dispute's row lock.
    Fine for a mock; a real provider would use an outbox / two-phase flow instead.
"""
import dataclasses
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from psycopg import Connection

from db import get_workflow_connection
from workflow.facts import load_facts
from workflow.models import Claim, Decision, Outcome, ReasonCategory, ReviewType
from workflow.payments_mock import PaymentResult, default_gateway
from workflow.rules import evaluate
from workflow.states import Status
from workflow.store import (
    count_refund_attempts, create_approval, create_dispute, decide_approval,
    lock_dispute, record_event, record_refund_attempt, transition,
)

AGENT_ID = "refund-agent-v1"
MAX_AUTO_REFUND_ATTEMPTS = 3
REFUNDABLE_STATES = {Status.AUTO_APPROVED.value, Status.APPROVED.value, Status.REFUND_FAILED.value}


class OrderNotFound(Exception):
    """The order does not exist: nothing is stored (the API answers 400)."""


class InvalidClaim(ValueError):
    """Bad input (amount, text): nothing is stored (the API answers 400)."""


class RefundNotAllowed(Exception):
    """The dispute is not in a state from which a refund may be attempted."""


@dataclass(frozen=True)
class SubmitResult:
    dispute_id: int
    status: Status
    decision: Decision
    approval_id: int | None = None


def _jsonable(obj) -> dict:
    return json.loads(json.dumps(dataclasses.asdict(obj), default=str))


def _validate(amount, reason_text: str) -> Decimal:
    try:
        amount = Decimal(str(amount))
    except InvalidOperation:
        raise InvalidClaim("amount is not a number")
    if not amount.is_finite() or amount <= 0 or amount >= Decimal("1e9"):
        raise InvalidClaim("amount must be a positive number")
    if amount != amount.quantize(Decimal("0.01")):
        raise InvalidClaim("amount may have at most 2 decimal places")
    if not reason_text or not reason_text.strip():
        raise InvalidClaim("reason_text is required")
    return amount


def submit_dispute(
    *, order_id: str, amount, reason_text: str,
    reason_category: ReasonCategory = ReasonCategory.OTHER,
    as_of: datetime | None = None, submitted_by: str = "customer", gateway=None,
) -> SubmitResult:
    gateway = gateway or default_gateway()
    amount = _validate(amount, reason_text)
    as_of = as_of or datetime.now()

    with get_workflow_connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (order_id,))
        facts = load_facts(conn, order_id)
        if facts is None:
            raise OrderNotFound(order_id)

        claim = Claim(order_id, amount, as_of, reason_category, reason_text)
        decision = evaluate(facts, claim)
        dispute_id = create_dispute(
            conn, order_id=order_id, customer_id=facts.customer_id, reason_text=reason_text,
            amount_requested=amount, actor_id=submitted_by, reason_category=reason_category.value,
        )
        evidence = {"decision": {"outcome": decision.outcome.value, "rule_id": decision.rule_id},
                    "facts": _jsonable(facts), "claim": _jsonable(claim)}

        if decision.outcome is Outcome.REJECT:
            transition(conn, dispute_id, Status.REJECTED, action="policy_rejected", actor_type="agent",
                       actor_id=AGENT_ID, reasoning=decision.reasoning, rule_id=decision.rule_id, details=evidence)
            return SubmitResult(dispute_id, Status.REJECTED, decision)

        if decision.outcome is Outcome.NEEDS_REVIEW:
            transition(conn, dispute_id, Status.PENDING_REVIEW, action="sent_to_human_review",
                       actor_type="agent", actor_id=AGENT_ID, reasoning=decision.reasoning,
                       rule_id=decision.rule_id, details=evidence,
                       review_reason_type=decision.review_reason_type.value)
            approval_id = create_approval(conn, dispute_id)
            return SubmitResult(dispute_id, Status.PENDING_REVIEW, decision, approval_id)

        transition(conn, dispute_id, Status.AUTO_APPROVED, action="policy_auto_approved", actor_type="agent",
                   actor_id=AGENT_ID, reasoning=decision.reasoning, rule_id=decision.rule_id, details=evidence)
        status = _execute_refund(conn, dispute_id, gateway)
        return SubmitResult(dispute_id, status, decision)


def decide(*, approval_id: int, decision: str, reviewer: str, comment: str | None = None,
           gateway=None) -> Status:
    """A human approves or rejects a pending review. Approved disputes are refunded immediately."""
    gateway = gateway or default_gateway()
    if not reviewer or not reviewer.strip():
        raise InvalidClaim("reviewer is required")
    with get_workflow_connection() as conn:
        dispute_id = decide_approval(conn, approval_id, decision, reviewer, comment)
        target = Status.APPROVED if decision == "approved" else Status.REJECTED
        transition(conn, dispute_id, target, action=f"human_{decision}", actor_type="human",
                   actor_id=reviewer, reasoning=comment or f"Reviewer {reviewer} {decision} the request.",
                   details={"approval_id": approval_id})
        if target is Status.REJECTED:
            return Status.REJECTED
        return _execute_refund(conn, dispute_id, gateway)


def retry_refund(dispute_id: int, gateway=None) -> Status:
    """
    Retry a failed refund. After MAX_AUTO_REFUND_ATTEMPTS failed attempts it stops
    retrying and escalates to a human instead (a new pending approval).
    """
    gateway = gateway or default_gateway()
    with get_workflow_connection() as conn:
        dispute = lock_dispute(conn, dispute_id)
        if dispute["status"] != Status.REFUND_FAILED.value:
            raise RefundNotAllowed(f"dispute {dispute_id} is '{dispute['status']}', not refund_failed")
        attempts = count_refund_attempts(conn, dispute_id)
        if attempts >= MAX_AUTO_REFUND_ATTEMPTS:
            transition(conn, dispute_id, Status.PENDING_REVIEW, action="escalated_after_failed_refunds",
                       actor_type="agent", actor_id=AGENT_ID, rule_id="REFUND_RETRIES_EXHAUSTED",
                       reasoning=f"The refund failed {attempts} times; I stop retrying and need a human.",
                       review_reason_type=ReviewType.POLICY.value)
            create_approval(conn, dispute_id)
            return Status.PENDING_REVIEW
        return _execute_refund(conn, dispute_id, gateway)


def _execute_refund(conn: Connection, dispute_id: int, gateway) -> Status:
    """One refund attempt inside the caller's transaction. Moves to refund_executed or refund_failed."""
    dispute = lock_dispute(conn, dispute_id)
    if dispute["status"] not in REFUNDABLE_STATES:
        raise RefundNotAllowed(f"dispute {dispute_id} is '{dispute['status']}'; no refund allowed")

    attempt = count_refund_attempts(conn, dispute_id) + 1
    key = f"dispute-{dispute_id}-attempt-{attempt}"
    amount = dispute["amount_requested"]
    try:
        result = gateway.refund(idempotency_key=key, order_id=dispute["order_id"], amount=amount)
    except Exception as e:  # a crashing provider is a failed attempt, not a lost dispute
        result = PaymentResult(False, error=f"gateway error: {e}")
    record_refund_attempt(conn, dispute_id, key, amount, result.success, result.as_dict())

    if result.success:
        transition(conn, dispute_id, Status.REFUND_EXECUTED, action="refund_succeeded", actor_type="system",
                   actor_id="mock-payments", reasoning=f"Refund of {amount} paid (attempt {attempt}).",
                   details={"idempotency_key": key, "reference": result.reference})
        return Status.REFUND_EXECUTED
    failure = dict(action="refund_failed", actor_type="system", actor_id="mock-payments",
                   reasoning=f"Refund attempt {attempt} failed: {result.error}",
                   details={"idempotency_key": key, "error": result.error})
    if dispute["status"] == Status.REFUND_FAILED.value:
        # Already refund_failed: the state machine has no self-loop, so log the event only.
        record_event(conn, dispute_id, **failure)
    else:
        transition(conn, dispute_id, Status.REFUND_FAILED, **failure)
    return Status.REFUND_FAILED
