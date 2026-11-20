"""
Audit replay: rebuild "who decided what, when, under which rule and why" from the
audit_log ALONE, and check that the trail is internally consistent.

    python -m workflow.replay 42        # print the story of dispute 42, exit 1 if the trail is broken

The core (build_steps / verify_entries / narrate) is pure: it takes audit rows and returns
text, so it can be tested without a database. replay_audit() is the thin DB wrapper.

verify_entries checks:
  * the trail starts with None -> submitted and every row continues from the previous status
  * every status change is a legal move of the state machine
  * rows that keep the status (from == to) are only the known "event" actions
  * decision rows carry a rule id, a reason and the evidence snapshot (facts + claim)
  * human rows come from a named human and from pending_review
  * timestamps never go backwards
  * money: at most one successful refund, none before an approval, and (when given) the
    trail agrees with disputes.status and with the refund_attempts table
"""
import argparse
import sys
from dataclasses import dataclass, field

from db import close_pool, get_workflow_connection
from workflow.states import Status, can_transition
from workflow.store import get_dispute, list_audit

EVENT_ACTIONS = {"refund_failed", "reviewer_brief_added"}          # may keep the status unchanged
DECISION_ACTIONS = {"policy_rejected", "sent_to_human_review", "policy_auto_approved"}
APPROVED_STATES = {Status.AUTO_APPROVED.value, Status.APPROVED.value}


@dataclass(frozen=True)
class Step:
    ts: object
    actor: str
    action: str
    from_status: str | None
    to_status: str
    rule_id: str | None
    reasoning: str
    details: dict


@dataclass
class Replay:
    dispute_id: int
    steps: list[Step]
    narrative: str
    summary: dict
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def build_steps(entries: list[dict]) -> list[Step]:
    return [Step(e["ts"], f"{e['actor_type']}:{e['actor_id']}", e["action"], e["from_status"], e["to_status"],
                 e["rule_id"], e["reasoning"], e["details"] or {}) for e in entries]


def _evidence(details: dict) -> str | None:
    f, c = details.get("facts"), details.get("claim")
    if not f or not c:
        return None
    day = lambda s: (s or "n/a")[:10]
    return (f"order status={f.get('status')}, paid={f.get('paid_total')}, requested={c.get('amount')}, "
            f"delivered {day(f.get('delivered_at'))} vs estimate {day(f.get('estimated_delivery_at'))}, "
            f"review score={f.get('review_score')}, open duplicate={f.get('has_open_dispute')}, "
            f"refunds in 90d={f.get('refunds_last_90d')}, already refunded={f.get('refunded_total')}")


def narrate(steps: list[Step]) -> str:
    lines = []
    for s in steps:
        move = f"{s.from_status or '-'} -> {s.to_status}" if s.from_status != s.to_status else f"(stays {s.to_status})"
        rule = f" [{s.rule_id}]" if s.rule_id else ""
        lines.append(f"{s.ts:%Y-%m-%d %H:%M:%S}  {s.actor:<24} {s.action}: {move}{rule}")
        lines.append(f"    why: {s.reasoning}")
        ev = _evidence(s.details)
        if ev:
            lines.append(f"    evidence: {ev}")
        llm = s.details.get("llm_second_opinion")
        if llm:
            rules_said = (s.details.get("rules_decision") or {}).get("outcome")
            lines.append(f"    second opinion: escalate={llm.get('escalate')} ({llm.get('reason')}); "
                         f"the rules alone said {rules_said}")
    return "\n".join(lines)


def summarize(steps: list[Step]) -> dict:
    if not steps:
        return {}
    first = steps[0].details
    decision = next((s for s in steps if s.action in DECISION_ACTIONS), None)
    return {
        "order_id": first.get("order_id"),
        "amount_requested": first.get("amount_requested"),
        "final_status": steps[-1].to_status,
        "decision": {"action": decision.action, "rule_id": decision.rule_id, "reason": decision.reasoning}
                    if decision else None,
        "rules_fired": [s.rule_id for s in steps if s.rule_id],
        "human_reviewers": [s.actor.split(":", 1)[1] for s in steps if s.action.startswith("human_")],
        "refund_attempts_logged": sum(s.action in ("refund_succeeded", "refund_failed") for s in steps),
        "refunded": any(s.action == "refund_succeeded" for s in steps),
    }


def verify_entries(entries: list[dict], *, current_status: str | None = None,
                   successful_attempts: int | None = None) -> list[str]:
    problems: list[str] = []
    if not entries:
        return ["the audit trail is empty"]

    first = entries[0]
    if first["from_status"] is not None or first["to_status"] != Status.SUBMITTED.value:
        problems.append("the trail does not start with None -> submitted")

    prev = None
    approved_seen = False
    successes = 0
    for i, e in enumerate(entries):
        where = f"entry {i + 1} ({e['action']})"
        if prev is not None:
            if e["from_status"] != prev["to_status"]:
                problems.append(f"{where}: starts from {e['from_status']} but the previous entry ended in {prev['to_status']}")
            if e["ts"] < prev["ts"]:
                problems.append(f"{where}: timestamp goes backwards")
        if i > 0 and e["from_status"] is not None:
            if e["from_status"] != e["to_status"]:
                if not can_transition(Status(e["from_status"]), Status(e["to_status"])):
                    problems.append(f"{where}: illegal move {e['from_status']} -> {e['to_status']}")
            elif e["action"] not in EVENT_ACTIONS:
                problems.append(f"{where}: changes nothing but is not a known event action")

        if not (e["reasoning"] or "").strip():
            problems.append(f"{where}: no reasoning recorded")
        if e["action"] in DECISION_ACTIONS:
            details = e["details"] or {}
            if not e["rule_id"]:
                problems.append(f"{where}: a decision without a rule id")
            if "facts" not in details or "claim" not in details:
                problems.append(f"{where}: the evidence snapshot (facts + claim) is missing")
        if e["action"].startswith("human_"):
            if e["actor_type"] != "human" or not e["actor_id"].strip():
                problems.append(f"{where}: a human decision without a named human")
            if e["from_status"] != Status.PENDING_REVIEW.value:
                problems.append(f"{where}: a human decision outside pending_review")

        if e["action"] == "refund_succeeded":
            successes += 1
            if not approved_seen:
                problems.append(f"{where}: money moved before any approval")
        if e["to_status"] in APPROVED_STATES:
            approved_seen = True
        prev = e

    if successes > 1:
        problems.append(f"{successes} successful refunds recorded; money moved more than once")
    last = entries[-1]["to_status"]
    if (last == Status.REFUND_EXECUTED.value) != (successes == 1):
        problems.append("the final status and the successful refund entries disagree")
    if current_status is not None and current_status != last:
        problems.append(f"the audit trail ends in {last} but the dispute is {current_status}")
    if successful_attempts is not None and successful_attempts != successes:
        problems.append(f"the audit shows {successes} successful refund(s) but refund_attempts has {successful_attempts}")
    return problems


def replay_audit(dispute_id: int) -> Replay:
    """Story + consistency check for one dispute. The story uses only audit rows; the checks
    also compare against disputes.status and refund_attempts to catch drift."""
    with get_workflow_connection() as conn:
        dispute = get_dispute(conn, dispute_id)   # raises DisputeNotFound
        entries = list_audit(conn, dispute_id)
        ok_attempts = conn.execute(
            "SELECT count(*) FROM workflow.refund_attempts WHERE dispute_id = %s AND result = 'success'",
            (dispute_id,)).fetchone()[0]
    steps = build_steps(entries)
    problems = verify_entries(entries, current_status=dispute["status"], successful_attempts=ok_attempts)
    return Replay(dispute_id, steps, narrate(steps), summarize(steps), problems)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Replay a dispute's decision trail from the audit log.")
    p.add_argument("dispute_id", type=int)
    args = p.parse_args(argv)
    try:
        r = replay_audit(args.dispute_id)
    finally:
        close_pool()  # otherwise the pool's worker threads print shutdown warnings on exit
    print(f"Dispute {r.dispute_id}: {r.summary.get('order_id')}  requested {r.summary.get('amount_requested')}  "
          f"-> {r.summary.get('final_status')}\n")
    print(r.narrative)
    print("\nAudit check:", "OK" if r.ok else "PROBLEMS FOUND")
    for prob in r.problems:
        print(" -", prob)
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
