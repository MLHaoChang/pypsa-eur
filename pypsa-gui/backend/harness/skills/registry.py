"""
Skill registry: reusable procedures the in-app assistant loads on demand.

A skill is ``harness/skills/<name>/SKILL.md`` with ``name`` and
``description`` front matter — the Agent Skills layout, the same one the
developer agents' ``.claude/skills/`` use, so one file can be copied between
the two. The system prompt carries only the catalogue (name and description,
stable, cached); the body reaches the model through the ``use_skill`` tool
(issue 05).

Two roots, in rank order (issue 14, owner decision Q16, spec D12):

* ``package``: this package's folders. A malformed file raises ``SkillError``
  at load, so a test sees it first.
* ``user``: ``<app data>/skills/<name>/SKILL.md``, for one install's own
  procedures. Never a project folder or bundle: a project can arrive from
  someone else, and a skill is instructions the model obeys. A user skill
  that is malformed, or named like a package skill, is refused with a logged
  reason (``refusals()``), never shadowing the package one.

Front matter may set ``model_invocable`` and ``user_invocable`` (default
true). The prompt's catalogue and ``use_skill`` see model-invocable skills
only; ``GET /api/chat/skills`` lists user-invocable ones with their
provenance, which never reaches the prompt.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger("pypsa_gui.chat")

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
    source: str = "package"
    model_invocable: bool = True
    user_invocable: bool = True

    def catalogue_line(self) -> str:
        return f"{self.name} — {self.description}"


def _flag(meta: dict, key: str, path: Path) -> bool:
    value = meta.get(key, True)
    if not isinstance(value, bool):
        raise SkillError(f"{path}: {key} must be true or false")
    return value


def _parse(path: Path, source: str = "package") -> Skill:
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
    return Skill(name=name, description=" ".join(meta["description"].split()), body=body,
                 path=path, source=source,
                 model_invocable=_flag(meta, "model_invocable", path),
                 user_invocable=_flag(meta, "user_invocable", path))


def load_all(directory: Path | None = None) -> dict[str, Skill]:
    out: dict[str, Skill] = {}
    for path in sorted((directory or _DIR).glob("*/SKILL.md")):
        skill = _parse(path)
        out[skill.name] = skill
    return out


def _user_root() -> Path:
    from app_paths import app_data_dir
    return app_data_dir() / "skills"


def roots() -> list[tuple[str, Path]]:
    """Where skills come from, in rank order. There is no third."""
    return [("package", _DIR), ("user", _user_root())]


def _load_user(root: Path, taken: dict[str, Skill]) -> tuple[dict[str, Skill], list[dict]]:
    out: dict[str, Skill] = {}
    refused: list[dict] = []
    if not root.is_dir():
        return out, refused
    for path in sorted(root.glob("*/SKILL.md")):
        name = path.parent.name
        try:
            skill = _parse(path, source="user")
        except (SkillError, OSError, UnicodeDecodeError, ValueError) as exc:
            reason = "malformed SKILL.md" if isinstance(exc, (SkillError, ValueError)) else "unreadable"
            logger.warning("chat: user skill %r refused: %s (%s)", name, reason, exc)
            refused.append({"name": name, "reason": reason})
            continue
        if skill.name in taken:
            logger.warning("chat: user skill %r refused: a package skill has this name", name)
            refused.append({"name": name, "reason": "a package skill has this name"})
            continue
        out[skill.name] = skill
    return out, refused


_CACHE: dict[str, Skill] | None = None
_REFUSED: list[dict] = []


def registry() -> dict[str, Skill]:
    global _CACHE, _REFUSED
    if _CACHE is None:
        package = load_all()
        user, refused = _load_user(_user_root(), package)
        _CACHE, _REFUSED = {**package, **user}, refused
    return _CACHE


def reload() -> dict[str, Skill]:
    """Forget the cache and read both roots again (``POST /api/chat/skills/reload``)."""
    global _CACHE
    _CACHE = None
    return registry()


def refusals() -> list[dict]:
    """User skills the last load refused, as ``{name, reason}``."""
    registry()
    return [dict(r) for r in _REFUSED]


def get(name: str) -> Skill:
    return registry()[name]


def catalogue_block() -> str:
    """The lines the system prompt will carry (issue 05): names and
    descriptions of model-invocable skills only, so the prompt stays stable
    while bodies change. Provenance is never shown here (Q16)."""
    skills = [s for s in registry().values() if s.model_invocable]
    if not skills:
        return ""
    lines = "\n".join(f"- {s.catalogue_line()}" for s in skills)
    return (
        "Skills you can load with the use_skill tool (load one only when the "
        "user's request matches its description):\n" + lines
    )
