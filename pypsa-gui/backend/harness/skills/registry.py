"""
Skill registry: reusable procedures the in-app assistant loads on demand.

A skill is ``harness/skills/<name>/SKILL.md`` with ``name`` and
``description`` front matter — the Agent Skills layout, the same one the
developer agents' ``.claude/skills/`` use, so one file can be copied between
the two. The system prompt carries only the catalogue (name and description,
stable, cached); the body reaches the model through the ``use_skill`` tool
(issue 05). Files are read from this package only (spec D12).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_DIR = Path(__file__).resolve().parent
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class SkillError(ValueError):
    """A SKILL.md is malformed. Raised at load, so a test — not a user — sees it."""


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    path: Path

    def catalogue_line(self) -> str:
        return f"{self.name} — {self.description}"


def _parse(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    m = _FRONT_MATTER.match(text)
    if not m:
        raise SkillError(f"{path}: missing front matter")
    meta = yaml.safe_load(m.group(1)) or {}
    for k in ("name", "description"):
        if not isinstance(meta.get(k), str) or not meta[k].strip():
            raise SkillError(f"{path}: front matter needs a non-empty {k!r}")
    name = meta["name"].strip()
    if not _NAME.match(name):
        raise SkillError(f"{path}: name {name!r} must be a lowercase slug")
    if name != path.parent.name:
        raise SkillError(f"{path}: name {name!r} must equal the folder name")
    body = m.group(2).strip()
    if not body:
        raise SkillError(f"{path}: empty body")
    return Skill(name=name, description=" ".join(meta["description"].split()), body=body, path=path)


def load_all(directory: Path | None = None) -> dict[str, Skill]:
    out: dict[str, Skill] = {}
    for path in sorted((directory or _DIR).glob("*/SKILL.md")):
        skill = _parse(path)
        out[skill.name] = skill
    return out


_CACHE: dict[str, Skill] | None = None


def registry() -> dict[str, Skill]:
    global _CACHE
    if _CACHE is None:
        _CACHE = load_all()
    return _CACHE


def get(name: str) -> Skill:
    return registry()[name]


def catalogue_block() -> str:
    """The lines the system prompt will carry (issue 05): names and
    descriptions only, so the prompt stays stable while bodies change."""
    skills = registry()
    if not skills:
        return ""
    lines = "\n".join(f"- {s.catalogue_line()}" for s in skills.values())
    return (
        "Skills you can load with the use_skill tool (load one only when the "
        "user's request matches its description):\n" + lines
    )
