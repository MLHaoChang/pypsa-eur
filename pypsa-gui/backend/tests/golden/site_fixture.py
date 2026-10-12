"""
The second golden network: the SITE shape the guided investment study builds
(plan S5, review v1 B6 / S8), solved for real.

`tests/golden/fixture.py` is multi-period, has no import Link and never saw a
tariff, so it cannot check the investment case. This one is the production
shape, built by the production pack (`services/study/packs.py::
build_site_network`) from a ledger seeded from the pinned generic defaults
pack (`library.load_defaults`):

* one flat, non-leap year of 8760 hourly snapshots, weightings 1;
* buses `grid` and `site`; `grid_import` / `grid_export` Links with NO price
  of their own: the tariff (the pack's German-style illustrative seed — one
  energy band plus a per-MWh network charge, a monthly demand charge on peak
  import, a fixed charge and an export price) is compiled into the option's
  `SolverConfig.commercial` and priced by the Investment Case engine at
  solve time, the export price through a flat series bound on the network;
* a StorageUnit (2 h) written as its two upfront parts (C1); an optional PV
  Generator on the overnight-cost path.

The load is an uploaded series (the production `series_mw` path), shaped so
the battery is WORTH building: a flat base with a one-hour evening spike on
weekdays, which a 2-hour battery can shave and PV (dark by then) cannot. A
sector profile with broad office peaks sizes the battery-only option to zero,
which would make every CAPEX, FOM and replacement check vacuous (0 = 0).

**U2 WP10.** Until WP10 this fixture solved with GS's own LP pieces (the
tariff written into the Links, `SolverConfig.demand_charge` and GS's demand
wrapper) and was the oracle `tests/fixtures/u2_pre_numbers.json` (WP0) was
recorded on. Those pieces are deleted; the fixture now solves the way the
production runner does (`compile.commercial_from_ledger`, bound with
`compile.bind_on_network`, the option's `single_owner` value flows,
`compile.solver_config`, `run_simulation`), and the WP0 record is the
reference its figures are compared with (equal to solver tolerance, WP6/WP7,
or a recorded delta). Each option is solved once per process.
"""
from __future__ import annotations

import pandas as pd

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

    return study_library.load_defaults()


def site_ledger():
    from services.study import library as study_library
    from services.study import questions as Q

    return study_library.seed_ledger(Q.BESS_AT_SITE, site_intake(), site_library())


def site_tariff(ledger=None):
    """The guided tariff form with the ledger applied (what the commercial config compiles)."""
    from services.study import packs

    return packs.effective_tariff(site_intake(), ledger or site_ledger(), site_library())


#: The study-shaped export series ref every golden option is bound to (no
#: Library, no org: resolved as the tariff's flat export price).
EXPORT_REF = {"id": "decision-study:fake:" + "5" * 32 + ":export", "version": 1,
              "hash": "f" * 64, "source": "decision_study"}

_SOLVED: dict[str, tuple] = {}


def site_case_option(option_id: str) -> tuple:
    """
    (solved network, its SolverConfig, the compiled commercial, the ledger) of
    one option as the production runner makes its fork, once per process.
    """
    import queue
    import threading

    from services.solver_service import run_simulation
    from services.study import compile as C
    from services.study import packs

    if option_id not in _SOLVED:
        ledger = site_ledger()
        defaults = site_library()
        n = packs.build_site_network(site_intake(), ledger, option_id, library=defaults)
        compiled = C.commercial_from_ledger(site_intake(), ledger, defaults, n.snapshots,
                                            export_series=EXPORT_REF)
        price = float(compiled.tariff_meta["export_price_per_mwh"])
        series = pd.Series(price, index=pd.DatetimeIndex(n.snapshots), name="price")
        compiled = C.bind_on_network(n, compiled, resolve_ref=lambda _ref: series)
        compiled = C.with_value_flows(compiled, n)
        cfg = C.solver_config(ledger, compiled)
        status = run_simulation(cfg, n, threading.RLock(), threading.Event(),
                                queue.SimpleQueue())
        assert status == ("ok", "optimal"), (option_id, status)
        _SOLVED[option_id] = (n, cfg, compiled, ledger)
    return _SOLVED[option_id]


def solve_site_option(option_id: str) -> tuple:
    """(solved network, its SolverConfig) for one option, once per process."""
    n, cfg, _compiled, _ledger = site_case_option(option_id)
    return n, cfg
