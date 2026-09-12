"""
Argument-SHAPE conformance for chat-tool delegations.

`test_chat_tools_imports.py` walks the same delegations and asserts the
imported NAME resolves. It says so itself, and the gap it names is this file's
subject:

    NOTE: this catches import-NAME drift, not argument-SHAPE drift. A handler
    that was renamed AND had its signature changed ... will pass this test once
    the name is fixed but still fail at call time with a TypeError.

That failure mode is not cosmetic. A chat tool whose delegation raises
`TypeError` loses the capability silently: the dispatcher reports a tool_error,
the model retries, then proceeds with worse data. That is exactly how the
time-series tools started reporting loads as flat — the fetch threw, so the
agent fell back to the static scalar `p_set`.

Until this file, ONE delegation was pinned against its handler's signature —
`sim_router.run`, by a hand-written `inspect.signature` assertion in
test_chat_e2e.py. The other ~103 had no argument-shape binding at all, while 76
of them point into routers/network.py, projects.py, simulation.py and
snapshots.py — the four files most likely to be under active edit.

METHOD: bind, don't compare. For each delegated call the AST shows, build the
same arity of sentinel arguments and hand them to `inspect.Signature.bind`.
One call catches all three drift shapes at once:

  * a positional the handler no longer accepts,
  * a keyword the handler never had (or had renamed),
  * a required parameter the handler GAINED and the call site does not pass.

The third is the one a rename-checker structurally cannot see, and the one a
router edit produces most often.

Values are never supplied — sentinels bind by arity and name only. This is a
PURE introspection test: no PyPSA, no FastAPI app, no disk, no network.

WHAT THIS CANNOT SEE, and it is the sharp edge: binding checks SHAPE, not
MEANING. `_h(upload)` against `def h(attribute=..., file=...)` binds cleanly —
straight into the WRONG parameter. That exact line existed here as the fallback
branch of both profile-upload wrappers when this file was written; it was
corrected to `_h(file=upload)` at the same time, but no assertion below would
have caught it. A delegation that passes the right number of arguments to the
wrong parameters is still a silent capability loss, and reading the call site
against the handler is the only thing that finds it.

FOUND ON FIRST RUN (2026-08-27): `upload_generator_profile` and
`upload_link_profile` both called `_h(upload, attribute=attribute)` against a
handler whose signature is `(attribute, file)` — the positional took
`attribute`, the keyword collided with it, and every invocation raised
`TypeError: got multiple values for argument 'attribute'`. Both tools had been
dead at call time; the name-resolution guard passed throughout, because the
NAME was never what drifted.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import pathlib

import pytest

CHAT_TOOLS = pathlib.Path(__file__).resolve().parents[1] / "services" / "chat_tools.py"

# Delegations whose call site is genuinely not bindable by arity alone. Each
# entry needs a REASON, not just a name — an unexplained waiver is how a real
# break gets parked here forever. Keyed (module, name) -> why.
# Empty today: every argument-passing delegation binds.
WAIVERS: dict[tuple[str, str], str] = {}


class _Sentinel:
    """Stands in for an argument value. Binding cares about shape, not type."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<arg>"


def _delegated_calls() -> list[tuple[int, str, str, int, tuple[str, ...]]]:
    """
    (lineno, module, handler_name, n_positional, kwarg_names) for every call in
    chat_tools.py to a name imported from routers.*/services.*/models.*.

    Aliases are resolved per enclosing function, because the file's idiom is a
    LOCAL `from routers.x import handler as _h` inside each wrapper — the same
    alias `_h` means a different handler in every one of them.
    """
    tree = ast.parse(CHAT_TOOLS.read_text(encoding="utf-8"))
    out: list[tuple[int, str, str, int, tuple[str, ...]]] = []
    for fn in [x for x in ast.walk(tree) if isinstance(x, ast.FunctionDef)]:
        aliases: dict[str, tuple[str, str]] = {}
        for node in ast.walk(fn):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(
                ("routers.", "services.", "models.")
            ):
                for a in node.names:
                    aliases[a.asname or a.name] = (node.module, a.name)
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in aliases
            ):
                mod, name = aliases[node.func.id]
                # A starred call (`_h(*args)`) has no statically-known arity.
                if any(isinstance(a, ast.Starred) for a in node.args) or any(
                    k.arg is None for k in node.keywords
                ):
                    continue
                kwargs = tuple(k.arg for k in node.keywords if k.arg is not None)
                out.append((node.lineno, mod, name, len(node.args), kwargs))
    return out


def _resolve(module: str, name: str):
    try:
        return getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError):  # covered by test_chat_tools_imports
        return None


ALL_CALLS = _delegated_calls()
WITH_ARGS = [c for c in ALL_CALLS if c[3] or c[4]]


def test_the_scan_actually_found_delegations():
    """
    A guard on the guard. If the file's idiom changes and the AST walk stops
    matching, every parametrised case below silently vanishes and this suite
    reports green while checking nothing.
    """
    assert len(ALL_CALLS) > 80, f"only {len(ALL_CALLS)} delegated calls found — walk is broken"
    assert len(WITH_ARGS) > 40, f"only {len(WITH_ARGS)} argument-passing calls found"


@pytest.mark.parametrize(
    "lineno,module,name,npos,kwargs",
    WITH_ARGS,
    ids=[f"{c[1].split('.')[-1]}.{c[2]}:L{c[0]}" for c in WITH_ARGS],
)
def test_delegated_call_binds_to_its_handler_signature(lineno, module, name, npos, kwargs):
    if (module, name) in WAIVERS:
        pytest.skip(f"waived: {WAIVERS[(module, name)]}")
    handler = _resolve(module, name)
    if handler is None:
        pytest.skip("name does not resolve — test_chat_tools_imports.py owns that failure")
    sig = inspect.signature(handler)
    try:
        sig.bind(*[_Sentinel()] * npos, **{k: _Sentinel() for k in kwargs})
    except TypeError as exc:
        pytest.fail(
            f"chat_tools.py:{lineno} calls {module}.{name}"
            f"({npos} positional, kwargs={list(kwargs)}) but the handler's "
            f"signature is {sig} — {exc}. The tool raises TypeError at call "
            f"time and the capability silently disappears."
        )


def test_waivers_reference_real_delegations():
    """A waiver for a delegation that no longer exists is stale — delete it."""
    present = {(m, n) for _, m, n, _, _ in ALL_CALLS}
    for key in WAIVERS:
        assert key in present, f"stale waiver {key}: no such delegation in chat_tools.py"
