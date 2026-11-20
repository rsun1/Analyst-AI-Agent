"""
Refund policy as an ordered list of explainable rules (see docs/refund-policy.md).

Pure functions: evaluate(facts, claim) -> Decision. No database, no clock, no LLM.
Rules run in priority order and the FIRST one that fires decides, so rule order is
the policy. Default stance: when unsure, send it to a human, never auto-approve.
"""
from datetime import timedelta
from decimal import Decimal
from typing import Callable

from workflow.models import Claim, Decision, OrderFacts, Outcome, ReasonCategory, ReviewType

# ── Policy constants (keep in sync with docs/refund-policy.md) ───────────────
SMALL_AMOUNT = Decimal("100")       # <= this may be auto-approved
HIGH_VALUE = Decimal("450")         # >= this always needs a human
REFUND_WINDOW = timedelta(days=30)  # after the delivered date
REPEAT_REFUND_LIMIT = 2             # refunds in the last 90 days that trigger a review
NOT_REFUNDABLE_STATUSES = {"canceled", "unavailable"}  # nothing was shipped or charged out

R01 = "R01_ORDER_NOT_FOUND"
R02 = "R02_DUPLICATE_OPEN"
R03 = "R03_NOT_REFUNDABLE_STATUS"
R03B = "R03B_NOT_DELIVERED"
R04 = "R04_AMOUNT_NOT_COVERED"
R05B = "R05B_MISSING_DELIVERY_DATE"
R05 = "R05_OUTSIDE_WINDOW"
R06 = "R06_CONTRADICTION"
R07 = "R07_HIGH_VALUE"
R08 = "R08_REPEAT_REFUNDER"
R09 = "R09_LATE_DELIVERY_LOW_SCORE"
R10 = "R10_SMALL_AMOUNT"
R99 = "R99_DEFAULT"


def _reject(rule_id: str, *reasons: str) -> Decision:
    return Decision(Outcome.REJECT, rule_id, tuple(reasons))


def _approve(rule_id: str, *reasons: str) -> Decision:
    return Decision(Outcome.AUTO_APPROVE, rule_id, tuple(reasons))


def _review(rule_id: str, kind: ReviewType, *reasons: str) -> Decision:
    return Decision(Outcome.NEEDS_REVIEW, rule_id, tuple(reasons), kind)


def _is_late(f: OrderFacts) -> bool:
    return (f.delivered_at is not None and f.estimated_delivery_at is not None
            and f.delivered_at > f.estimated_delivery_at)


# ── Rules: each returns a Decision if it fires, else None ────────────────────

def rule_duplicate_open(f: OrderFacts, c: Claim) -> Decision | None:
    if f.has_open_dispute:
        return _reject(R02, "Another dispute on this order is still open.")


def rule_not_refundable_status(f: OrderFacts, c: Claim) -> Decision | None:
    if f.status in NOT_REFUNDABLE_STATUSES:
        return _reject(R03, f"Order status is '{f.status}': the order never completed, so there is nothing to refund.")


def rule_not_delivered(f: OrderFacts, c: Claim) -> Decision | None:
    if f.status != "delivered":
        return _review(R03B, ReviewType.POLICY,
                       f"Order status is '{f.status}' (not delivered yet); a human should decide.")


def rule_amount_not_covered(f: OrderFacts, c: Claim) -> Decision | None:
    if f.paid_total is None or f.paid_total <= 0:
        return _reject(R04, "No payment is recorded for this order, so there is nothing to refund.")
    remaining = f.paid_total - f.refunded_total
    if c.amount > remaining:
        if f.refunded_total > 0:
            return _reject(R04, f"Requested {c.amount} but only {remaining} of the {f.paid_total} paid is "
                                f"still refundable ({f.refunded_total} already refunded).")
        return _reject(R04, f"Requested {c.amount} exceeds the amount paid ({f.paid_total}).")


def rule_missing_delivery_date(f: OrderFacts, c: Claim) -> Decision | None:
    if f.delivered_at is None:
        return _review(R05B, ReviewType.AGENT_UNCERTAIN,
                       "Order is marked delivered but has no delivery date, so I cannot check the "
                       "refund window. I cannot judge this; a human needs to look.")


def rule_outside_window(f: OrderFacts, c: Claim) -> Decision | None:
    elapsed = c.as_of - f.delivered_at
    if elapsed > REFUND_WINDOW:
        return _reject(R05, f"Delivered {elapsed.days} days ago; the refund window is {REFUND_WINDOW.days} days.")


def rule_contradiction(f: OrderFacts, c: Claim) -> Decision | None:
    if (c.reason_category is ReasonCategory.NOT_RECEIVED
            and f.review_score is not None and f.review_score >= 4):
        return _review(R06, ReviewType.AGENT_UNCERTAIN,
                       f"Claim says the order was not received, but it is recorded as delivered "
                       f"on {f.delivered_at:%Y-%m-%d} and the customer left a {f.review_score}-star review. "
                       "These contradict each other; I cannot judge this, a human needs to look.")
    if (c.reason_category is ReasonCategory.LATE_DELIVERY
            and f.delivered_at is not None and f.estimated_delivery_at is not None
            and not _is_late(f)):
        return _review(R06, ReviewType.AGENT_UNCERTAIN,
                       f"Claim says the delivery was late, but it arrived {f.delivered_at:%Y-%m-%d}, "
                       f"on or before the estimate {f.estimated_delivery_at:%Y-%m-%d}. "
                       "The claim contradicts the records; a human needs to look.")


def rule_high_value(f: OrderFacts, c: Claim) -> Decision | None:
    if c.amount >= HIGH_VALUE:
        return _review(R07, ReviewType.POLICY, f"Requested {c.amount} is at or above the high-value limit ({HIGH_VALUE}).")


def rule_repeat_refunder(f: OrderFacts, c: Claim) -> Decision | None:
    if f.refunds_last_90d >= REPEAT_REFUND_LIMIT:
        return _review(R08, ReviewType.POLICY,
                       f"Customer already had {f.refunds_last_90d} refunds in the last 90 days "
                       f"(limit {REPEAT_REFUND_LIMIT}).")


def rule_late_delivery_low_score(f: OrderFacts, c: Claim) -> Decision | None:
    if _is_late(f) and f.review_score is not None and f.review_score <= 2 and c.amount <= SMALL_AMOUNT:
        return _approve(R09, f"Delivered late ({f.delivered_at:%Y-%m-%d} vs estimate "
                             f"{f.estimated_delivery_at:%Y-%m-%d}), review score {f.review_score}, "
                             f"amount {c.amount} <= {SMALL_AMOUNT}.")


def rule_small_amount(f: OrderFacts, c: Claim) -> Decision | None:
    if c.amount <= SMALL_AMOUNT:
        return _approve(R10, f"Amount {c.amount} <= {SMALL_AMOUNT} and within the refund window.")


def rule_default(f: OrderFacts, c: Claim) -> Decision | None:
    return _review(R99, ReviewType.POLICY, "No auto-approval rule applies; sending to a human by default.")


# Order matters: this list IS the policy.
RULES: list[Callable[[OrderFacts, Claim], Decision | None]] = [
    rule_duplicate_open,
    rule_not_refundable_status,
    rule_not_delivered,
    rule_amount_not_covered,
    rule_missing_delivery_date,
    rule_outside_window,
    rule_contradiction,
    rule_high_value,
    rule_repeat_refunder,
    rule_late_delivery_low_score,
    rule_small_amount,
    rule_default,
]


def evaluate(facts: OrderFacts | None, claim: Claim) -> Decision:
    """First rule that fires wins. `facts` is None when the order does not exist."""
    if facts is None:
        return _reject(R01, f"Order '{claim.order_id}' does not exist.")
    for rule in RULES:
        decision = rule(facts, claim)
        if decision is not None:
            return decision
    raise AssertionError("unreachable: rule_default always fires")
