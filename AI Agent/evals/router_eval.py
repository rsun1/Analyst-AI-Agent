"""
Router evaluation over the unified dataset (evals/test_dataset.json).

Every record has expected_route (a string, or a list of acceptable routes) and
expected_skills. Cheap: one embedding + one small model call per question, no SQL,
no judge. Run from "AI Agent/":  python evals/router_eval.py [out.json]

Skill labels may name skills that are planned but not written yet; those records are
reported as 'pending' for the skill check (the route is still checked) so the dataset
can lead the skills without the score being wrong.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from router import route_question  # noqa: E402
from skill_loader import load_skills  # noqa: E402


def main(out_path: str = "evals/router_results.json") -> int:
    path = os.path.join(os.path.dirname(__file__), "test_dataset.json")
    data = json.load(open(path, encoding="utf-8"))
    existing = {s.name for s in load_skills()}
    rows = []
    route_ok = skill_ok = skill_total = pending = 0
    for item in data:
        exp_route = item["expected_route"]
        ok_routes = exp_route if isinstance(exp_route, list) else [exp_route]
        d = route_question(item["question"])
        r_ok = d.route in ok_routes
        route_ok += r_ok
        wanted = item.get("expected_skills") or []
        s_ok = None
        if wanted:
            if all(w in existing for w in wanted):
                skill_total += 1
                s_ok = all(w in d.skills for w in wanted)
                skill_ok += s_ok
            else:
                pending += 1
                s_ok = "pending"
        rows.append({"id": item["id"], "question": item["question"], "expected_route": exp_route,
                     "expected_skills": wanted, "got_route": d.route, "got_skills": d.skills,
                     "fallback": d.fallback, "route_ok": r_ok, "skill_ok": s_ok})
        flag = "ok  " if r_ok and s_ok in (True, None, "pending") else "MISS"
        note = " (skill pending)" if s_ok == "pending" else ""
        print(f"{flag} [{item['id']}] {item['question'][:62]!r:66} -> {d.route} {d.skills}{note}")
    n = len(data)
    print(f"\nroute accuracy: {route_ok}/{n} ({100 * route_ok / n:.1f}%)")
    print(f"skill accuracy: {skill_ok}/{skill_total} ({100 * skill_ok / max(skill_total, 1):.1f}%)"
          f"   [{pending} records wait for planned skills]")
    json.dump(rows, open(out_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"saved {out_path}")
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(*sys.argv[1:2]))
