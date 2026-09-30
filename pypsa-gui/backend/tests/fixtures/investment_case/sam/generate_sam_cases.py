"""
Generate the SAM "Single Owner" oracle cases for the Edge Investment Case P4
gate (plan `docs/superpowers/plans/2026-09-30-edge-investment-case-p4.md`,
"Fixtures and oracles").

NOT run in CI: it needs NREL's PySAM (`pip install nrel-pysam==7.1.1.post1`,
BSD-3-Clause), which is not an app dependency. Run it in a developer venv:

    python tests/fixtures/investment_case/sam/generate_sam_cases.py

It writes `gen_profile.csv` and one `<case>.json` per case beside itself.
Each JSON carries the provenance (PySAM / SSC versions, this script's and the
profile's sha256), the FULL input export (the 8,760-long arrays replaced by
their sha256 — the profile is `gen_profile.csv`), the outputs (scalars and
every `cf_*` array) and the case's recorded deviations. A test pins
`script_sha` and `profile_sha` against the committed files, so a changed
script without regenerated fixtures fails.
"""
from __future__ import annotations

import hashlib
import json
import math
import pathlib
import random
import sys
from datetime import date

HERE = pathlib.Path(__file__).resolve().parent
PYSAM_VERSION = "7.1.1.post1"

# The one synthetic generation profile (kW, 8,760 hours): a clear-sky PV
# shape with a seasonal factor, times a seeded daily cloud factor.
PROFILE_SEED = 1
CAPACITY_KW = 100_000.0


def gen_profile() -> list[float]:
    rng = random.Random(PROFILE_SEED)
    cloud = [rng.uniform(0.5, 1.0) for _ in range(365)]
    out = []
    for h in range(8760):
        hod, doy = h % 24, h // 24
        shape = max(0.0, math.sin((hod - 6) / 12 * math.pi))
        season = 0.75 + 0.25 * math.cos(2 * math.pi * (doy - 172) / 365)
        out.append(round(CAPACITY_KW * 0.85 * shape * season * cloud[doy], 6))
    return out


# SAM defaults P4 does not model, zeroed in every case (plan: "Zeroed
# defaults"). Group → {input: value}.
ZEROED = {
    "FinancialParameters": {
        "property_tax_rate": 0.0, "insurance_rate": 0.0, "months_working_reserve": 0.0,
        "months_receivables_reserve": 0.0, "reserves_interest": 0.0,
        "construction_financing_cost": 0.0, "cost_debt_closing": 0.0, "cost_other_financing": 0.0,
        "salvage_percentage": 0.0, "equip1_reserve_cost": 0.0, "equip2_reserve_cost": 0.0,
        "equip3_reserve_cost": 0.0, "cost_debt_fee": 0.0, "dscr_reserve_months": 0.0,
        "loan_moratorium": 0.0, "federal_tax_rate": [21.0], "state_tax_rate": [7.0],
        "inflation_rate": 2.5, "real_discount_rate": 6.4, "analysis_period": 25.0,
    },
    "TaxCreditIncentives": {
        "ptc_fed_amount": [0.0], "ptc_sta_amount": [0.0], "itc_fed_amount": [0.0],
        "itc_fed_percent": [0.0], "itc_sta_amount": [0.0], "itc_sta_percent": [0.0],
    },
    "Depreciation": {
        **{f"depr_alloc_{c}_percent": 0.0 for c in
           ("custom", "macrs_5", "macrs_15", "sl_5", "sl_15", "sl_20", "sl_39")},
        "depr_bonus_fed": 0.0, "depr_bonus_sta": 0.0,
    },
    "PaymentIncentives": {
        # CBI / IBI / PBI: not modelled in P4 — explicit, not left to SAM's defaults.
        **{f"cbi_{o}_amount": 0.0 for o in ("fed", "sta", "uti", "oth")},
        **{f"ibi_{o}_{k}": 0.0 for o in ("fed", "sta", "uti", "oth") for k in ("amount", "percent")},
        **{f"pbi_{o}_amount": [0.0] for o in ("fed", "sta", "uti", "oth")},
    },
    "GridLimits": {"grid_curtailment_price": [0.0]},
    "CapacityPayments": {"cp_capacity_payment_amount": [0.0]},
    "LandLease": {"om_land_lease": [0.0]},
    "ElectricityRates": {"en_electricity_rates": 1.0},   # needed by ppa_soln_mode=0 standalone
    "Revenue": {"ppa_multiplier_model": 0.0, "dispatch_tod_factors": [1.0] * 9,
                "ppa_soln_mode": 1.0, "ppa_price_input": [0.10], "ppa_escalation": 1.0},
    "SystemCosts": {"om_capacity": [19.0], "om_capacity_escal": 1.0, "om_fixed": [50_000.0],
                    "om_fixed_escal": 1.0, "om_production": [0.0], "om_production_escal": 0.0,
                    "om_fuel_cost": [0.0], "om_opt_fuel_1_cost": [0.0],
                    "om_opt_fuel_2_cost": [0.0], "system_use_recapitalization": 0.0},
    "SystemOutput": {"degradation": [0.5], "system_capacity": CAPACITY_KW},
}

# The cases (plan table). Each override is applied after ZEROED.
CASES: dict[str, dict] = {
    "s1": {
        "doc": "All-equity; SL-20 federal and state; 21 % federal + 7 % state; PPA with 1 %/yr "
               "escalation; O&M per kW-yr and fixed, inflation 2.5 % + escalation 1 % on both; "
               "degradation 0.5 %/yr; 25 years.",
        "set": {"FinancialParameters": {"debt_option": 0.0, "debt_percent": 0.0},
                "Depreciation": {"depr_alloc_sl_20_percent": 100.0}},
        "deviations": [],
    },
    "s1b": {
        "doc": "S1 solving the PPA price for an 11 % after-tax IRR in year 20 (ppa_soln_mode=0).",
        "set": {"FinancialParameters": {"debt_option": 0.0, "debt_percent": 0.0},
                "Depreciation": {"depr_alloc_sl_20_percent": 100.0},
                "Revenue": {"ppa_soln_mode": 0.0, "flip_target_percent": 11.0,
                            "flip_target_year": 20.0}},
        "deviations": [],
    },
    "s2": {
        "doc": "DSCR-sculpted debt (1.3, 18 yrs, 7 %, closing fee 2.75 %, DSRA 6 months, reserve "
               "interest 1.75 %); MACRS-5 90 % + SL-20 10 %; federal bonus 100 % on MACRS-5, state "
               "bonus 0.",
        "set": {"FinancialParameters": {"debt_option": 1.0, "dscr": 1.3, "term_tenor": 18.0,
                                        "term_int_rate": 7.0, "cost_debt_fee": 2.75,
                                        "dscr_reserve_months": 6.0, "reserves_interest": 1.75,
                                        "payment_option": 0.0},
                "Depreciation": {"depr_alloc_macrs_5_percent": 90.0,
                                 "depr_alloc_sl_20_percent": 10.0,
                                 "depr_bonus_fed": 100.0, "depr_bonus_fed_macrs_5": 1.0,
                                 "depr_bonus_sta": 0.0}},
        "deviations": [],
    },
    "s3": {
        "doc": "Gearing 60 % of TIC (fee 0, DSRA 0), standard amortisation, 15 yrs, 6 %, a one-year "
               "moratorium inside the tenor; federal ITC 30 % with the federal basis-reduction flag "
               "on and the state flag off; MACRS-5 100 %, no bonus; salvage 10 %.",
        "set": {"FinancialParameters": {"debt_option": 0.0, "debt_percent": 60.0,
                                        "payment_option": 0.0, "term_tenor": 15.0,
                                        "term_int_rate": 6.0, "loan_moratorium": 1.0,
                                        "salvage_percentage": 10.0},
                "TaxCreditIncentives": {"itc_fed_percent": [30.0],
                                        "itc_fed_percent_deprbas_fed": 1.0,
                                        "itc_fed_percent_deprbas_sta": 0.0},
                "Depreciation": {"depr_alloc_macrs_5_percent": 100.0}},
        "deviations": [],
    },
    "s3f": {
        "doc": "S3 with a 2.75 % closing fee: SAM sizes D = g·TIC·(1 + g·f) in one step; P4's "
               "gearing_base='capex' sizes D = g·TIC (a recorded deviation, sized by the test).",
        "set": {"FinancialParameters": {"debt_option": 0.0, "debt_percent": 60.0,
                                        "payment_option": 0.0, "term_tenor": 15.0,
                                        "term_int_rate": 6.0, "loan_moratorium": 1.0,
                                        "salvage_percentage": 10.0, "cost_debt_fee": 2.75},
                "TaxCreditIncentives": {"itc_fed_percent": [30.0],
                                        "itc_fed_percent_deprbas_fed": 1.0,
                                        "itc_fed_percent_deprbas_sta": 0.0},
                "Depreciation": {"depr_alloc_macrs_5_percent": 100.0}},
        "deviations": [{"name": "gearing_with_fee",
                        "what": "SAM debt = g·TIC·(1+g·f) (one step); P4 debt = g·TIC",
                        "size": "0.36·f·TIC with g=0.6"}],
    },
}

SCALARS = ("project_return_aftertax_irr", "project_return_aftertax_npv", "size_of_debt",
           "size_of_equity", "min_dscr", "ppa_price", "lcoe_nom", "lcoe_real", "itc_total",
           "nominal_discount_rate", "debt_fraction", "cost_installed", "analysis_period_irr",
           "flip_actual_irr", "flip_actual_year")


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]


def _jsonable(v):
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def run_case(name: str, profile: list[float]):
    import PySAM
    import PySAM.Singleowner as so

    case = CASES[name]
    m = so.default("PVWattsSingleOwner")
    m.SystemOutput.gen = profile
    for block in (ZEROED, case["set"]):
        for grp, vals in block.items():
            for k, v in vals.items():
                setattr(getattr(m, grp), k, v)
    m.execute()
    inputs = {}
    for grp, vals in m.export().items():
        if grp == "Outputs":
            continue
        g = {}
        for k, v in vals.items():
            v = _jsonable(v)
            g[k] = {"sha256_16": _sha(v), "len": len(v)} if isinstance(v, list) and len(v) > 1000 else v
        inputs[grp] = g
    out = m.Outputs.export()
    scalars = {k: out.get(k) for k in SCALARS}
    arrays = {k: list(v) for k, v in sorted(out.items())
              if k.startswith("cf_") and isinstance(v, (list, tuple))
              and len(v) == int(m.FinancialParameters.analysis_period) + 1}
    return {"case": name, "doc": case["doc"],
            "provenance": {"pysam_version": PySAM.__version__, "ssc_version": _ssc_version(),
                           "generated": date.today().isoformat(),
                           "script_sha": script_sha(), "profile_sha": profile_sha(),
                           "license": "BSD-3-Clause (SAM / PySAM, see SAM_LICENSE)"},
            "sam_inputs": inputs, "outputs": {"scalars": scalars, "arrays": arrays},
            "deviations": case["deviations"]}


def _ssc_version() -> str | None:
    try:
        import PySAM.PySSC as pssc
        return str(pssc.PySSC().version())
    except Exception:      # noqa: BLE001 — provenance only
        return None


def script_sha() -> str:
    return hashlib.sha256((HERE / "generate_sam_cases.py").read_bytes()).hexdigest()


def profile_sha() -> str:
    return hashlib.sha256((HERE / "gen_profile.csv").read_bytes()).hexdigest()


def write_profile(profile: list[float]) -> None:
    (HERE / "gen_profile.csv").write_text(
        "hour,gen_kw\n" + "".join(f"{h},{v!r}\n" for h, v in enumerate(profile)))


def main() -> int:
    import PySAM
    if PySAM.__version__ != PYSAM_VERSION:
        print(f"PySAM {PySAM.__version__} != pinned {PYSAM_VERSION}", file=sys.stderr)
        return 1
    profile = gen_profile()
    write_profile(profile)
    for name in CASES:
        res = run_case(name, profile)
        (HERE / f"{name}.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
        print(name, {k: res["outputs"]["scalars"][k] for k in
                     ("project_return_aftertax_irr", "size_of_debt", "ppa_price", "min_dscr")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
