"""AA-05 S5a (A4.6, AA05-FREEZE-1): the window identity an evidence record stores.

A checked fact names its window: start, end, the number of snapshots in it,
and, per metric, the snapshot weighting that multiplies it. The weight basis
is declared here for every metric in `harness.calculations.DEFINITIONS`. A
test holds the two key sets equal. The raw-data oracles in
tests/assurance_acceptance check one metric per profile module against it.

Tokens: "generators", "objective" or "stores" name a snapshot_weightings
column; "generators*years" is generator weights times the investment period's
years; "none" means no snapshot weighting (peaks, ratings, capacities, counts,
values read from a report); "mixed:<a>+<b>" combines two columns;
"derived" metrics are computed from other metrics only.
"""

import pandas as pd

from harness import grid_readiness_calculations as readiness
from harness import investment_calculations as investment

WEIGHT_BASIS: dict[str, str] = {
    # calculations.py
    "dispatch_energy": "generators",
    "marginal_expenditure": "objective",
    "peak_dispatch": "none",
    "annual_fixed_cost": "none",
    "allocated_fixed_cost": "derived",
    "total_cost": "derived",
    "cost_delta": "derived",
    "cost_change_percent": "derived",
    # storage_calculations.py: duration = stores weights
    "charged_energy": "stores",
    "discharged_energy": "stores",
    "net_storage_energy": "derived",
    "peak_charging_power": "none",
    "peak_discharging_power": "none",
    "final_state_of_charge": "none",
    # storage_costs.py
    "storage_discharge_expenditure": "objective",
    "storage_holding_expenditure": "objective",
    "storage_operating_expenditure": "derived",
    # carrier_calculations.py: storage members through storage.calculate
    "carrier_generator_energy": "generators",
    "carrier_peak_generator_output": "none",
    "carrier_storage_discharged_energy": "stores",
    "carrier_storage_charged_energy": "stores",
    # branch_calculations.py
    "peak_branch_utilisation": "none",
    "hours_above_rating": "generators",
    "peak_branch_flow": "none",
    "branch_rating": "none",
    "max_branch_utilisation": "none",
    "branches_above_rating": "none",
    # lost_load_calculations.py
    "lost_load_energy": "generators",
    "peak_shed": "none",
    "shed_hours": "generators",
    "lost_load_cost": "objective",
    # price_calculations.py: the mean divides by the sum of weights
    "mean_price": "generators",
    "min_price": "none",
    "max_price": "none",
    "hours_at_voll": "generators",
    # emissions_calculations.py
    "generator_emissions": "generators",
    "total_emissions": "generators",
    "co2_cap_shadow_price": "none",
    # curtailment_calculations.py
    "available_energy": "generators",
    "curtailed_energy": "generators",
    "curtailment_share": "generators",
    # capacity_calculations.py
    "optimal_capacity": "none",
    "capacity_change": "none",
    "configured_capacity": "none",
    # storage_cycles_calculations.py
    "window_discharged_energy": "stores",
    "energy_capacity": "none",
    "equivalent_cycles": "stores",
    # energy_balance_calculations.py: the state change is rescaled by generator over store weights
    "generation_energy": "generators",
    "served_energy": "generators",
    "unserved_energy": "generators",
    "storage_change": "mixed:generators+stores",
    "export_losses_and_conversion": "derived",
    # site_import_calculations.py
    "grid_import_energy": "generators",
    "peak_grid_import": "none",
    "export_energy": "generators",
    "hours_at_import_limit": "generators",
    # investment_calculations.py: yearly rows of the stored report
    "equity_npv": "none",
    "project_npv": "none",
    "equity_irr": "none",
    "project_irr": "none",
    "lcoe_nominal": "none",
    "lcoe_real": "none",
    # copt_calculations.py: years apply only on a multi-period network
    "screening_lole": "generators*years",
    "screening_eue": "generators*years",
    "screening_lolp_max": "none",
    # grid_readiness_calculations.py: row counts
    "checked_pass": "none",
    "checked_warn": "none",
    "checked_fail": "none",
    "not_checked": "none",
}

# Profiles whose captured index is not snapshots: the investment report's
# years and the saved readiness scorecard.
NO_SNAPSHOT_INDEX = frozenset({*investment.PROFILES, readiness.PROFILE})


def _count(index, start, end):
    """Snapshots in [start, end]. Profiles write the index as isoformat or as
    str(Timestamp), so instants are compared; a boundary not in it gives None."""
    try:
        stamps = [pd.Timestamp(v) for v in index]
        a, b = stamps.index(pd.Timestamp(start)), stamps.index(pd.Timestamp(end))
    except (ValueError, TypeError):
        return None
    return b - a + 1 if b >= a else None


def window(index, scope):
    """The record's window: exact boundaries, snapshot count and weight basis.

    `index` is the profile's snapshot index as captured (isoformat strings).
    The scope's end is inclusive, as `calculations._window` reads it: in index
    positions the window is [a, b + 1). `snapshots` is None for a profile
    whose index is not snapshots (investment years, the readiness scorecard)."""
    from harness.calculations import requested_metrics

    start = scope.get("start", index[0] if index else None)
    end = scope.get("end", index[-1] if index else None)
    return {
        "start": start,
        "end": end,
        "snapshots": None
        if scope["profile"] in NO_SNAPSHOT_INDEX
        else _count(index, start, end),
        # A metric no profile defines (the pilot's unsupported ask) has no
        # weighting; its slot stays unavailable with its own reason.
        "weight_basis": {
            m: WEIGHT_BASIS[m] for m in requested_metrics(scope) if m in WEIGHT_BASIS
        },
    }
