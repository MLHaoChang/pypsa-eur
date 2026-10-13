"""
U2 plan WP0, "freeze the pre-U2 numbers"
(docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md §7 WP0, §4).

Recorded ``tests/fixtures/u2_pre_numbers.json`` from the guided-study (GS)
engine BEFORE any U2 change, so WP5-WP10 compare the IC engine's figures with
GS's, and ``tests/test_u2_pre_numbers_recorded.py`` re-derives them on the
current code (a master merge that moves a number goes red there).

**Since U2 WP10 the GS engine is deleted** (its bill calculator, pro forma,
link prices and demand wrapper), so the freeze is FINAL: ``main`` refuses to
re-record, and every section below is re-derived on the Investment Case
engine — the site golden fixture solved the way the production runner
solves (``tests/golden/site_fixture.py``), its cases valued by
``engine_adapter.option_case``, its bills by ``engine_adapter.bill``, the toy
priced by the compiled toy tariff. Where the engine differs BY DESIGN the
comparison applies the recorded delta (``tests/fixtures/u2_deltas.json``).

Sections:

* ``driver``: ``qa_decision_study.py``'s EVIDENCE (run as a subprocess with
  ``QA_DECISION_STUDY_EVIDENCE_OUT``): sizes, verdict (class, drivers, named
  option, skipped options), battery NPV at the centre and every tornado
  bound, the named option's CAPEX, FOM, IRR, paybacks, both LCOS figures and
  every option's bill by component.
* ``golden_s5``: the investment case of ``bess_2h`` and ``bess_pv_2h`` on
  ``tests/golden/site_fixture.py`` (plus the three LP objectives and sizes);
  recorded by GS's pro forma, re-derived by ``engine_adapter.option_case``.
* ``seed_bills``: both seed tariffs (ledger-applied) billed on the golden
  dispatch of each site option; recorded by GS's ``BillCalculator``,
  re-derived by ``engine_adapter.bill`` (its seven components, the total
  their sum: the engine vouches for a total only under the tariff it solved).
* ``toy_3month``: :func:`toy_network` with a demand charge (plan §4.7 / WP6):
  objective and monthly peaks; recorded with GS's ``_wrap_with_demand_charge``,
  re-derived on the engine's commercial chain (``add_demand_terms``).
* ``provenance``: commit, PyPSA / linopy / HiGHS versions, ledger hashes.

It was run once, on purpose (the repo's ``RECORD_*`` convention), at the
commit the fixture's ``provenance`` names; since WP10 it refuses.

Not collected by pytest (``python_files = test_*.py``) and not a ``qa_*``
driver (``run_qa_drivers.py`` globs ``qa_*.py``).
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

_TESTS = pathlib.Path(__file__).resolve().parent
_BACKEND = _TESTS.parent
# As the qa drivers do: the backend first, so `tests` is this package even when
# the file runs as a script (another `tests` package is importable here).
sys.path.insert(0, str(_BACKEND))

FIXTURE = _TESTS / "fixtures" / "u2_pre_numbers.json"
SCHEMA = "u2_pre_numbers/1"
DRIVER = _TESTS / "qa_decision_study.py"
DRIVER_EVIDENCE_ENV = "QA_DECISION_STUDY_EVIDENCE_OUT"

#: How the re-derivation test compares (stated in the fixture too).
TOLERANCES = {
    "arithmetic_rel": 1e-9,
    "arithmetic_abs": 1e-9,
    "rate_and_years_abs": 1e-9,
    "lp_objective_rel": 1e-6,
    "mw_abs": 1e-4,
    "rule": ("lp_objective_rel: every 'objective' leaf; mw_abs: p_nom_opt, e_nom_opt, "
             "battery_p_nom_mw, pv_p_nom_mw, p_nom_mw and every leaf of a '*_mw' map; "
             "rate_and_years_abs: irr, payback_simple, payback_discounted, discount_rate; "
             "everything else (money, LCOS, bills, ledger bounds) is arithmetic downstream "
             "of the dispatch, which the same code reproduces bit for bit: "
             "|got - want| <= arithmetic_rel * max(|got|, |want|) + arithmetic_abs. "
             "Strings, booleans and nulls compare exactly."),
}

SITE_CASE_OPTIONS = ("bess_2h", "bess_pv_2h")
SEED_TARIFFS = ("de_industrial_illustrative", "tou_reference_illustrative")

# ── the 3-month toy (plan §4.7, WP6) ─────────────────────────────────────

TOY_YEAR = 2030  # not a leap year
TOY_MONTHS = ("2030-01", "2030-02", "2030-03")
TOY_DEMAND_PRICE = 15_000.0  # EUR per MW per month
TOY_IMPORT_LINK = "grid_import"
TOY_EXPORT_LINK = "grid_export"


def toy_tariff():
    """GS form of the toy's tariff: two energy bands, a monthly demand charge, export."""
    from models.study import Tariff

    return Tariff.model_validate(dict(
        tariff_id="u2_toy", name="U2 toy two-band", source="illustrative",
        currency="EUR", currency_year=2020, billing_period="month",
        energy_bands=[
            {"label": "day", "price_per_mwh": 110.0, "applies": {"hours": list(range(8, 20))}},
            {"label": "night", "price_per_mwh": 90.0, "applies": {}},
        ],
        demand_charge={"price_per_mw_per_period": TOY_DEMAND_PRICE,
                       "basis": "billing_period_peak"},
        export={"price_per_mwh": 40.0},
    ))


def toy_network(*, gs_prices: bool = False):
    """
    Three months hourly (January-March 2030, 2,160 snapshots): buses ``grid``
    and ``site``, a zero-cost balancing Generator on ``grid`` (``p_min_pu=-1``),
    ``grid_import`` / ``grid_export`` Links (60 MW), a seeded sinusoidal site
    load and an extendable 2-hour StorageUnit, with NO price on the Links: the
    engine prices them (WP6). WP0 recorded it with GS's link prices
    (``gs_prices=True``), which WP10 deleted; asking for them is refused.
    """
    if gs_prices:
        raise ValueError("GS's link prices were deleted in U2 WP10; the engine prices the toy")
    import numpy as np
    import pandas as pd
    import pypsa

    sn = pd.date_range(f"{TOY_YEAR}-01-01", f"{TOY_YEAR}-03-31 23:00", freq="h")
    n = pypsa.Network()
    n.set_snapshots(sn)
    n.add("Bus", "grid")
    n.add("Bus", "site")
    n.add("Generator", "grid_supply", bus="grid", p_nom=1e3, p_min_pu=-1.0, marginal_cost=0.0)
    n.add("Link", TOY_IMPORT_LINK, bus0="grid", bus1="site", p_nom=60.0)
    n.add("Link", TOY_EXPORT_LINK, bus0="site", bus1="grid", p_nom=60.0)
    hr = np.asarray(sn.hour)
    rng = np.random.default_rng(0)
    load = 20 + 15 * np.sin((hr - 6) / 24 * 2 * np.pi).clip(0) + 5 * rng.random(len(sn))
    n.add("Load", "site_load", bus="site", p_set=pd.Series(load, index=sn))
    n.add("StorageUnit", "bess", bus="site", p_nom_extendable=True, p_nom_max=40.0,
          max_hours=2.0, overnight_cost=650_000.0, lifetime=15, discount_rate=0.07,
          fom_cost=5_000.0, efficiency_store=0.95 ** 0.5,
          efficiency_dispatch=0.95 ** 0.5, cyclic_state_of_charge=True)
    return n


#: Recorded at WP0 (`toy_3month.spec.solved_with`): the wrapper that solved
#: the frozen toy, deleted in WP10; the re-derivation names the engine's chain.
WP0_TOY_SOLVER = "services.solver.objective._wrap_with_demand_charge"


def toy_3month() -> dict:
    """
    The toy priced by the compiled toy tariff on the engine's commercial chain
    (`run_simulation`; the demand charge is `add_demand_terms`, its committed
    monthly peaks `lp_bindings.META_DEMAND`). Same keys as the WP0 record.
    """
    import queue
    import threading

    import pandas as pd

    from services.commercial import lp_bindings as LP
    from services.solver_service import run_simulation
    from services.study import compile as C
    from services.study import engine_adapter as A

    n = toy_network()
    c = C.commercial_from_form(toy_tariff(), n.snapshots, export_series=_TOY_EXPORT_REF)
    series = pd.Series(40.0, index=pd.DatetimeIndex(n.snapshots), name="price")
    c = C.bind_on_network(n, c, resolve_ref=lambda _ref: series)
    cfg = C.solver_config(None, c, discount_rate=0.07)
    status = run_simulation(cfg, n, threading.RLock(), threading.Event(), queue.SimpleQueue())
    assert status == ("ok", "optimal"), status
    peaks = {v["month"]: float(v["peak_mw"]) for v in n.meta[LP.META_DEMAND].values()}
    p0 = n.links_t.p0[TOY_IMPORT_LINK]
    monthly = p0.groupby(p0.index.strftime("%Y-%m")).max()
    return {
        "spec": {"months": list(TOY_MONTHS), "snapshots": len(n.snapshots),
                 "demand_price_eur_per_mw_month": TOY_DEMAND_PRICE,
                 "builder": "tests/u2_record_pre_numbers.py::toy_network",
                 "solved_with": WP0_TOY_SOLVER},
        "objective": float(n.objective),
        "objective_constant": float(getattr(n, "objective_constant", 0.0) or 0.0),
        "peak_import_mw": {m: peaks[m] for m in sorted(peaks)},
        "monthly_max_import_mw": {str(k): float(v) for k, v in monthly.items()},
        "demand_charge_eur": float(A.demand_charge_eur(n, c)),
        "battery_p_nom_mw": float(n.storage_units.at["bess", "p_nom_opt"]),
    }


#: A study-shaped export series ref for the in-memory toy (no Library).
_TOY_EXPORT_REF = {"id": "decision-study:fake:" + "5" * 32 + ":export", "version": 1,
                   "hash": "f" * 64, "source": "decision_study"}


# ── the S5 golden table and the seed bills ───────────────────────────────

def _wp0_components(by_component: dict) -> dict:
    """
    The six components WP0 recorded. U2 WP5 adds a seventh, `taxes_levies`
    (owner decision 7), which a GS bill carries as a real 0.0: dropped here
    only when it is 0.0, so a non-zero one still fails the re-derivation.
    """
    out = dict(by_component)
    if out.get("taxes_levies", None) == 0.0:
        out.pop("taxes_levies")
    return out


def _bill_dict(bill) -> dict:
    return {"total": bill.total, "annual_bill": bill.annual_bill,
            "by_component": _wp0_components(bill.by_component.model_dump(mode="json"))}


def _sizes(n) -> dict:
    out = {"battery_p_nom_mw": float(n.storage_units.at["battery", "p_nom_opt"])
           if "battery" in n.storage_units.index else None}
    out["pv_p_nom_mw"] = (float(n.generators.at["pv", "p_nom_opt"])
                          if "pv" in n.generators.index else None)
    return out


def golden_s5() -> dict:
    """
    The investment case of each case option on the site golden fixture, on
    the engine (`engine_adapter.option_case` on the fork as the runner makes
    it); the keys WP0's pro forma recorded.
    """
    from services.adequacy.eh_report import _live_result_df
    from services.results.asset_economics import compute_asset_economics
    from services.study import engine_adapter as A
    from tests.golden import site_fixture as sf

    nb, _ = sf.solve_site_option("none")
    out: dict = {"none": {"objective": float(nb.objective), **_sizes(nb)}}
    for option in SITE_CASE_OPTIONS:
        n, cfg, compiled, ledger = sf.site_case_option(option)
        case = A.option_case(n, cfg, ledger, compiled=compiled, option_id=option,
                             study_id=sf.SITE_STUDY_ID, fidelity="full_study").view
        econ = compute_asset_economics(n, cfg, result_df=_live_result_df)
        [bat] = [r for r in econ["storage_units"] if r["name"] == "battery"]
        k = case.kpis
        y1 = case.years[1]
        out[option] = {
            "objective": float(n.objective), **_sizes(n),
            "capex_total": k.capex_total,
            "replacements": {str(y.year): y.replacements for y in case.years if y.replacements},
            "fom_annual": y1.opex_fixed, "vom_annual": y1.opex_variable,
            "bill_baseline": y1.bill_baseline, "bill_option": y1.bill_option,
            "savings_annual": y1.savings, "terminal_value_eur": k.terminal_value_eur,
            "npv": k.npv, "irr": k.irr, "payback_simple": k.payback_simple,
            "payback_discounted": k.payback_discounted,
            # The engine's storage LCOS (charging included, owner decision 6):
            # never under the WP0 charging-free key (gate U2-WP7 X5 c).
            "levelised_cost": k.levelised_cost,
            "lcos_incl_charging": bat.get("lcos_eur_per_mwh"),
            "market_revenue_at_duals": case.market_revenue_at_duals.annual_value,
            "value_streams": _wp0_components({s.key: s.annual_value
                                              for s in case.value_streams}),
            "net_cash_flow": [y.net_cash_flow for y in case.years],
            "horizon_years": case.horizon_years, "discount_rate": case.discount_rate,
            "ledger_hash": case.provenance.ledger_hash,
        }
    return out


def seed_bills() -> dict:
    """
    Both seeds, ledger-applied, billed by the engine (`engine_adapter.bill`)
    on each site option's golden dispatch (`u2_targets.golden_dispatch`): the seven
    components with their flags, and the total as their sum (on a dispatch
    the engine did not solve with that seed it states no total of its own).
    """
    import pandas as pd

    from services.study import compile as C
    from services.study import engine_adapter as A
    from services.study import library as L
    from services.study import questions as Q
    from tests.golden import site_fixture as sf
    from tests.u2_targets import golden_dispatch

    lib = sf.site_library()
    out: dict = {}
    for tid in SEED_TARIFFS:
        intake = {**sf.site_intake(), "tariff": {"tariff_id": tid}}
        ledger = L.seed_ledger(Q.BESS_AT_SITE, intake, lib)
        out[tid] = {}
        for option in sf.SITE_OPTIONS:
            n = golden_dispatch(option)
            c = C.commercial_from_ledger(intake, ledger, lib, n.snapshots,
                                         export_series=sf.EXPORT_REF)
            price = float(c.tariff_meta["export_price_per_mwh"])
            series = pd.Series(price, index=pd.DatetimeIndex(n.snapshots), name="price")
            c = C.bind_on_network(n, c, resolve_ref=lambda _ref, s=series: s)
            bill = A.bill(n, c)
            comps = {k: getattr(bill.by_component, k) for k in A.BILL_COMPONENT_KEYS}
            total = sum(v for v in comps.values() if v is not None)
            comps["unavailable"] = dict(bill.by_component.unavailable)
            out[tid][option] = {"total": total, "annual_bill": total,
                                "by_component": _wp0_components(comps)}
    return out


# ── the driver ───────────────────────────────────────────────────────────

def run_driver(timeout: float = 1800.0) -> dict:
    """``qa_decision_study.py`` in a subprocess; its EVIDENCE dump, or raise."""
    with tempfile.TemporaryDirectory(prefix="u2-pre-") as tmp:
        out = pathlib.Path(tmp) / "evidence.json"
        env = {**os.environ, DRIVER_EVIDENCE_ENV: str(out)}
        proc = subprocess.run([sys.executable, str(DRIVER)], cwd=_BACKEND, env=env,
                              capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0 or not out.is_file():
            raise RuntimeError(f"qa_decision_study.py exited {proc.returncode}:\n"
                               + _failure_tail(proc.stdout, proc.stderr))
        return json.loads(out.read_text(encoding="utf-8"))


def _failure_tail(stdout: str, stderr: str) -> str:
    """
    What a failed driver run says: its FAIL steps, then the last 40 lines of
    its output without the per-request httpx log lines. A poll that ran out its
    timeout used to fill the whole tail with `GET .../run` lines, hiding the
    failed step and the logged exception (CI, 13 Oct).
    """
    lines = [*stdout.splitlines(), *stderr.splitlines()]
    fails = [ln for ln in lines if "[FAIL]" in ln]
    rest = [ln for ln in lines if "HTTP Request:" not in ln][-40:]
    return "\n".join([*fails, "--- last lines ---", *rest])


def driver_section(ev: dict) -> dict:
    """The figures U2 compares (timings, prose and the abort's race are left out)."""
    v = ev["verdict"]
    att = ev["attribution"]
    return {
        "checks": ev["checks"],
        "sizes": ev["sizes"],
        "verdict": {"class": v["class"], "drivers": v["drivers"], "option": v["option"],
                    "skipped_options": sorted(o for o, a in att.items()
                                              if a["status"] == "skipped")},
        "attribution": att,
        "npv_bounds": ev["npv_bounds"],
        "capex": ev["capex"],
        "fom": ev["fom"],
        "upfront_cost_series_eur_per_mw": ev["upfront_cost_series_eur_per_mw"],
        "case_kpis": ev["case_kpis"],
        "bills": {o: {**b, "by_component": _wp0_components(b["by_component"])}
                  if isinstance(b, dict) and isinstance(b.get("by_component"), dict) else b
                  for o, b in ev["bills"].items()},
    }


def main() -> int:
    # U2 WP10: the GS engine the freeze was recorded on is deleted, so a
    # re-record would write the engine's figures over the pre-U2 ones. The
    # freeze is final; the engine's moves are rows of u2_deltas.json.
    print(f"Refusing: {FIXTURE.relative_to(_BACKEND)} is final since U2 WP10 (the GS "
          "engine it was recorded on is deleted); the engine's moves are rows of "
          "tests/fixtures/u2_deltas.json.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
