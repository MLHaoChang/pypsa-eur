"""
How ``tests/fixtures/pre_u2_fork/`` was written (U2 WP10, gate WP6 W4), kept as
the record; it ran ONCE on the tree before WP10 deleted the GS tariff code
(`write_tariff_prices`, `demand_charge_config`, `_wrap_with_demand_charge`,
`SolverConfig.demand_charge`) and cannot run after it.

A decision study before U2 wrote each network the way this script does:

* the site pack of the golden site intake (DE seed, monthly demand charge),
  seeded from the legacy ``study_library`` ledger;
* the battery as ONE annualised ``capital_cost`` (two annuities, pack meta
  ``cost_basis = derived_from_two_annuities``), no upfront part columns;
* the tariff's energy and network prices written into
  ``links_t.marginal_cost`` of ``grid_import`` (positive) and ``grid_export``
  (the export credit, negative) as PERMANENT network data;
* ``solver_config.json`` = ``asdict(SolverConfig)`` carrying
  ``demand_charge`` (the spec GS's LP wrapper read) and no ``commercial``.

``base/`` is the unsolved grid-only baseline (``none``); ``fork/`` is the
``bess_1h`` option solved through ``run_simulation`` with GS's wrapper, so
it also carries F1-B4's ``n.meta["pypsa_gui_solve_demand_charge"]`` record.
Both are cut to the first 14 days (336 snapshots) to keep the fixture small:
the refusal reads the network's shape, not its horizon.

The two netCDFs are committed with ``git add -f`` (``*.nc`` is gitignored) and
cannot be rebuilt; their sha256 (final U2 gate, U1):

* ``base/network.nc`` 6116c2a3793f8bdb6bf8418edcd05182fc2afd68885beafdb53f4430605e7cbc
* ``fork/network.nc`` dbdf588a0f86c4a0704bc3f3ddec667eb2aac88a80d796b93a38092f816d05f9

    cd pypsa-gui/backend
    PYTHONPATH=.. python tests/fixtures/pre_u2_fork/make_pre_u2_fork.py
"""
from __future__ import annotations

import json
import pathlib
import queue
import sys
import threading
from dataclasses import asdict

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

HOURS = 336


def _pre_u2_network(option_id: str):
    from services.study import library as L
    from services.study import packs
    from services.study import questions as Q
    from services.study.tariff import write_tariff_prices
    from tests.golden import site_fixture as sf

    lib = L.load_library()
    ledger = L.seed_ledger(Q.BESS_AT_SITE, sf.site_intake(), lib)
    tariff = packs.effective_tariff(sf.site_intake(), ledger, lib)
    n = packs.build_site_network(sf.site_intake(), ledger, option_id, library=lib)
    n.set_snapshots(n.snapshots[:HOURS])
    meta = dict(n.meta[packs.PACK_META_KEY])
    if packs.BATTERY_NAME in n.storage_units.index:
        hours = float(n.storage_units.at[packs.BATTERY_NAME, "max_hours"])
        parts = [c for c in n.storage_units.columns if c.startswith(("inv_power_", "inv_energy_"))]
        n.storage_units.drop(columns=parts, inplace=True)
        n.storage_units.loc[packs.BATTERY_NAME, "capital_cost"] = (
            packs.battery_capital_cost_eur_per_mw(ledger, hours))
        meta["cost_basis"] = {**meta["cost_basis"],
                              packs.BATTERY_NAME: "derived_from_two_annuities"}
    meta["ledger_version"] = ledger.ledger_version
    n.meta[packs.PACK_META_KEY] = meta
    write_tariff_prices(n, tariff, packs.IMPORT_LINK, packs.EXPORT_LINK)
    return n, ledger, tariff


def _gs_config(ledger, tariff):
    from tests.golden import site_fixture as sf

    return sf.gs_solver_config(ledger, tariff)


def main() -> int:
    from services.solver_service import run_simulation

    for sub, option, solve in (("base", "none", False), ("fork", "bess_1h", True)):
        n, ledger, tariff = _pre_u2_network(option)
        cfg = _gs_config(ledger, tariff)
        assert cfg.demand_charge, "the DE seed carries a demand charge"
        if solve:
            status = run_simulation(cfg, n, threading.RLock(), threading.Event(),
                                    queue.SimpleQueue())
            assert status == ("ok", "optimal"), status
            assert n.meta.get("pypsa_gui_solve_demand_charge"), "F1-B4 record"
        out = HERE / sub
        out.mkdir(exist_ok=True)
        n.export_to_netcdf(str(out / "network.nc"))
        (out / "solver_config.json").write_text(json.dumps(asdict(cfg), indent=2) + "\n",
                                                encoding="utf-8")
        print(sub, option, len(n.snapshots), "snapshots")
    return 0


if __name__ == "__main__":
    sys.exit(main())
