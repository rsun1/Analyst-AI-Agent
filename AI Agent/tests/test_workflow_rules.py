"""Rules engine tests: pure Python, no database."""
import itertools
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from workflow import rules
from workflow.models import Claim, Decision, OrderFacts, Outcome, ReasonCategory, ReviewType
from workflow.rules import evaluate

D = Decimal
AS_OF = datetime(2018, 3, 10)
DELIVERED = datetime(2018, 3, 5)        # 5 days before AS_OF: inside the window
ESTIMATED_LATE = datetime(2018, 3, 1)   # delivered after this -> late
ESTIMATED_OK = datetime(2018, 3, 10)    # delivered before this -> on time


def facts(**over) -> OrderFacts:
    base = dict(order_id="o1", status="delivered", paid_total=D("200"), delivered_at=DELIVERED,
                estimated_delivery_at=ESTIMATED_OK, review_score=4)
    return OrderFacts(**{**base, **over})


def claim(amount="50", **over) -> Claim:
    return Claim(**{**dict(order_id="o1", amount=D(amount), as_of=AS_OF), **over})


def check(decision: Decision, outcome: Outcome, rule_id: str, review: ReviewType | None = None):
    assert (decision.outcome, decision.rule_id, decision.review_reason_type) == (outcome, rule_id, review), decision


# ── one test per rule (positive case), plus the near-miss ────────────────────

def test_r01_unknown_order():
    check(evaluate(None, claim()), Outcome.REJECT, rules.R01)


def test_r02_duplicate_open_dispute():
    check(evaluate(facts(has_open_dispute=True), claim()), Outcome.REJECT, rules.R02)


@pytest.mark.parametrize("status", ["canceled", "unavailable"])
def test_r03_not_refundable_status(status):
    check(evaluate(facts(status=status, delivered_at=None), claim()), Outcome.REJECT, rules.R03)


@pytest.mark.parametrize("status", ["created", "approved", "invoiced", "processing", "shipped"])
def test_r03b_not_delivered_goes_to_human(status):
    check(evaluate(facts(status=status, delivered_at=None), claim()),
          Outcome.NEEDS_REVIEW, rules.R03B, ReviewType.POLICY)


def test_r04_cannot_refund_more_than_what_is_left():
    f = facts(paid_total=D("100"), refunded_total=D("70"))
    check(evaluate(f, claim("30.01")), Outcome.REJECT, rules.R04)
    check(evaluate(f, claim("30")), Outcome.AUTO_APPROVE, rules.R10)
    assert "already refunded" in evaluate(f, claim("30.01")).reasoning


@pytest.mark.parametrize("paid", [None, D("0")])
def test_r04_nothing_was_paid(paid):
    check(evaluate(facts(paid_total=paid), claim()), Outcome.REJECT, rules.R04)


def test_r04_amount_above_paid_but_equal_is_fine():
    check(evaluate(facts(paid_total=D("60")), claim("60.01")), Outcome.REJECT, rules.R04)
    check(evaluate(facts(paid_total=D("60")), claim("60")), Outcome.AUTO_APPROVE, rules.R10)


def test_r05b_delivered_without_date_is_uncertain():
    check(evaluate(facts(delivered_at=None), claim()), Outcome.NEEDS_REVIEW, rules.R05B, ReviewType.AGENT_UNCERTAIN)


def test_r05_window_boundary():
    inside = AS_OF - timedelta(days=30)
    outside = AS_OF - timedelta(days=30, seconds=1)
    check(evaluate(facts(delivered_at=inside), claim()), Outcome.AUTO_APPROVE, rules.R10)
    check(evaluate(facts(delivered_at=outside), claim()), Outcome.REJECT, rules.R05)


def test_r06_not_received_but_delivered_and_happy():
    c = claim(reason_category=ReasonCategory.NOT_RECEIVED)
    check(evaluate(facts(review_score=5), c), Outcome.NEEDS_REVIEW, rules.R06, ReviewType.AGENT_UNCERTAIN)
    # near miss: an unhappy customer is not a contradiction
    assert evaluate(facts(review_score=1), c).rule_id != rules.R06


def test_r06_late_claim_but_delivered_on_time():
    c = claim(reason_category=ReasonCategory.LATE_DELIVERY)
    check(evaluate(facts(estimated_delivery_at=ESTIMATED_OK), c),
          Outcome.NEEDS_REVIEW, rules.R06, ReviewType.AGENT_UNCERTAIN)
    check(evaluate(facts(estimated_delivery_at=ESTIMATED_LATE, review_score=1), c),
          Outcome.AUTO_APPROVE, rules.R09)


def test_r06_message_says_the_agent_cannot_judge():
    d = evaluate(facts(review_score=5), claim(reason_category=ReasonCategory.NOT_RECEIVED))
    assert "cannot judge" in d.reasoning


def test_r07_high_value_boundary():
    check(evaluate(facts(paid_total=D("900")), claim("450")), Outcome.NEEDS_REVIEW, rules.R07, ReviewType.POLICY)
    check(evaluate(facts(paid_total=D("900")), claim("449.99")), Outcome.NEEDS_REVIEW, rules.R99, ReviewType.POLICY)


def test_r08_repeat_refunder():
    check(evaluate(facts(refunds_last_90d=2), claim()), Outcome.NEEDS_REVIEW, rules.R08, ReviewType.POLICY)
    check(evaluate(facts(refunds_last_90d=1), claim()), Outcome.AUTO_APPROVE, rules.R10)


def test_r09_late_and_unhappy_and_small():
    f = facts(estimated_delivery_at=ESTIMATED_LATE, review_score=2)
    check(evaluate(f, claim("100")), Outcome.AUTO_APPROVE, rules.R09)
    # near misses fall through to R99, never to approval
    check(evaluate(f, claim("100.01")), Outcome.NEEDS_REVIEW, rules.R99, ReviewType.POLICY)
    check(evaluate(facts(estimated_delivery_at=ESTIMATED_LATE, review_score=3), claim("100.01")),
          Outcome.NEEDS_REVIEW, rules.R99, ReviewType.POLICY)


def test_r10_small_amount_boundary():
    check(evaluate(facts(), claim("100")), Outcome.AUTO_APPROVE, rules.R10)


def test_r99_default_goes_to_a_human():
    check(evaluate(facts(), claim("150")), Outcome.NEEDS_REVIEW, rules.R99, ReviewType.POLICY)


# ── priority: the first matching rule wins ───────────────────────────────────

def test_canceled_beats_high_value():
    check(evaluate(facts(status="canceled", delivered_at=None), claim("500")), Outcome.REJECT, rules.R03)


def test_outside_window_beats_contradiction_and_high_value():
    f = facts(delivered_at=AS_OF - timedelta(days=60), review_score=5, paid_total=D("900"))
    check(evaluate(f, claim("500", reason_category=ReasonCategory.NOT_RECEIVED)), Outcome.REJECT, rules.R05)


def test_contradiction_beats_auto_approval():
    f = facts(review_score=5)
    d = evaluate(f, claim("10", reason_category=ReasonCategory.NOT_RECEIVED))
    assert d.outcome is Outcome.NEEDS_REVIEW  # small amount, but the story does not add up


def test_repeat_refunder_beats_auto_approval():
    f = facts(refunds_last_90d=3, estimated_delivery_at=ESTIMATED_LATE, review_score=1)
    check(evaluate(f, claim("10")), Outcome.NEEDS_REVIEW, rules.R08, ReviewType.POLICY)


# ── safety net: sweep many situations, check the auto-approve envelope ───────

def test_auto_approve_only_inside_the_safe_envelope():
    grid = itertools.product(
        ["delivered", "shipped", "canceled", "unavailable", "created"],        # status
        [None, D("0"), D("80"), D("300"), D("1000")],                          # paid
        [None, DELIVERED, AS_OF - timedelta(days=31)],                         # delivered_at
        [None, 1, 2, 3, 5],                                                    # review score
        [False, True],                                                         # open dispute
        [0, 1, 2, 5],                                                          # refunds in 90d
        [D("1"), D("100"), D("100.01"), D("449"), D("450"), D("5000")],        # amount
        list(ReasonCategory),
    )
    approved = 0
    for status, paid, delivered, score, open_d, refunds, amount, cat in grid:
        f = facts(status=status, paid_total=paid, delivered_at=delivered, review_score=score,
                  has_open_dispute=open_d, refunds_last_90d=refunds)
        d = evaluate(f, claim(str(amount), reason_category=cat))
        assert d.reasons and d.rule_id
        if d.outcome is Outcome.AUTO_APPROVE:
            approved += 1
            assert status == "delivered" and delivered is not None
            assert paid is not None and D("0") < amount <= min(paid, rules.SMALL_AMOUNT)
            assert (AS_OF - delivered) <= rules.REFUND_WINDOW
            assert not open_d and refunds < rules.REPEAT_REFUND_LIMIT
    assert approved > 0  # the sweep actually exercised the approve path


def test_decision_invariants():
    with pytest.raises(ValueError):
        Decision(Outcome.NEEDS_REVIEW, "X", ("why",))                          # review needs a type
    with pytest.raises(ValueError):
        Decision(Outcome.REJECT, "X", ("why",), ReviewType.POLICY)             # type only for review
    with pytest.raises(ValueError):
        Decision(Outcome.REJECT, "X", ())                                      # must explain itself
