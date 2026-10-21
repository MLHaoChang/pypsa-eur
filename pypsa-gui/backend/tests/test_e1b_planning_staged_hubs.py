"""E1b: the staged H2 hub and microgrid built for any entered planning years.

The staged data center's planning entry (E1a, ``tests/test_e1_planning_staged_dc.py``)
on ``eh_h2_hub_staged`` and ``eh_microgrid_staged``. The gatekeeper's E1
conditions and its E1b review (2026-10-21):

1. weights only through ``auto_discount_periods``: Σ ipw.years equals the
   case's ``analysis_years``;
2. the default build is unchanged (network canonically, sidecars by bytes),
   with no planning and at the native planning;
3. a growth value the example has no figure for is a placeholder (the
   example's same-stage value, never interpolated): an assumed ledger row
   and an investment-case flag. The microgrid's load scale is an electric
   peak input, so its period reads unknown; the H2 offtake is a load on the
   H2 bus, so the electric peak check is as it was;
4. the case's horizon is the planning horizon;
5. a start before the CO2 path's first year holds the first point
   (ledgered "assumed, held from 2027"), on the template's own path only;
   the case and the baseline read that one path.
"""
from __future__ import annotations

import json
import pathlib
import queue
import sys
import threading

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
TPL_DIR = BACKEND / "project_templates"
sys.path.insert(0, str(TPL_DIR))
import eh_templates as T  # noqa: E402

H2, MG = "eh_h2_hub_staged", "eh_microgrid_staged"
IDS = (H2, MG)
NATIVE = T.Planning((2027, 2030, 2035), 2045)
#: Shifted and uneven, starting before the CO2 path (2027): periods of 3, 11 and 10 years.
UNEVEN = {"start_year": 2026, "end_year": 2050, "dates": [2029, 2040]}
#: The example's spacing shifted to 2029: every value a placeholder.
SHIFTED = {"start_year": 2029, "end_year": 2047}


def _plan(tid, raw):
    return T.planning_spec(tid, raw)


def _canonical(n) -> dict:
    out = {"snapshots": [str(s) for s in n.snapshots],
           "periods": list(map(int, n.investment_periods)),
           "ipw": n.investment_period_weightings.to_dict(),
           "sw": n.snapshot_weightings.to_dict()}
    for c in n.iterate_components():
        out[c.name] = c.static.sort_index(axis=0).sort_index(axis=1).to_json()
        for attr, df in c.dynamic.items():
            if not df.empty:
                out[f"{c.name}_t.{attr}"] = df.sort_index(axis=1).to_json()
    return out


# ── the planning entry ──────────────────────────────────────────────────────

@pytest.mark.parametrize("tid", IDS)
def test_both_take_planning_at_the_examples_years(tid):
    assert T.NATIVE_PLANNING[tid] == NATIVE
    assert _plan(tid, SHIFTED) == T.Planning((2029, 2032, 2037), 2047)
    with pytest.raises(T.PlanningError, match="single-year example"):
        _plan(tid, {"start_year": 2030, "end_year": 2030})


# ── 2. the default build is unchanged ───────────────────────────────────────

@pytest.mark.parametrize("tid", IDS)
def test_the_native_planning_builds_the_default_network(tid):
    assert _canonical(T.BUILDERS[tid](planning=NATIVE)) == _canonical(T.BUILDERS[tid]())


@pytest.mark.parametrize("planning", [None, NATIVE], ids=["none", "native"])
@pytest.mark.parametrize("tid", IDS)
def test_the_default_sidecars_are_the_shipped_bytes(tmp_path, tid, planning):
    T.write_sidecars(tmp_path, tid, planning)
    for name in (*T.SIDECAR_FILES, T.ISLAND_FILE):
        assert (tmp_path / name).read_bytes() == (TPL_DIR / tid / name).read_bytes(), name


def test_the_co2_path_is_the_shipped_one_unless_the_start_is_before_it():
    """The point is added only when the start is before the path's first year."""
    path = T.solver_config(H2)["commercial"]["co2_price"]
    assert T.solver_config(H2, NATIVE)["commercial"]["co2_price"] == path
    late = T.solver_config(H2, _plan(H2, SHIFTED))["commercial"]["co2_price"]
    assert late == path
    assert {int(y): v for y, v in path["eur_per_t_by_year"].items()} == {2027: 80.0, 2030: 120.0}


# ── 1 and 4. weights and the case's horizon ─────────────────────────────────

@pytest.mark.parametrize("tid", IDS)
def test_the_period_years_are_the_cases_operating_years(tid):
    p = _plan(tid, UNEVEN)
    n = T.BUILDERS[tid](planning=p)
    ipw = n.investment_period_weightings
    assert list(n.investment_periods) == [2026, 2029, 2040]
    assert list(ipw["years"]) == [3.0, 11.0, 10.0] == list(ipw["objective"])
    cfg = T.solver_config(tid, p)
    fin = cfg["finance"]
    assert cfg["auto_discount_periods"] is True
    assert ipw["years"].sum() == fin["analysis_years"] == 24
    assert fin["financial_close"] == "2025-01-01"
    assert set(fin["cod_by_asset"].values()) == {"2026-01-01"}
    covered = [y for period, span in p.period_years.items() for y in range(period, period + span)]
    assert list(range(2026, 2026 + fin["analysis_years"])) == covered
    # Every candidate of the plan has a degradation entry (the engine never reads 0).
    new = [a for a in [*n.generators.index, *n.storage_units.index] if "_new_" in a]
    assert new and {a.rsplit("_", 1)[1] for a in new} == {"2026", "2029", "2040"}
    assert set(new) <= set(fin["degradation_by_asset"])


def test_the_h2_tariff_is_valid_from_the_start_year_and_held_ledgered():
    """As the staged DC's (gatekeeper review of #540): a start before the
    tariff's 2027 holds its rates, with an assumed ledger row."""
    p = _plan(H2, UNEVEN)
    cfg = T.solver_config(H2, p)
    assert cfg["commercial"]["import_tariff"]["valid_from"] == "2026-01-01"
    row = next(r for r in T.template_meta(H2, p)["ledger"]
               if r["item"] == "import tariff before its year")
    assert row["source"] == "assumed" and row["note"].startswith("assumed, held from 2027")
    assert not any(r["item"] == "import tariff before its year"
                   for r in T.template_meta(H2, _plan(H2, SHIFTED))["ledger"])


@pytest.mark.parametrize("tid", IDS)
def test_the_ledger_states_the_entered_period_and_the_money_year(tid):
    meta = T.template_meta(tid, _plan(tid, UNEVEN))
    rows = {r["item"]: r for r in meta["ledger"]}
    assert rows["planning period"]["value"] == "2026 / 2029 / 2040 → 2050 (entered)"
    assert "does not re-date" in rows["money year"]["note"]
    assert "close 2025-01-01, to 2050" in rows["finance inputs"]["value"]
    assert meta["period_years"] == {"2026": 3, "2029": 11, "2040": 10}


# ── 5. the CO2 path holds its first point ───────────────────────────────────

def test_a_start_before_the_co2_path_holds_its_first_point_ledgered():
    p = _plan(H2, UNEVEN)
    co2 = T.solver_config(H2, p)["commercial"]["co2_price"]
    assert {int(y): v for y, v in co2["eur_per_t_by_year"].items()} == {
        2026: 80.0, 2027: 80.0, 2030: 120.0}
    row = next(r for r in T.template_meta(H2, p)["ledger"]
               if r["item"] == "CO2 price before the path")
    assert row["source"] == "assumed" and row["note"].startswith("assumed, held from 2027")
    assert not any(r["item"] == "CO2 price before the path"
                   for r in T.template_meta(H2, _plan(H2, SHIFTED))["ledger"])


def test_the_baseline_prices_the_held_point_as_the_case_does():
    """One source: the commercial config's path prices the baseline's grey-H2
    supply (``baseline.reference_supplies``) and the case's CO2 lines
    (``finance_case._price_co2``). The 2026 period's supply carries the held
    80 EUR/t, as 2027 does; without the point it would carry no CO2."""
    from routers.projects import _solver_config_from_dict
    from services.results import baseline as Bl

    p = _plan(H2, UNEVEN)
    cfg = _solver_config_from_dict(T.solver_config(H2, p))
    (sup,) = Bl.reference_supplies(T.BUILDERS[H2](planning=p), cfg)
    gas = 2.0 * 1000.0 / 33.33                                   # EUR/MWh before CO2
    co2 = {y: sup["mc"][y] - gas for y in (2026, 2029, 2040)}
    assert co2[2026] == pytest.approx(co2[2029])                  # 80 EUR/t in both
    assert co2[2040] == pytest.approx(co2[2026] * 120.0 / 80.0)
    assert co2[2026] > 0


# ── 3. placeholders ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("tid", IDS)
def test_the_native_years_carry_no_placeholder(tid):
    from services.adequacy import site_peak as SP

    for meta in (T.template_meta(tid), T.template_meta(tid, NATIVE)):
        assert "growth_inputs" not in meta
        assert SP.unset_periods(meta) == {}


def test_a_shifted_h2_offtake_is_a_placeholder_by_stage():
    meta = T.template_meta(H2, _plan(H2, SHIFTED))
    assert meta["growth_inputs"] == [
        {"kind": "h2_offtake_mw", "year": y, "value": v, "set": False, "example_value": v,
         "source": "unset"} for y, v in ((2029, 10.0), (2032, 18.0), (2037, 25.0))]
    rows = [r for r in meta["ledger"] if r["item"].startswith("placeholder")]
    assert [r["item"] for r in rows] == [f"placeholder h2_offtake_mw {y}"
                                         for y in (2029, 2032, 2037)]
    for r in rows:
        assert r["source"] == "assumed" and r["section"] == "loads"
        assert "investment case flags it" in r["note"] and "peak check" not in r["note"]
    assert meta["description"].startswith(
        "The hydrogen hub planned from a projected H2 demand: the offtake grows 10 → 18 → 25 MW "
        "over 2029, 2032 and 2037")


def test_an_example_year_keeps_its_value_set():
    meta = T.template_meta(MG, _plan(MG, {"start_year": 2027, "end_year": 2045,
                                          "dates": [2030, 2038]}))
    rows = {g["year"]: g for g in meta["growth_inputs"]}
    assert rows[2027]["set"] and rows[2030]["set"]
    assert rows[2038] == {"kind": "load_scale", "year": 2038,
                          "value": {"residential": 1.4, "hospital_load": 1.2}, "set": False,
                          "example_value": {"residential": 1.4, "hospital_load": 1.2},
                          "source": "unset"}
    row = next(r for r in meta["ledger"] if r["item"] == "placeholder load_scale 2038")
    assert row["value"] == "residential ×1.4, hospital_load ×1.2"
    assert "it takes its stage 3 value, residential ×1.4, hospital_load ×1.2 (" in row["note"]
    assert "peak check" in row["note"] and "stays unknown" in row["note"]


def test_an_unset_h2_offtake_leaves_the_electric_peak_as_it_was():
    """The gatekeeper's E1b change 1: the H2 offtake is not an electric load."""
    from services.adequacy import site_peak as SP

    p = _plan(H2, SHIFTED)
    unset = SP.unset_periods(T.template_meta(H2, p))
    assert unset == {}
    n = T.BUILDERS[H2](planning=p)
    isl = T.island_config(H2, p)
    assert SP.peak_check(n, isl, unset=unset) == SP.peak_check(n, isl)


def test_an_unset_load_scale_makes_the_microgrid_peak_unknown():
    from services.adequacy import site_peak as SP

    assert "load_scale" in SP.PEAK_INPUTS and "h2_offtake_mw" not in SP.PEAK_INPUTS
    assert set(SP.PEAK_INPUTS) == set(T._PEAK_KINDS)
    p = _plan(MG, {"start_year": 2027, "end_year": 2045, "dates": [2030, 2038]})
    unset = SP.unset_periods(T.template_meta(MG, p))
    assert unset == {"2038": ["load_scale"]}
    rows = {r["period"]: r for r in SP.peak_check(T.BUILDERS[MG](planning=p),
                                                  T.island_config(MG, p), unset=unset)}
    assert rows["2038"]["headroom_mw"] is None and rows["2038"]["unset"] == ["load_scale"]
    assert rows["2030"]["unset"] == []


@pytest.mark.parametrize("tid, flags", [
    (H2, ("growth_placeholder:h2_offtake_mw_2029", "growth_placeholder:h2_offtake_mw_2032",
          "growth_placeholder:h2_offtake_mw_2037")),
    (MG, ("growth_placeholder:load_scale_2029", "growth_placeholder:load_scale_2032",
          "growth_placeholder:load_scale_2037")),
])
def test_the_investment_case_flags_each_placeholder_in_plain_words(tid, flags):
    from routers import results as R
    from services.finance.baseline_text import flag_text

    assert R._growth_flags_of(T.template_meta(tid, _plan(tid, SHIFTED))) == flags
    words = flag_text(flags[0])
    assert words.startswith(("The H2 offtake in 2029", "The load growth in 2029"))
    assert "placeholder" in words


# ── the island sidecars follow the periods ──────────────────────────────────

@pytest.mark.parametrize("tid", IDS)
def test_the_island_sidecar_names_the_plans_candidates(tid):
    from services.adequacy.island_config import validate

    p = _plan(tid, UNEVEN)
    isl = T.island_config(tid, p)
    validate(isl)
    n = T.BUILDERS[tid](planning=p)
    for u in isl["units"]:
        assert u in n.generators.index or u in n.storage_units.index \
            or u in n.loads.index or u in n.links.index, u
    assert [s for s in isl["requirements"]["ride_through_storage"] if "_new_" in s] == [
        f"bess_new_{y}" for y in p.years]


# ── it builds, passes the rules and solves ──────────────────────────────────

def _solve(tid, planning, monkeypatch, tmp_path):
    """As run_simulation solves a created project (the island rows on where the
    template ships them): an island sidecar naming another plan's units refuses."""
    import routers.simulation as sim_router
    from routers.projects import _solver_config_from_dict
    from services.adequacy.island_config import SIDECAR_NAME, load_island_config
    from services.pypsa_service import PyPSAService
    from services.solver import island as I
    from services.solver_service import run_simulation
    from tests.conftest import install_network_into_backend

    T.write_sidecars(tmp_path, tid, planning)
    isl = load_island_config(tmp_path / SIDECAR_NAME)
    isl["cuts"] = []
    monkeypatch.setattr(I, "load_project_island", lambda: isl)
    install_network_into_backend(T.BUILDERS[tid](planning=planning))
    cfg = _solver_config_from_dict(T.solver_config(tid, planning))
    sim_router._state["solver_config"] = cfg
    n = PyPSAService.get_network()
    state: dict = {}
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                  queue.SimpleQueue(), state_update=lambda **k: state.update(k))
    assert status in ("ok", "optimal"), (tid, status, cond)
    return n, cfg, state


@pytest.mark.parametrize("tid", IDS)
def test_it_passes_the_template_rules(tid):
    from services.process_template_rules import refusals

    for raw in (UNEVEN, SHIFTED):
        p = _plan(tid, raw)
        assert refusals(T.BUILDERS[tid](planning=p), T.solver_config(tid, p)) == [], raw


@pytest.mark.live_solve
def test_the_microgrid_solves_with_its_island_rows_at_shifted_years(reset_backend, monkeypatch,
                                                                   tmp_path):
    n, _cfg, _state = _solve(MG, _plan(MG, SHIFTED), monkeypatch, tmp_path)
    assert list(n.investment_periods) == [2029, 2032, 2037]
    assert _cfg.island_constraints is True


@pytest.mark.live_solve
def test_the_h2_hub_solves_and_its_case_builds_from_2026(reset_backend, monkeypatch, tmp_path):
    """Uneven years from 2026: the case's CO2 lines are priced from the held
    point (no ``co2_price_year_uncovered``) and the baseline buys grey H2."""
    import routers.results as R
    from models.finance import FinanceInputs
    from services.finance.engine import run_case
    from services.results import baseline as Bl
    from services.results.finance_case import baseline_plan, build_finance_case

    p = _plan(H2, UNEVEN)
    n, cfg, state = _solve(H2, p, monkeypatch, tmp_path)
    Bl.clear_cache()
    base = Bl.solve_baseline(n, cfg, baseline_plan(n, cfg))
    case = build_finance_case(n, cfg, FinanceInputs.model_validate(cfg.finance),
                              result_df=R._result_df, lost_load=state.get("last_lost_load"),
                              baseline=base)
    Bl.clear_cache()
    assert "counterfactual_buys:h2_offtake" in case.flags
    assert not any(f.startswith("co2_price_year_uncovered") for f in case.flags)
    assert run_case(case).metrics["project_pre_tax_npv"] is not None


# ── create from the template with ``planning`` ──────────────────────────────

@pytest.mark.parametrize("tid", IDS)
def test_create_builds_the_entered_years_and_writes_their_sidecars(
        tid, client, tmp_projects_dir, bound_ctx):
    import pypsa

    from services import network_lengths

    r = client.post(f"/api/projects/from_template/{tid}", params={"name": f"{tid}_e1b"},
                    json={"planning": SHIFTED})
    assert r.status_code == 200, r.text
    with bound_ctx(client):
        d = network_lengths.active_project_dir()
    n = pypsa.Network(str(d / "network.nc"))
    assert list(n.investment_periods) == [2029, 2032, 2037]
    p = _plan(tid, SHIFTED)
    for name, want in ((T.META_FILE, T.template_meta(tid, p)),
                       (T.SOLVER_FILE, T.solver_config(tid, p)),
                       (T.ISLAND_FILE, T.island_config(tid, p))):
        assert json.loads((d / name).read_text()) == json.loads(json.dumps(want)), name
    body = client.get(f"/api/projects/{tid}_e1b/flow").json()
    codes = {i["code"] for s in body["sections"] for i in s["items"]}
    kind = "h2_offtake_mw" if tid == H2 else "load_scale"
    assert f"assumed_placeholder {kind} 2032" in codes
