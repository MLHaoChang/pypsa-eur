"""E1c: the staged ammonia hub built for any entered planning years.

The staged H2 hub's planning entry (E1b, ``tests/test_e1b_planning_staged_hubs.py``) on
``eh_ammonia_staged``, under the gatekeeper's E1 conditions:

1. weights only through ``auto_discount_periods``: Σ ipw.years equals the case's ``analysis_years``;
2. the default build is unchanged (network canonically, sidecars by bytes), with no planning and at the
   native planning;
3. a delivery the example has no figure for is a placeholder (the example's same-stage value, never
   interpolated): an assumed ledger row and an investment-case flag. The NH3 delivery is a load on the
   NH3 bus, so the electric peak check is as it was;
4. the case's horizon is the planning horizon;
5. a start before the CO2 path's first year holds the first point (ledgered "assumed, held from 2027"):
   the plan's grey-NH3 purchase, the case and the baseline read that one path.
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

NH3 = "eh_ammonia_staged"
NATIVE = T.Planning((2027, 2030, 2035), 2045)
#: Shifted and uneven, starting before the CO2 path (2027): periods of 3, 11 and 10 years.
UNEVEN = {"start_year": 2026, "end_year": 2050, "dates": [2029, 2040]}
#: The example's spacing shifted to 2029: every delivery a placeholder.
SHIFTED = {"start_year": 2029, "end_year": 2047}


def _plan(raw):
    return T.planning_spec(NH3, raw)


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


def _buy_cost(n) -> dict[int, float]:
    mc = n.generators_t.marginal_cost["__buy_nh3"]
    return {int(y): float(v) for y, v in mc.groupby(level=0).first().items()}


# ── the planning entry ──────────────────────────────────────────────────────

def test_it_takes_planning_at_the_examples_years():
    assert T.NATIVE_PLANNING[NH3] == NATIVE
    assert _plan(SHIFTED) == T.Planning((2029, 2032, 2037), 2047)
    with pytest.raises(T.PlanningError, match="single-year example"):
        _plan({"start_year": 2030, "end_year": 2030})


def test_the_module_names_keep_the_examples_values():
    """The 08 tests and ticket 09 read them."""
    import eh_ammonia as A

    assert A.YEARS == (2027, 2030, 2035) and A.HORIZON_END == 2045
    assert A.DELIVERY_TPD == {2027: 50.0, 2030: 100.0, 2035: 150.0}
    assert A.co2_price(2027) == 80.0 and A.co2_price(2035) == 120.0


# ── 2. the default build is unchanged ───────────────────────────────────────

def test_the_native_planning_builds_the_default_network():
    # The first build in a process leaves ``efficiency2`` NaN on the links without a bus2 (1.0 on
    # every later build; as on master): compare two later builds.
    T.BUILDERS[NH3]()
    assert _canonical(T.BUILDERS[NH3](planning=NATIVE)) == _canonical(T.BUILDERS[NH3]())


@pytest.mark.parametrize("planning", [None, NATIVE], ids=["none", "native"])
def test_the_default_sidecars_are_the_shipped_bytes(tmp_path, planning):
    T.write_sidecars(tmp_path, NH3, planning)
    assert T.island_config(NH3, planning) is None
    for name in T.SIDECAR_FILES:
        assert (tmp_path / name).read_bytes() == (TPL_DIR / NH3 / name).read_bytes(), name


# ── 1 and 4. weights and the case's horizon ─────────────────────────────────

def test_the_period_years_are_the_cases_operating_years():
    p = _plan(UNEVEN)
    n = T.BUILDERS[NH3](planning=p)
    ipw = n.investment_period_weightings
    assert list(n.investment_periods) == [2026, 2029, 2040]
    assert list(ipw["years"]) == [3.0, 11.0, 10.0] == list(ipw["objective"])
    cfg = T.solver_config(NH3, p)
    fin = cfg["finance"]
    assert cfg["auto_discount_periods"] is True
    assert ipw["years"].sum() == fin["analysis_years"] == 24
    assert fin["financial_close"] == "2025-01-01"
    assert set(fin["cod_by_asset"].values()) == {"2026-01-01"}
    assert "haber_bosch" in fin["cod_by_asset"]
    new = [a for a in [*n.generators.index, *n.storage_units.index] if "_new_" in a]
    assert new and {a.rsplit("_", 1)[1] for a in new} == {"2026", "2029", "2040"}
    assert set(new) <= set(fin["degradation_by_asset"])
    assert set(n.stores.loc[["h2_storage", "nh3_tank"], "build_year"]) == {2026}


def test_the_delivery_follows_the_stage():
    """The example's same-stage tonnage, never interpolated: 50 / 100 / 150 t/d."""
    p = _plan(UNEVEN)
    n = T.BUILDERS[NH3](planning=p)
    tpd = n.loads_t.p_set["nh3_delivery"].groupby(level=0).first() * 24.0 / 5.166
    assert tpd.round(6).to_dict() == {2026: 50.0, 2029: 100.0, 2040: 150.0}
    assert n.generators.at["__buy_nh3", "p_nom"] == pytest.approx(150.0 / 24.0 * 5.166)


def test_the_tariff_is_valid_from_the_start_year_and_held_ledgered():
    p = _plan(UNEVEN)
    assert T.solver_config(NH3, p)["commercial"]["import_tariff"]["valid_from"] == "2026-01-01"
    row = next(r for r in T.template_meta(NH3, p)["ledger"]
               if r["item"] == "import tariff before its year")
    assert row["source"] == "assumed" and row["note"].startswith("assumed, held from 2027")
    assert not any(r["item"] == "import tariff before its year"
                   for r in T.template_meta(NH3, _plan(SHIFTED))["ledger"])


def test_the_ledger_states_the_entered_period_and_the_money_year():
    meta = T.template_meta(NH3, _plan(UNEVEN))
    rows = {r["item"]: r for r in meta["ledger"]}
    assert rows["planning period"]["value"] == "2026 / 2029 / 2040 → 2050 (entered)"
    assert "does not re-date" in rows["money year"]["note"]
    assert "close 2025-01-01, to 2050" in rows["finance inputs"]["value"]
    assert rows["NH3 delivery"]["value"] == "50 / 100 / 150 t/d (2026 / 2029 / 2040)"
    assert "on the example's plan (2027 / 2030 / 2035)" in rows["unit commitment in planning"]["note"]
    assert "128 EUR/MWh_NH3 at 80 EUR/t, 143 at 120 EUR/t" in rows["grey NH3 buy-in (__buy_nh3)"]["note"]
    assert meta["period_years"] == {"2026": 3, "2029": 11, "2040": 10}
    assert [e["year"] for e in meta["energization"]] == [2026, 2029, 2040]
    assert meta["description"].startswith(
        "The hydrogen hub making green ammonia for a contracted delivery of 50 → 100 → 150 t/d "
        "over 2026, 2029 and 2040")


# ── 5. the CO2 path holds its first point ───────────────────────────────────

def test_a_start_before_the_co2_path_holds_its_first_point_ledgered():
    p = _plan(UNEVEN)
    co2 = T.solver_config(NH3, p)["commercial"]["co2_price"]
    assert {int(y): v for y, v in co2["eur_per_t_by_year"].items()} == {
        2026: 80.0, 2027: 80.0, 2030: 120.0}
    row = next(r for r in T.template_meta(NH3, p)["ledger"]
               if r["item"] == "CO2 price before the path")
    assert row["source"] == "assumed" and row["note"].startswith("assumed, held from 2027")
    shifted = _plan(SHIFTED)
    assert T.solver_config(NH3, shifted)["commercial"]["co2_price"] == \
        T.solver_config(NH3)["commercial"]["co2_price"]
    assert not any(r["item"] == "CO2 price before the path"
                   for r in T.template_meta(NH3, shifted)["ledger"])


def test_the_grey_purchase_in_the_plan_carries_the_held_co2():
    """``buy_cost_eur_per_mwh``: 97 EUR/MWh plus 2 t/t at the period's CO2 price, per MWh_NH3."""
    per_t = 2.0 / 5.166
    assert _buy_cost(T.BUILDERS[NH3](planning=_plan(UNEVEN))) == pytest.approx(
        {2026: 97.0 + per_t * 80.0, 2029: 97.0 + per_t * 80.0, 2040: 97.0 + per_t * 120.0})
    assert _buy_cost(T.BUILDERS[NH3]()) == pytest.approx(
        {2027: 97.0 + per_t * 80.0, 2030: 97.0 + per_t * 120.0, 2035: 97.0 + per_t * 120.0})


# ── 3. placeholders ─────────────────────────────────────────────────────────

def test_the_native_years_carry_no_placeholder():
    from services.adequacy import site_peak as SP

    for meta in (T.template_meta(NH3), T.template_meta(NH3, NATIVE)):
        assert "growth_inputs" not in meta and "planning" not in meta
        assert SP.unset_periods(meta) == {}


def test_a_shifted_delivery_is_a_placeholder_by_stage():
    meta = T.template_meta(NH3, _plan(SHIFTED))
    assert meta["growth_inputs"] == [
        {"kind": "nh3_delivery_tpd", "year": y, "value": v, "set": False, "example_value": v,
         "source": "unset"} for y, v in ((2029, 50.0), (2032, 100.0), (2037, 150.0))]
    rows = [r for r in meta["ledger"] if r["item"].startswith("placeholder")]
    assert [r["item"] for r in rows] == [f"placeholder nh3_delivery_tpd {y}"
                                         for y in (2029, 2032, 2037)]
    for r in rows:
        assert r["source"] == "assumed" and r["section"] == "loads" and r["unit"] == "t/d"
        assert r["entity"] == "nh3_delivery"
        assert "investment case flags it" in r["note"] and "peak check" not in r["note"]
    assert all(s["note"].startswith("placeholder") for s in meta["projected_load"])


def test_an_example_year_keeps_its_value_set():
    meta = T.template_meta(NH3, _plan({"start_year": 2027, "end_year": 2045,
                                       "dates": [2030, 2038]}))
    rows = {g["year"]: g for g in meta["growth_inputs"]}
    assert rows[2027]["set"] and rows[2030]["set"]
    assert rows[2038] == {"kind": "nh3_delivery_tpd", "year": 2038, "value": 150.0,
                          "set": False, "example_value": 150.0, "source": "unset"}
    row = next(r for r in meta["ledger"] if r["item"] == "placeholder nh3_delivery_tpd 2038")
    assert "the example has no NH3 delivery for 2038; it takes its stage 3 value, 150 t/d" \
        in row["note"]


def test_an_unset_delivery_leaves_the_electric_peak_as_it_was():
    """The NH3 delivery is a load on the NH3 bus, as the H2 offtake is on the H2 bus."""
    from services.adequacy import site_peak as SP

    assert "nh3_delivery_tpd" not in SP.PEAK_INPUTS
    assert set(SP.PEAK_INPUTS) == set(T._PEAK_KINDS)
    p = _plan(SHIFTED)
    assert SP.unset_periods(T.template_meta(NH3, p)) == {}


def test_the_investment_case_flags_each_placeholder_in_plain_words():
    from routers import results as R
    from services.finance.baseline_text import flag_text

    flags = R._growth_flags_of(T.template_meta(NH3, _plan(SHIFTED)))
    assert flags == ("growth_placeholder:nh3_delivery_tpd_2029",
                     "growth_placeholder:nh3_delivery_tpd_2032",
                     "growth_placeholder:nh3_delivery_tpd_2037")
    words = flag_text(flags[0])
    assert words.startswith("The NH3 delivery in 2029 is a placeholder")


# ── it builds, passes the rules and solves ──────────────────────────────────

def test_it_passes_the_template_rules():
    from services.process_template_rules import refusals

    for raw in (UNEVEN, SHIFTED):
        p = _plan(raw)
        assert refusals(T.BUILDERS[NH3](planning=p), T.solver_config(NH3, p)) == [], raw


@pytest.mark.live_solve
def test_it_solves_and_its_case_builds_from_2026(reset_backend):
    """Uneven years from 2026: linearised UC with the binary check per period; the case's CO2 lines
    are priced from the held point (no ``co2_price_year_uncovered``)."""
    import routers.results as R
    import routers.simulation as sim_router
    from models.finance import FinanceInputs
    from routers.projects import _solver_config_from_dict
    from services.finance.engine import run_case
    from services.pypsa_service import PyPSAService
    from services.results import baseline as Bl
    from services.results.finance_case import baseline_plan, build_finance_case
    from services.solver_service import run_simulation
    from tests.conftest import install_network_into_backend

    p = _plan(UNEVEN)
    install_network_into_backend(T.BUILDERS[NH3](planning=p))
    cfg = _solver_config_from_dict(T.solver_config(NH3, p))
    sim_router._state["solver_config"] = cfg
    n = PyPSAService.get_network()
    state: dict = {}
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                  queue.SimpleQueue(), state_update=lambda **k: state.update(k))
    assert status in ("ok", "optimal"), (status, cond)
    assert list(n.investment_periods) == [2026, 2029, 2040]
    assert [r["period"] for r in state["last_process_uc_check"]["periods"]] in (
        [2026, 2029, 2040], ["2026", "2029", "2040"])
    Bl.clear_cache()
    base = Bl.solve_baseline(n, cfg, baseline_plan(n, cfg))
    case = build_finance_case(n, cfg, FinanceInputs.model_validate(cfg.finance),
                              result_df=R._result_df, lost_load=state.get("last_lost_load"),
                              baseline=base)
    Bl.clear_cache()
    assert not any(f.startswith("co2_price_year_uncovered") for f in case.flags)
    assert run_case(case).metrics["project_pre_tax_npv"] is not None


# ── create from the template with ``planning`` ──────────────────────────────

def test_create_builds_the_entered_years_and_writes_their_sidecars(
        client, tmp_projects_dir, bound_ctx):
    import pypsa

    from services import network_lengths

    r = client.post(f"/api/projects/from_template/{NH3}", params={"name": "nh3_e1c"},
                    json={"planning": SHIFTED})
    assert r.status_code == 200, r.text
    with bound_ctx(client):
        d = network_lengths.active_project_dir()
    n = pypsa.Network(str(d / "network.nc"))
    assert list(n.investment_periods) == [2029, 2032, 2037]
    p = _plan(SHIFTED)
    for name, want in ((T.META_FILE, T.template_meta(NH3, p)),
                       (T.SOLVER_FILE, T.solver_config(NH3, p))):
        assert json.loads((d / name).read_text()) == json.loads(json.dumps(want)), name
    body = client.get("/api/projects/nh3_e1c/flow").json()
    codes = {i["code"] for s in body["sections"] for i in s["items"]}
    assert "assumed_placeholder nh3_delivery_tpd 2032" in codes
