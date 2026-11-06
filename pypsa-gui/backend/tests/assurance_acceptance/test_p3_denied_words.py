"""Frontend words for every denied outcome (issue 04, slice S4a).

A refusal before any effect carries `_outcome` = denied with its error kind
as the reason (harness/confirm.py `_deny`, harness/guards.py `denied`). The
chat tool line words that reason from TOOL_OUTCOME_DENIED_REASON_WORDS in
activityWords.ts; a kind with no words would show a bare "denied". These
tests read the frontend source (review-gk.md #1060: frontend copies of server
sets carry a parity test) and derive the backend kinds statically, as
tests/test_tool_error_kind_manifest.py does.
"""

from __future__ import annotations

import ast
import pathlib
import re

from harness import guards, outcomes

BACKEND = pathlib.Path(__file__).resolve().parents[2]
COMPONENTS = BACKEND.parent / "frontend/src/components"
# The modules that put a denied `_outcome` on a frame.
DENY_MODULES = {"harness/confirm.py", "harness/guards.py"}


def _ts(name: str) -> str:
    return (COMPONENTS / name).read_text()


def _confirm_deny_kinds() -> set[str]:
    """`{"error_kind": "..."}` literals and literal `_deny(..., "kind", ...)` args."""
    tree = ast.parse((BACKEND / "harness/confirm.py").read_text())
    kinds: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "error_kind"
                    and isinstance(value, ast.Constant)
                ):
                    kinds.add(value.value)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_deny"
            and len(node.args) > 2
            and isinstance(node.args[2], ast.Constant)
        ):
            kinds.add(node.args[2].value)
    return kinds


def _deny_kinds() -> set[str]:
    return _confirm_deny_kinds() | {
        guards.GUARD_FAILED,
        guards.ARGS_CHANGED,
        guards.NO_PROGRESS,
    } | guards.LOCK_KINDS


def test_the_frontend_outcomes_match_the_backend():
    block = re.search(
        r"export const TOOL_OUTCOMES = \[(.*?)\]", _ts("toolOutcome.ts"), re.S
    )
    assert set(re.findall(r"'(\w+)'", block.group(1))) == set(outcomes.OUTCOMES)


def test_only_the_known_modules_attach_a_denied_outcome():
    # A new emitter must join DENY_MODULES (and its kinds the derivation above).
    found = set()
    for path in sorted(BACKEND.glob("**/*.py")):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith(("tests/", ".pixi/")) or "__pycache__" in rel:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(
            r"from harness\.outcomes import[^\n]*\bdenied\b|outcomes\.denied\(", text
        ):
            found.add(rel)
    assert found == DENY_MODULES


def test_every_deny_kind_has_frontend_words():
    kinds = _deny_kinds()
    # The derivation itself still sees the gate's refusals.
    assert {"confirmation_denied", "confirmation_expired", "user_restriction"} <= kinds
    words = re.search(
        r"export const TOOL_OUTCOME_DENIED_REASON_WORDS[^=]*= \{(.*?)\n\}",
        _ts("activityWords.ts"),
        re.S,
    ).group(1)
    worded = set(re.findall(r"^\s*(\w+):", words, re.M))
    assert sorted(kinds - worded) == []


def _words(const: str) -> set[str]:
    block = re.search(
        rf"export const {const}: Record<string, string> = {{(.*?)\n}}",
        _ts("activityWords.ts"),
        re.S,
    )
    return set(re.findall(r"^\s*(\w+):", block.group(1), re.M))


def test_every_s4b_reason_has_frontend_words():
    """Issue 04 S4b: the raise path, the timeout and extra edits."""
    uncertain = set(outcomes.RAISE_REASONS) | {"not_as_requested"}
    assert uncertain <= _words("TOOL_OUTCOME_REASON_WORDS")
    assert "extra_edits" in _words("TOOL_OUTCOME_PARTIAL_REASON_WORDS")
