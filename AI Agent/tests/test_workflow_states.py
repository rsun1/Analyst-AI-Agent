"""
State machine tests (pure Python, no database). Run from "AI Agent/":
    pytest tests/test_workflow_states.py -v
"""
import itertools

import pytest

from workflow.states import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    InvalidTransition,
    Status,
    assert_transition,
    can_transition,
    is_terminal,
    next_states,
)

S = Status

# ── Given: structural checks that catch a half-finished table ────────────────

def test_every_status_has_an_entry():
    assert set(ALLOWED_TRANSITIONS) == set(Status)


def test_transition_targets_are_statuses():
    for targets in ALLOWED_TRANSITIONS.values():
        assert all(isinstance(t, Status) for t in targets)


def test_no_self_transitions():
    for s, targets in ALLOWED_TRANSITIONS.items():
        assert s not in targets


# ── TODO: the legal moves, copied from the diagram in PHASE_A_PLAN.md ────────
# Write one (from, to) pair per arrow. Start with these two and add the rest.
LEGAL = [
    (S.SUBMITTED, S.AUTO_APPROVED),
    (S.SUBMITTED, S.PENDING_REVIEW),
    (S.SUBMITTED, S.REJECTED),
    (S.AUTO_APPROVED, S.REFUND_EXECUTED),
    (S.AUTO_APPROVED, S.REFUND_FAILED),
    (S.PENDING_REVIEW, S.APPROVED),
    (S.PENDING_REVIEW, S.REJECTED),
    (S.APPROVED, S.REFUND_EXECUTED),
    (S.APPROVED, S.REFUND_FAILED),
    (S.REFUND_FAILED, S.REFUND_EXECUTED),
    (S.REFUND_FAILED, S.PENDING_REVIEW),
]


@pytest.mark.parametrize("frm,to", LEGAL)
def test_legal_moves_are_allowed(frm, to):
    assert can_transition(frm, to) is True
    assert_transition(frm, to)  # must not raise


# ── Trick: derive the illegal moves instead of listing them by hand ──────────
# Every ordered pair of statuses that is NOT in LEGAL must be rejected.
ILLEGAL = [
    (a, b) for a, b in itertools.product(Status, Status)
    if (a, b) not in set(LEGAL)
]


@pytest.mark.parametrize("frm,to", ILLEGAL)
def test_everything_else_is_rejected(frm, to):
    assert can_transition(frm, to) is False
    with pytest.raises(InvalidTransition):
        assert_transition(frm, to)


# ── TODO: business-rule tests, one per line of reasoning in the plan ─────────

def test_terminal_states_are_exactly_these():
    # TODO: which states are terminal? Fill in the expected set.
    expected = {S.REJECTED, S.REFUND_EXECUTED}
    assert set(TERMINAL_STATES) == expected


def test_terminal_states_have_no_exit():
    for s in TERMINAL_STATES:
        assert is_terminal(s)
        assert next_states(s) == frozenset()


def test_money_never_moves_without_approval():
    # TODO: refund_executed may only be reached from approved, auto_approved or
    # refund_failed. Assert that no other status can move to REFUND_EXECUTED.
    allowed_sources = {S.APPROVED, S.REFUND_FAILED, S.AUTO_APPROVED}
    for s in Status:
        if can_transition(s, S.REFUND_EXECUTED):
            assert s in allowed_sources


def test_pending_review_cannot_skip_the_human():
    # TODO: from pending_review the only exits are approved / rejected.
    assert next_states(S.PENDING_REVIEW) == {S.APPROVED, S.REJECTED}


def test_invalid_transition_message_names_both_states():
    with pytest.raises(InvalidTransition) as exc:
        assert_transition(S.REJECTED, S.APPROVED)
    # TODO: assert the message contains "rejected" and "approved",
    # and that exc.value.from_status / .to_status are set correctly.
    assert "rejected" in str(exc.value)
    assert "approved" in str(exc.value)
    assert exc.value.from_status == S.REJECTED
    assert exc.value.to_status == S.APPROVED