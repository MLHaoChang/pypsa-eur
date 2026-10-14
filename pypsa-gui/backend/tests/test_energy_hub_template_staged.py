"""The staged-growth data centre template (E2E demo plan 2026-10-14, slice 8).

The single-year campus planned from a projected load: three investment
periods (2027, 2030, 2035), the IT load ramping 12 → 24 → 36 MW, the utility
energizing the 40 MW connection in steps (20 MW until 2030), and one genset
and one battery candidate per period with that period as its build year. The
single-year template is untouched (a golden digest of its build).
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
TPL_DIR = BACKEND / "project_templates"
sys.path.insert(0, str(TPL_DIR))
import eh_templates as T  # noqa: E402

TID = "eh_datacenter_staged"
YEARS = [2027, 2030, 2035]


def _digest(n) -> str:
    h = hashlib.sha256()
    for c in sorted(n.components.keys()):
        comp = n.components[c]
        if len(comp.static):
            h.update(c.encode())
            h.update(comp.static.sort_index(axis=1).to_json(
                orient="split", double_precision=10).encode())
        for attr, ts in sorted(comp.dynamic.items()):
            if len(ts.columns):
                h.update(f"{c}.{attr}".encode())
                h.update(ts.sort_index(axis=1).to_json(
                    orient="split", double_precision=10).encode())
    h.update(n.snapshot_weightings.to_json(orient="split", double_precision=10).encode())
    return h.hexdigest()[:16]


@pytest.fixture(scope="module")
def staged():
    return T.BUILDERS[TID]()


@pytest.fixture(scope="module")
def solved(staged):
    probe = staged.copy()
    probe.snapshots.name = "snapshot"      # PyPSA's copy() drops the MultiIndex's name
    status, cond = probe.optimize(solver_name="highs", multi_investment_periods=True)
    assert (status, cond) == ("ok", "optimal")
    return probe


def test_the_single_year_template_is_unchanged():
    # the digest of master's build_eh_datacenter() before staging was added
    assert _digest(T.build_eh_datacenter()) == "921e5526675e79eb"


def test_three_periods_each_a_year_of_hours_with_their_own_length(staged):
    assert list(staged.investment_periods) == YEARS
    w = staged.snapshot_weightings
    for col in ("objective", "generators"):
        assert list(w[col].groupby(level=0).sum()) == pytest.approx([8760.0] * 3)
    assert (w["stores"] == 1.0).all()
    assert list(staged.investment_period_weightings["years"]) == [3.0, 5.0, 10.0]


def test_the_it_load_and_cooling_ramp_with_the_projected_load(staged):
    flat = T.build_eh_datacenter()
    p = staged.loads_t.p_set
    for y, it_mw, _pue in T.DC_PHASES:
        scale = it_mw / 36.0
        for load in ("it_load", "cooling"):
            assert p.loc[y, load].to_numpy() == pytest.approx(
                flat.loads_t.p_set[load].to_numpy() * scale), (y, load)
    assert p.loc[2035, "it_load"].max() == pytest.approx(flat.loads_t.p_set["it_load"].max())


def test_the_import_follows_the_energization_schedule(staged):
    cap = float(staged.links.at["grid_import", "p_nom"])
    pmax = staged.links_t.p_max_pu["grid_import"]
    for y in YEARS:
        assert (pmax.loc[y] * cap).to_numpy() == pytest.approx(T.DC_ENERGIZATION_MW[y])


def test_one_candidate_per_period_that_outlives_the_horizon(staged):
    gens = staged.generators[staged.generators.p_nom_extendable]
    bess = staged.storage_units[staged.storage_units.p_nom_extendable]
    assert sorted(gens.build_year) == YEARS and sorted(bess.build_year) == YEARS
    assert "genset_new" not in staged.generators.index and "bess_new" not in staged.storage_units.index
    horizon_end = YEARS[-1] + T.DC_PERIOD_YEARS[YEARS[-1]]          # 2045
    for df in (gens, bess):
        assert ((df.build_year + df.lifetime) >= horizon_end).all()


def test_the_plan_solves_and_respects_the_20_mw_cap_in_2027(solved):
    flow = solved.links_t.p0["grid_import"]
    assert flow.loc[2027].max() <= 20.0 + 1e-6
    assert flow.loc[2035].max() <= 40.0 + 1e-6
    # a candidate built in a later period produces nothing before it
    for g in ("genset_new_2030", "genset_new_2035"):
        y = int(g.rsplit("_", 1)[1])
        early = solved.generators_t.p[g].loc[[p for p in YEARS if p < y]]
        assert float(early.abs().max()) == pytest.approx(0.0, abs=1e-6)


def test_the_meta_and_solver_config_say_staged_and_multi_period():
    meta = T.TEMPLATE_META[TID]
    assert [r["year"] for r in meta["projected_load"]] == YEARS
    assert [r["available_import_mw"] for r in meta["energization"]] == [20.0, 40.0, 40.0]
    assert all(r["source"] == "assumed" for r in meta["energization"])
    assert meta["ledger"] and meta["ledger"][0]["source"] == "assumed"
    cfg = T.solver_config(TID)
    assert cfg["multi_investment_periods"] is True and cfg["auto_discount_periods"] is True
    assert cfg["voll"] == T.SOLVER_CONFIG["voll"]


def test_the_island_sidecar_names_every_per_period_candidate(staged):
    cfg = T.ISLAND_CONFIG[TID]
    for y in YEARS:
        assert f"genset_new_{y}" in cfg["units"] and f"bess_new_{y}" in cfg["units"]
    assert "genset_new" not in cfg["units"] and "bess_new" not in cfg["units"]
    assert set(cfg["requirements"]["ride_through_storage"]) == {f"bess_new_{y}" for y in YEARS}
    named = (set(staged.generators.index) | set(staged.storage_units.index)
             | set(staged.links.index) | set(staged.loads.index))
    assert set(cfg["units"]) <= named
    # the single-year sidecar is not mutated by the copy
    assert "genset_new" in T.ISLAND_CONFIG["eh_datacenter"]["units"]


def test_the_committed_sidecars_are_written(staged):
    d = TPL_DIR / TID
    meta = json.loads((d / T.META_FILE).read_text())
    assert meta["id"] == TID
