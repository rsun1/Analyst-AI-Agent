"""
Dispute lifecycle: the states and the ONLY legal moves between them.

Pure Python, no database. M3's store.transition() will call assert_transition()
before touching the DB, so this file is the single source of truth for "is this
move allowed?". See PHASE_A_PLAN.md section 2 for the diagram.
"""
from enum import Enum


class Status(str, Enum):
    # Inheriting from str means Status.SUBMITTED == "submitted", which makes it
    # easy to store in Postgres and to return from the API without conversion.
    SUBMITTED = "submitted"
    # TODO(M1-a): add the other six states. Use lowercase snake_case values that
    # match the CHECK constraint you will write in M2:
    #   auto_approved, pending_review, approved, rejected, refund_executed, refund_failed
    AUTO_APPROVED = "auto_approved"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    REFUND_EXECUTED = "refund_executed"
    REFUND_FAILED = "refund_failed"

# Map: current status -> set of statuses it may move to.
# Every Status must appear as a key (terminal states map to an empty set), so a
# missing key is a bug, not "no moves allowed".
ALLOWED_TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.SUBMITTED: frozenset({
        Status.AUTO_APPROVED,
        Status.PENDING_REVIEW,
        Status.REJECTED,
    }),
    # TODO(M1-b): fill in the remaining states from the diagram in the plan.
    # Things to decide / double-check as you go:
    #   - which states are terminal? (empty frozenset)
    #   - from refund_failed, where can a retry go? (see plan section 2)
    #   - can pending_review go straight to refund_executed? (it should not)
    Status.AUTO_APPROVED: frozenset({
        Status.REFUND_EXECUTED,
        Status.REFUND_FAILED,
    }),
    Status.PENDING_REVIEW: frozenset({
        Status.APPROVED,
        Status.REJECTED
    }),    
    Status.APPROVED: frozenset({
        Status.REFUND_EXECUTED,
        Status.REFUND_FAILED,
    }),
    Status.REFUND_FAILED: frozenset({
        Status.REFUND_EXECUTED,
        Status.PENDING_REVIEW
    }),
    # Terminal states: still need a key, with an empty set.
    Status.REJECTED: frozenset(),
    Status.REFUND_EXECUTED: frozenset(),
}

# Derived, not hand-maintained: a terminal state is one with no outgoing moves.
TERMINAL_STATES: frozenset[Status] = frozenset(
    s for s, nxt in ALLOWED_TRANSITIONS.items() if not nxt
)


class InvalidTransition(Exception):
    """Raised when a move is not in ALLOWED_TRANSITIONS."""

    def __init__(self, from_status: Status, to_status: Status):
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"Illegal transition: {from_status.value} -> {to_status.value}")


def can_transition(from_status: Status, to_status: Status) -> bool:
    """True if moving from `from_status` to `to_status` is allowed."""
    # Index directly (no .get default): if a status is missing from the table,
    # that is a bug and we want a loud KeyError, not a silent "nothing allowed".
    return to_status in ALLOWED_TRANSITIONS[from_status]


def assert_transition(from_status: Status, to_status: Status) -> None:
    """Raise InvalidTransition if the move is not allowed; otherwise return None."""
    if not can_transition(from_status, to_status):
        raise InvalidTransition(from_status, to_status)


def is_terminal(status: Status) -> bool:
    """True if no further moves are possible from `status`."""
    return status in TERMINAL_STATES


def next_states(status: Status) -> frozenset[Status]:
    """Legal next states from `status` (useful for the UI and error messages)."""
    return ALLOWED_TRANSITIONS[status]
