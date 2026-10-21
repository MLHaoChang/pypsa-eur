"""
P19 — Energy Hub project templates (plan 2026-09-26).

Each template must be usable straight away: feasible, tagged, carrying the
outage data its archetype needs, and arriving in a new project with its
stress registry, solver settings (VOLL) and study metadata.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pandas as pd
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
TPL_DIR = BACKEND / "project_templates"
sys.path.insert(0, str(TPL_DIR))
import eh_templates as T  # noqa: E402

IDS = sorted(T.BUILDERS)
#: The templates that carry the Energy Hub adequacy study: a generation-only site (the IPP) has
#: no load to make adequate, so the study's boundary refuses it by design (typed, never a crash).
STUDY_IDS = [t for t in IDS if not T.TEMPLATE_META.get(t, {}).get("generation_only")]


def _multi(n):
    return n.snapshots.nlevels > 1


def _optimize(n):
    """Solve a copy; a staged template is multi-period (its own solver config)."""
    probe = n.copy()
    if _multi(n):
        # PyPSA's Network.copy() drops a multi-period snapshot index's name,
        # which a storage unit's spillage variables then cannot select on
        probe.snapshots.name = "snapshot"
    from services.adequacy.energization import apply_energization

    apply_energization(probe)                     # as run_simulation does
    # A committable process unit is planned linearised, as the app does (process_uc auto).
    from services.solver.process_uc import planning_uc_kwargs

    status, cond = probe.optimize(solver_name="highs", multi_investment_periods=_multi(n),
                                  **planning_uc_kwargs(probe, None))
    return probe, status, cond


def _year_sums(n, column):
    """Each modelled year's weighting sums to 8760 h (one per period if staged)."""
    w = n.snapshot_weightings[column]
    return list(w.groupby(level=0).sum()) if _multi(n) else [float(w.sum())]


@pytest.mark.parametrize("tid", IDS)
def test_template_is_feasible_tagged_and_mttr_safe(tid):
    n = T.BUILDERS[tid]()
    _probe, status, cond = _optimize(n)
    assert (status, cond) == ("ok", "optimal")
    assert all(abs(x - 8760.0) < 1e-6 for x in _year_sums(n, "generators"))
    assert n.buses["eh_poc"].dtype == bool and n.buses["eh_poc"].any()
    # A generation-only site (the IPP) has no load, so no critical bus (its meta says so).
    assert n.buses["eh_critical"].any() or tid not in STUDY_IDS
    assert (n.links["eh_role"] == "grid_import").sum() >= 1
    for df in (n.generators, n.links):
        if "mttr_hours" in df.columns:
            assert float(df["mttr_hours"].max(skipna=True)) <= T.HOURS


@pytest.mark.parametrize("tid", STUDY_IDS)
def test_readiness_predicts_every_default_stage_runs(tid):
    from services.adequacy.eh_readiness import eh_readiness
    from services.adequacy.eh_study_runner import (
        _PACK_FACTORY,
        apply_pack_overrides,
    )
    meta = T.TEMPLATE_META[tid]
    pack = apply_pack_overrides(_PACK_FACTORY[meta["recommended_archetype"]](),
                                meta["pack_overrides"])
    ready = eh_readiness(T.BUILDERS[tid](), pack, budget_solves=30,
                         voll=T.SOLVER_CONFIG["voll"],
                         dtc_attribution=meta["dtc_attribution"])
    requested = [r for r in ready["stages"] if r["prediction"] != "not_requested"]
    assert requested and all(r["prediction"] == "run" for r in requested), requested
    assert ready["mc_boundary"]["ok"] is True
    assert ready["warnings"] == []


@pytest.mark.parametrize("tid", IDS)
def test_committed_sidecars_match_the_builder(tid, tmp_path):
    """The committed JSON is what _build.py writes (no drift)."""
    T.write_sidecars(tmp_path, tid)
    for f in T.SIDECAR_FILES:
        assert (TPL_DIR / tid / f).read_text() == (tmp_path / f).read_text(), f


def test_sidecar_schema_and_registry_are_valid():
    from services.adequacy import stress
    assert T.SCHEMA == stress.SCHEMA
    for tid in IDS:
        stress._validate(T.STRESS_SCENARIOS[tid])
        meta = T.TEMPLATE_META[tid]
        assert meta["id"] == tid and meta["provenance"]


def test_every_template_is_registered_and_listed():
    from routers.projects import _TEMPLATE_DEFAULT_NAMES
    wizard = (BACKEND.parent / "frontend" / "src" / "layout"
              / "NewProjectWizard.tsx").read_text()
    for tid in IDS:
        assert _TEMPLATE_DEFAULT_NAMES[tid] == T.TEMPLATE_META[tid]["name"]
        assert f"id: '{tid}'" in wizard


def test_from_template_copies_sidecars_and_solver_settings(
        client, tmp_path, monkeypatch, tmp_projects_dir):
    from routers import projects as projects_router
    tid = "eh_datacenter"
    src = tmp_path / tid
    src.mkdir()
    T.BUILDERS[tid]().export_to_netcdf(str(src / "network.nc"))
    T.write_sidecars(src, tid)
    (src / "stray.json").write_text("{}")          # not allow-listed
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)

    r = client.post(f"/api/projects/from_template/{tid}", params={"name": "dc1"})
    assert r.status_code == 200, r.text
    name = r.json().get("name", "dc1")
    meta = client.get(f"/api/projects/{name}/eh_template")
    assert meta.status_code == 200 and meta.json()["recommended_archetype"] == \
        "weak_flexible"
    reg = client.get(f"/api/projects/{name}/stress_scenarios").json()
    assert [s["id"] for s in reg["scenarios"]] == ["heatwave", "dunkelflaute"]
    cfg = client.get("/api/simulation/solver_config").json()
    assert cfg["voll"] == 5000.0
    # untagged / non-EH projects answer 204
    r2 = client.post("/api/projects/from_template/eh_h2_hub",
                     params={"name": "h2x"})
    assert r2.status_code == 404                     # no network.nc staged


def test_chat_names_every_template():
    from routers.projects import _TEMPLATE_DEFAULT_NAMES
    from services.chat_tools_schema import TOOLS
    tool = next(t for t in TOOLS if t["name"] == "create_project_from_template")
    enum = tool["input_schema"]["properties"]["template_id"]["enum"]
    assert set(enum) == set(_TEMPLATE_DEFAULT_NAMES)


def test_eh_templates_build_on_demand_when_network_nc_is_missing(
        client, tmp_path, monkeypatch, tmp_projects_dir):
    """network.nc is a gitignored build artifact (only build-macos.sh makes
    it). The EH templates are pure builders, so a fresh checkout / dev start
    must still create them instead of 404-ing."""
    import shutil

    from routers import projects as projects_router
    shutil.copy(TPL_DIR / "eh_templates.py", tmp_path / "eh_templates.py")
    src = tmp_path / "eh_microgrid"
    src.mkdir()
    T.write_sidecars(src, "eh_microgrid")               # sidecars, no .nc
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)
    r = client.post("/api/projects/from_template/eh_microgrid",
                    params={"name": "mg_fresh"})
    assert r.status_code == 200, r.text
    links = {r["name"]: r for r in client.get("/api/network/links").json()}
    assert links["subsea_tie"]["eh_role"] == "grid_import"
    buses = {r["name"]: r for r in client.get("/api/network/buses").json()}
    assert buses["hospital"]["eh_critical"] is True
    name = r.json().get("name", "mg_fresh")
    assert client.get(f"/api/projects/{name}/eh_template").status_code == 200
    # a non-EH template still needs its prebuilt network.nc
    assert client.post("/api/projects/from_template/3bus",
                       params={"name": "x"}).status_code == 404


@pytest.mark.parametrize("tid", IDS)
def test_template_buses_start_unplaced(tid):
    """
    Visual layers plan 2, M4: a template campus opens with every bus
    UNPLACED (PyPSA's (0, 0) default, which the map reads as "no location")
    so it appears in the map's UnplacedBusesPanel and places itself around
    the user's first click — never at a placeholder in the Gulf of Guinea.
    """
    from services.network_geometry import _bus_coord
    n = T.BUILDERS[tid]()
    assert len(n.buses) >= 2
    for bus in n.buses.index:
        assert _bus_coord(n, str(bus)) is None, f"{tid}: bus {bus!r} has a real coordinate"


@pytest.mark.parametrize("tid", IDS)
def test_templates_validate_without_gen_zero_costs(tid):
    """P30 (B5): the preflight (`validate_for_run`, what POST
    /api/simulation/preflight runs) raises no `gen_zero_costs` on a template:
    its zero-cost generators are the grid supply (a price series) and the
    weather-profiled renewables."""
    from services.solver_service import SolverConfig
    from services.validation_service import validate_for_run
    cfg = SolverConfig(**T.SOLVER_CONFIG)
    codes = [i.code for i in validate_for_run(T.BUILDERS[tid](), cfg)]
    assert "gen_zero_costs" not in codes, codes


# ── S0 (plan 2026-10-07 campus island): storage integrates physical hours ──

@pytest.mark.parametrize("tid", IDS)
def test_storage_integrates_physical_hours_while_costs_still_scale_to_a_year(tid):
    """A representative week stands for a year in COST and ENERGY (objective
    and generators weightings 8760/168), but a battery's state of charge moves
    by one hour of power per hourly snapshot. PyPSA uses the ``stores``
    weighting as elapsed hours in the SoC balance, so it must be 1."""
    n = T.BUILDERS[tid]()
    for column in ("objective", "generators"):
        assert all(abs(x - 8760.0) < 1e-6 for x in _year_sums(n, column))
    # pinned directly: a template whose storage happens to sit idle in the
    # solve below would otherwise pass the behavioural check vacuously
    assert (n.snapshot_weightings["stores"] == 1.0).all()
    if n.storage_units.empty:
        return
    probe, status, cond = _optimize(n)
    assert (status, cond) == ("ok", "optimal")
    su_t = probe.storage_units_t
    # each period's first snapshot wraps cyclically within its period; skip it
    first = (n.snapshots.get_level_values(0) != pd.Index(n.snapshots.get_level_values(0)).to_series().shift().to_numpy()
             if _multi(n) else n.snapshots == n.snapshots[0])
    for s in probe.storage_units.index:
        eff_in = float(probe.storage_units.at[s, "efficiency_store"])
        eff_out = float(probe.storage_units.at[s, "efficiency_dispatch"])
        soc = su_t.state_of_charge[s].to_numpy()
        step = (eff_in * su_t.p_store[s] - su_t.p_dispatch[s] / eff_out).to_numpy()
        prev = soc[:-1]
        # one hour of power per snapshot (a period's first wraps; skip it)
        gap = abs(soc[1:] - prev - step[1:])[~first[1:]]
        assert gap.max() < 1e-4, s


def test_the_datacenter_ups_carries_the_it_load_it_protects():
    """Owner decision (plan S0): 40 MW for about 36 MW of IT, with a realistic
    15-minute autonomy that still covers the island plan's 600 s bridge."""
    n = T.BUILDERS["eh_datacenter"]()
    ups = n.storage_units.loc["ups_battery"]
    it_peak = float(n.loads_t.p_set["it_load"].max())
    assert float(ups["p_nom"]) >= it_peak
    assert float(ups["max_hours"]) == 0.25
    bridge_s = T.ISLAND_CONFIG["eh_datacenter"]["requirements"]["bridge_s"]["value"]
    energy = float(ups["p_nom"]) * float(ups["max_hours"]) * float(ups["efficiency_dispatch"])
    assert energy >= it_peak * bridge_s / 3600.0
