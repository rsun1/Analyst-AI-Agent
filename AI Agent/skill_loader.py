"""
Load and validate metric skills from skills/<domain>/<name>.md.

A skill is a Markdown file with YAML frontmatter (routing metadata) and a body
with fixed sections (Definition / SQL / Pitfalls / Interpretation). This module
has no database or network dependency, so it is cheap to run in tests and CI.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from config import BASE_DIR

SKILLS_DIR = BASE_DIR / "skills"

DOMAINS = (
    "sales", "orders", "fulfillment", "payments", "reviews", "customers",
    "sellers", "products", "leads", "dashboards", "unsupported",
)
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.S)
_FENCE_RE = re.compile(r"```sql\s*\n(.*?)```", re.S)


class Dashboard(BaseModel):
    provider: Literal["streamlit", "looker_studio", "tableau_public", "other"] = "streamlit"
    page: str = Field(min_length=1, description="Streamlit page id, or a full URL for external providers.")
    # Entities from the question that the link may carry, mapped to their type.
    params: dict[str, Literal["state", "date", "text"]] = Field(default_factory=dict)
    placeholder: bool = False


class SkillMeta(BaseModel):
    name: str
    domain: Literal[DOMAINS]  # type: ignore[valid-type]
    title: str = Field(min_length=3)
    route: Literal["analyze", "redirect", "not_computable"]
    description: str = Field(min_length=20, max_length=400)
    triggers: list[str] = Field(min_length=2)
    disambiguation: str = ""
    tables: list[str] = Field(default_factory=list)
    grain: str = ""
    dimensions: list[str] = Field(default_factory=list)
    dashboard: Dashboard | None = None
    # Questions to evaluate this skill with (expected values are computed from the SQL).
    eval_questions: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name_ok(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError("name must be snake_case, 3-64 chars")
        return v

    @model_validator(mode="after")
    def _route_rules(self) -> "SkillMeta":
        if self.route == "redirect" and self.dashboard is None:
            raise ValueError("redirect skills need a dashboard block")
        if self.route != "redirect" and self.dashboard is not None:
            raise ValueError("only redirect skills may have a dashboard block")
        if self.route == "analyze" and len(self.eval_questions) < 2:
            raise ValueError("analyze skills need at least 2 eval_questions")
        return self


class Skill(BaseModel):
    meta: SkillMeta
    body: str
    path: str
    sql_blocks: dict[str, str] = Field(default_factory=dict)  # label -> SQL

    @property
    def name(self) -> str:
        return self.meta.name

    def embedding_text(self) -> str:
        """What the router embeds: description + sample phrasings (not the SQL)."""
        m = self.meta
        return f"{m.title}. {m.description} Example questions: " + " | ".join(m.triggers)

    def context_text(self) -> str:
        """What the SQL generator sees when this skill is selected."""
        m = self.meta
        return f"### skill: {m.name} ({m.title})\n{self.body.strip()}"


def extract_sql_blocks(body: str) -> dict[str, str]:
    """
    Return {label: sql} from the '## SQL' section. A block's label is the nearest
    preceding '### <label>' heading inside that section (default 'main').
    """
    m = re.search(r"^## SQL\s*\n(.*?)(?=^## |\Z)", body, re.S | re.M)
    if not m:
        return {}
    section = m.group(1)
    blocks: dict[str, str] = {}
    label = "main"
    pos = 0
    for fence in _FENCE_RE.finditer(section):
        heading = None
        for h in re.finditer(r"^### (.+)$", section[pos:fence.start()], re.M):
            heading = h.group(1).strip()
        if heading:
            label = heading
        key, n = label, 2
        while key in blocks:
            key, n = f"{label}_{n}", n + 1
        blocks[key] = fence.group(1).strip()
        pos = fence.end()
    return blocks


def parse_skill(text: str, path: str = "<memory>") -> Skill:
    m = _FM_RE.match(text)
    if not m:
        raise ValueError(f"{path}: missing YAML frontmatter")
    try:
        raw = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"{path}: invalid YAML: {e}") from e
    try:
        meta = SkillMeta(**raw)
    except ValidationError as e:
        raise ValueError(f"{path}: {e}") from e
    body = m.group(2)
    blocks = extract_sql_blocks(body)
    if meta.route == "analyze" and not blocks:
        raise ValueError(f"{path}: analyze skill has no SQL block under '## SQL'")
    if meta.route != "analyze" and blocks:
        raise ValueError(f"{path}: only analyze skills may contain SQL")
    for section in ("Definition",) if meta.route != "analyze" else ("Definition", "SQL", "Pitfalls"):
        if not re.search(rf"^## {section}\b", body, re.M):
            raise ValueError(f"{path}: missing '## {section}' section")
    return Skill(meta=meta, body=body, path=path, sql_blocks=blocks)


def load_skills(skills_dir: Path | str | None = None) -> list[Skill]:
    """Load every skill under skills_dir; raise one error listing all problems."""
    root = Path(skills_dir) if skills_dir else SKILLS_DIR
    skills: list[Skill] = []
    errors: list[str] = []
    for p in sorted(root.glob("*/*.md")):
        try:
            s = parse_skill(p.read_text(encoding="utf-8"), str(p.relative_to(root)))
        except ValueError as e:
            errors.append(str(e))
            continue
        if p.stem != s.name:
            errors.append(f"{p.name}: file name must match skill name '{s.name}'")
        if p.parent.name != s.meta.domain:
            errors.append(f"{p.name}: lives in '{p.parent.name}/' but domain is '{s.meta.domain}'")
        skills.append(s)
    names = [s.name for s in skills]
    for dup in {n for n in names if names.count(n) > 1}:
        errors.append(f"duplicate skill name: {dup}")
    if errors:
        raise ValueError("Invalid skills:\n  - " + "\n  - ".join(errors))
    return skills


def skills_by_name(skills_dir: Path | str | None = None) -> dict[str, Skill]:
    return {s.name: s for s in load_skills(skills_dir)}
