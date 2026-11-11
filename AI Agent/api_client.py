"""
Thin HTTP client for api.py. The Streamlit UI uses this instead of importing the
agent code, so the UI and the agent are separate deployable pieces.

Every method raises ApiError with a human-readable message on failure.
"""
import requests

from config import API_KEY, API_URL

# /analyze makes many model calls; everything else is quick.
_TIMEOUT = 60
_ANALYZE_TIMEOUT = 300


class ApiError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _call(method: str, path: str, *, json=None, timeout: int = _TIMEOUT):
    try:
        r = requests.request(
            method, f"{API_URL}{path}", json=json, timeout=timeout,
            headers={"X-API-Key": API_KEY or ""},
        )
    except requests.RequestException as e:
        raise ApiError(f"Could not reach the API at {API_URL}: {e}") from e
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        if isinstance(detail, list):  # FastAPI validation errors
            detail = "; ".join(str(d.get("msg", d)) for d in detail)
        raise ApiError(str(detail), r.status_code)
    return r.json() if r.content else None


def _history(history: list[dict]) -> list[dict]:
    """Last 12 turns, trimmed to the API's field limits."""
    out = []
    for t in history[-12:]:
        turn = {"role": t["role"], "content": t["content"][:4000]}
        if t.get("sql"):
            turn["sql"] = t["sql"][:10_000]
        out.append(turn)
    return out


def ask(question: str, history: list[dict]) -> dict:
    return _call("POST", "/ask", json={"question": question, "history": _history(history)})


def revise(question: str, sql: str, explanation: str, instruction: str, last_db_error: str | None) -> dict:
    return _call("POST", "/revise", json={
        "question": question, "sql": sql, "explanation": explanation,
        "instruction": instruction, "last_db_error": last_db_error,
    })


def execute(question: str, sql: str, proposed_sql: str, revision_history: list[dict],
            include_insights: bool = True) -> dict:
    return _call("POST", "/execute", json={
        "question": question, "sql": sql, "proposed_sql": proposed_sql,
        "revision_history": revision_history, "include_insights": include_insights,
    })


def scope(question: str) -> bool:
    return _call("POST", "/scope", json={"question": question})["broad"]


def analyze(question: str, history: list[dict]) -> dict:
    return _call("POST", "/analyze", json={"question": question, "history": _history(history)},
                 timeout=_ANALYZE_TIMEOUT)


def schema_status() -> dict:
    return _call("GET", "/schema/status")


def feedback(question: str, sql: str, rating: str, comment: str | None = None) -> None:
    _call("POST", "/feedback", json={"question": question, "sql": sql, "rating": rating, "comment": comment})
