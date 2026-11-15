"""
Router evaluation: does a question go to the right route and skill?

Cheap (one embedding + one small model call per question, no SQL, no judge).
Run from "AI Agent/":  python evals/router_eval.py [evals/router_results.json]

Reports route accuracy, skill accuracy on analyze/redirect/not_computable questions
(the expected skill must be among the chosen skills), and lists every miss.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from router import route_question  # noqa: E402


def main(out_path: str = "evals/router_results.json") -> int:
    path = os.path.join(os.path.dirname(__file__), "router_dataset.json")
    data = json.load(open(path, encoding="utf-8"))
    rows, route_ok, skill_ok, skill_total = [], 0, 0, 0
    for item in data:
        d = route_question(item["q"])
        r_ok = d.route == item["route"]
        s_ok = None
        if item["skills"]:
            skill_total += 1
            s_ok = all(s in d.skills for s in item["skills"])
            skill_ok += s_ok
        route_ok += r_ok
        rows.append({**item, "got_route": d.route, "got_skills": d.skills, "fallback": d.fallback,
                     "route_ok": r_ok, "skill_ok": s_ok})
        flag = "ok " if r_ok and s_ok is not False else "MISS"
        print(f"{flag} {item['q'][:70]!r:74} -> {d.route} {d.skills}")
    n = len(data)
    print(f"\nroute accuracy: {route_ok}/{n} ({100 * route_ok / n:.1f}%)")
    print(f"skill accuracy: {skill_ok}/{skill_total} ({100 * skill_ok / max(skill_total, 1):.1f}%)")
    json.dump(rows, open(out_path, "w", encoding="utf-8"), indent=2)
    print(f"saved {out_path}")
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(*sys.argv[1:2]))
