"""
The backend half of the `tool_error` kind manifest (`pypsa-gui/tool-error-kinds.json`).

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
import json
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = BACKEND.parent / "tool-error-kinds.json"

# The two kinds `_run_one_tool`'s except block invents when a tool raises
# something that carries no structured `error_kind` of its own. They appear in
# no `HTTPException` and at no `yield` site, so nothing below can derive them.
_FORWARDER_FALLBACKS = frozenset({"tool_error", "validation_error"})

_SKIP_DIRS = frozenset({"tests", "__pycache__", "smoke", ".pixi", "alembic"})


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


def _http_exception_kinds(tree: ast.AST) -> set[str]:
    """
    `error_kind` literals inside `HTTPException(detail={...})`, ANYWHERE.

    Deliberately not scoped to `chat_tools`. Tool dispatch forwards any
    exception carrying `detail["error_kind"]`, and tool handlers call into
    `upload_service`, `routers/projects` and others that raise their own — so
    scoping to the tool module would miss `descendants_exist`,
    `upload_quota_exceeded` and the rest, which is precisely the class of miss
    this guard exists to prevent.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name != "HTTPException":
            continue
        for keyword in node.keywords:
            if keyword.arg != "detail" or not isinstance(keyword.value, ast.Dict):
                continue
            for key, value in zip(keyword.value.keys, keyword.value.values):
                if (isinstance(key, ast.Constant) and key.value == "error_kind"
                        and isinstance(value, ast.Constant)):
                    found.add(value.value)
    return found


def derive_tool_error_kinds() -> set[str]:
    """Every `error_kind` that can reach the client on a `tool_error` frame."""
    kinds: set[str] = set(_FORWARDER_FALLBACKS)
    for path in _source_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        kinds |= _yielded_tool_error_kinds(tree)
        kinds |= _http_exception_kinds(tree)
    return kinds


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_the_manifest_still_describes_the_backend():
    declared = set(_manifest()["kinds"])
    derived = derive_tool_error_kinds()

    missing = sorted(derived - declared)
    stale = sorted(declared - derived)
    assert not missing, (
        "these error_kinds can reach a tool_error frame and the manifest does "
        f"not classify them: {missing}. Add each to "
        f"{MANIFEST.name} with a `surface` ('banner' or 'inline') and a `why`. "
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
    source = (BACKEND / "services" / "chat_service.py").read_text(encoding="utf-8")
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
    assert "not_authorized" in derived          # HTTPException in chat_tools
    assert "upload_quota_exceeded" in derived   # HTTPException in a SIBLING
    assert "validation_error" in derived        # forwarder fallback
    assert len(derived) > 40, f"suspiciously few kinds derived: {len(derived)}"
