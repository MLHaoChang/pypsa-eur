"""
Every route and chat tool that names a project must run the per-project ACL.

OPEN-ITEMS item 6, the ACL half. `tests/test_write_surface_lock_policy.py`
covers the edit lock ("may this caller WRITE this project while someone else
holds it"). This file covers the question before that one: "may this caller
SEE this project at all".

The primitive is `project_acl.ensure_project_access`, which
`project_registry.resolve_project` calls after the org lookup. It is NOT
`find_project`. That function is scoped to the org only, and its docstring
says it resolves "WITHOUT the access check". Projects carry member lists
(`PUT /api/projects/{name}/members`), so a route that only reached
`find_project` would show a project to every member of the org, listed or
not. That is the regression this file exists to catch.

On 2026-10-06 the answer was clean: all 75 project-naming routes reach the
check, and so does every project-taking tool that names a project. No gap
was found, so this file guards against the NEXT route rather than fixing
one. That is still the point of item 6: a mechanism that denies by omission
has to fail loudly when something is omitted.

Two things this file does that a naive version would not:

  * `{name}` is overloaded. It means a project under `/api/projects/`, a
    component under `/api/network/`, and a library item under `/api/library/`.
    Keying on the parameter name alone would mis-flag 31 routes. Keying on a
    fixed family list would re-create the omission problem: a new router with
    `{name}` would be unchecked by default. So every family that uses `{name}`
    must DECLARE what it means here, and an undeclared family fails.
  * The call walker follows `module.func(...)` and the handler passed to
    `_route(handler, ...)`. Without the first, every `ProjectAccessDep` route
    reads as unprotected, because `require_project_access` reaches the check
    via `project_registry.resolve_project`. Without the second, chat tools
    that delegate to a handler read as unprotected.

The limit, stated rather than hidden: this proves the ACL check RUNS on the
route, not that it runs on the very project named in the path. Binding an
argument to a call site is beyond a static check. The behavioural tenancy
tests (`test_projects_tenancy.py` and others) hold that part. Out of scope:
the routers that act on the session's ACTIVE project (`/api/network/*` and
the rest) name no project, and their ACL is established once, at `activate`.
That route is in the checked set below.
"""
from __future__ import annotations

import ast
import inspect
import re
from functools import lru_cache

import pytest

import main
from tests.test_write_surface_lock_policy import (
    _OUR_MODULES,
    _function_ast,
    _local_imports,
    _resolve,
)

_ACL_PRIMITIVE = "ensure_project_access"

# Path parameters that always mean a project, wherever they appear.
UNAMBIGUOUS_PROJECT_PARAMS = frozenset({"project_id", "base"})

# What `{name}` means in each API family. A family that uses `{name}` and is
# not declared here fails `test_every_family_declares_what_name_means`.
NAME_MEANS: dict[str, str] = {
    "/api/projects/": "project",
    "/api/gridspine/": "project",  # a planning_dynamics study is a project
    "/api/campus-electrical/": "project",  # #79: the hub project the campus study runs on
    "/api/network/": "component on the active project",
    "/api/results/": "component on the active project",
    "/api/library/": "org library item",
}

# Project-naming routes that legitimately do NOT run the per-project ACL. Empty
# on purpose: on 2026-10-06 every one of them did. An entry here is a
# (METHOD, path) -> reason decision, the same shape as the lock policy table.
ACL_POLICY: dict[tuple[str, str], str] = {}

# Chat-tool arguments that always mean a project.
UNAMBIGUOUS_PROJECT_ARGS = frozenset({"project_id", "base", "project_name"})

# Tools whose `name` argument is NOT a project, so they owe no ACL check on it.
# Grouped by what `name` actually is. A new tool with a `name` argument that
# does not reach the ACL must be listed here, or it fails.
_COMPONENT = "a component on the ACTIVE project, whose ACL was established at activate"
TOOL_NAME_IS_NOT_A_PROJECT: dict[str, str] = {
    **{t: _COMPONENT for t in (
        "attach_tariff", "cascade_delete_bus", "create_carrier",
        "create_component", "delete_component", "delete_timeseries",
        "delete_vintage_bounds", "explain_investment", "export_asset_results",
        "generate_exemplary_timeseries", "get_asset_results", "get_component",
        "get_timeseries", "set_vintage_bounds", "ui_open_asset_detail",
        "ui_select_component", "update_component", "upload_timeseries",
    )},
    "get_library_item": "an org Library item",
    "import_urdb_tariff": "the Library item the import creates",
    "update_meta": "the display name of the ACTIVE network (`NetworkMeta`)",
    "gridspine_create_study": "the name of the NEW study it creates; nothing to resolve",
    "list_scenarios": (
        "a parent filter over `list_projects()`, which only lists projects "
        "the caller may see; it resolves nothing itself"
    ),
}


# ── reach ───────────────────────────────────────────────────────────────────

def _reaches_acl(fn, depth: int = 0, seen: set | None = None) -> list[str] | None:
    """
    The call chain from `fn` to `ensure_project_access`, or None.

    Follows, into `routers.*`/`services.*` only and at most seven levels deep:
    bare-name calls (module globals and function-local imports), `module.func`
    calls, and the handler passed as the first argument of `_route(...)`.
    """
    seen = set() if seen is None else seen
    fn = inspect.unwrap(fn)
    key = (getattr(fn, "__module__", ""), getattr(fn, "__qualname__", ""))
    if depth > 7 or key in seen:
        return None
    seen.add(key)
    try:
        node = _function_ast(fn)
    except (OSError, TypeError, SyntaxError):
        return None
    local = _local_imports(node)

    def follow(target):
        if inspect.isfunction(target) and target.__module__.startswith(_OUR_MODULES):
            return _reaches_acl(target, depth + 1, seen)
        return None

    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Attribute):
            if f.attr == _ACL_PRIMITIVE:
                return [fn.__name__, ast.unparse(f)]
            if isinstance(f.value, ast.Name):
                mod = _resolve(f.value.id, fn, local)
                if inspect.ismodule(mod) and mod.__name__.startswith(_OUR_MODULES):
                    chain = follow(getattr(mod, f.attr, None))
                    if chain:
                        return [fn.__name__, *chain]
            continue
        if not isinstance(f, ast.Name):
            continue
        if f.id == _ACL_PRIMITIVE:
            return [fn.__name__, f.id]
        if f.id == "_route" and n.args and isinstance(n.args[0], ast.Name):
            chain = follow(_resolve(n.args[0].id, fn, local))
            if chain:
                return [fn.__name__, "_route", *chain]
        chain = follow(_resolve(f.id, fn, local))
        if chain:
            return [fn.__name__, *chain]
    return None


def _dependency_calls(dependant, seen=None):
    seen = set() if seen is None else seen
    for d in dependant.dependencies:
        if d.call is not None and id(d.call) not in seen:
            seen.add(id(d.call))
            yield d.call
            yield from _dependency_calls(d, seen)


# ── enumeration ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def _routes() -> tuple:
    from fastapi.routing import APIRoute

    out = []
    for r in main.app.routes:
        if isinstance(r, APIRoute):
            out.append((r.path, frozenset(r.methods or ()), r.endpoint, r.dependant))
        elif hasattr(r, "effective_route_contexts"):
            for c in r.effective_route_contexts():
                out.append((c.path, frozenset(c.methods or ()), c.endpoint, c.dependant))
    return tuple(out)


def _family(path: str) -> str:
    parts = path.split("/")
    return "/".join(parts[:3]) + "/" if path.startswith("/api/") else path


def _path_params(path: str) -> set[str]:
    return set(re.findall(r"\{([^}:]+)", path))


def _names_a_project(path: str) -> bool:
    params = _path_params(path)
    if params & UNAMBIGUOUS_PROJECT_PARAMS:
        return True
    return "name" in params and NAME_MEANS.get(_family(path)) == "project"


def _route_acl_chain(endpoint, dependant):
    for call in (endpoint, *_dependency_calls(dependant)):
        chain = _reaches_acl(call)
        if chain:
            return chain
    return None


def _project_routes():
    for path, methods, endpoint, dependant in _routes():
        if _names_a_project(path):
            for m in sorted(methods - {"HEAD", "OPTIONS"}):
                yield (m, path), endpoint, dependant


# ── HTTP routes ─────────────────────────────────────────────────────────────

def test_the_route_enumeration_is_complete():
    """Guard on the guard: the lazy-router trap would let this pass on nothing."""
    spec = main.app.openapi()
    openapi = {(m.upper(), p) for p, ops in spec["paths"].items() for m in ops
               if m.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}}
    seen = {(m, path) for path, methods, _e, _d in _routes() for m in methods}
    missing = sorted(openapi - seen)
    assert not missing, f"routes in OpenAPI this file never sees: {missing}"


def test_every_family_declares_what_name_means():
    """
    The omission guard for the overloaded parameter. A new router that uses
    `{name}` fails here until someone says what it names. If it names a
    project, every route in it is then held to the ACL below.
    """
    undeclared = sorted({_family(p) for p, _m, _e, _d in _routes()
                         if "name" in _path_params(p) and _family(p) not in NAME_MEANS})
    assert not undeclared, (
        f"these API families use `{{name}}` without declaring what it means: "
        f"{undeclared}. Add them to NAME_MEANS. If it is a project, the ACL test "
        f"below then applies to every route in the family."
    )


def test_every_project_naming_route_runs_the_project_acl():
    """
    THE test. A route that names a project and reaches neither
    `ensure_project_access` nor an ACL_POLICY reason fails here, which is what
    a route resolving its project with `find_project` alone looks like.
    """
    missing = sorted(key for key, ep, dep in _project_routes()
                     if key not in ACL_POLICY and _route_acl_chain(ep, dep) is None)
    assert not missing, (
        "these routes name a project but never run the per-project ACL:\n  "
        + "\n  ".join(f"{m} {p}" for m, p in missing)
        + "\n\nResolve the project through `ProjectAccessDep`, `ProjectDep` or "
        "`project_registry.resolve_project`. `find_project` alone is scoped to the "
        "org only and skips the member check."
    )


def test_the_project_naming_set_is_the_size_it_should_be():
    """
    Pin the selector itself. If `_names_a_project` silently matched nothing,
    the test above would pass on an empty set.
    """
    n = sum(1 for _ in _project_routes())
    assert n >= 60, f"only {n} project-naming routes matched; the app has ~75"


def test_no_acl_policy_entry_is_stale_or_redundant():
    routes = {key: (ep, dep) for key, ep, dep in _project_routes()}
    gone = sorted(k for k in ACL_POLICY if k not in routes)
    assert not gone, f"ACL_POLICY names routes that do not name a project: {gone}"
    covered = sorted(k for k in ACL_POLICY if _route_acl_chain(*routes[k]))
    assert not covered, f"these run the ACL now; delete their entries: {covered}"


def test_the_verifier_sees_each_way_the_acl_is_reached():
    """Pin the reach paths the main test relies on, each with a real route."""
    by_key = {key: (ep, dep) for key, ep, dep in _project_routes()}
    via_dep = _route_acl_chain(*by_key[("GET", "/api/projects/{name}/worksheet")])
    assert via_dep and "resolve_project" in via_dep, f"ProjectAccessDep path: {via_dep}"
    via_handler = _route_acl_chain(*by_key[("GET", "/api/projects/{name}")])
    assert via_handler, "in-handler `project_registry.resolve_project` (module call)"
    via_projectdep = _route_acl_chain(*by_key[("GET", "/api/projects/{project_id}/network/meta")])
    assert via_projectdep, "ProjectDep / resolve_project_context"


# ── chat tools ──────────────────────────────────────────────────────────────

def _project_taking_tools():
    from services import chat_tools

    for name, fn in sorted(chat_tools.DISPATCHERS.items()):
        try:
            params = set(inspect.signature(inspect.unwrap(fn)).parameters)
        except (TypeError, ValueError):
            continue
        if params & UNAMBIGUOUS_PROJECT_ARGS or "name" in params:
            yield name, inspect.unwrap(fn), params


def test_every_project_taking_tool_runs_the_project_acl():
    """
    A tool that takes a project argument calls its handler in-process, so the
    route's `ProjectAccessDep` never runs. It has to reach the ACL itself, as
    `_authorized_project` does. A tool whose `name` is something else has to
    say so in TOOL_NAME_IS_NOT_A_PROJECT, so a new one cannot slip past.
    """
    missing = sorted(
        (name, sorted(params & (UNAMBIGUOUS_PROJECT_ARGS | {"name"})))
        for name, fn, params in _project_taking_tools()
        if name not in TOOL_NAME_IS_NOT_A_PROJECT and _reaches_acl(fn) is None
    )
    assert not missing, (
        f"these chat tools take a project-like argument but never run the "
        f"per-project ACL: {missing}. Resolve it through `_authorized_project` "
        f"(or `_route` to a ProjectAccessDep handler with it), or, if `name` is "
        f"not a project, add the tool to TOOL_NAME_IS_NOT_A_PROJECT with what it is."
    )


def test_an_unambiguous_project_argument_is_never_waved_through():
    """`project_id`/`base` always mean a project; no entry can excuse them."""
    excused = sorted(name for name, _fn, params in _project_taking_tools()
                     if name in TOOL_NAME_IS_NOT_A_PROJECT
                     and params & UNAMBIGUOUS_PROJECT_ARGS)
    assert not excused, f"these take an unambiguous project arg yet are excused: {excused}"


def test_no_tool_name_entry_is_stale_or_redundant():
    tools = {name: fn for name, fn, _p in _project_taking_tools()}
    from services import chat_tools

    gone = sorted(t for t in TOOL_NAME_IS_NOT_A_PROJECT if t not in chat_tools.DISPATCHERS)
    assert not gone, f"TOOL_NAME_IS_NOT_A_PROJECT names tools that no longer exist: {gone}"
    covered = sorted(t for t in TOOL_NAME_IS_NOT_A_PROJECT if t in tools and _reaches_acl(tools[t]))
    assert not covered, (
        f"these reach the ACL now, so `name` may be a project after all; "
        f"re-check and delete their entries: {covered}"
    )
