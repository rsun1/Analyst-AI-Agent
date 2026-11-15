"""
Orchestrator: decide which skill(s) should answer a question.

Two stages:
  1. retrieve  - embed the question and take the nearest skills (pgvector) as candidates.
  2. decide    - one small-model JSON call picks the route and the skill(s) among those
                 candidates. The model can only choose from the candidate list; anything
                 else is discarded and we fall back to the top candidate.

Routes: analyze (generate SQL), redirect (dashboard link, no SQL), not_computable
(explain the data gap), clarify (ask the user), out_of_scope.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from openai import OpenAI

from config import EMBEDDING_MODEL, OPENAI_API_KEY, ROUTER_CANDIDATES, ROUTER_MODEL
from db import get_connection

log = logging.getLogger("router")

ROUTES = ("analyze", "redirect", "not_computable", "clarify", "out_of_scope")


@dataclass
class Candidate:
    name: str
    route: str
    title: str
    description: str
    disambiguation: str
    distance: float


@dataclass
class RouteDecision:
    route: str
    skills: list[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)  # entities for dashboard links (validated later)
    message: str = ""                           # for clarify / out_of_scope
    confidence: float = 0.0
    reason: str = ""
    fallback: bool = False                      # True if the model's answer was unusable
    candidates: list[str] = field(default_factory=list)


def retrieve_candidates(question: str, k: int | None = None, client: OpenAI | None = None) -> list[Candidate]:
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not set in .env")
    client = client or OpenAI(api_key=OPENAI_API_KEY)
    emb = client.embeddings.create(model=EMBEDDING_MODEL, input=[question]).data[0].embedding
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name, route, title, description, disambiguation, embedding <=> %s::vector AS dist "
            "FROM agent_meta.skills ORDER BY dist LIMIT %s",
            (str(emb), k or ROUTER_CANDIDATES),
        ).fetchall()
    return [Candidate(*r) for r in rows]


_SYSTEM = """You are the router of an analytics assistant for the Olist Brazilian e-commerce dataset.
Given a user question and a list of candidate metric skills, choose how to handle it.

Routes:
- analyze: the question is answered by computing one or more candidate metrics. Pick 1-3 skills.
- redirect: a candidate skill with route "redirect" is a dashboard that fits the question (broad overview or monitoring, not a specific number). Pick exactly that skill.
- not_computable: the question needs data we do not have; pick the matching candidate with route "not_computable".
- clarify: the question is too vague to choose (say what to ask in "message").
- out_of_scope: unrelated to this dataset or business metrics (say so in "message").

Rules:
- Only use skill names from the candidate list. Never invent names.
- Prefer analyze for any concrete number, ranking, trend or comparison.
- Read each candidate's note.
- If the question mentions a Brazilian state (two letters, e.g. SP) or a period (YYYY-MM or YYYY-MM-DD), copy it to "params" as {"state": "SP", "date_from": "2017-07", "date_to": "2017-09"} (only the keys that apply).
Return ONLY JSON: {"route": "...", "skills": ["..."], "params": {}, "confidence": 0.0-1.0, "message": "", "reason": "one sentence"}"""


def _format_candidates(cands: list[Candidate]) -> str:
    lines = []
    for c in cands:
        extra = f" | note: {c.disambiguation}" if c.disambiguation else ""
        lines.append(f"- {c.name} [route={c.route}] {c.title}: {c.description}{extra}")
    return "\n".join(lines)


def decide(question: str, cands: list[Candidate], client: OpenAI | None = None) -> RouteDecision:
    """Ask the model to pick a route; validate it against the candidates."""
    names = [c.name for c in cands]
    by_name = {c.name: c for c in cands}
    client = client or OpenAI(api_key=OPENAI_API_KEY)
    raw: dict = {}
    try:
        resp = client.chat.completions.create(
            model=ROUTER_MODEL, temperature=0, response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": f"Candidates:\n{_format_candidates(cands)}\n\nQuestion: {question}"},
            ],
        )
        raw = json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:  # network, quota, bad JSON: fall back instead of failing the request
        log.warning("router decide failed: %s", e)
    if not isinstance(raw, dict):
        raw = {}

    route = raw.get("route")
    skills = [s for s in (raw.get("skills") or []) if isinstance(s, str) and s in by_name][:3]
    conf = raw.get("confidence")
    d = RouteDecision(
        route=route if route in ROUTES else "analyze",
        skills=skills,
        params=raw.get("params") if isinstance(raw.get("params"), dict) else {},
        message=str(raw.get("message") or ""),
        confidence=float(conf) if isinstance(conf, (int, float)) else 0.0,
        reason=str(raw.get("reason") or ""),
        candidates=names,
    )
    # Keep the route and the chosen skills consistent; otherwise fall back to the nearest candidate.
    if d.route in ("analyze", "redirect", "not_computable"):
        d.skills = [s for s in d.skills if by_name[s].route == d.route]
        if not d.skills:
            d.fallback = True
            if cands:
                d.route, d.skills = cands[0].route, [cands[0].name]
    return d


def route_question(question: str) -> RouteDecision:
    cands = retrieve_candidates(question)
    if not cands:
        return RouteDecision(route="analyze", fallback=True, reason="no skills indexed")
    return decide(question, cands)
