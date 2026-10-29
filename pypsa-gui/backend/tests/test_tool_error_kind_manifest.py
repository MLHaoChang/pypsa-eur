"""
The backend half of the `tool_error` kind manifest (`pypsa-gui/tool-error-kinds.json`
plus the fragments in `pypsa-gui/tool-error-kinds.d/`).

THE HOLE THIS FILLS. `ChatPanel.profile.test.tsx` proves every kind in
`TOOL_ERROR_BANNER_KINDS` has `KIND_COPY` — it catches a routed kind losing
its copy. Nothing could catch the other direction: a kind the backend emits
that SHOULD route and does not. That direction is the one that actually bit
us, and its symptom is silent — the copy exists and is simply unreachable, so
the user gets a truncated gray tool line. `inactive_acting_user` was corrected
that way; so were `not_authorized` and `unknown_profile_id`.

Neither side can close it alone. The backend knows what it can emit and
nothing about copy; the frontend knows its copy and cannot enumerate the
backend. So this file asserts the manifest still describes the backend, and
the frontend asserts the UI still honours the manifest.

WHAT FAILING HERE MEANS. Somebody added (or removed) an `error_kind`. That is
not a bug — it is a decision nobody has made yet. Add it to the manifest with
a `surface` and a `why`, and the frontend test will then tell you whether it
also needs copy and routing.

WHY DERIVED STATICALLY RATHER THAN TRIGGERED. Triggering all 51 kinds means
building 51 failure states across uploads, projects, solving and vision — a
suite that would be mostly fixtures, and one whose gaps would be invisible in
exactly the way this guard exists to prevent. The static derivation cannot
miss an emitter that exists in the source, which is the property that matters
here. It over-approximates instead (see below), and over-approximating costs
a line of JSON.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from smoke.json_fragments import tool_error_kinds

BACKEND = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = BACKEND.parent / "tool-error-kinds.json"

# The two kinds `_run_one_tool`'s except block invents when a tool raises
# something that carries no structured `error_kind` of its own. They appear in
# no `HTTPException` and at no `yield` site, so nothing below can derive them.
_FORWARDER_FALLBACKS = frozenset({"tool_error", "validation_error"})

_SKIP_DIRS = frozenset({"tests", "__pycache__", "smoke", ".pixi", "alembic"})

_REFUSE_HELPER_MODULES: tuple[str, ...] = (
    "harness/edit_card.py",
    "harness/sensitivity_comparison.py",
    "harness/sweep_card.py",
    "services/assistant_tasks.py",
    "services/experiments.py",
    "services/run_evidence.py",
    "services/run_envelope.py",
)
# Modules that import `harness.edit_card._refuse` instead of defining it: scanned the same way.
_REFUSE_IMPORTING_MODULES: tuple[str, ...] = (
    "harness/edit_card_campus.py",
    "harness/edit_card_library.py",
    "harness/edit_card_records.py",
    "harness/edit_card_reports.py",
    "harness/edit_card_site.py",
)
_NON_LITERAL_REFUSE_CALLS: list[tuple[int, str]] = []


def _source_files() -> list[pathlib.Path]:
    return [
        p for p in sorted(BACKEND.rglob("*.py"))
        if not any(part in _SKIP_DIRS for part in p.relative_to(BACKEND).parts)
    ]


def _yielded_tool_error_kinds(tree: ast.AST) -> set[str]:
    """`error_kind` literals at `yield "tool_error", {...}` sites."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Yield):
            continue
        if not isinstance(node.value, ast.Tuple) or len(node.value.elts) != 2:
            continue
        tag, payload = node.value.elts
        if not (isinstance(tag, ast.Constant) and tag.value == "tool_error"):
            continue
        if not isinstance(payload, ast.Dict):
            continue
        for key, value in zip(payload.keys, payload.values):
            if (isinstance(key, ast.Constant) and key.value == "error_kind"
                    and isinstance(value, ast.Constant)):
                found.add(value.value)
    return found


def _error_kind_dict_kinds(tree: ast.AST) -> set[str]:
    """
    Every `"error_kind": "<literal>"` in ANY dict literal, ANYWHERE.

    NOT scoped to `HTTPException(detail={...})`, and that is the whole point.
    The first cut of this matched only dicts written inline at the raise site,
    and it MISSED `study_in_flight` — `routers/projects.py` builds that detail
    in `project_context.study_swap_refusal()` and raises the variable. The
    guard therefore under-approximated, which is the one direction it exists
    to prevent; the miss was caught by checking a kind the frontend already
    knew about against what this derived.

    Matching every dict literal over-approximates instead: it picks up detail
    dicts that never reach a tool, and those are classified `inline` with that
    as their reason. A spare line of JSON is the right price for not missing
    an emitter.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (isinstance(key, ast.Constant) and key.value == "error_kind"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)):
                found.add(value.value)
    return found


def _refuse_helper_kinds(tree: ast.AST) -> set[str]:
    """Literal bare-helper calls only; dynamic kinds must be reviewed explicitly."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_refuse"):
            continue
        arg = node.args[0] if node.args else None
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            found.add(arg.value)
        else:
            _NON_LITERAL_REFUSE_CALLS.append((node.lineno, ast.unparse(node)))
    return found


def _workflow_refuse_kinds(tree: ast.AST) -> set[str]:
    """`workflow._refuse("kind", ...)`: the shared helper in workflow_tools,
    called by attribute from services/assistant_tools.py (the preview/apply
    tools), so the bare-helper walk above never sees it."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_refuse" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "workflow" and node.args
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            found.add(node.args[0].value)
    return found


def derive_tool_error_kinds() -> set[str]:
    """Every `error_kind` that can reach the client on a `tool_error` frame."""
    kinds: set[str] = set(_FORWARDER_FALLBACKS)
    _NON_LITERAL_REFUSE_CALLS.clear()
    for path in _source_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        kinds |= _yielded_tool_error_kinds(tree)
        kinds |= _error_kind_dict_kinds(tree)
        kinds |= _workflow_refuse_kinds(tree)
        if path.relative_to(BACKEND).as_posix() in (*_REFUSE_HELPER_MODULES, *_REFUSE_IMPORTING_MODULES):
            kinds |= _refuse_helper_kinds(tree)
    return kinds


def _manifest() -> dict:
    # The base file plus tool-error-kinds.d/ fragments (RETRO-COST-1).
    return {"kinds": tool_error_kinds()}


def test_the_manifest_still_describes_the_backend():
    declared = set(_manifest()["kinds"])
    derived = derive_tool_error_kinds()

    missing = sorted(derived - declared)
    stale = sorted(declared - derived)
    assert not missing, (
        "these error_kinds can reach a tool_error frame and the manifest does "
        f"not classify them: {missing}. Add each to "
        f"a new fragment in {MANIFEST.with_suffix('.d').name}/ (named after your branch) "
        "with a `surface` ('banner' or 'inline') and a `why`. "
        "Choosing 'banner' means the frontend must also give it KIND_COPY and "
        "TOOL_ERROR_BANNER_KINDS membership — its own test will say so."
    )
    assert not stale, (
        "the manifest classifies error_kinds the backend can no longer emit: "
        f"{stale}. Delete them, or the frontend keeps routing for a kind that "
        "never arrives."
    )


def test_every_entry_states_a_surface_and_a_reason():
    """
    A `why` is not decoration. The one entry most likely to be 'corrected' by
    a future reader — `project_switched_mid_turn`, which is inline ON PURPOSE
    because the same guard also emits it as an `error` frame — is only safe
    because the reason is written down next to it.
    """
    for kind, entry in _manifest()["kinds"].items():
        assert entry.get("surface") in {"banner", "inline"}, (
            f"{kind}: surface must be 'banner' or 'inline', got "
            f"{entry.get('surface')!r}"
        )
        why = entry.get("why", "")
        assert len(why) > 30, f"{kind}: `why` must say something ({why!r})"


def test_the_forwarder_fallbacks_are_still_what_the_code_invents():
    """
    `_FORWARDER_FALLBACKS` is hardcoded because nothing can derive it — the
    two names are assigned to a local, not emitted at a `yield` site. If the
    except block starts inventing a third, this catches it rather than letting
    a kind reach users that no test has ever seen.
    """
    source = (BACKEND / "harness" / "loop.py").read_text(encoding="utf-8")
    block = source[source.index("# noqa: BLE001 — surface as tool_error"):]
    block = block[:block.index("yield \"tool_error\"")]
    assigned = {
        value for value in ("tool_error", "validation_error")
        if f'error_kind = "{value}"' in block
    }
    assert assigned == set(_FORWARDER_FALLBACKS), (
        f"the tool-dispatch except block now invents {sorted(assigned)}; "
        f"_FORWARDER_FALLBACKS says {sorted(_FORWARDER_FALLBACKS)}"
    )


def test_the_derivation_finds_the_kinds_it_is_supposed_to_find():
    """
    Guard the guard. A derivation that silently found nothing would make
    `test_the_manifest_still_describes_the_backend` pass forever — the failure
    mode that makes a green suite meaningless, which is what this whole
    manifest is about.
    """
    derived = derive_tool_error_kinds()
    # One from each extraction path, so a broken extractor cannot hide.
    assert "tool_not_offered" in derived        # yielded literal
    assert "not_authorized" in derived          # dict literal in chat_tools
    assert "upload_quota_exceeded" in derived   # dict literal in a SIBLING
    # Built in a helper and raised as a variable — the miss that made
    # the first version of this derivation under-approximate.
    assert "study_in_flight" in derived
    assert "validation_error" in derived        # forwarder fallback
    assert "task_paused_at_card" in derived      # assistant plan helper
    assert "experiment_not_found" in derived     # experiment helper
    assert "evidence_unavailable" in derived     # run evidence helper
    assert "change_selection_empty" in derived   # workflow._refuse in assistant_tools
    assert len(derived) > 40, f"suspiciously few kinds derived: {len(derived)}"


@pytest.mark.parametrize("module", _REFUSE_HELPER_MODULES)
def test_each_refuse_helper_module_still_raises_its_kind(module):
    tree = ast.parse((BACKEND / module).read_text(encoding="utf-8"))
    helper = next((n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "_refuse"), None)
    assert helper is not None, f"{module}: _refuse helper removed or renamed"
    assert helper.args.args and helper.args.args[0].arg == "kind", module
    assert any(
        isinstance(node, ast.Dict) and any(
            isinstance(key, ast.Constant) and key.value == "error_kind"
            and isinstance(value, ast.Name) and value.id == "kind"
            for key, value in zip(node.keys, node.values)
        ) for node in ast.walk(helper)
    ), f"{module}: _refuse no longer forwards kind to error_kind"


def test_refuse_helper_calls_use_literal_kinds():
    failures = []
    for module in (*_REFUSE_HELPER_MODULES, *_REFUSE_IMPORTING_MODULES):
        _NON_LITERAL_REFUSE_CALLS.clear()
        _refuse_helper_kinds(ast.parse((BACKEND / module).read_text(encoding="utf-8")))
        failures.extend((module, line, call) for line, call in _NON_LITERAL_REFUSE_CALLS)
    assert not failures, f"Unclassified dynamic _refuse calls: {failures}"


def test_refuse_helper_extraction_reports_dynamic_calls_and_ignores_attributes():
    _NON_LITERAL_REFUSE_CALLS.clear()
    tree = ast.parse('_refuse("literal", "reason"); _refuse(kind, "reason"); other._refuse("outside", "reason")')
    assert _refuse_helper_kinds(tree) == {"literal"}
    assert len(_NON_LITERAL_REFUSE_CALLS) == 1
    assert '_refuse(kind,' in _NON_LITERAL_REFUSE_CALLS[0][1]
    _NON_LITERAL_REFUSE_CALLS.clear()
