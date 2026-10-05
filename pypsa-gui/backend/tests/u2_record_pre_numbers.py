"""
U2 plan WP0, "freeze the pre-U2 numbers"
(docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md §7 WP0, §4).

Records ``tests/fixtures/u2_pre_numbers.json`` from the guided-study (GS)
engine BEFORE any U2 change, so WP5-WP10 compare the IC engine's figures with
GS's, and ``tests/test_u2_pre_numbers_recorded.py`` re-derives them on the
current code (a master merge that moves a number goes red there).

Sections:

* ``driver``: ``qa_decision_study.py``'s EVIDENCE (run as a subprocess with
  ``QA_DECISION_STUDY_EVIDENCE_OUT``): sizes, verdict (class, drivers, named
  option, skipped options), battery NPV at the centre and every tornado
  bound, the named option's CAPEX, FOM, IRR, paybacks, both LCOS figures and
  every option's bill by component.
* ``golden_s5``: the pro forma on ``tests/golden/site_fixture.py`` for
  ``bess_2h`` and ``bess_pv_2h`` (plus the three LP objectives and sizes).
* ``seed_bills``: both seed tariffs (ledger-applied, ``packs.effective_tariff``)
  billed by ``BillCalculator`` on the golden dispatch of each site option.
* ``toy_3month``: :func:`toy_network` solved with GS's
  ``_wrap_with_demand_charge`` (plan §4.7 / WP6): objective and monthly peaks.
* ``provenance``: commit, PyPSA / linopy / HiGHS versions, ledger hashes.

Run only on purpose (the repo's ``RECORD_*`` convention)::

    cd pypsa-gui/backend
    RECORD_U2_PRE=1 python tests/u2_record_pre_numbers.py

Not collected by pytest (``python_files = test_*.py``) and not a ``qa_*``
driver (``run_qa_drivers.py`` globs ``qa_*.py``).
"""
from __future__ import annotations

import datetime as _dt
import importlib.metadata as _md
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


def toy_network(*, gs_prices: bool = True):
    """
    Three months hourly (January-March 2030, 2,160 snapshots): buses ``grid``
    and ``site``, a zero-cost balancing Generator on ``grid`` (``p_min_pu=-1``),
    ``grid_import`` / ``grid_export`` Links (60 MW), a seeded sinusoidal site
    load and an extendable 2-hour StorageUnit. ``gs_prices`` writes the
    tariff's energy and export prices into the Links the GS way
    (``write_tariff_prices``); WP6 builds it with ``gs_prices=False`` and lets
    IC price it.
    """
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
    if gs_prices:
        from services.study.tariff import write_tariff_prices

        write_tariff_prices(n, toy_tariff(), TOY_IMPORT_LINK, TOY_EXPORT_LINK)
    return n


def toy_solver_config():
    from services.solver_service import SolverConfig
    from services.study.tariff import demand_charge_config

    return SolverConfig(solver_name="highs", discount_rate=0.07,
                        demand_charge=demand_charge_config(toy_tariff(), [TOY_IMPORT_LINK]))


def toy_3month() -> dict:
    """The toy solved with GS's wrapper, as `site_fixture.solve_site_option` does."""
    from services.solver.objective import _wrap_with_demand_charge
    from services.solver_service import with_periodized_cost_defaults

    n = toy_network()
    cfg = toy_solver_config()
    with with_periodized_cost_defaults(n, cfg):
        status, condition = n.optimize(
            solver_name="highs", extra_functionality=_wrap_with_demand_charge(n, None, cfg))
    assert (status, condition) == ("ok", "optimal"), (status, condition)
    peak = n.model.variables["peak_import"].solution.to_series()
    p0 = n.links_t.p0[TOY_IMPORT_LINK]
    monthly = p0.groupby(p0.index.strftime("%Y-%m")).max()
    return {
        "spec": {"months": list(TOY_MONTHS), "snapshots": len(n.snapshots),
                 "demand_price_eur_per_mw_month": TOY_DEMAND_PRICE,
                 "builder": "tests/u2_record_pre_numbers.py::toy_network",
                 "solved_with": "services.solver.objective._wrap_with_demand_charge"},
        "objective": float(n.objective),
        "objective_constant": float(getattr(n, "objective_constant", 0.0) or 0.0),
        "peak_import_mw": {str(k): float(v) for k, v in peak.items()},
        "monthly_max_import_mw": {str(k): float(v) for k, v in monthly.items()},
        "demand_charge_eur": float(TOY_DEMAND_PRICE * peak.sum()),
        "battery_p_nom_mw": float(n.storage_units.at["bess", "p_nom_opt"]),
    }


# ── the S5 golden table and the seed bills ───────────────────────────────

def _bill_dict(bill) -> dict:
    return {"total": bill.total, "annual_bill": bill.annual_bill,
            "by_component": bill.by_component.model_dump(mode="json")}


def _sizes(n) -> dict:
    out = {"battery_p_nom_mw": float(n.storage_units.at["battery", "p_nom_opt"])
           if "battery" in n.storage_units.index else None}
    out["pv_p_nom_mw"] = (float(n.generators.at["pv", "p_nom_opt"])
                          if "pv" in n.generators.index else None)
    return out


def golden_s5() -> dict:
    from services.adequacy.eh_report import _live_result_df
    from services.results.asset_economics import compute_asset_economics
    from services.study import proforma as P
    from tests.golden import site_fixture as sf

    ledger = sf.site_ledger()
    tariff = sf.site_tariff(ledger)
    nb, _ = sf.solve_site_option("none")
    out: dict = {"none": {"objective": float(nb.objective), **_sizes(nb)}}
    for option in SITE_CASE_OPTIONS:
        n, cfg = sf.solve_site_option(option)
        case = P.build_investment_case(n, cfg, nb, ledger, None, option,
                                       study_id=sf.SITE_STUDY_ID, tariff=tariff,
                                       fidelity="full_study")
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
            "savings_annual": y1.savings, "salvage_eur": k.salvage_eur,
            "npv": k.npv, "irr": k.irr, "payback_simple": k.payback_simple,
            "payback_discounted": k.payback_discounted,
            "lcos_excl_charging": k.lcos,
            "lcos_incl_charging": bat.get("lcos_eur_per_mwh"),
            "market_revenue_at_duals": case.market_revenue_at_duals.annual_value,
            "value_streams": {s.key: s.annual_value for s in case.value_streams},
            "net_cash_flow": [y.net_cash_flow for y in case.years],
            "horizon_years": case.horizon_years, "discount_rate": case.discount_rate,
            "ledger_hash": case.provenance.ledger_hash,
        }
    return out


def seed_bills() -> dict:
    """Both seeds, ledger-applied, billed on each site option's golden dispatch."""
    from services.study import library as L
    from services.study import packs
    from services.study import questions as Q
    from services.study.tariff import BillCalculator
    from tests.golden import site_fixture as sf

    lib = sf.site_library()
    out: dict = {}
    for tid in SEED_TARIFFS:
        intake = {**sf.site_intake(), "tariff": {"tariff_id": tid}}
        ledger = L.seed_ledger(Q.BESS_AT_SITE, intake, lib)
        tariff = packs.effective_tariff(intake, ledger, lib)
        out[tid] = {}
        for option in sf.SITE_OPTIONS:
            n, _ = sf.solve_site_option(option)
            p0 = n.links_t.p0
            bill = BillCalculator().bill(p0[packs.IMPORT_LINK], p0[packs.EXPORT_LINK],
                                         tariff, n.snapshot_weightings)
            out[tid][option] = _bill_dict(bill)
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
            tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
            raise RuntimeError(f"qa_decision_study.py exited {proc.returncode}:\n{tail}")
        return json.loads(out.read_text(encoding="utf-8"))


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
        "bills": ev["bills"],
    }


# ── provenance ───────────────────────────────────────────────────────────

def _git(*args: str) -> str | None:
    try:
        return subprocess.run(["git", *args], cwd=_BACKEND, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def provenance(golden: dict, driver: dict) -> dict:
    from tests.golden import site_fixture as sf

    engine_paths = ["pypsa-gui/backend/services", "pypsa-gui/backend/models"]
    dirty = _git("status", "--porcelain", "--", *(f":/{p}" for p in engine_paths))
    return {
        "commit": _git("rev-parse", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "engine_code_uncommitted_changes": dirty.splitlines() if dirty else [],
        "recorded_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "pypsa": _md.version("pypsa"),
        "linopy": _md.version("linopy"),
        "highspy": _md.version("highspy"),
        "ledger_hash": {"site_golden": golden["bess_2h"]["ledger_hash"],
                        "driver": driver["case_kpis"]["ledger_hash"]},
        "library_version": sf.site_ledger().ledger_version,
    }


def record(driver_evidence: dict | None = None) -> dict:
    driver = driver_section(driver_evidence or run_driver())
    golden = golden_s5()
    payload = {
        "schema": SCHEMA,
        "plan": "docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md WP0",
        "tolerances": TOLERANCES,
        "driver": driver,
        "golden_s5": golden,
        "seed_bills": seed_bills(),
        "toy_3month": toy_3month(),
    }
    payload["provenance"] = provenance(golden, driver)
    return payload


def main() -> int:
    if os.environ.get("RECORD_U2_PRE") != "1":
        print("Refusing: set RECORD_U2_PRE=1 to (re-)record "
              f"{FIXTURE.relative_to(_BACKEND)} (only before any U2 change).")
        return 2
    payload = record()
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"recorded {FIXTURE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
