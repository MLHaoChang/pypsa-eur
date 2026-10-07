"""
Every write path into the app makes an EXPLICIT edit-lock decision.

OPEN-ITEMS item 6. The edit lock ("may this caller write this project while
someone else is editing it") is enforced in three places, each a list someone
has to remember to extend:

  * `main._FOREIGN_LOCK_GATE_PREFIXES`: HTTP middleware, by path prefix.
  * in-handler `_check_project_lock` / `_enforce_project_lock`, for the
    `/api/projects/*` family, which the middleware deliberately does not gate.
  * `chat_tools._lock_gated_tool_names()`: the chat seam, derived from tool
    routes plus the hand list `_LOCK_GATE_SERVICE_CALL_MUTATORS`.

Each of those denies by omission. A new route or tool that no list mentions
is ungated, and nothing fails. That has now happened at least five times in
the handler family alone: `uploads` twice, `worksheet` and `stress_scenarios`
(`68e5f62`), `asset_health` (OPEN-ITEMS item 12, ungated for 20 days). On the
chat seam it happened again: seven tools write the upload store with no check
(item 13, found while writing this file), excluded by a docstring
justification that went stale when the HTTP upload routes gained their check.

This file turns "nobody remembered" into a failing test. Every write route
and every non-read chat tool must land in exactly one of:

  1. GATED: middleware prefix (HTTP), or the seam's own gated set (tools).
  2. HOLDER-CHECKED: the handler reaches a lock-check primitive, VERIFIED by
     following its calls (across modules, bounded) rather than taken on trust.
     A route here needs no table entry. That is the default this file wants
     to make cheap.
  3. A written POLICY entry: a category and a reason a reviewer can disagree
     with. "Not project data" is a decision, and here it is written down.
  4. KNOWN_GAPS: a real, recorded defect, pointing at its OPEN-ITEMS entry.
     A ratchet: when the gap is fixed this file fails until the entry is
     deleted, so the list can only shrink.

Why not scan handler source for the check? It was tried while writing this
file, and on this tree it reported 60 write routes as uncovered when the true
number of real gaps there was 0. It missed every check made through a helper,
including all nine report routes (`_check_lock`) and delete/rename
(`_delete_project_db` → `_enforce_project_lock`). A test that cries wolf gets
an allowlist bolted on and then means nothing. Calls are followed instead.

Why not `app.routes`? The routers are mounted lazily; `app.routes` yields 31
entries for 321 operations (the 2026-09-12 audit hit the same trap).
`effective_route_contexts()` reaches the handlers, and
`test_the_route_enumeration_is_complete` holds it to OpenAPI, so this file
cannot pass by silently enumerating nothing.

Out of scope here: the ACL ("may this caller SEE this project"). Item 6
recorded `ProjectAccessDep` adoption as "6 of 23 routers", but that figure
missed `ProjectDep` (`resolve_project_context`), a second dependency on the
same `project_registry.resolve_project`. The five large routers act on the
session's ACTIVE project, which no path-param dependency can resolve; their
ACL is established at activate. See the item-6 plan.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import re
import textwrap
from functools import lru_cache

import pytest

import main

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_LOCK_PRIMITIVES = frozenset({"_check_project_lock", "_enforce_project_lock"})
_LOCK_ATTR_PRIMITIVES = frozenset({"project_locks.get_lock"})
_OUR_MODULES = ("routers.", "services.")
# Any canonical uuid: `_foreign_lock_gate_exempt` matches REAL request paths,
# and its job-scoped patterns are anchored to the dashed-uuid shape.
_SAMPLE_UUID = "123e4567-e89b-12d3-a456-426614174000"

# ── categories ──────────────────────────────────────────────────────────────
LOCK_PROTOCOL = "lock-protocol"
CREATES_PROJECT = "creates-project"
STOPS_WORK = "stops-work"
NOT_PROJECT_DATA = "not-project-data"
CHAT_SESSION = "chat-session"
STUDY_NOT_LOCK_COORDINATED = "study-not-lock-coordinated"
IN_MEMORY_BOOKKEEPING = "in-memory-bookkeeping"
CATEGORIES = frozenset({
    LOCK_PROTOCOL, CREATES_PROJECT, STOPS_WORK, NOT_PROJECT_DATA,
    CHAT_SESSION, STUDY_NOT_LOCK_COORDINATED, IN_MEMORY_BOOKKEEPING,
})

_R_LOCK_PROTOCOL = (
    "the lock protocol itself. Acquire/heartbeat/release refuse a foreign "
    "holder by their own semantics, and activate MUST stay open to a "
    "non-holder: it is how a user views a project someone else is editing, or "
    "leaves one. Gating it would trap them (the middleware exempts the "
    "`/activate` suffix for the same reason)."
)
_R_CREATES = (
    "makes a NEW project (or registers an unregistered one); no lock row can "
    "exist for it yet, so there is no holder to respect. Verified for "
    "`create_scenario`: it reads the base dir and writes only the child dir."
)
_R_IDENTITY = "identity/session, not project data: no project is resolved at all."
_R_ADMIN = (
    "instance administration (super-admin, `_require_admin_actor`, refused "
    "in local mode); acts on orgs/users/mail, not on a project's stored data."
)
_R_LIBRARY = (
    "the org-level Library, not a project: series/items are referenced by "
    "projects via pinned (id, version, hash) in `library_refs.json`, never "
    "written into them by these routes."
)
_R_CHANGELOG = (
    "the org's audit trail, not a project; scoped per org (OPEN-ITEMS item 4, "
    "`tests/test_changelog_scoping.py`)."
)
_R_STOP = (
    "only STOPS work already running; it writes nothing to the project. Gating "
    "it would trap a running job under a lock taken afterwards: the same "
    "reason the middleware exempts every study and queue abort."
)
_R_LLM_SETTINGS = (
    "LLM provider settings: instance/user scope, `_require_super_admin`-gated "
    "where they are instance-wide. Not project data."
)
_R_LOCAL = "desktop-only (`reject_unless_local_mode` on the router); one identity, no holder."
_R_CHAT_OWNED = (
    "acts on a chat SESSION, authorized by its owner (`session_owner_allows`, "
    "OPEN-ITEMS item 3). Any tool a turn runs is lock-checked at the chat seam, "
    "per tool; that is the second half of this file."
)
_R_CHAT_HISTORY = (
    "chat history is per-project and DOES travel into snapshots "
    "(`routers/snapshots.py`) and moves on Save-As, but it is not edit-lock "
    "coordinated on any surface: a non-holder chatting on a project they may "
    "view appends to it by design. OPEN QUESTION, not settled here: whether "
    "import/clear by a non-holder should be refused (see the item-6 plan)."
)
_R_STUDY = (
    "a planning_dynamics study: the UI opens one WITHOUT activate and without "
    "acquiring the edit lock (`frontend/src/utils/projectActions.ts`), so no "
    "holder exists to respect. Concurrent edits during a run are refused by "
    "`_refuse_while_active`; every one of these refuses a non-study project "
    "(`require_planning`). Whether studies should join the lock is an open "
    "product question, not a gap in a control that exists."
)

# HTTP write routes the middleware does not gate and whose handler does not
# reach a lock check. Each one is a DECISION; the key is (METHOD, path template).
ROUTE_POLICY: dict[tuple[str, str], tuple[str, str]] = {
    # lock protocol
    ("POST", "/api/projects/{project_id}/activate"): (LOCK_PROTOCOL, _R_LOCK_PROTOCOL),
    ("POST", "/api/projects/{project_id}/lock"): (LOCK_PROTOCOL, _R_LOCK_PROTOCOL),
    ("POST", "/api/projects/{project_id}/lock/heartbeat"): (LOCK_PROTOCOL, _R_LOCK_PROTOCOL),
    ("DELETE", "/api/projects/{project_id}/lock"): (LOCK_PROTOCOL, _R_LOCK_PROTOCOL),
    # new projects
    ("POST", "/api/projects/import-folder"): (CREATES_PROJECT, _R_CREATES),
    ("POST", "/api/projects/unclaimed/{legacy_name}/import"): (CREATES_PROJECT, _R_CREATES),
    ("POST", "/api/projects/import_bundle"): (CREATES_PROJECT, _R_CREATES),
    ("POST", "/api/projects/from_template/{template_id}"): (CREATES_PROJECT, _R_CREATES),
    ("POST", "/api/projects/{base}/scenarios"): (CREATES_PROJECT, _R_CREATES),
    ("POST", "/api/admin/legacy-projects/{legacy_name}/claim"): (CREATES_PROJECT, _R_CREATES),
    ("POST", "/api/gridspine/projects"): (CREATES_PROJECT, _R_CREATES),
    # stops work
    ("POST", "/api/projects/{name}/reports/generate/abort"): (STOPS_WORK, _R_STOP),
    # not project data
    ("POST", "/api/auth/login"): (NOT_PROJECT_DATA, _R_IDENTITY),
    ("POST", "/api/auth/logout"): (NOT_PROJECT_DATA, _R_IDENTITY),
    ("POST", "/api/auth/forgot-password"): (NOT_PROJECT_DATA, _R_IDENTITY),
    ("POST", "/api/auth/set-password"): (NOT_PROJECT_DATA, _R_IDENTITY),
    ("POST", "/api/auth/reset-password"): (NOT_PROJECT_DATA, _R_IDENTITY),
    ("POST", "/api/admin/organizations"): (NOT_PROJECT_DATA, _R_ADMIN),
    ("POST", "/api/admin/users"): (NOT_PROJECT_DATA, _R_ADMIN),
    ("POST", "/api/admin/users/{user_id}/resend-set-password"): (NOT_PROJECT_DATA, _R_ADMIN),
    ("POST", "/api/admin/email/test"): (NOT_PROJECT_DATA, _R_ADMIN),
    ("POST", "/api/library/series"): (NOT_PROJECT_DATA, _R_LIBRARY),
    ("POST", "/api/library/series/upload"): (NOT_PROJECT_DATA, _R_LIBRARY),
    ("POST", "/api/library/meter_data"): (NOT_PROJECT_DATA, _R_LIBRARY),
    ("PUT", "/api/library/items/{kind}/{name}"): (NOT_PROJECT_DATA, _R_LIBRARY),
    ("POST", "/api/library/items/tariff/import_urdb"): (NOT_PROJECT_DATA, _R_LIBRARY),
    ("DELETE", "/api/changelog/"): (NOT_PROJECT_DATA, _R_CHANGELOG),
    ("PUT", "/api/chat/settings/api-key"): (NOT_PROJECT_DATA, _R_LLM_SETTINGS),
    ("DELETE", "/api/chat/settings/api-key"): (NOT_PROJECT_DATA, _R_LLM_SETTINGS),
    ("PUT", "/api/chat/settings/llm/profiles/{profile_id}"): (NOT_PROJECT_DATA, _R_LLM_SETTINGS),
    ("DELETE", "/api/chat/settings/llm/profiles/{profile_id}"): (NOT_PROJECT_DATA, _R_LLM_SETTINGS),
    ("PUT", "/api/chat/settings/llm/profiles/{profile_id}/key"): (NOT_PROJECT_DATA, _R_LLM_SETTINGS),
    ("DELETE", "/api/chat/settings/llm/profiles/{profile_id}/key"): (NOT_PROJECT_DATA, _R_LLM_SETTINGS),
    ("POST", "/api/chat/settings/llm/active"): (NOT_PROJECT_DATA, _R_LLM_SETTINGS),
    ("POST", "/api/chat/settings/llm/profiles/{profile_id}/test"): (NOT_PROJECT_DATA, _R_LLM_SETTINGS),
    ("PUT", "/api/local-settings/anthropic-key"): (NOT_PROJECT_DATA, _R_LOCAL),
    ("POST", "/api/local-settings/reveal-log"): (NOT_PROJECT_DATA, _R_LOCAL),
    # chat session
    ("POST", "/api/chat/stream"): (CHAT_SESSION, _R_CHAT_OWNED),
    ("POST", "/api/chat/{session_id}/confirm"): (CHAT_SESSION, _R_CHAT_OWNED),
    ("POST", "/api/chat/{session_id}/rewind"): (CHAT_SESSION, _R_CHAT_OWNED),
    ("POST", "/api/chat/{session_id}/abort"): (CHAT_SESSION, _R_CHAT_OWNED),
    ("POST", "/api/chat/import"): (CHAT_SESSION, _R_CHAT_HISTORY),
    # gridspine studies
    ("POST", "/api/gridspine/{name}/dispatch-source"): (STUDY_NOT_LOCK_COORDINATED, _R_STUDY),
    ("PUT", "/api/gridspine/{name}/config"): (STUDY_NOT_LOCK_COORDINATED, _R_STUDY),
    ("POST", "/api/gridspine/{name}/run"): (STUDY_NOT_LOCK_COORDINATED, _R_STUDY),
    ("PUT", "/api/gridspine/{name}/templates/{unit_id}/{param}"): (STUDY_NOT_LOCK_COORDINATED, _R_STUDY),
    ("POST", "/api/gridspine/{name}/readback/{hour}"): (STUDY_NOT_LOCK_COORDINATED, _R_STUDY),
    ("POST", "/api/gridspine/{name}/dispatch-source/external"): (STUDY_NOT_LOCK_COORDINATED, _R_STUDY),
    ("POST", "/api/gridspine/{name}/capacity"): (STUDY_NOT_LOCK_COORDINATED, _R_STUDY),
    ("POST", "/api/gridspine/{name}/connection"): (STUDY_NOT_LOCK_COORDINATED, _R_STUDY),
}

# Non-read chat tools that are neither seam-gated nor covered through an HTTP
# write route (a tool routed to one inherits that route's decision). Mostly
# routeless (`_service_call_`), which the seam's route-derived rule cannot see.
TOOL_POLICY: dict[str, tuple[str, str]] = {
    "load_project": (LOCK_PROTOCOL, _R_LOCK_PROTOCOL),
    "set_active_profile": (NOT_PROJECT_DATA, (
        "selects among already-configured LLM profiles; super-admin-only, "
        "matching `POST /api/chat/settings/llm/active`. Instance scope."
    )),
    "gridspine_create_study": (CREATES_PROJECT, _R_CREATES),
    "gridspine_set_dispatch_source": (STUDY_NOT_LOCK_COORDINATED, _R_STUDY),
    "gridspine_update_config": (STUDY_NOT_LOCK_COORDINATED, _R_STUDY),
    "gridspine_run_pipeline": (STUDY_NOT_LOCK_COORDINATED, _R_STUDY),
    "gridspine_edit_template_param": (STUDY_NOT_LOCK_COORDINATED, _R_STUDY),
    "gridspine_export_handoff_bundle": (STUDY_NOT_LOCK_COORDINATED, _R_STUDY),
    "gridspine_compute_capacity": (STUDY_NOT_LOCK_COORDINATED, _R_STUDY),
    "gridspine_assess_connection": (STUDY_NOT_LOCK_COORDINATED, _R_STUDY),
}

# Recorded, reproduced, open defects. Each MUST still be a gap. When one is
# fixed, `test_every_known_gap_is_still_a_gap` fails until its line is deleted.
KNOWN_GAPS: dict[str, str] = {
}


# ── enumeration ─────────────────────────────────────────────────────────────

def _walk_routes(routes):
    from fastapi.routing import APIRoute

    for r in routes:
        if isinstance(r, APIRoute):
            yield r.path, r.methods, r.endpoint
        elif hasattr(r, "effective_route_contexts"):  # lazily-included router
            for c in r.effective_route_contexts():
                yield c.path, c.methods, c.endpoint


@lru_cache(maxsize=None)
def _write_routes() -> dict[tuple[str, str], object]:
    out: dict[tuple[str, str], object] = {}
    for path, methods, endpoint in _walk_routes(main.app.routes):
        for m in methods or ():
            if m in WRITE_METHODS:
                out[(m, path)] = endpoint
    return out


def _middleware_gated(path: str) -> bool:
    """Under a gated prefix. Exempt routes count too: the exemption list in
    `main.py` is itself an explicit, per-route, written decision."""
    return any(path.startswith(p) for p in main._FOREIGN_LOCK_GATE_PREFIXES)


def _middleware_refuses(path: str) -> bool:
    """Gated AND not exempt: the middleware itself refuses a non-holder here."""
    concrete = re.sub(r"\{[^}]+\}", _SAMPLE_UUID, path)
    return _middleware_gated(path) and not main._foreign_lock_gate_exempt(concrete)


# ── verification: does a function reach a lock check? ───────────────────────

def _function_ast(fn):
    return ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]


def _local_imports(node) -> dict[str, tuple[str, str]]:
    out = {}
    for n in ast.walk(node):
        if isinstance(n, ast.ImportFrom) and n.module:
            for a in n.names:
                out[a.asname or a.name] = (n.module, a.name)
    return out


def _resolve(name, fn, local):
    if name in local:
        mod, attr = local[name]
        try:
            return getattr(importlib.import_module(mod), attr, None)
        except Exception:
            return None
    return getattr(fn, "__globals__", {}).get(name)


def _lock_check_path(fn, depth: int = 0, seen: set | None = None) -> list[str] | None:
    """
    The call chain from `fn` to a lock-check primitive, or None.

    Follows calls by NAME into functions defined in `routers.*`/`services.*`
    (module globals AND function-local imports), up to six levels deep. It
    does not follow a function passed as an argument; a handler that checks
    the lock that way needs a ROUTE_POLICY entry saying so. That is a false
    negative, the safe direction: it asks for a reason, it never hides a gap.
    """
    seen = set() if seen is None else seen
    fn = inspect.unwrap(fn)
    key = (getattr(fn, "__module__", ""), getattr(fn, "__qualname__", ""))
    if depth > 6 or key in seen:
        return None
    seen.add(key)
    try:
        node = _function_ast(fn)
    except (OSError, TypeError, SyntaxError):
        return None
    local = _local_imports(node)
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        if isinstance(n.func, ast.Attribute):
            if ast.unparse(n.func) in _LOCK_ATTR_PRIMITIVES:
                return [fn.__name__, ast.unparse(n.func)]
            continue
        if not isinstance(n.func, ast.Name):
            continue
        if n.func.id in _LOCK_PRIMITIVES:
            return [fn.__name__, n.func.id]
        target = _resolve(n.func.id, fn, local)
        if inspect.isfunction(target) and target.__module__.startswith(_OUR_MODULES):
            chain = _lock_check_path(target, depth + 1, seen)
            if chain:
                return [fn.__name__, *chain]
    return None


def _route_is_covered(key) -> bool:
    _method, path = key
    return (_middleware_gated(path)
            or _lock_check_path(_write_routes()[key]) is not None
            or key in ROUTE_POLICY)


# ── HTTP routes ─────────────────────────────────────────────────────────────

def test_the_route_enumeration_is_complete():
    """
    Guard on the guard. If the enumeration silently returned fewer routes,
    every assertion below would pass on the routes it never saw, which is the
    lazy-router trap the 2026-09-12 audit recorded.
    """
    spec = main.app.openapi()
    openapi_writes = {
        (m.upper(), p) for p, ops in spec["paths"].items() for m in ops
        if m.upper() in WRITE_METHODS
    }
    enumerated = set(_write_routes())
    missing = sorted(openapi_writes - enumerated)
    assert not missing, f"write routes in OpenAPI this file never sees: {missing}"
    assert len(enumerated) > 100, (
        f"only {len(enumerated)} write routes enumerated; the app has ~160. "
        f"Has the router mounting changed shape?"
    )


def test_every_write_route_has_a_lock_decision():
    """
    THE test. A write route that no mechanism covers and nobody has written a
    reason for fails here, which is the moment `put_asset_health` would have
    been caught (it landed with no `db`/`user` parameter at all).
    """
    undecided = sorted(k for k in _write_routes() if not _route_is_covered(k))
    assert not undecided, (
        "these write routes reach no edit-lock decision:\n  "
        + "\n  ".join(f"{m} {p}" for m, p in undecided)
        + "\n\nEach needs ONE of: a gated prefix in "
        "`main._FOREIGN_LOCK_GATE_PREFIXES`; a call (direct or through a "
        "helper) to `_check_project_lock` / `_enforce_project_lock`; or a "
        "ROUTE_POLICY entry in this file with a category and a reason. If it "
        "writes another user's project data, it is the first of those two."
    )


def test_no_route_policy_entry_is_stale_or_redundant():
    """
    The table stays exactly the set of exceptions. An entry for a route that
    no longer exists, or that is now gated or verifiably lock-checked, is a
    reason nobody needs and the next reader would trust.
    """
    routes = _write_routes()
    gone = sorted(k for k in ROUTE_POLICY if k not in routes)
    assert not gone, f"ROUTE_POLICY names routes that no longer exist: {gone}"
    gated = sorted(k for k in ROUTE_POLICY if _middleware_gated(k[1]))
    assert not gated, f"ROUTE_POLICY names middleware-gated routes: {gated}"
    checked = sorted(k for k in ROUTE_POLICY if _lock_check_path(routes[k]))
    assert not checked, (
        f"these now reach a lock check; delete their ROUTE_POLICY entries: {checked}"
    )


def test_every_policy_entry_carries_a_category_and_a_reason():
    for table in (ROUTE_POLICY, TOOL_POLICY):
        for key, (category, reason) in table.items():
            assert category in CATEGORIES, f"{key}: unknown category {category!r}"
            assert reason and len(reason.strip()) >= 12, f"{key}: no real reason given"


def test_the_verifier_sees_through_helpers():
    """
    Pin the property the test rests on. If `_lock_check_path` stopped following
    calls, every helper-checked route would turn into a 'needs a reason' miss.
    That would fail loudly above, but here it fails with the actual cause.
    """
    routes = _write_routes()
    assert _lock_check_path(routes[("POST", "/api/projects/{name}/reports")]), "local helper `_check_lock`"
    assert _lock_check_path(routes[("POST", "/api/projects/{name}/reports/generate")]), "cross-module helper"
    assert _lock_check_path(routes[("DELETE", "/api/projects/{name}")]), "`_delete_project_db`"
    assert _lock_check_path(routes[("PUT", "/api/projects/{name}/asset_health")]), "OPEN-ITEMS item 12"


# ── chat tools ──────────────────────────────────────────────────────────────

def _tool_tables():
    from services import chat_tools
    from services.chat_tools_schema import TOOL_ROUTES, safety_tier_for

    nonread = sorted(n for n in chat_tools.DISPATCHERS if safety_tier_for(n) != "read")
    return chat_tools, TOOL_ROUTES, nonread, chat_tools._lock_gated_tool_names()


def _tool_write_routes(TOOL_ROUTES, name) -> list[tuple[str, str]]:
    return [(m.upper(), p) for r in TOOL_ROUTES.get(name, ())
            if isinstance(r, tuple) for m, p in [r] if m.upper() in WRITE_METHODS]


def _tool_is_covered(name, chat_tools, TOOL_ROUTES, gated) -> bool:
    if name in gated:
        return True
    # A tool that checks the lock in its own body is holder-checked, the same
    # rule the HTTP routes get above, and verified the same way. Without this
    # the main test and the ratchet below disagreed: the ratchet counted an
    # in-body check as a fix, while this function did not count it as a
    # decision. That only surfaced with the first real in-body fixes: #76's
    # `_check_foreign_lock("export")` inside `_save_agent_export`, then
    # OPEN-ITEMS 14's `_gridspine_project_for_write`. Until then every
    # covered tool was either seam-gated or routed.
    if _lock_check_path(chat_tools.DISPATCHERS[name]) is not None:
        return True
    writes = _tool_write_routes(TOOL_ROUTES, name)
    routes = _write_routes()
    return bool(writes) and all(k in routes and _route_is_covered(k) for k in writes)


def test_every_write_tool_has_a_lock_decision():
    """
    The chat seam's half. A tool is covered when the seam gates it, or when
    every write route it maps to is covered (it inherits that decision; the
    `_route` wiring that makes an in-handler check actually run is held by
    `test_chat_tools_handler_dependencies`). Anything else needs a
    TOOL_POLICY reason or a KNOWN_GAPS entry. Routeless tools always do.
    """
    chat_tools, TOOL_ROUTES, nonread, gated = _tool_tables()
    undecided = [n for n in nonread
                 if not _tool_is_covered(n, chat_tools, TOOL_ROUTES, gated)
                 and n not in TOOL_POLICY and n not in KNOWN_GAPS]
    assert not undecided, (
        f"these non-read chat tools reach no edit-lock decision: {undecided}\n"
        "Gate them at the seam (`chat_tools._lock_gated_tool_names`), route them "
        "through a lock-checked handler, or add a TOOL_POLICY reason. If the tool "
        "writes another user's project data, it is one of the first two."
    )


def test_no_tool_reaches_a_gated_route_around_the_seam():
    """
    The middleware is HTTP-only; a tool calls its handler in-process. So a tool
    mapped to a route the middleware REFUSES non-holders on must be gated at the
    seam, or it is a way around the middleware with nothing to notice.
    """
    chat_tools, TOOL_ROUTES, nonread, gated = _tool_tables()
    bypass = sorted(
        (n, k) for n in nonread if n not in gated
        for k in _tool_write_routes(TOOL_ROUTES, n) if _middleware_refuses(k[1])
    )
    assert not bypass, f"tools that reach a middleware-gated route ungated: {bypass}"


def test_no_tool_policy_entry_is_stale_or_redundant():
    chat_tools, TOOL_ROUTES, nonread, gated = _tool_tables()
    for table, label in ((TOOL_POLICY, "TOOL_POLICY"), (KNOWN_GAPS, "KNOWN_GAPS")):
        gone = sorted(n for n in table if n not in chat_tools.DISPATCHERS)
        assert not gone, f"{label} names tools that no longer exist: {gone}"
    covered = sorted(n for n in TOOL_POLICY
                     if _tool_is_covered(n, chat_tools, TOOL_ROUTES, gated))
    assert not covered, f"these are covered now; delete their TOOL_POLICY entries: {covered}"
    both = sorted(set(TOOL_POLICY) & set(KNOWN_GAPS))
    assert not both, f"a tool is either decided or a known gap, not both: {both}"


@pytest.mark.parametrize("tool", sorted(KNOWN_GAPS))
def test_every_known_gap_is_still_a_gap(tool):
    """
    The ratchet. A fixed gap that stays listed here is a false record, and the
    next person to read this file would believe the hole is still open, or
    worse, re-open it to "match". Delete the entry in the fix's commit.
    """
    chat_tools, TOOL_ROUTES, _nonread, gated = _tool_tables()
    fixed = (_tool_is_covered(tool, chat_tools, TOOL_ROUTES, gated)
             or _lock_check_path(chat_tools.DISPATCHERS[tool]) is not None)
    assert not fixed, (
        f"{tool!r} is lock-checked now ({KNOWN_GAPS[tool]}). Delete its "
        f"KNOWN_GAPS entry, and close the OPEN-ITEMS entry in the same change."
    )
