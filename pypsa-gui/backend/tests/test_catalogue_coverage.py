"""
Coverage rule, component columns (spec expert-catalogue §3.5, issue 01a-2).

Every PyPSA input column of the catalogue's classes and every custom column the GUI may
write is a catalogue entry or a `not_offered.py` row with a reason, never both.
"""

import pypsa
import pytest

from models.energy_hub import EH_CUSTOM_COLUMNS
from models.process_unit import PROCESS_CUSTOM_COLUMNS
from services.asset_schema import catalogue as C
from services.asset_schema.fields import known_components
from services.asset_schema.not_offered import NOT_OFFERED, REASON_KINDS
from services.attribute_catalog import input_attributes

#: E4: the whole not-offered table, pinned as a literal (class → column → reason). A row added or removed changes this
#: on purpose, in review.
PINNED_NOT_OFFERED = {
    "Bus": {
        "location": "unsupported: a free-text place name; the site holds the coordinates.",
        "type": "unsupported: a free-text engine tag no study reads; the asset type comes from the catalogue.",
        "v_mag_pu_max": "pending: the voltage band for load flow; the PSS/E importer reads it (follow-up).",
        "v_mag_pu_min": "pending: the voltage band for load flow; the PSS/E importer reads it (follow-up).",
        "v_mag_pu_set": "pending: the voltage set point for load flow; the PSS/E importer reads it (follow-up).",
    },
    "Carrier": {
        "max_growth": "unsupported: a build-rate cap across investment periods is not offered.",
        "max_relative_growth": "unsupported: a build-rate cap across investment periods is not offered.",
    },
    "Generator": {
        "active": "unsupported: an engine switch that drops the asset; the tool keeps or removes an asset instead.",
        "down_time_before": "unsupported: the starting state of unit commitment; every solve starts from the engine default.",
        "marginal_cost_quadratic": "unsupported: quadratic running cost is not offered; the solve is linear.",
        "p_init": "unsupported: the starting state of unit commitment; every solve starts from the engine default.",
        "p_nom_mod": "unsupported: modular expansion (sizes in fixed blocks) is not offered.",
        "p_nom_set": "engine-internal: a fixed-size override for PyPSA's iterative solve; the tool sizes through the size and its bounds.",
        "p_set": "time series: the fixed power comes from the asset's profile.",
        "q_set": "time series: reactive power set points come from a profile; no study writes one value.",
        "ramp_limit_shut_down": "unsupported: start-up and shut-down ramp limits; the ramp limits are offered.",
        "ramp_limit_start_up": "unsupported: start-up and shut-down ramp limits; the ramp limits are offered.",
        "sign": "engine-internal: the sign convention PyPSA applies to the flow.",
        "type": "unsupported: a free-text engine tag no study reads; the asset type comes from the catalogue.",
        "up_time_before": "unsupported: the starting state of unit commitment; every solve starts from the engine default.",
        "weight": "engine-internal: the weight PyPSA uses when it clusters a network.",
    },
    "Line": {
        "active": "unsupported: an engine switch that drops the asset; the tool keeps or removes an asset instead.",
        "b": "pending: line shunt susceptance for load flow; the PSS/E importer writes it (follow-up).",
        "g": "pending: line shunt conductance for load flow; the PSS/E importer writes it (follow-up).",
        "num_parallel": "pending: parallel circuits; today one branch row is one circuit.",
        "overnight_cost": "unsupported: a line's building cost is set per km (cost per km × length).",
        "r": "pending: line resistance for load flow; the PSS/E importer writes it (follow-up).",
        "s_max_pu": "pending: the usable share of the rating (thermal margin); the rating is offered.",
        "s_nom_mod": "unsupported: modular expansion (sizes in fixed blocks) is not offered.",
        "s_nom_set": "engine-internal: a fixed-size override for PyPSA's iterative solve; the tool sizes through the size and its bounds.",
        "terrain_factor": "unsupported: the terrain cost factor is folded into the per-km cost.",
        "type": "pending: a standard line type that sets the impedances (follow-up).",
        "v_ang_max": "unsupported: voltage angle limits for the linear power flow are not offered.",
        "v_ang_min": "unsupported: voltage angle limits for the linear power flow are not offered.",
        "x": "pending: line reactance for load flow; the PSS/E importer writes it (follow-up).",
    },
    "Link": {
        "active": "unsupported: an engine switch that drops the asset; the tool keeps or removes an asset instead.",
        "down_time_before": "unsupported: the starting state of unit commitment; every solve starts from the engine default.",
        "length": "unsupported: a link's length feeds no cost or loss in the tool.",
        "marginal_cost_quadratic": "unsupported: quadratic running cost is not offered; the solve is linear.",
        "p_init": "unsupported: the starting state of unit commitment; every solve starts from the engine default.",
        "p_nom_mod": "unsupported: modular expansion (sizes in fixed blocks) is not offered.",
        "p_nom_set": "engine-internal: a fixed-size override for PyPSA's iterative solve; the tool sizes through the size and its bounds.",
        "p_set": "time series: the fixed power comes from the asset's profile.",
        "ramp_limit_shut_down": "unsupported: start-up and shut-down ramp limits; the ramp limits are offered.",
        "ramp_limit_start_up": "unsupported: start-up and shut-down ramp limits; the ramp limits are offered.",
        "shut_down_cost": "unsupported: a shut-down cost; the start-up cost is offered.",
        "terrain_factor": "unsupported: the terrain cost factor is folded into the per-km cost.",
        "type": "unsupported: a free-text engine tag no study reads; the asset type comes from the catalogue.",
        "up_time_before": "unsupported: the starting state of unit commitment; every solve starts from the engine default.",
    },
    "Load": {
        "active": "unsupported: an engine switch that drops the asset; the tool keeps or removes an asset instead.",
        "type": "unsupported: a free-text engine tag no study reads; the asset type comes from the catalogue.",
    },
    "ShuntImpedance": {
        "active": "unsupported: an engine switch that drops the asset; the tool keeps or removes an asset instead.",
        "sign": "engine-internal: the sign convention PyPSA applies to the flow.",
    },
    "StorageUnit": {
        "active": "unsupported: an engine switch that drops the asset; the tool keeps or removes an asset instead.",
        "control": "engine-internal: the load-flow control type PyPSA derives from the bus.",
        "cyclic_state_of_charge_per_period": "unsupported: per-period storage cycling for multi-period expansion is not offered.",
        "marginal_cost_quadratic": "unsupported: quadratic running cost is not offered; the solve is linear.",
        "marginal_cost_storage": "unsupported: a cost per stored MWh per hour is not offered.",
        "overnight_cost": "pending: a storage unit's building cost is set per part (power and energy); one total is a follow-up.",
        "p_dispatch_set": "time series: the fixed power comes from the asset's profile.",
        "p_max_pu": "time series: the availability comes from the asset's profile.",
        "p_min_pu": "time series: the minimum output share comes from the asset's profile.",
        "p_nom_mod": "unsupported: modular expansion (sizes in fixed blocks) is not offered.",
        "p_nom_set": "engine-internal: a fixed-size override for PyPSA's iterative solve; the tool sizes through the size and its bounds.",
        "p_set": "time series: the fixed power comes from the asset's profile.",
        "p_store_set": "time series: the fixed power comes from the asset's profile.",
        "q_set": "time series: reactive power set points come from a profile; no study writes one value.",
        "sign": "engine-internal: the sign convention PyPSA applies to the flow.",
        "spill_cost": "unsupported: spilling inflow is not modelled; the tool has no hydro inflow.",
        "state_of_charge_initial_per_period": "unsupported: per-period storage cycling for multi-period expansion is not offered.",
        "state_of_charge_set": "time series: a fixed state of charge comes from a profile.",
        "type": "unsupported: a free-text engine tag no study reads; the asset type comes from the catalogue.",
    },
    "Store": {
        "active": "unsupported: an engine switch that drops the asset; the tool keeps or removes an asset instead.",
        "delivery_interval_h": "engine-internal: set by the process-unit builder from the delivery plan, never in a form.",
        "e_cyclic_per_period": "unsupported: per-period storage cycling for multi-period expansion is not offered.",
        "e_initial_per_period": "unsupported: per-period storage cycling for multi-period expansion is not offered.",
        "e_nom_mod": "unsupported: modular expansion (sizes in fixed blocks) is not offered.",
        "e_nom_set": "engine-internal: a fixed-size override for PyPSA's iterative solve; the tool sizes through the size and its bounds.",
        "e_set": "time series: a fixed energy level comes from a profile.",
        "marginal_cost_quadratic": "unsupported: quadratic running cost is not offered; the solve is linear.",
        "marginal_cost_storage": "unsupported: a cost per stored MWh per hour is not offered.",
        "p_set": "time series: the fixed power comes from the asset's profile.",
        "q_set": "time series: reactive power set points come from a profile; no study writes one value.",
        "sign": "engine-internal: the sign convention PyPSA applies to the flow.",
        "type": "unsupported: a free-text engine tag no study reads; the asset type comes from the catalogue.",
    },
    "Transformer": {
        "active": "unsupported: an engine switch that drops the asset; the tool keeps or removes an asset instead.",
        "b": "pending: magnetising susceptance for load flow; the PSS/E importer writes it (follow-up).",
        "g": "pending: magnetising conductance for load flow; the PSS/E importer writes it (follow-up).",
        "num_parallel": "pending: parallel circuits; today one branch row is one circuit.",
        "s_max_pu": "pending: the usable share of the rating (thermal margin); the rating is offered.",
        "s_nom_mod": "unsupported: modular expansion (sizes in fixed blocks) is not offered.",
        "s_nom_set": "engine-internal: a fixed-size override for PyPSA's iterative solve; the tool sizes through the size and its bounds.",
        "tap_position": "pending: the tap step; the tap ratio is offered (follow-up).",
        "tap_side": "pending: which winding carries the tap; the tap ratio is offered (follow-up).",
        "type": "pending: a standard transformer type that sets the impedances (follow-up).",
        "v_ang_max": "unsupported: voltage angle limits for the linear power flow are not offered.",
        "v_ang_min": "unsupported: voltage angle limits for the linear power flow are not offered.",
    },
}

ENTRY_COLUMNS = {(e.scope, e.model_name) for e in C.entries()}


def _writable_columns() -> set[tuple[str, str]]:
    n = pypsa.Network()
    out = {(c, a) for c in known_components() for a in input_attributes(n, c)}
    for registry in (EH_CUSTOM_COLUMNS, PROCESS_CUSTOM_COLUMNS):
        out |= {(c, col) for c, cols in registry.items() for col in cols}
    return out


def _not_offered() -> set[tuple[str, str]]:
    return {(c, col) for c, cols in NOT_OFFERED.items() for col in cols}


def test_every_writable_column_is_an_entry_or_not_offered():
    missing = sorted(_writable_columns() - ENTRY_COLUMNS - _not_offered())
    assert not missing, (
        f"columns in neither the catalogue nor not_offered.py: {missing}. Add an entry, "
        "or a not_offered.py row with a one-line reason."
    )


def test_no_column_is_both_an_entry_and_not_offered():
    assert not sorted(ENTRY_COLUMNS & _not_offered())


def test_not_offered_rows_name_real_columns():
    """A stale row (a column PyPSA or the GUI no longer has) is removed, not kept."""
    assert not sorted(_not_offered() - _writable_columns())


@pytest.mark.parametrize("cls,col", sorted(_not_offered()))
def test_every_not_offered_row_has_a_one_line_reason(cls, col):
    reason = NOT_OFFERED[cls][col]
    kind, _, rest = reason.partition(": ")
    assert kind in REASON_KINDS, (cls, col, reason)
    assert rest and "\n" not in reason and len(reason) <= 140, (cls, col, reason)


def test_not_offered_is_the_pinned_table():
    assert NOT_OFFERED == PINNED_NOT_OFFERED
    assert all(r.strip() for cols in NOT_OFFERED.values() for r in cols.values())
