"""Plain data types shared by the rules engine, the loaders and the service."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


class Outcome(str, Enum):
    AUTO_APPROVE = "auto_approve"
    NEEDS_REVIEW = "needs_review"
    REJECT = "reject"


class ReviewType(str, Enum):
    """Why a human is needed. Stored as disputes.review_reason_type."""
    POLICY = "policy"                    # a rule requires a human (e.g. high value)
    AGENT_UNCERTAIN = "agent_uncertain"  # the agent cannot judge (contradiction, missing data)


class ReasonCategory(str, Enum):
    NOT_RECEIVED = "not_received"
    LATE_DELIVERY = "late_delivery"
    DAMAGED = "damaged_or_defective"
    WRONG_ITEM = "wrong_item"
    OTHER = "other"


@dataclass(frozen=True)
class OrderFacts:
    """Everything the rules may know. All datetimes are naive (as stored in Olist)."""
    order_id: str
    status: str                              # Olist order_status
    paid_total: Decimal | None               # SUM(payment_value); None = no payment rows
    delivered_at: datetime | None
    estimated_delivery_at: datetime | None
    review_score: int | None                 # lowest score if the order has several reviews
    customer_id: str = ""
    customer_unique_id: str = ""
    has_open_dispute: bool = False           # another unfinished dispute on this order
    refunds_last_90d: int = 0                # executed refunds for this person in the last 90 days


@dataclass(frozen=True)
class Claim:
    order_id: str
    amount: Decimal
    as_of: datetime                          # "now" for the order timeline (injectable: data is 2016-2018)
    reason_category: ReasonCategory = ReasonCategory.OTHER
    reason_text: str = ""


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    rule_id: str
    reasons: tuple[str, ...]                 # human-readable, goes into audit_log.reasoning
    review_reason_type: ReviewType | None = None

    def __post_init__(self):
        if not self.reasons:
            raise ValueError("a decision must carry at least one reason")
        if (self.outcome is Outcome.NEEDS_REVIEW) != (self.review_reason_type is not None):
            raise ValueError("review_reason_type is set if and only if the outcome is needs_review")

    @property
    def reasoning(self) -> str:
        return "; ".join(self.reasons)
