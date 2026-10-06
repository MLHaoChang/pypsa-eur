"""
Workflow registry: the start menu and the step-by-step flows the assistant leads.

A workflow is one Markdown file in this folder with YAML front matter and one
``## Step: <id>`` section per step. The front matter is the machine half
(menu label, where it is offered, the steps and their completion criteria);
the body is what the assistant reads for the current step. See
``harness/README.md`` for the authoring rules and ``.scratch/harness/spec.md``
D4–D5 for the runtime that consumes this (phase 2).

Files are read from this package directory only — never from a project
directory or an upload — because their bodies are instructions to the model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_DIR = Path(__file__).resolve().parent
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)
_STEP_HEADING = re.compile(r"^## Step: ([a-z0-9-]+)\s*$", re.M)

# Where a workflow may be offered. `unbound` = no project open; `expert` and
# `guided` are the two UI modes of a bound project (CONTEXT.md).
CONTEXTS: frozenset[str] = frozenset(["unbound", "expert", "guided"])

# A workflow's lifecycle. `planned` definitions are loaded and validated but
# never offered in the menu: their tools do not exist yet.
STATUSES: frozenset[str] = frozenset(["active", "planned"])


class WorkflowError(ValueError):
    """A workflow file is malformed. Raised at load, so a test — not a user —
    sees it."""


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    done_when: str
    body: str


@dataclass(frozen=True)
class Workflow:
    id: str
    title: str
    intent: str
    when: frozenset[str]
    order: int
    status: str
    opening_request: str
    steps: tuple[Step, ...]
    # Text before the first step heading: rules that hold for every step
    # (the Guided rules of `hub-design`, for example). Empty when absent.
    preamble: str = ""
    # Contexts in which the preamble is included (default: every context in
    # `when`). `hub-design` uses it to bind the Guided rules to Guided mode
    # while its steps are offered in Expert too (owner decisions Q11, Q13).
    preamble_when: frozenset[str] = field(default_factory=frozenset)
    project_kinds: frozenset[str] = field(default_factory=frozenset)
    path: Path | None = None

    def step(self, step_id: str) -> Step:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(step_id)

    def preamble_for(self, context: str) -> str:
        """The preamble when `context` is one it applies to, else ""."""
        return self.preamble if context in self.preamble_when else ""

    def offered_in(self, context: str, project_kind: str | None = None) -> bool:
        if self.status != "active" or context not in self.when:
            return False
        if self.project_kinds and (project_kind not in self.project_kinds):
            return False
        return True


def _parse(path: Path) -> Workflow:
    text = path.read_text(encoding="utf-8")
    m = _FRONT_MATTER.match(text)
    if not m:
        raise WorkflowError(f"{path.name}: missing front matter")
    meta = yaml.safe_load(m.group(1)) or {}
    body = m.group(2)
    required = ("id", "title", "intent", "when", "order", "opening_request", "steps")
    missing = [k for k in required if k not in meta]
    if missing:
        raise WorkflowError(f"{path.name}: front matter missing {missing}")
    if meta["id"] != path.stem:
        raise WorkflowError(f"{path.name}: id {meta['id']!r} must equal the file stem")
    when = frozenset(meta["when"])
    bad = when - CONTEXTS
    if bad:
        raise WorkflowError(f"{path.name}: unknown contexts {sorted(bad)}")
    status = meta.get("status", "active")
    if status not in STATUSES:
        raise WorkflowError(f"{path.name}: unknown status {status!r}")
    preamble_when = frozenset(meta.get("preamble_when") or when)
    if not preamble_when <= when:
        raise WorkflowError(f"{path.name}: preamble_when must be a subset of when")
    # Split the body into step sections, keyed by the heading's id.
    sections: dict[str, str] = {}
    parts = _STEP_HEADING.split(body)
    # parts = [preamble, id1, body1, id2, body2, ...]
    preamble = parts[0].strip()
    for i in range(1, len(parts), 2):
        sections[parts[i]] = parts[i + 1].strip()
    steps: list[Step] = []
    for s in meta["steps"]:
        for k in ("id", "title", "done_when"):
            if k not in s:
                raise WorkflowError(f"{path.name}: step {s!r} missing {k!r}")
        if s["id"] not in sections:
            raise WorkflowError(f"{path.name}: step {s['id']!r} has no '## Step: {s['id']}' section")
        steps.append(Step(id=s["id"], title=s["title"], done_when=s["done_when"], body=sections[s["id"]]))
    stray = set(sections) - {s.id for s in steps}
    if stray:
        raise WorkflowError(f"{path.name}: body sections not in front matter: {sorted(stray)}")
    if not steps:
        raise WorkflowError(f"{path.name}: a workflow needs at least one step")
    return Workflow(
        id=meta["id"], title=meta["title"], intent=meta["intent"], when=when,
        order=int(meta["order"]), status=status,
        opening_request=meta["opening_request"].strip(), steps=tuple(steps),
        preamble=preamble, preamble_when=preamble_when, project_kinds=frozenset(meta.get("project_kinds", []) or []), path=path,
    )


def load_all(directory: Path | None = None) -> dict[str, Workflow]:
    """Every workflow in the folder, keyed by id, validated. Raises
    `WorkflowError` on the first malformed file."""
    out: dict[str, Workflow] = {}
    for path in sorted((directory or _DIR).glob("*.md")):
        if path.name.upper() == "README.MD":
            continue
        wf = _parse(path)
        if wf.id in out:
            raise WorkflowError(f"duplicate workflow id {wf.id!r}")
        out[wf.id] = wf
    return out


_CACHE: dict[str, Workflow] | None = None


def registry() -> dict[str, Workflow]:
    global _CACHE
    if _CACHE is None:
        _CACHE = load_all()
    return _CACHE


def get(workflow_id: str) -> Workflow:
    return registry()[workflow_id]


def menu(context: str, project_kind: str | None = None) -> list[Workflow]:
    """The start menu for one context, in `order`. This is what
    `GET /api/chat/workflows` serves (issue 03) and what replaces the
    hardcoded chip arrays in the panel."""
    if context not in CONTEXTS:
        raise ValueError(f"unknown context {context!r}")
    return sorted(
        (wf for wf in registry().values() if wf.offered_in(context, project_kind)),
        key=lambda wf: (wf.order, wf.id),
    )
