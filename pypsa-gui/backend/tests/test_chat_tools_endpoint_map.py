"""
Phase 1 — chat tools endpoint coverage.

Asserts that every tool in services.chat_tools_schema.TOOLS either:
  (a) maps to a real HTTP route present in the openapi.json fixture from
      Phase 0 (route_inventory_phase0.txt), or
  (b) is documented as a sentinel (_service_call_ / _derived_ / _ui_event_
      / _chat_jsonl_).

Plus integrity checks:
  * TOOLS, TOOL_ROUTES and DISPATCHERS are name-aligned.
  * Tool count is derived (v4-NIT-1 / v6-F4 — no magic number).
  * get_results path-lookup contains the v4-MAJOR-4 ac_pf_status outlier.

Reads the inventory at backend/tests/fixtures/route_inventory_phase0.txt.
Regenerate with `python tools/openapi_diff.py --phase0-fixture` after any
backend route change.
"""
from __future__ import annotations

import pathlib

import pytest

from services import chat_tools
from services.chat_tools_schema import (
    NON_HTTP_SENTINELS,
    RESULTS_ENUM,
    TOOL_COUNT,
    TOOL_ROUTES,
    TOOLS,
)


# ── Inventory loader ────────────────────────────────────────────────────────


_INVENTORY_PATH = (
    pathlib.Path(__file__).resolve().parent / "fixtures" / "route_inventory_phase0.txt"
)


def _load_inventory() -> set[tuple[str, str]]:
    """Parse `METHOD<spaces> PATH<spaces> -> op_id` → set of (METHOD, PATH)."""
    out: set[tuple[str, str]] = set()
    text = _INVENTORY_PATH.read_text(encoding="utf-8")
    for line in text.splitlines():
        if "->" not in line:
            continue
        head, _ = line.split("->", 1)
        parts = head.strip().split()
        if len(parts) < 2:
            continue
        method, path = parts[0], parts[1]
        out.add((method.upper(), path))
    return out


@pytest.fixture(scope="module")
def inventory() -> set[tuple[str, str]]:
    assert _INVENTORY_PATH.exists(), (
        f"missing route inventory fixture at {_INVENTORY_PATH}. "
        f"Regenerate with: python tools/openapi_diff.py --phase0-fixture"
    )
    inv = _load_inventory()
    assert inv, "inventory is empty — fixture parse failed"
    return inv


# ── Integrity checks ───────────────────────────────────────────────────────


def test_tool_count_is_derived_from_registry():
    """v4-NIT-1 / v6-F4 — TOOL_COUNT must equal len(TOOLS); no magic numbers."""
    assert TOOL_COUNT == len(TOOLS), (
        f"TOOL_COUNT={TOOL_COUNT} drifted from len(TOOLS)={len(TOOLS)}"
    )


def test_tool_names_are_unique():
    names = [t["name"] for t in TOOLS]
    assert len(names) == len(set(names)), (
        f"duplicate tool names: {sorted(set(n for n in names if names.count(n) > 1))}"
    )


def test_every_tool_has_dispatcher():
    """Every TOOLS entry has a callable in chat_tools.DISPATCHERS."""
    for tool in TOOLS:
        name = tool["name"]
        assert name in chat_tools.DISPATCHERS, (
            f"tool {name!r} declared in TOOLS but missing from DISPATCHERS"
        )
        assert callable(chat_tools.DISPATCHERS[name]), (
            f"DISPATCHERS[{name!r}] is not callable"
        )


def test_dispatchers_are_all_tools():
    """Every DISPATCHERS entry must appear in TOOLS (no orphans)."""
    tool_names = {t["name"] for t in TOOLS}
    for dn in chat_tools.DISPATCHERS:
        assert dn in tool_names, (
            f"DISPATCHERS[{dn!r}] has no corresponding TOOLS entry"
        )


def test_every_tool_has_route_entry():
    """Every TOOLS entry has a TOOL_ROUTES entry."""
    for tool in TOOLS:
        name = tool["name"]
        assert name in TOOL_ROUTES, (
            f"tool {name!r} in TOOLS but missing TOOL_ROUTES entry"
        )


def test_tool_input_schemas_well_formed():
    """Each tool's input_schema is a JSON Schema 'object' with properties dict."""
    for tool in TOOLS:
        assert tool["input_schema"]["type"] == "object"
        assert isinstance(tool["input_schema"]["properties"], dict)
        assert isinstance(tool["input_schema"]["required"], list)
        # Required must reference real properties
        props = set(tool["input_schema"]["properties"].keys())
        for r in tool["input_schema"]["required"]:
            assert r in props, (
                f"tool {tool['name']!r}: required field {r!r} not in properties"
            )


# ── Route-coverage checks ──────────────────────────────────────────────────


def test_every_http_route_resolves_in_inventory(inventory):
    """
    For every tool with an HTTP route, each declared (method, path) must
    match a row in route_inventory_phase0.txt. Sentinels are skipped.
    """
    misses: list[str] = []
    for tool_name, routes in TOOL_ROUTES.items():
        if routes and routes[0] in NON_HTTP_SENTINELS:
            continue
        for entry in routes:
            method, path = entry
            if (method, path) not in inventory:
                misses.append(f"{tool_name}: {method} {path}")
    assert not misses, (
        "tool route(s) not found in route_inventory_phase0.txt:\n"
        + "\n".join(misses)
    )


def test_non_http_sentinels_are_documented():
    """Every sentinel used in TOOL_ROUTES is in NON_HTTP_SENTINELS."""
    for tool_name, routes in TOOL_ROUTES.items():
        if not routes:
            continue
        first = routes[0]
        if isinstance(first, str) and first.startswith("_") and first.endswith("_"):
            assert first in NON_HTTP_SENTINELS, (
                f"tool {tool_name!r} uses undocumented sentinel {first!r}"
            )


# ── get_results dispatcher specifics ───────────────────────────────────────


def test_get_results_enum_has_28_values():
    """v6 plan: 28 distinct result_kind values matching results.py grep."""
    assert len(set(RESULTS_ENUM)) == 28, (
        f"RESULTS_ENUM has {len(RESULTS_ENUM)} unique values; expected 28"
    )


def test_get_results_lookup_dict_handles_ac_pf_status():
    """
    v4-MAJOR-4: ac_pf_status routes to /ac_pf/status (NOT /ac_pf_status).
    All other 27 enums map 1:1 to /results/{kind}.
    """
    assert chat_tools.results_path_for("ac_pf_status") == "/api/results/ac_pf/status"
    for kind in RESULTS_ENUM:
        if kind == "ac_pf_status":
            continue
        assert chat_tools.results_path_for(kind) == f"/api/results/{kind}"


def test_get_results_handler_resolves_for_every_enum():
    """Every result_kind has a real handler symbol in routers.results."""
    for kind in RESULTS_ENUM:
        handler = chat_tools._resolve_results_handler(kind)
        assert callable(handler)


def test_get_results_routes_all_present_in_inventory(inventory):
    """All 28 result endpoints exist in the route inventory."""
    misses: list[str] = []
    for entry in TOOL_ROUTES["get_results"]:
        method, path = entry
        if (method, path) not in inventory:
            misses.append(f"{method} {path}")
    assert not misses, "get_results routes missing from inventory: " + ", ".join(misses)


# ── update_component dispatcher routing coverage ──────────────────────────


def test_update_component_routes_cover_bus_rename_and_dispatcher_branches():
    """
    update_component must declare ALL four dispatcher targets:
      F1 — POST /api/network/buses/{name}/rename
      Bus non-rename — PUT /api/network/buses/{name}
      F2 — PUT /api/network/transformers/{name}
      F3 — PUT /api/network/global_constraints/{name}
    """
    routes = TOOL_ROUTES["update_component"]
    needed = {
        ("POST", "/api/network/buses/{name}/rename"),     # F1
        ("PUT", "/api/network/buses/{name}"),             # Bus non-rename
        ("PUT", "/api/network/transformers/{name}"),      # F2
        ("PUT", "/api/network/global_constraints/{name}"),  # F3
    }
    declared = set(routes)
    missing = needed - declared
    assert not missing, f"update_component missing dispatcher routes: {missing}"


def test_cascade_delete_bus_distinct_from_delete_component():
    """cascade_delete_bus maps to a distinct /cascade endpoint."""
    assert TOOL_ROUTES["cascade_delete_bus"] == [
        ("DELETE", "/api/network/buses/{name}/cascade"),
    ]


# ── Read-only meta checks ──────────────────────────────────────────────────


def test_every_tool_has_description():
    for tool in TOOLS:
        assert tool["description"], f"tool {tool['name']!r} missing description"
        assert "Safety:" in tool["description"], (
            f"tool {tool['name']!r}: description must declare 'Safety: <tier>'"
        )
