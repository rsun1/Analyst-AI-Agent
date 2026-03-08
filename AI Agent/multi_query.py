"""
Multi-Query Analysis: autonomous decomposition, parallel sub-query execution,
and report synthesis for broad business questions.

Flow:
  is_broad_question(q) → True
    → decompose_question(q) → [sub_q1, sub_q2, ...]
    → run_sub_query(sub_q) for each → [SubQueryResult, ...]
    → synthesise_report(broad_q, results) → markdown report
    → MultiQueryReport
"""
from __future__ import annotations

import dataclasses
from typing import Callable

from openai import OpenAI

from config import (
    CHAT_MODEL,
    MULTI_QUERY_DOMAIN_CONTEXT,
    OPENAI_API_KEY,
)


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclasses.dataclass
class SubQueryResult:
    sub_question: str
    sql: str
    columns: list[str]
    rows: list[tuple]
    summary: str
    error: str | None = None


@dataclasses.dataclass
class MultiQueryReport:
    broad_question: str
    sub_results: list[SubQueryResult]
    report_markdown: str
    failed_count: int


# ── Classifier ─────────────────────────────────────────────────────────────────

_BROAD_SIGNALS = {
    "analyze", "analysis", "full picture", "overview", "deep dive",
    "comprehensive", "diagnose", "performance", "behavior", "behaviour",
    "profile", "breakdown", "landscape", "assessment", "audit",
}

_NARROW_SIGNALS = {
    "how many", "what is", "what was", "total", "average", "avg",
    "top ", "per month", "per week", "per year", "by category",
    "how much", "count", "sum", "list", "show me", "what are the",
}


def _keyword_classify(question: str) -> str:
    """Return 'broad', 'narrow', or 'ambiguous'."""
    q_lower = question.lower()
    is_narrow = any(signal in q_lower for signal in _NARROW_SIGNALS)
    is_broad = any(signal in q_lower for signal in _BROAD_SIGNALS)

    if is_narrow and not is_broad:
        return "narrow"
    if is_broad and not is_narrow:
        return "broad"
    return "ambiguous"


def _llm_classify(question: str) -> bool:
    """
    Ask the LLM: does this question require multiple SQL queries?
    Returns True for broad, False for narrow.
    Defaults to False on any error.
    """
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You decide if a business question needs multiple SQL queries to answer "
                        "fully, or if a single SQL query suffices. "
                        "Reply with exactly YES (multiple queries needed) or NO (single query)."
                    ),
                },
                {"role": "user", "content": f'Question: "{question}"'},
            ],
            max_tokens=5,
            temperature=0,
        )
        answer = (response.choices[0].message.content or "").strip().upper()
        return answer.startswith("YES")
    except Exception:
        return False  # default to single-query path on any error


def is_broad_question(question: str) -> bool:
    """
    Two-stage classifier.
    Stage 1 (free): keyword heuristic.
    Stage 2 (LLM): only when stage 1 is ambiguous.
    Returns True when the question warrants multi-query decomposition.
    """
    result = _keyword_classify(question)
    if result == "broad":
        return True
    if result == "narrow":
        return False
    # Ambiguous — ask LLM
    return _llm_classify(question)


# ── Decomposition ──────────────────────────────────────────────────────────────

def _parse_subquestions(llm_response: str) -> list[str]:
    """Parse a numbered/bulleted list from the LLM response. Returns at most 5."""
    lines = llm_response.strip().splitlines()
    questions: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Strip leading numbering like "1.", "1)", "-", "*", "•"
        for prefix in ("1.", "2.", "3.", "4.", "5.", "1)", "2)", "3)", "4)", "5)", "-", "*", "•"):
            if line.startswith(prefix):
                line = line[len(prefix):].strip()
                break
        if line:
            questions.append(line)
    return questions[:5]


def decompose_question(broad_question: str) -> list[str]:
    """
    Call OpenAI to decompose broad_question into 3-5 specific sub-questions,
    each answerable by a single SQL query.
    Raises ValueError if fewer than 2 sub-questions are returned.
    """
    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an analytics planning expert. Given a broad business question "
                    f"about the {MULTI_QUERY_DOMAIN_CONTEXT}, decompose it into 3-5 specific "
                    "sub-questions. Each sub-question must be answerable by a single SQL query "
                    "and cover a distinct, non-overlapping aspect. "
                    "Return a numbered list only. No explanation, no preamble."
                ),
            },
            {
                "role": "user",
                "content": f'Broad question: "{broad_question}"\n\nList 3-5 specific sub-questions:',
            },
        ],
    )
    content = (response.choices[0].message.content or "").strip()
    sub_questions = _parse_subquestions(content)

    if len(sub_questions) < 2:
        raise ValueError(
            f"Decomposition returned too few sub-questions ({len(sub_questions)}). "
            "Cannot run multi-query analysis."
        )

    return sub_questions


# ── Sub-query runner ───────────────────────────────────────────────────────────

def run_sub_query(
    sub_question: str,
    conversation_history: list[dict] | None = None,
) -> SubQueryResult:
    """
    Run one sub-question through the full pipeline without user approval.
    Returns SubQueryResult. Never raises — populates .error on failure.
    """
    # Import here to avoid circular imports at module load time
    from agent_core import step_explain, step_execute, step_generate, step_rag

    try:
        rag_context = step_rag(sub_question)
        sql, _ = step_generate(sub_question, rag_context, conversation_history)

        if not sql:
            return SubQueryResult(
                sub_question=sub_question,
                sql="",
                columns=[],
                rows=[],
                summary="",
                error="SQL generation returned an empty query.",
            )

        columns, rows, _ = step_execute(sql)
        summary = step_explain(sub_question, sql, columns, rows)

        return SubQueryResult(
            sub_question=sub_question,
            sql=sql,
            columns=columns,
            rows=rows,
            summary=summary,
        )

    except Exception as exc:
        return SubQueryResult(
            sub_question=sub_question,
            sql="",
            columns=[],
            rows=[],
            summary="",
            error=str(exc),
        )


# ── Synthesis ──────────────────────────────────────────────────────────────────

def synthesise_report(broad_question: str, sub_results: list[SubQueryResult]) -> str:
    """
    Call OpenAI to produce a structured markdown report from all sub-results.
    Raw rows are NOT sent — only summaries — to keep token count bounded.
    """
    successful = [r for r in sub_results if r.error is None]
    failed = [r for r in sub_results if r.error is not None]

    if not successful:
        failed_list = "\n".join(f"- {r.sub_question}: {r.error}" for r in failed)
        return (
            "## Analysis Failed\n\n"
            "All sub-queries encountered errors. Please try rephrasing your question "
            "or ask a more specific question.\n\n"
            f"**Errors:**\n{failed_list}"
        )

    sub_sections = []
    for i, r in enumerate(successful, 1):
        sub_sections.append(
            f"Sub-question {i}: {r.sub_question}\n"
            f"Finding: {r.summary}\n"
            f"Row count: {len(r.rows)}"
        )

    sub_block = "\n\n".join(sub_sections)

    failed_note = ""
    if failed:
        failed_note = (
            f"\n\nNote: {len(failed)} sub-question(s) could not be executed due to errors: "
            + "; ".join(r.sub_question for r in failed)
        )

    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a senior business analyst. Given a broad question and findings "
                    "from multiple data queries, write a structured markdown report. Include: "
                    "(1) an executive summary paragraph, "
                    "(2) one H3 section per sub-question with the key finding, "
                    "(3) a 'Key Takeaways' section with 3-5 bullet points. "
                    "Be concise, use plain English, and cite specific numbers from the findings."
                ),
            },
            {
                "role": "user",
                "content": (
                    f'Broad question: "{broad_question}"\n\n'
                    f"Query findings:\n\n{sub_block}"
                    f"{failed_note}"
                ),
            },
        ],
    )
    return (response.choices[0].message.content or "").strip()


# ── Top-level orchestrator ─────────────────────────────────────────────────────

def run_multi_query_analysis(
    broad_question: str,
    conversation_history: list[dict] | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> MultiQueryReport:
    """
    Full orchestration:
    1. decompose_question → sub_questions
    2. For each sub_question: run_sub_query (calls progress_callback with status)
    3. synthesise_report → markdown
    Returns MultiQueryReport.
    """
    def _notify(msg: str) -> None:
        if progress_callback:
            try:
                progress_callback(msg)
            except Exception:
                pass

    _notify("Breaking down your question into sub-analyses...")
    sub_questions = decompose_question(broad_question)

    sub_results: list[SubQueryResult] = []
    for i, sq in enumerate(sub_questions, 1):
        _notify(f"Running analysis {i}/{len(sub_questions)}: {sq}")
        result = run_sub_query(sq, conversation_history)
        sub_results.append(result)

    _notify("Synthesising final report...")
    report_md = synthesise_report(broad_question, sub_results)

    failed_count = sum(1 for r in sub_results if r.error is not None)

    return MultiQueryReport(
        broad_question=broad_question,
        sub_results=sub_results,
        report_markdown=report_md,
        failed_count=failed_count,
    )
