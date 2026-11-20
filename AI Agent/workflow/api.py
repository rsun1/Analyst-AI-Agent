"""
HTTP endpoints for the dispute workflow (FastAPI router, mounted by the root api.py,
which also adds the X-API-Key check to every route here).

    POST /disputes                     submit a dispute -> decision (+ refund if auto-approved)
    GET  /disputes/{id}                current state
    GET  /disputes/{id}/audit          the full decision trail, oldest first
    POST /disputes/{id}/retry-refund   retry a failed refund (3 failures -> human review)
    GET  /approvals?status=pending     the review queue
    POST /approvals/{id}/decision      a human approves or rejects (header X-Reviewer)

Error convention
    400  bad input, or the order does not exist (nothing is stored)
    404  dispute / approval id not found
    409  the request is valid but the dispute is not in a state that allows it
         (already decided, illegal transition, refund not allowed)
    422  malformed request body (FastAPI/pydantic)

`as_of` on POST /disputes exists because the Olist data is from 2016-2018; leave it out
to use the current time.
"""
from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from db import get_workflow_connection
from workflow import service
from workflow.models import ReasonCategory
from workflow.service import InvalidClaim, OrderNotFound, RefundNotAllowed
from workflow.states import InvalidTransition
from workflow.store import (
    ApprovalAlreadyDecided, ApprovalNotFound, DisputeNotFound, get_dispute, list_approvals, list_audit,
)

router = APIRouter(tags=["workflow"])


class SubmitDisputeRequest(BaseModel):
    order_id: str = Field(min_length=1, max_length=64)
    amount: Decimal = Field(description="Amount to refund, at most 2 decimals.")
    reason_text: str = Field(min_length=1, max_length=2000)
    reason_category: ReasonCategory = ReasonCategory.OTHER
    as_of: datetime | None = Field(default=None, description="Simulated 'now' for the order timeline.")


class SubmitDisputeResponse(BaseModel):
    dispute_id: int
    status: str
    outcome: Literal["auto_approve", "needs_review", "reject"]
    rule_id: str
    reasoning: str
    approval_id: int | None = None


class DisputeOut(BaseModel):
    id: int
    order_id: str
    customer_id: str
    reason_text: str
    reason_category: str | None
    amount_requested: Decimal
    status: str
    review_reason_type: str | None
    created_at: datetime
    updated_at: datetime
    pending_approval_id: int | None = None


class AuditEntry(BaseModel):
    id: int
    ts: datetime
    action: str
    actor_type: str
    actor_id: str
    from_status: str | None
    to_status: str
    rule_id: str | None
    reasoning: str
    details: dict


class ApprovalOut(BaseModel):
    id: int
    dispute_id: int
    status: str
    requested_at: datetime
    decided_at: datetime | None
    decided_by: str | None
    comment: str | None


class DecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    comment: str | None = Field(default=None, max_length=2000)


class StatusResponse(BaseModel):
    status: str


@router.post("/disputes", response_model=SubmitDisputeResponse, status_code=201)
def submit_dispute(req: SubmitDisputeRequest):
    """Evaluate the policy and act on it: reject, send to a human, or approve and refund."""
    try:
        r = service.submit_dispute(order_id=req.order_id, amount=req.amount, reason_text=req.reason_text,
                                   reason_category=req.reason_category, as_of=req.as_of)
    except OrderNotFound:
        raise HTTPException(400, f"Order '{req.order_id}' does not exist.")
    except InvalidClaim as e:
        raise HTTPException(400, str(e))
    return SubmitDisputeResponse(
        dispute_id=r.dispute_id, status=r.status.value, outcome=r.decision.outcome.value,
        rule_id=r.decision.rule_id, reasoning=r.decision.reasoning, approval_id=r.approval_id)


@router.get("/disputes/{dispute_id}", response_model=DisputeOut)
def read_dispute(dispute_id: int):
    with get_workflow_connection() as conn:
        try:
            d = get_dispute(conn, dispute_id)
        except DisputeNotFound:
            raise HTTPException(404, f"Dispute {dispute_id} not found.")
        pending = [a["id"] for a in list_approvals(conn) if a["dispute_id"] == dispute_id]
    return DisputeOut(**d, pending_approval_id=pending[0] if pending else None)


@router.get("/disputes/{dispute_id}/audit", response_model=list[AuditEntry])
def read_audit(dispute_id: int):
    with get_workflow_connection() as conn:
        try:
            get_dispute(conn, dispute_id)
        except DisputeNotFound:
            raise HTTPException(404, f"Dispute {dispute_id} not found.")
        return [AuditEntry(**a) for a in list_audit(conn, dispute_id)]


@router.post("/disputes/{dispute_id}/retry-refund", response_model=StatusResponse)
def retry_refund(dispute_id: int):
    try:
        return StatusResponse(status=service.retry_refund(dispute_id).value)
    except DisputeNotFound:
        raise HTTPException(404, f"Dispute {dispute_id} not found.")
    except (RefundNotAllowed, InvalidTransition) as e:
        raise HTTPException(409, str(e))


@router.get("/approvals", response_model=list[ApprovalOut])
def read_approvals(status: Literal["pending", "approved", "rejected", "all"] = Query("pending")):
    with get_workflow_connection() as conn:
        rows = list_approvals(conn, None if status == "all" else status)
    return jsonable_encoder(rows)


@router.post("/approvals/{approval_id}/decision", response_model=StatusResponse)
def decide(approval_id: int, req: DecisionRequest, x_reviewer: str | None = Header(default=None)):
    """A human decision. The reviewer is named by the X-Reviewer header and recorded in the audit."""
    if not x_reviewer or not x_reviewer.strip():
        raise HTTPException(400, "The X-Reviewer header is required.")
    try:
        status = service.decide(approval_id=approval_id, decision=req.decision,
                                reviewer=x_reviewer.strip(), comment=req.comment)
    except ApprovalNotFound:
        raise HTTPException(404, f"Approval {approval_id} not found.")
    except (ApprovalAlreadyDecided, InvalidTransition, RefundNotAllowed) as e:
        raise HTTPException(409, str(e))
    return StatusResponse(status=status.value)
