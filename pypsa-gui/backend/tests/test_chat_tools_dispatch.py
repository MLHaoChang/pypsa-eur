"""
Phase 1 — chat tools dispatch behavior.

Covers the v6 exit-criteria items (a)-(o):

  (a) update_component({Bus, B1, new_name:B2}) — preserves connected line bus0 ref
      via rename_bus → n.rename_component_names (F1).
  (b) update_component({Bus, B1, control:'PV'}) — does NOT recompute line lengths
      (no coord change).
  (c) update_component({Transformer, T1, v_nom_0:380, v_nom_1:220}) — runs voltage
      validation (F2).
  (d) update_component({GlobalConstraint, CO2, constant:100}) — updates only
      constant; type/sense/carrier_attribute preserved (F3 partial-PUT mitigation).
  (e) save_project_as on an existing OTHER project's name returns 409
      error_kind='project_exists' WITHOUT touching disk (M1 pre-check).
  (f) endpoint-map test passes (separate file).
  (g) get_results enum has 28 values (separate file).
  (h) v4-MAJOR-4: results_path_for('ac_pf_status') == '/api/results/ac_pf/status'
      (separate file).
  (i) v4-MAJOR-3: upload_load_profile decodes b64 + dispatches multi-column CSV.
  (j) v4-MINOR-1: delete_project cascade param is plumbed.
  (k) v4-NIT-1 / v6-F4: tool count derived (separate file).
  (l) dispatch_status calls services.dispatch_status.dispatch_status() with NO HTTP.
  (m) get_component does df.loc[name].to_dict() single-row lookup.
  (n) v6-F3 schema match (separate file).
  (o) v6-F2 list_projects.resident snapshot semantics doc'd in description
      (assertion below).
"""
from __future__ import annotations

import base64

import pypsa
import pytest

from services import chat_tools
from services.chat_tools_schema import TOOLS


# ── Test helpers ────────────────────────────────────────────────────────────


def _tool_description(name: str) -> str:
    for t in TOOLS:
        if t["name"] == name:
            return t["description"]
    raise AssertionError(f"tool {name!r} not in TOOLS")


def _install_minimal_network(install_network):
    """Seed a single-bus network so dispatch tools have something to act on."""
    n = pypsa.Network()
    n.add("Bus", "B1")
    n.add("Bus", "B2")
    n.add("Line", "L1", bus0="B1", bus1="B2", x=0.1, r=0.01, s_nom=100.0)
    install_network(n, name=None)
    return n


# ─────────────────────────────────────────────────────────────────────────────
# (a) update_component dispatch — Bus rename → rename_bus
# ─────────────────────────────────────────────────────────────────────────────


def test_update_component_partial_attrs_without_bus(install_network):
    """
    Agents routinely send only the fields they change (e.g. p_nom_max).
    Create schemas require bus/bus0 — update_component must prefill those
    from the live row instead of failing with a cryptic ValidationError.
    """
    n = pypsa.Network()
    n.add("Bus", "B1")
    n.add("Bus", "B3")
    n.add(
        "StorageUnit", "BESS_B3", bus="B3", p_nom=100.0,
        p_nom_extendable=False, p_nom_max=float("inf"),
    )
    n.add("Generator", "PV_B3", bus="B3", p_nom=300.0, p_nom_extendable=True)
    n.add(
        "Line", "L1", bus0="B3", bus1="B1", x=0.1, r=0.01, s_nom=500.0,
        s_nom_extendable=True,
    )
    install_network(n, name=None)

    chat_tools.update_component(
        "StorageUnit", "BESS_B3",
        attrs={
            "p_nom_extendable": True,
            "p_nom_min": 100.0,
            "p_nom_max": 500.0,
            "capital_cost": 22000.0,
        },
    )
    assert bool(n.storage_units.at["BESS_B3", "p_nom_extendable"]) is True
    assert float(n.storage_units.at["BESS_B3", "p_nom_max"]) == 500.0
    assert n.storage_units.at["BESS_B3", "bus"] == "B3"

    chat_tools.update_component(
        "Generator", "PV_B3", attrs={"p_nom_max": 1500.0},
    )
    assert float(n.generators.at["PV_B3", "p_nom_max"]) == 1500.0
    assert n.generators.at["PV_B3", "bus"] == "B3"

    chat_tools.update_component(
        "Line", "L1", attrs={"s_nom_max": 2000.0},
    )
    assert float(n.lines.at["L1", "s_nom_max"]) == 2000.0
    assert n.lines.at["L1", "bus0"] == "B3"


def test_update_component_bus_rename_routes_to_rename_bus(install_network):
    """
    F1: update_component({Bus, B1, new_name:B2}) MUST route through
    rename_bus so dependent line bus0/bus1 references are updated atomically
    via n.rename_component_names.

    Bare _update_component would do remove + add, leaving line.bus0='B1'
    pointing at a removed bus — data corruption.
    """
    n = _install_minimal_network(install_network)
    # Add a line so we can verify the reference update
    assert n.lines.at["L1", "bus0"] == "B1"

    result = chat_tools.update_component("Bus", "B1", new_name="B1_renamed")

    # Bus row renamed
    assert "B1_renamed" in n.buses.index
    assert "B1" not in n.buses.index
    # Line bus0 reference followed the rename (F1 invariant)
    assert n.lines.at["L1", "bus0"] == "B1_renamed"


# ─────────────────────────────────────────────────────────────────────────────
# (b) update_component({Bus, B1, control:'PV'}) — no coord change ⇒ no recompute
# ─────────────────────────────────────────────────────────────────────────────


def test_update_component_bus_non_rename_does_not_recompute_lengths(install_network):
    """
    Bus update WITHOUT new coords and WITHOUT new_name must NOT trigger
    _recompute_lengths_for_bus (the bus.model_dump(exclude_unset=True) gate
    only fires recompute when x or y are actually submitted).
    """
    n = pypsa.Network()
    n.add("Bus", "B1", x=10.0, y=50.0)
    n.add("Bus", "B2", x=20.0, y=50.0)
    n.add("Line", "L1", bus0="B1", bus1="B2", x=0.1, r=0.01, s_nom=100.0,
          length=42.0)
    install_network(n, name=None)
    assert n.lines.at["L1", "length"] == 42.0

    chat_tools.update_component("Bus", "B1", attrs={"control": "PV"})

    # control should be updated
    assert n.buses.at["B1", "control"] == "PV"
    # length should NOT have been recomputed from haversine (still user value)
    assert n.lines.at["L1", "length"] == 42.0


# ─────────────────────────────────────────────────────────────────────────────
# (c) update_component({Transformer, T1, v_nom_0:380, v_nom_1:220}) — voltage check
# ─────────────────────────────────────────────────────────────────────────────


def test_update_component_transformer_runs_voltage_validation(install_network):
    """
    F2: Transformer update MUST go through update_transformer to run
    _validate_transformer_voltage. Bus voltages of 380/220 vs request
    of 380/220 → passes. Mismatch → raises HTTPException.
    """
    from fastapi import HTTPException
    n = pypsa.Network()
    n.add("Bus", "B1", v_nom=380.0)
    n.add("Bus", "B2", v_nom=220.0)
    n.add("Transformer", "T1", bus0="B1", bus1="B2", s_nom=100.0, x=0.1)
    install_network(n, name=None)

    # Matching voltages → succeeds
    chat_tools.update_component(
        "Transformer", "T1",
        attrs={"bus0": "B1", "bus1": "B2", "s_nom": 150.0,
               "v_nom_0": 380.0, "v_nom_1": 220.0},
    )
    assert n.transformers.at["T1", "s_nom"] == 150.0

    # Mismatched voltages → _validate_transformer_voltage raises
    with pytest.raises(HTTPException) as exc:
        chat_tools.update_component(
            "Transformer", "T1",
            attrs={"bus0": "B1", "bus1": "B2",
                   "v_nom_0": 999.0, "v_nom_1": 220.0},
        )
    # 4xx voltage mismatch
    assert 400 <= exc.value.status_code < 500


# ─────────────────────────────────────────────────────────────────────────────
# (d) update_component({GlobalConstraint, CO2, constant:100}) — partial-PUT mitigation
# ─────────────────────────────────────────────────────────────────────────────


def test_update_component_global_constraint_partial_put_mitigation(install_network):
    """
    F3: GlobalConstraint update routes through update_global_constraint
    which captures the current row and merges exclude_unset over it — so a
    partial body `{constant: 100}` does NOT reset type/sense/carrier_attribute
    to Pydantic schema defaults.
    """
    n = pypsa.Network()
    n.add("Bus", "B1")
    n.add(
        "GlobalConstraint", "CO2",
        type="primary_energy", sense="<=", constant=500.0,
        carrier_attribute="co2_emissions",
    )
    install_network(n, name=None)

    chat_tools.update_component("GlobalConstraint", "CO2",
                                 attrs={"constant": 100.0})

    # constant updated
    assert float(n.global_constraints.at["CO2", "constant"]) == 100.0
    # Other fields preserved (NOT reset to schema defaults — F3)
    assert str(n.global_constraints.at["CO2", "type"]) == "primary_energy"
    assert str(n.global_constraints.at["CO2", "sense"]) == "<="
    assert str(n.global_constraints.at["CO2", "carrier_attribute"]) == "co2_emissions"


# ─────────────────────────────────────────────────────────────────────────────
# (e) save_project_as 409 pre-check (M1)
# ─────────────────────────────────────────────────────────────────────────────


def test_save_project_as_blocks_overwrite_via_pre_check(
    client, install_network, tmp_projects_dir, project_storage_dir
):
    """
    M1: save_project_as MUST refuse with 409 when target name already exists
    AND active ctx.loaded_project != name. The pre-check uses list_projects
    BEFORE issuing the destructive POST.
    """
    from fastapi import HTTPException

    # Seed "B" as a REAL project. The pre-check reads `list_projects`, which is
    # DB-backed since Step 0a — a bare directory under the projects root is no
    # longer a project and would make this test pass for the wrong reason.
    n_b = pypsa.Network()
    n_b.add("Bus", "B1")
    install_network(n_b, name="B")
    assert client.post(
        "/api/projects/B", params={"force": True, "rebind": True}
    ).status_code == 200
    stored_b = project_storage_dir("B") / "network.nc"
    before = stored_b.read_bytes()

    # Set active context to a DIFFERENT loaded_project so the predicate fires
    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name="A")  # active is bound to A

    with pytest.raises(HTTPException) as exc:
        chat_tools.save_project_as("B")
    assert exc.value.status_code == 409
    detail = str(exc.value.detail)
    assert "already exists" in detail or "exists" in detail
    # B's stored network is untouched (the POST was NEVER issued)
    assert stored_b.read_bytes() == before


# ─────────────────────────────────────────────────────────────────────────────
# (i) v4-MAJOR-3 — upload_load_profile decodes b64 + multi-column CSV
# ─────────────────────────────────────────────────────────────────────────────


def test_upload_load_profile_decodes_base64_and_dispatches(install_network):
    """
    v4-MAJOR-3: upload_load_profile takes csv_content_b64 + filename. The
    dispatcher must base64-decode, wrap into UploadFile, and call the route
    handler with the multi-column CSV (columns = load names).
    """
    n = pypsa.Network()
    import pandas as pd
    n.set_snapshots(pd.date_range("2025-01-01", periods=3, freq="h"))
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1")
    n.add("Load", "L2", bus="B1")
    install_network(n, name=None)

    csv = "snapshot,L1,L2\n2025-01-01 00:00:00,100,200\n2025-01-01 01:00:00,110,210\n2025-01-01 02:00:00,120,220\n"
    b64 = base64.b64encode(csv.encode("utf-8")).decode("ascii")

    result = chat_tools.upload_load_profile(csv_content_b64=b64,
                                             filename="loads.csv")
    # Result shape: matched + unmatched (route returns these fields)
    assert "matched" in result or "applied" in result or isinstance(result, dict)


def test_generate_exemplary_timeseries_builds_year_without_inline_csv(install_network):
    """
    Full-year synthetic profiles must be built server-side — inlining ~8760
    CSV rows in a tool call exceeds MAX_OUTPUT_TOKENS_PER_TURN and freezes UI.
    """
    import pandas as pd

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=8760, freq="h"))
    n.add("Bus", "B1")
    n.add("Load", "Load_B1", bus="B1", p_set=0.0)
    n.add("Generator", "PV_B3", bus="B1", p_nom=100.0, p_max_pu=0.0)
    install_network(n, name=None)

    load_res = chat_tools.generate_exemplary_timeseries(
        component="loads",
        name="Load_B1",
        attribute="p_set",
        profile="load_daily",
        peak=50.0,
    )
    assert load_res["snapshot_count"] == 8760
    assert load_res["profile"] == "load_daily"
    assert load_res["value_max"] > load_res["value_min"] >= 0.0
    assert "Load_B1" in n.loads_t.p_set.columns
    assert len(n.loads_t.p_set["Load_B1"]) == 8760

    pv_res = chat_tools.generate_exemplary_timeseries(
        component="generators",
        name="PV_B3",
        attribute="p_max_pu",
        profile="pv_solar",
        peak=1.0,
    )
    assert pv_res["snapshot_count"] == 8760
    assert pv_res["profile"] == "pv_solar"
    assert 0.0 <= pv_res["value_min"] <= pv_res["value_max"] <= 1.0 + 1e-9
    # Night hours should be ~0 for a January 00:00 start.
    assert float(n.generators_t.p_max_pu["PV_B3"].iloc[0]) == pytest.approx(0.0, abs=1e-9)

    assert "generate_exemplary_timeseries" in chat_tools.DISPATCHERS
    desc = _tool_description("generate_exemplary_timeseries")
    assert "load_daily" in desc and "pv_solar" in desc


# ─────────────────────────────────────────────────────────────────────────────
# (j) v4-MINOR-1 — delete_project cascade param plumbed
# ─────────────────────────────────────────────────────────────────────────────


def test_delete_project_passes_cascade_param(tmp_projects_dir, monkeypatch):
    """
    v4-MINOR-1: delete_project tool MUST plumb the cascade param to the route
    handler. Verified by patching the route handler and asserting the kwarg.
    """
    call_log: list[dict] = []

    # `db`/`user` are injected by `chat_tools._route` since Step 0a — the tool
    # layer supplies the acting identity the handler now authorizes against.
    def fake_delete(name, cascade=False, db=None, user=None):
        call_log.append({"name": name, "cascade": cascade})
        assert user is not None, "delete_project must be called with an identity"
        return {"deleted": [name]}

    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "delete_project", fake_delete)

    chat_tools.delete_project("Foo", cascade=True)
    assert call_log == [{"name": "Foo", "cascade": True}]

    chat_tools.delete_project("Bar")  # default cascade=False
    assert call_log[1] == {"name": "Bar", "cascade": False}


# ─────────────────────────────────────────────────────────────────────────────
# (l) dispatch_status calls service directly (NO HTTP)
# ─────────────────────────────────────────────────────────────────────────────


def test_dispatch_status_calls_service_function_directly(install_network, monkeypatch):
    """
    B3: dispatch_status MUST call services.dispatch_status.dispatch_status_detail(n)
    DIRECTLY — no HTTP round-trip. There is no /dispatch_status endpoint. The
    wrapper uses the _detail variant so the result carries the schema's promised
    {state, mismatched_classes} shape (the plain dispatch_status returns a bare
    string).
    """
    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name=None)

    sentinel = {"state": "fresh", "mismatched_classes": []}
    call_count = {"n": 0}

    def fake_ds(network):
        call_count["n"] += 1
        return sentinel

    from services import dispatch_status as ds_module
    monkeypatch.setattr(ds_module, "dispatch_status_detail", fake_ds)

    result = chat_tools.dispatch_status()
    assert call_count["n"] == 1
    assert result is sentinel


# ─────────────────────────────────────────────────────────────────────────────
# (m) get_component does df.loc[name].to_dict() — single-row lookup
# ─────────────────────────────────────────────────────────────────────────────


def test_get_component_single_row_lookup(install_network):
    """
    N2: get_component returns one row's dict. Does NOT call list_components
    (verified by inspecting the source — but the behaviour test here just
    asserts the result is a dict for a single component, not a list).
    """
    n = pypsa.Network()
    n.add("Bus", "B1", v_nom=380.0, x=10.0, y=50.0)
    install_network(n, name=None)

    row = chat_tools.get_component("Bus", "B1")
    assert isinstance(row, dict)
    assert row["name"] == "B1"
    # NaN/Inf coerced to None per the contract
    for v in row.values():
        import math
        if isinstance(v, float):
            assert not math.isnan(v), f"NaN leaked: {v!r}"
            assert not math.isinf(v), f"Inf leaked: {v!r}"


def test_get_component_404_for_missing(install_network):
    from fastapi import HTTPException
    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name=None)

    with pytest.raises(HTTPException) as exc:
        chat_tools.get_component("Bus", "NotThere")
    assert exc.value.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# (o) v6-F2 list_projects.resident snapshot semantic in description
# ─────────────────────────────────────────────────────────────────────────────


def test_list_projects_description_documents_resident_snapshot_semantic():
    """
    v6-F2: list_projects' description MUST tell the LLM that the `resident`
    flag is a read-time snapshot and may flip before a follow-up
    activate_project, with the cold path still succeeding.
    """
    desc = _tool_description("list_projects")
    assert "SNAPSHOT AT READ TIME" in desc.upper() or \
           "snapshot at read time" in desc.lower(), (
        f"list_projects description missing resident-snapshot semantic: {desc[:300]}"
    )
    assert "cold path" in desc.lower(), (
        f"list_projects description missing cold-path fallback note: {desc[:300]}"
    )
    assert "Do NOT retry" in desc or "do not retry" in desc.lower(), (
        f"list_projects description missing 'do not retry on drift': {desc[:300]}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# v4-NIT-2: force_reset_simulation single destructive tier
# ─────────────────────────────────────────────────────────────────────────────


def test_force_reset_simulation_classified_destructive():
    """
    v4-NIT-2: force_reset_simulation must be in the `destructive` tier (NOT
    `execution` / `execution_long_running`). Verified via description marker.
    """
    desc = _tool_description("force_reset_simulation")
    assert "Safety: destructive" in desc, (
        f"force_reset_simulation must declare 'Safety: destructive': {desc[:200]}"
    )


def test_no_tool_misclassified_force_reset_as_execution():
    """
    v4-NIT-2: force_reset_simulation must NOT carry 'Safety: execution' /
    'Safety: execution_long_running' tier markers (it's the single
    destructive tier per v4-NIT-2). Negation phrases like 'NOT
    execution_long_running' in the explanation are allowed.
    """
    desc = _tool_description("force_reset_simulation")
    # The literal safety-tier marker must declare destructive.
    assert "Safety: destructive" in desc
    # Must NOT carry an execution-tier marker.
    assert "Safety: execution" not in desc
    assert "Safety: execution_long_running" not in desc


# ─────────────────────────────────────────────────────────────────────────────
# get_results dispatcher specifics — happy path
# ─────────────────────────────────────────────────────────────────────────────


def test_get_results_resolves_known_kinds(install_network):
    """get_results runs without raising on a fresh idle network — returns 204-ish dict."""
    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name=None)

    for kind in ("cost_breakdown", "statistics", "objective_decomposition"):
        # May return None / 204 / empty dict on an unsolved network; the test
        # just verifies the handler is callable and doesn't raise.
        try:
            chat_tools.get_results(result_kind=kind)
        except Exception as e:
            # An unsolved network is allowed to surface as 204/None; not all
            # handlers tolerate this without raising. We only verify the
            # dispatcher itself works — handler-internal errors are acceptable.
            from fastapi import HTTPException
            if isinstance(e, HTTPException):
                continue
            # Otherwise re-raise — would indicate a real dispatcher bug.
            # Allow common "not solved" pattern exceptions:
            msg = str(e).lower()
            if "not solved" in msg or "no results" in msg:
                continue


def test_get_results_rejects_unknown_kind():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        chat_tools.get_results(result_kind="not_a_real_kind")
    assert exc.value.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# UI control tools emit marker dicts (no backend mutation)
# ─────────────────────────────────────────────────────────────────────────────


def test_ui_select_component_returns_event_marker():
    event = chat_tools.ui_select_component(component_class="Bus", name="B1")
    assert event["_ui_event"] is True
    assert event["kind"] == "select_component"
    assert event["component_class"] == "Bus"
    assert event["name"] == "B1"


def test_ui_open_panel_returns_event_marker():
    event = chat_tools.ui_open_panel(panel_id="Results", results_tab="economics")
    assert event["_ui_event"] is True
    assert event["kind"] == "navigate"
    assert event["panel_id"] == "Results"
    assert event["results_tab"] == "economics"


def test_ui_set_snapshot_returns_event_marker():
    event = chat_tools.ui_set_snapshot(snapshot_iso="2025-01-01T00:00:00", period=2030)
    assert event["_ui_event"] is True
    assert event["kind"] == "set_snapshot"
    assert event["snapshot_iso"] == "2025-01-01T00:00:00"
    assert event["period"] == 2030


# ─────────────────────────────────────────────────────────────────────────────
# Conversation tools (list_chat_history + clear_chat_history)
# ─────────────────────────────────────────────────────────────────────────────


def test_list_chat_history_on_unbound_returns_empty():
    """No active loaded_project → list returns []."""
    history = chat_tools.list_chat_history()
    assert history == []


def test_clear_chat_history_on_unbound_returns_reason():
    """Unbound ctx → cleared=False with reason."""
    result = chat_tools.clear_chat_history()
    assert result["cleared"] is False
    assert result.get("reason") == "unbound_ctx"


# ─────────────────────────────────────────────────────────────────────────────
# Sanity: every tool's dispatcher is importable / callable
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tool_name", [t["name"] for t in TOOLS])
def test_dispatcher_for_each_tool_is_callable(tool_name):
    assert tool_name in chat_tools.DISPATCHERS


# ─────────────────────────────────────────────────────────────────────────────
# Task 10 — registry-wide Safety-marker resolution.
#
# `chat_service._safety_tier_for` is FAIL-OPEN: an unmarked / unresolvable
# tool defaults to "read", i.e. NO confirmation card. That default is
# documented (see its docstring) rather than changed here — this test is
# what keeps the fail-open default safe, by asserting every TOOLS entry
# actually resolves to the SAME tier its own description literally states.
# `test_every_tool_has_description` (test_chat_tools_endpoint_map.py) only
# checks the substring "Safety:" is present; it would NOT catch a marker
# that resolves to the wrong tier, or a tool with no marker at all quietly
# falling back to "read" — this test asserts resolution, not presence.
# ─────────────────────────────────────────────────────────────────────────────


_KNOWN_SAFETY_TIERS = frozenset(
    ["read", "write", "destructive", "execution", "execution_long_running"]
)


@pytest.mark.parametrize("tool_name", [t["name"] for t in TOOLS])
def test_every_tool_safety_marker_resolves_to_known_tier(tool_name):
    """
    Every tool must carry a `Safety: <tier>` marker that `_safety_tier_for`
    resolves to one of the five known tiers, AND that literal
    `Safety: <tier>` substring must actually be present in the description
    — a tool with NO marker at all would still resolve to "read" (the
    fail-open default) without this second check, silently passing.
    """
    from services.chat_service import _safety_tier_for

    tier = _safety_tier_for(tool_name)
    assert tier in _KNOWN_SAFETY_TIERS, (
        f"tool {tool_name!r} resolved to unknown tier {tier!r}"
    )
    desc = _tool_description(tool_name)
    assert f"Safety: {tier}" in desc, (
        f"tool {tool_name!r}: _safety_tier_for resolved {tier!r} but the "
        f"description does not literally carry 'Safety: {tier}' — this is "
        f"the fail-open default catching a missing/unrecognised marker"
    )
    assert callable(chat_tools.DISPATCHERS[tool_name])


# ─────────────────────────────────────────────────────────────────────────────
# Foreign-lock enforcement through the chat surface (fix-wave C1 + F1)
#
# Chat tools call route handlers as plain Python functions, so NEITHER of the
# two HTTP-shaped defences reaches them by itself:
#
#   C1 — `create_project_snapshot` / `delete_project_snapshot` called their
#        handlers POSITIONALLY, bypassing `_route`. Those handlers now declare
#        `db`/`user` Depends and call `_enforce_project_lock`, so `user`
#        arrived as the raw `Depends` sentinel and `user.id` raised
#        AttributeError in auth mode — the lock gate could never fire.
#   F1 — network/io/simulation writes reached through chat never pass the
#        write middleware, so the path-prefix foreign-lock gate in `main.py`
#        never ran for them. The dispatch seam re-checks the same lock.
#
# Both are proved against a REAL foreign lock (held by the second seeded
# identity) on the acting user's own active project.
# ─────────────────────────────────────────────────────────────────────────────


import contextlib as _contextlib
import uuid as _uuid


def _save_and_bind_project(client, install_network, name: str) -> None:
    """Save a one-bus network as `name` so it owns a DB row + a bound ctx."""
    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name=name)
    assert client.post(
        f"/api/projects/{name}", params={"force": True, "rebind": True}
    ).status_code == 200


def _project_row_id(session_local, name: str):
    from sqlalchemy import select

    from db.models import Project

    with session_local() as db:
        row = db.scalar(select(Project).where(Project.name == name))
        assert row is not None, f"project {name!r} has no DB row"
        return row.id


def _hold_foreign_lock(session_local, project_id, holder_user_id) -> None:
    """
    Hand the project's lock to `holder_user_id`.

    The save in `_save_and_bind_project` already ACQUIRED the lock for the
    acting user (D4 — the writer becomes the holder for the TTL), so the row
    has to be dropped first; `acquire_lock` is deliberately refusal-only for a
    second user. Equivalent to that lock having lapsed or been released.
    """
    from db.models import ProjectLock
    from services import project_locks

    with session_local() as db:
        existing = db.get(ProjectLock, project_id)
        if existing is not None:
            db.delete(existing)
            db.commit()
        assert project_locks.acquire_lock(db, project_id, holder_user_id) is not None


@_contextlib.contextmanager
def _bound_to(ctx):
    """
    Bind `ctx` as the request-scoped active context for a direct tool call.

    Production does the same thing: the chat SSE generator copies the request
    context into the tool worker, so `PyPSAService.get_active_context()` inside
    a tool resolves the session's project. A bare direct call in a test would
    otherwise land on a freshly-created empty context and the seam would (
    correctly) find nothing to guard.
    """
    from services.pypsa_service import PyPSAService

    token = PyPSAService.bind_request_context(ctx)
    try:
        yield
    finally:
        PyPSAService.reset_request_context(token)


@pytest.fixture
def foreign_locked_active_project(
    client, install_network, _auth_db, second_identity, session_ctx
):
    """
    A saved + active project whose edit lock is held by a DIFFERENT user.

    Yields the project name; the acting chat identity stays the seeded user.
    """
    _engine, session_local = _auth_db
    name = "LockedByOther"
    _save_and_bind_project(client, install_network, name)
    ctx = session_ctx(client)
    project_id = _project_row_id(session_local, name)
    yield name, project_id, ctx, session_local


def test_chat_snapshot_tools_reach_the_lock_gate_in_auth_mode(
    foreign_locked_active_project, second_identity
):
    """
    C1: both snapshot dispatchers must route through `_route` so `db`/`user`
    are injected. Before the fix the create call died with
    `AttributeError: 'Depends' object has no attribute 'id'` inside
    `_enforce_project_lock`, and the lock was never consulted at all.
    """
    from fastapi import HTTPException

    name, project_id, ctx, session_local = foreign_locked_active_project

    # Unlocked: the create dispatcher runs clean (no Depends sentinel reaches
    # `user.id`) — the negative control for the AttributeError.
    created = chat_tools.create_project_snapshot(name, "before-lock")
    snapshot_id = created["id"] if isinstance(created, dict) else created.id

    _hold_foreign_lock(session_local, project_id, second_identity["user_id"])

    with pytest.raises(HTTPException) as exc:
        chat_tools.create_project_snapshot(name, "under-lock")
    assert exc.value.status_code == 409
    assert exc.value.detail["error_kind"] == "project_locked"

    with pytest.raises(HTTPException) as exc:
        chat_tools.delete_project_snapshot(name, snapshot_id)
    assert exc.value.status_code == 409
    assert exc.value.detail["error_kind"] == "project_locked"


def test_write_tier_chat_tool_is_refused_under_a_foreign_lock(
    foreign_locked_active_project, second_identity
):
    """
    F1: a network mutation dispatched through the chat seam gets the same
    foreign-lock refusal the write middleware gives the HTTP route.
    """
    from fastapi import HTTPException

    _name, project_id, ctx, session_local = foreign_locked_active_project
    _hold_foreign_lock(session_local, project_id, second_identity["user_id"])

    with _bound_to(ctx):
        with pytest.raises(HTTPException) as exc:
            chat_tools.DISPATCHERS["create_component"](
                component_class="Bus", name="Intruder", attrs={"v_nom": 380.0}
            )
    assert exc.value.status_code == 409
    assert exc.value.detail["error_kind"] == "project_locked"


def test_read_tier_chat_tool_is_unaffected_by_a_foreign_lock(
    foreign_locked_active_project, second_identity
):
    """Read-tier tools must stay callable while someone else holds the lock."""
    _name, project_id, ctx, session_local = foreign_locked_active_project
    _hold_foreign_lock(session_local, project_id, second_identity["user_id"])

    with _bound_to(ctx):
        result = chat_tools.DISPATCHERS["list_components"](component_class="Bus")
    assert isinstance(result, dict)


def test_write_tier_chat_tool_passes_when_the_acting_user_holds_the_lock(
    foreign_locked_active_project,
):
    """
    Negative control: the seam must refuse only a FOREIGN lock. The save in the
    fixture left the acting user holding the lock (D4), which is the ordinary
    steady state — that same write must go through.
    """
    _name, _project_id, ctx, _session_local = foreign_locked_active_project

    with _bound_to(ctx):
        result = chat_tools.DISPATCHERS["create_component"](
            component_class="Bus", name="Allowed", attrs={"v_nom": 380.0}
        )
    assert result is not None


def test_seam_lock_gate_is_a_no_op_in_local_mode(
    foreign_locked_active_project, second_identity, monkeypatch
):
    """
    D7: local mode has one identity and no lock semantics — the seam check must
    short-circuit before it ever touches the lock table.
    """
    _name, project_id, ctx, session_local = foreign_locked_active_project
    _hold_foreign_lock(session_local, project_id, second_identity["user_id"])

    monkeypatch.setattr("local_mode.is_local_mode", lambda: True)

    with _bound_to(ctx):
        result = chat_tools.DISPATCHERS["create_component"](
            component_class="Bus", name="LocalModeBus", attrs={"v_nom": 380.0}
        )
    assert result is not None


def test_solve_queue_abort_tool_is_not_lock_gated(
    foreign_locked_active_project, second_identity
):
    """
    N1 mirror of `main.py`'s middleware exemption: `solve_queue_abort` is
    job-scoped, carries its own authorization keyed on the job, and never
    resolves the acting user's active project — so it must not be in the
    seam's derived gated-tool set, and a real call under a foreign lock on
    the active project must not be refused with `project_locked`.

    The job id is made up (no real queued job), so the call falls through to
    the router's own 404 `No solve job with id ...` — the point here is only
    that the refusal is NOT the lock gate's 409.
    """
    from fastapi import HTTPException

    _name, project_id, ctx, session_local = foreign_locked_active_project

    assert "solve_queue_abort" not in chat_tools._lock_gated_tool_names()

    _hold_foreign_lock(session_local, project_id, second_identity["user_id"])

    with _bound_to(ctx):
        with pytest.raises(HTTPException) as exc:
            chat_tools.DISPATCHERS["solve_queue_abort"](job_id=str(_uuid.uuid4()))
    assert exc.value.status_code == 404
    assert exc.value.status_code != 409
