"""
A fake payment gateway (no real money, no real provider).

It behaves like a real one in the ways that matter for the design:
  * idempotent: calling refund() again with the SAME idempotency key replays the first
    result instead of moving money twice (so a retry after a crash is safe);
  * can fail on demand (`fail_next`), so the failure path can be tested;
  * counts real executions in `calls` (replays are not counted), so tests can assert
    that money moved exactly once.
"""
import threading
import uuid
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PaymentResult:
    success: bool
    reference: str | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        return {"success": self.success, "reference": self.reference, "error": self.error}


class MockPaymentGateway:
    def __init__(self, fail_next: int = 0):
        self.fail_next = fail_next          # the next N real calls fail
        self.calls = 0                      # real executions (replays excluded)
        self.refunded: list[tuple[str, Decimal]] = []   # (order_id, amount) that actually "moved"
        self._results: dict[str, PaymentResult] = {}
        self._lock = threading.Lock()

    def refund(self, *, idempotency_key: str, order_id: str, amount: Decimal) -> PaymentResult:
        with self._lock:
            if idempotency_key in self._results:
                return self._results[idempotency_key]  # replay, no second refund
            self.calls += 1
            if self.fail_next > 0:
                self.fail_next -= 1
                result = PaymentResult(False, error="mock: payment provider unavailable")
            else:
                result = PaymentResult(True, reference=f"mock-refund-{uuid.uuid4().hex[:12]}")
                self.refunded.append((order_id, amount))
            self._results[idempotency_key] = result
            return result


_default = MockPaymentGateway()


def default_gateway() -> MockPaymentGateway:
    return _default
