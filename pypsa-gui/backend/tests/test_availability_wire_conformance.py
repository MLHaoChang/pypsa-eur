"""
An endpoint that reuses a Compare block must forward its availability flag.

ADR-0001 says an unresolvable figure never ships as 0.0. Every `*Comparison`
block carries `available` to honour that, and CompareView branches on it. But
the flag only protects a consumer that RECEIVES it, and an endpoint that
hand-builds its response dict from a block can silently drop it. When that
happens the figures still arrive, still look like measurements, and nothing
anywhere fails.

That is not hypothetical. `get_economics_by_carrier` dropped it at two
separate sites and shipped that way through a whole branch — eight tasks,
five code reviews, a green 2540-test suite. It was found by reading the
function. This file exists because reading the function does not scale and
has now had to happen three times.

The check is deliberately structural rather than behavioural: it does not
need a solved network, a fixture, or an endpoint call, so it cannot be
defeated by a fixture that fails to reach the interesting branch — the way
the previous branch's vacuous happy-path test was. It reads the source.

Scope: functions in `routers/` that consume a `_compute_*_summary` helper
and return a dict literal. Returning the Pydantic model itself is always
fine — serialisation carries every field, which is why `compare.py`'s own
endpoints are not at risk and are not flagged.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

ROUTERS = pathlib.Path(__file__).resolve().parents[1] / "routers"

# The flag under test, plus the sibling that qualifies it (Task 7's lost-load
# `captured`, and curtailment's `partial`). Any ONE of these present means the
# response says something about resolution, which is what ADR-0001 requires.
AVAILABILITY_KEYS = {"available", "captured", "partial"}

# A response whose shape is an error is already unambiguous — nobody reads
# `{"error": ...}` as a measured zero.
ERROR_KEYS = {"error", "detail"}


def _consumes_a_summary_helper(fn: ast.AST) -> bool:
    """True when this function body mentions a `_compute_<x>_summary` name."""
    for node in ast.walk(fn):
        name = None
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, ast.alias):
            name = node.name
        if name and name.startswith("_compute_") and name.endswith("_summary"):
            return True
    return False


def _dict_returns(fn: ast.AST) -> list[ast.Dict]:
    """Every `return {...}` literal in this function, nested ones included."""
    out: list[ast.Dict] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            out.append(node.value)
    return out


def _literal_keys(d: ast.Dict) -> set[str]:
    return {
        k.value for k in d.keys
        if isinstance(k, ast.Constant) and isinstance(k.value, str)
    }


def _offenders() -> list[str]:
    problems: list[str] = []
    for path in sorted(ROUTERS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # The helpers themselves match their own name — they RETURN the
            # model, they do not consume it.
            if fn.name.startswith("_compute_") and fn.name.endswith("_summary"):
                continue
            if not _consumes_a_summary_helper(fn):
                continue
            for d in _dict_returns(fn):
                keys = _literal_keys(d)
                if keys & ERROR_KEYS:
                    continue
                if keys & AVAILABILITY_KEYS:
                    continue
                shape = "{}" if not d.keys else "{" + ", ".join(sorted(keys)) + "}"
                problems.append(
                    f"{path.name}:{d.lineno} in {fn.name}() returns {shape} — "
                    f"no availability key"
                )
    return problems


def test_endpoints_reusing_a_compare_block_forward_its_availability():
    """
    The regression this file was written for.

    A failure here means an endpoint hands a caller figures derived from a
    Comparison block without saying whether they resolved. The caller then
    cannot tell a real zero from an absence — ADR-0001's exact prohibition —
    and, because the numbers are all present and well-formed, nothing else
    in the suite will notice.

    Fix it by forwarding the flag, not by adding the endpoint to an
    exemption list. There is deliberately no exemption list.
    """
    problems = _offenders()
    assert not problems, (
        "endpoint(s) drop the availability flag when reusing a Compare "
        "block:\n  " + "\n  ".join(problems)
    )


def test_the_conformance_check_actually_detects_a_drop():
    """
    Proves the check above is not vacuous.

    A structural test that finds nothing looks identical whether the codebase
    is clean or the matcher is broken — which is how the previous branch
    shipped a happy-path test that asserted nothing. So this drives the same
    machinery over a synthetic module carrying the exact defect
    `get_economics_by_carrier` had, and requires that it be caught.
    """
    source = '''
def get_something():
    from routers.compare import _compute_economics_summary
    result = _compute_economics_summary(n, [], False, True)
    if not ready:
        return {}
    return {"by_carrier": {k: v.model_dump() for k, v in result.by_carrier.items()}}
'''
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))

    assert _consumes_a_summary_helper(fn), "matcher missed the helper import"

    returns = _dict_returns(fn)
    assert len(returns) == 2, "both return sites must be seen"
    for d in returns:
        keys = _literal_keys(d)
        assert not (keys & AVAILABILITY_KEYS), (
            "this fixture reproduces the DEFECT — neither return may carry "
            "an availability key, or the check is being proved against the "
            "wrong thing"
        )


@pytest.mark.parametrize("shape,expected_clean", [
    ('return {"available": False, "by_carrier": {}}', True),
    ('return {"error": "boom", "trace": []}', True),
    ('return {"by_carrier": {}}', False),
    ('return {}', False),
])
def test_conformance_matcher_classifies_each_return_shape(shape, expected_clean):
    """
    Pins the classifier per shape, so a future edit that loosens it — say,
    treating `{}` as acceptable — fails here rather than silently widening
    what the codebase is allowed to ship.
    """
    tree = ast.parse(f"def f():\n    _compute_x_summary()\n    {shape}\n")
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    d = _dict_returns(fn)[0]
    keys = _literal_keys(d)
    clean = bool(keys & AVAILABILITY_KEYS) or bool(keys & ERROR_KEYS)
    assert clean is expected_clean
