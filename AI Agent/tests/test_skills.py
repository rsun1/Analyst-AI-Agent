"""
Tests for the skills layer: file format/validation, router decision logic (mocked
model), dashboard link safety, and the /ask routes. No OpenAI calls, no database
except the optional SQL gate at the bottom (skipped if Postgres is down).
"""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import api
import dashboards
import router
from skill_loader import Dashboard, extract_sql_blocks, load_skills, parse_skill

KEY = "test-key"
H = {"X-API-Key": KEY}

ANALYZE = """---
name: demo_metric
domain: sales
title: Demo metric
route: analyze
description: A demo metric used only in tests, with a long enough description.
triggers: ["demo question one", "demo question two"]
eval_questions: ["What is the demo metric?", "Demo by month?"]
---
## Definition
Something.
## SQL
### main
```sql
SELECT 1 AS x;
```
### by_state
```sql
SELECT 2 AS x;
```
## Pitfalls
- none
"""


def _with(text, old, new):
    assert old in text
    return text.replace(old, new, 1)


# -- skill files -----------------------------------------------------------------

def test_parse_valid_skill_extracts_labelled_sql():
    s = parse_skill(ANALYZE)
    assert s.name == "demo_metric" and s.meta.route == "analyze"
    assert s.sql_blocks == {"main": "SELECT 1 AS x;", "by_state": "SELECT 2 AS x;"}
    assert "Demo metric" in s.embedding_text() and "demo question one" in s.embedding_text()
    assert s.context_text().startswith("### skill: demo_metric")


@pytest.mark.parametrize("old,new,msg", [
    ("name: demo_metric", "name: Demo-Metric", "snake_case"),
    ("route: analyze", "route: magic", "route"),
    ("domain: sales", "domain: weather", "domain"),
    ('eval_questions: ["What is the demo metric?", "Demo by month?"]', 'eval_questions: ["only one"]', "eval_questions"),
    ("## SQL", "## Query", "no SQL block"),
    ("## Pitfalls", "## Notes", "Pitfalls"),
])
def test_invalid_skills_are_rejected(old, new, msg):
    with pytest.raises(ValueError, match=msg):
        parse_skill(_with(ANALYZE, old, new))


def test_missing_frontmatter_rejected():
    with pytest.raises(ValueError, match="frontmatter"):
        parse_skill("## Definition\nno frontmatter")


def test_redirect_needs_dashboard_and_only_redirect_may_have_one():
    redirect = ANALYZE.replace("route: analyze", "route: redirect").split("## SQL")[0]
    with pytest.raises(ValueError, match="dashboard"):
        parse_skill(redirect)
    with pytest.raises(ValueError, match="dashboard"):
        parse_skill(_with(ANALYZE, "route: analyze", "route: analyze\ndashboard: {page: x}"))


def test_non_analyze_skills_cannot_contain_sql():
    text = _with(ANALYZE, "route: analyze", "route: not_computable").replace(
        'eval_questions: ["What is the demo metric?", "Demo by month?"]\n', "")
    with pytest.raises(ValueError, match="only analyze"):
        parse_skill(text)


def test_extract_sql_blocks_default_label_and_duplicates():
    body = "## SQL\n```sql\nSELECT 1\n```\n```sql\nSELECT 2\n```\n## Pitfalls\n"
    assert extract_sql_blocks(body) == {"main": "SELECT 1", "main_2": "SELECT 2"}


def test_all_shipped_skills_are_valid_and_unique():
    skills = load_skills()
    assert len(skills) >= 19
    names = [s.name for s in skills]
    assert len(names) == len(set(names))
    for s in skills:
        if s.meta.route == "analyze":
            assert len(s.meta.eval_questions) >= 2 and s.sql_blocks
        if s.meta.route == "redirect":
            assert s.meta.dashboard


def test_loader_reports_all_problems(tmp_path):
    d = tmp_path / "sales"
    d.mkdir()
    (d / "demo_metric.md").write_text(ANALYZE, encoding="utf-8")
    (d / "wrong_name.md").write_text(ANALYZE, encoding="utf-8")        # name != file name
    (d / "broken.md").write_text("no frontmatter", encoding="utf-8")
    with pytest.raises(ValueError) as e:
        load_skills(tmp_path)
    msg = str(e.value)
    assert "wrong_name.md" in msg and "broken.md" in msg


# -- dashboards ------------------------------------------------------------------

DASH = Dashboard(provider="streamlit", page="executive_overview", params={"state": "state", "date_from": "date"})


def test_dashboard_link_keeps_only_allowlisted_valid_params(monkeypatch):
    monkeypatch.setattr(dashboards, "UI_BASE_URL", "https://ui.example")
    url = dashboards.build_link(DASH, {"state": "sp", "date_from": "2017-07", "evil": "x", "date_to": "2017-09"})
    assert url == "https://ui.example/?page=executive_overview&state=SP&date_from=2017-07"


@pytest.mark.parametrize("raw", [{"state": "XX"}, {"state": "SP; DROP"}, {"date_from": "yesterday"}, {"date_from": "2017-07-01&x=1"}])
def test_dashboard_link_drops_invalid_values(monkeypatch, raw):
    monkeypatch.setattr(dashboards, "UI_BASE_URL", "https://ui.example")
    assert dashboards.build_link(DASH, raw) == "https://ui.example/?page=executive_overview"


def test_external_provider_link_is_unchanged():
    d = Dashboard(provider="tableau_public", page="https://public.tableau.com/views/x/y", params={"state": "state"})
    assert dashboards.build_link(d, {"state": "SP"}) == "https://public.tableau.com/views/x/y"


# -- router ----------------------------------------------------------------------

CANDS = [
    router.Candidate("monthly_gmv", "analyze", "Monthly GMV", "gmv per month", "", 0.1),
    router.Candidate("executive_overview", "redirect", "Executive overview", "overview dashboard", "", 0.2),
    router.Candidate("gross_margin", "not_computable", "Gross margin", "no cost data", "", 0.3),
]


class FakeClient:
    def __init__(self, content=None, raises=False):
        self.content, self.raises = content, raises
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        if self.raises:
            raise RuntimeError("boom")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


def decide(payload, **kw):
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return router.decide("q", CANDS, client=FakeClient(content, **kw))


def test_router_accepts_valid_decision():
    d = decide({"route": "analyze", "skills": ["monthly_gmv"], "confidence": 0.9, "params": {"state": "SP"}})
    assert (d.route, d.skills, d.fallback, d.params) == ("analyze", ["monthly_gmv"], False, {"state": "SP"})


def test_router_discards_invented_skill_names_and_falls_back():
    d = decide({"route": "analyze", "skills": ["made_up_skill"]})
    assert d.fallback and d.skills == ["monthly_gmv"]  # nearest candidate


def test_router_rejects_route_skill_mismatch():
    # model says analyze but names a dashboard skill: not allowed to run SQL for it
    d = decide({"route": "analyze", "skills": ["executive_overview"]})
    assert d.fallback and d.route == "analyze" and d.skills == ["monthly_gmv"]


def test_router_redirect_and_not_computable():
    assert decide({"route": "redirect", "skills": ["executive_overview"]}).route == "redirect"
    d = decide({"route": "not_computable", "skills": ["gross_margin"]})
    assert d.route == "not_computable" and d.skills == ["gross_margin"]


def test_router_clarify_and_out_of_scope_need_no_skills():
    d = decide({"route": "clarify", "skills": [], "message": "Which metric?"})
    assert d.route == "clarify" and d.message == "Which metric?" and not d.fallback
    assert decide({"route": "out_of_scope", "message": "no"}).route == "out_of_scope"


@pytest.mark.parametrize("bad", ["not json", "[]", '{"route": 5, "skills": "x"}', '{"route": "teleport"}'])
def test_router_survives_garbage_model_output(bad):
    d = decide(bad)
    assert d.route == "analyze" and d.fallback and d.skills == ["monthly_gmv"]


def test_router_survives_model_exception():
    d = decide("", raises=True)
    assert d.fallback and d.skills == ["monthly_gmv"]


def test_router_caps_skills_at_three():
    cands = [router.Candidate(f"m{i}", "analyze", "t", "d", "", 0.1) for i in range(5)]
    c = FakeClient(json.dumps({"route": "analyze", "skills": [c.name for c in cands]}))
    assert len(router.decide("q", cands, client=c).skills) == 3


# -- /ask routes -----------------------------------------------------------------

client = TestClient(api.app)


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(api, "API_KEY", KEY)
    monkeypatch.setattr(api, "USE_SKILLS", True)
    monkeypatch.setattr(api, "log_query_event", lambda **kw: None)


def _route(monkeypatch, decision):
    monkeypatch.setattr(api.agent_core, "step_route", lambda q: decision)


def test_ask_redirect_returns_link_and_no_sql(monkeypatch):
    called = []
    monkeypatch.setattr(api.agent_core, "step_generate", lambda *a, **k: called.append(1))
    monkeypatch.setattr(dashboards, "UI_BASE_URL", "https://ui.example")
    _route(monkeypatch, router.RouteDecision(route="redirect", skills=["executive_overview"], params={"date_from": "2017-01"}))
    r = client.post("/ask", json={"question": "show me the overview"}, headers=H).json()
    assert r["route"] == "redirect" and r["sql"] == ""
    assert r["dashboard_url"] == "https://ui.example/?page=executive_overview&date_from=2017-01"
    assert not called, "no SQL generation for a redirect"


def test_ask_not_computable_explains_the_gap(monkeypatch):
    monkeypatch.setattr(api.agent_core, "step_generate", lambda *a, **k: pytest.fail("must not generate SQL"))
    _route(monkeypatch, router.RouteDecision(route="not_computable", skills=["gross_margin"]))
    r = client.post("/ask", json={"question": "what is our margin?"}, headers=H).json()
    assert r["route"] == "not_computable" and r["sql"] == "" and "cost" in r["message"].lower()


def test_ask_clarify_and_out_of_scope(monkeypatch):
    _route(monkeypatch, router.RouteDecision(route="clarify", message="Which metric do you mean?"))
    r = client.post("/ask", json={"question": "numbers"}, headers=H).json()
    assert (r["route"], r["message"]) == ("clarify", "Which metric do you mean?")
    _route(monkeypatch, router.RouteDecision(route="out_of_scope"))
    r = client.post("/ask", json={"question": "weather?"}, headers=H).json()
    assert r["route"] == "out_of_scope" and "Olist" in r["message"]


def test_ask_analyze_uses_selected_skill_context(monkeypatch):
    seen = {}
    _route(monkeypatch, router.RouteDecision(route="analyze", skills=["monthly_gmv"]))
    monkeypatch.setattr(api.agent_core, "step_rag", lambda q: pytest.fail("old retrieval must not run"))

    def gen(q, ctx, hist):
        seen["ctx"] = ctx
        return "SELECT 1", "why"
    monkeypatch.setattr(api.agent_core, "step_generate", gen)
    r = client.post("/ask", json={"question": "gmv per month"}, headers=H).json()
    assert r["route"] == "analyze" and r["skills"] == ["monthly_gmv"] and r["sql"] == "SELECT 1"
    assert "monthly_gmv" in seen["ctx"][0] and "SELECT" in seen["ctx"][0]


def test_ask_flag_off_keeps_old_path(monkeypatch):
    monkeypatch.setattr(api, "USE_SKILLS", False)
    monkeypatch.setattr(api.agent_core, "step_route", lambda q: pytest.fail("router must not run"))
    monkeypatch.setattr(api.agent_core, "step_rag", lambda q: ["chunk"])
    monkeypatch.setattr(api.agent_core, "step_generate", lambda q, c, h: ("SELECT 1", "x"))
    r = client.post("/ask", json={"question": "q"}, headers=H).json()
    assert r["route"] == "analyze" and r["skills"] == []


# -- SQL gate (needs local Postgres) ---------------------------------------------

def test_every_skill_sql_passes_the_gate():
    psycopg = pytest.importorskip("psycopg")
    from skillgen.check import _connect, check_skill
    try:
        conn = _connect()
    except psycopg.OperationalError:
        pytest.skip("Postgres not available")
    with conn:
        failures = {s.name: f for s in load_skills() if (f := check_skill(conn, s))}
    assert not failures, failures


# -- unified eval dataset ---------------------------------------------------------

def _dataset():
    import pathlib
    p = pathlib.Path(__file__).resolve().parent.parent / "evals" / "test_dataset.json"
    return json.loads(p.read_text(encoding="utf-8"))


def test_eval_dataset_records_are_consistent():
    data = _dataset()
    ids = [r["id"] for r in data]
    assert len(ids) == len(set(ids)), "duplicate ids"
    valid = set(router.ROUTES)
    for r in data:
        routes = r["expected_route"] if isinstance(r["expected_route"], list) else [r["expected_route"]]
        assert routes and set(routes) <= valid, r["id"]
        assert isinstance(r["expected_skills"], list), r["id"]
        if r["eval_type"] == "routing_only":
            assert "expected_answer_english" not in r and "expected_sql" not in r, r["id"]
        elif r["eval_type"] == "answer_verification":
            assert r.get("expected_answer_english") or r.get("expected_answer"), r["id"]
        elif r["eval_type"] == "ambiguity_handling":
            assert r.get("eval_criteria"), r["id"]


def test_scorer_skips_routing_only_records():
    from evals import scorer  # noqa: F401  (import check)
    import evals.scorer as sc
    kept = sc.load_test_dataset(str(__import__("pathlib").Path(__file__).resolve().parent.parent / "evals" / "test_dataset.json"))
    assert kept and all(r["eval_type"] != "routing_only" for r in kept)
    assert len(kept) == 20
