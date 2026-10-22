"""Every checked metric name belongs to one profile module.

`calculations.DEFINITIONS` merges the modules' metric tables; a name defined
twice would silently replace another profile's label, unit or formula id.
"""

from harness import (
    branch_calculations, calculations, capacity_calculations, carrier_calculations, energy_balance_calculations,
    curtailment_calculations, emissions_calculations, lost_load_calculations,
    price_calculations, site_import_calculations, storage_calculations, storage_costs, storage_cycles_calculations,
)

TABLES = (
    calculations.METRICS, calculations.COST_METRICS, calculations.COMPARISON_METRICS,
    storage_calculations.METRICS, storage_costs.METRICS, carrier_calculations.METRICS,
    branch_calculations.METRICS, branch_calculations.NETWORK_METRICS, lost_load_calculations.METRICS,
    price_calculations.METRICS, emissions_calculations.GENERATOR_METRICS,
    emissions_calculations.NETWORK_METRICS, curtailment_calculations.METRICS,
    capacity_calculations.METRICS, storage_cycles_calculations.METRICS, energy_balance_calculations.METRICS,
    site_import_calculations.METRICS,
)


def test_no_metric_name_is_defined_twice():
    names = [name for table in TABLES for name in table]
    assert len(names) == len(set(names)), sorted({n for n in names if names.count(n) > 1})
    assert set(calculations.DEFINITIONS) == set(names)


def test_every_profile_names_only_defined_metrics():
    for profile, metrics in calculations.PROFILES.items():
        assert set(metrics) <= set(calculations.DEFINITIONS), profile
