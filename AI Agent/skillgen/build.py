"""
Render skill specs (skillgen/specs_*.py) into skills/<domain>/<name>.md.

Spec files hold the reviewed content; this script only formats it, so a batch can be
regenerated deterministically. Then run the gate: python -m skillgen.check

Usage (from "AI Agent/"):  python -m skillgen.build batch1 [batch2 ...]
"""
from __future__ import annotations

import importlib
import sys

from config import BASE_DIR
from skillgen.migrate_existing import render

OUT = BASE_DIR / "skills"


def build(batch: str) -> list[str]:
    mod = importlib.import_module(f"skillgen.specs_{batch}")
    written = []
    for spec in mod.SPECS:
        meta = {k: spec[k] for k in ("domain", "title", "description", "triggers", "disambiguation",
                                      "tables", "grain", "dimensions", "eval", "pitfalls", "interp")}
        d = OUT / spec["domain"]
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{spec['name']}.md"
        path.write_text(render(spec["name"], meta, spec["sql"], spec["definition"]), encoding="utf-8", newline="\n")
        written.append(f"{spec['domain']}/{spec['name']}")
    return written


if __name__ == "__main__":
    for b in sys.argv[1:] or ["batch1"]:
        for w in build(b):
            print("wrote", w)
