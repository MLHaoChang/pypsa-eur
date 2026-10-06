"""
The second golden network: the SITE shape the guided investment study builds
(plan S5, review v1 B6 / S8), solved for real.

`tests/golden/fixture.py` is multi-period, has no import Link and never saw a
tariff, so it cannot check the pro forma. This one is the production shape,
built by the production pack (`services/study/packs.py::build_site_network`)
from a ledger seeded from the vendored library:

* one flat, non-leap year of 8760 hourly snapshots, weightings 1;
* buses `grid` and `site`; `grid_import` / `grid_export` Links priced with a
  FLAT tariff (one energy band plus a per-MWh network charge, a monthly demand
  charge on peak import, a fixed charge and an export price — the library's
  German-style illustrative seed);
* a StorageUnit (2 h) costed by the pack's two-annuity rule; an optional PV
  Generator on the overnight-cost path.

The load is an uploaded series (the production `series_mw` path), shaped so
the battery is WORTH building: a flat base with a one-hour evening spike on
weekdays, which a 2-hour battery can shave and PV (dark by then) cannot. A
sector profile with broad office peaks sizes the battery-only option to zero,
which would make every CAPEX, FOM and replacement check vacuous (0 = 0).

The solve uses the PRE-U2 LP pieces directly (the periodized-cost fill and
GS's `_wrap_with_demand_charge`, with the tariff written into the Links by
`tariff.write_tariff_prices`): this fixture is the GS oracle the WP0 record
(`tests/fixtures/u2_pre_numbers.json`) and the legacy pro forma tests read.
Since U2 WP6 the production pack writes no prices and the guided study solves
through the Investment Case engine (`compile.solver_config`); its equality
with this oracle is `tests/test_u2_wp6_lp_switch.py`'s test. WP10 rewrites
this fixture onto the engine when the GS pieces go. Each option is solved once
per process.
"""
from __future__ import annotations

import pandas as pd
import pypsa

SITE_YEAR = 2025
CONNECTION_MW = 2.0
BASE_MW = 0.6
SPIKE_MW = 0.8
SPIKE_HOUR = 17
SITE_OPTIONS = ("none", "bess_2h", "bess_pv_2h")
SITE_STUDY_ID = "5" * 32


def site_load_series() -> list[float]:
    idx = pd.date_range(f"{SITE_YEAR}-01-01", periods=8760, freq="h")
    spike = (idx.weekday < 5) & (idx.hour == SPIKE_HOUR)
    return [BASE_MW + (SPIKE_MW if s else 0.0) for s in spike]


def site_intake() -> dict:
    return {
        "site": {"zone": "DE", "connection_mw": CONNECTION_MW, "year": SITE_YEAR,
                 "latitude": 51.0},
        "tariff": {"tariff_id": "de_industrial_illustrative"},
        "load": {"source": "upload", "series_mw": site_load_series()},
        "pv": {"enabled": True, "kind": "rooftop"},
    }


def site_library():
    from services.study import library as study_library

    return study_library.load_library()


def site_ledger():
    from services.study import library as study_library
    from services.study import questions as Q

    return study_library.seed_ledger(Q.BESS_AT_SITE, site_intake(), site_library())


def site_tariff(ledger=None):
    from services.study import packs

    return packs.effective_tariff(site_intake(), ledger or site_ledger(), site_library())


_SOLVED: dict[str, tuple[pypsa.Network, object]] = {}


def gs_solver_config(ledger, tariff):
    """
    The pre-U2 option config (GS's `demand_charge`, no commercial block) the
    oracle solves with; removed with the GS wrapper in WP10.
    """
    from services.solver_service import SolverConfig
    from services.study import packs
    from services.study.tariff import demand_charge_config

    v = packs.ledger_values(ledger)
    return SolverConfig(
        solver_name="highs", mode="lopf", multi_investment_periods=False,
        solve_strategy="full", sclopf=False, run_ac_pf_after_lopf=False,
        extra_functionality_code="", discount_rate=float(v["discount_rate"]),
        default_lifetime=float(v["battery_storage_lifetime_years"]),
        demand_charge=demand_charge_config(tariff, [packs.IMPORT_LINK]))


def solve_site_option(option_id: str) -> tuple[pypsa.Network, object]:
    """(solved network, its SolverConfig) for one option, once per process."""
    from services.solver.objective import _wrap_with_demand_charge
    from services.solver_service import with_periodized_cost_defaults
    from services.study import packs
    from services.study.tariff import write_tariff_prices

    if option_id not in _SOLVED:
        ledger = site_ledger()
        tariff = site_tariff(ledger)
        n = packs.build_site_network(site_intake(), ledger, option_id,
                                     library=site_library())
        write_tariff_prices(n, tariff, packs.IMPORT_LINK, packs.EXPORT_LINK)
        cfg = gs_solver_config(ledger, tariff)
        with with_periodized_cost_defaults(n, cfg):
            status, condition = n.optimize(
                solver_name="highs",
                extra_functionality=_wrap_with_demand_charge(n, None, cfg))
        assert (status, condition) == ("ok", "optimal"), (option_id, status, condition)
        _SOLVED[option_id] = (n, cfg)
    return _SOLVED[option_id]
