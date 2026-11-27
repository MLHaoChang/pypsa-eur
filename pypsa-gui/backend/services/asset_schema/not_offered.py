"""
Component columns the catalogue does not offer as entries, each with a one-line reason
(spec expert-catalogue §3.5, coverage rule).

Every PyPSA input column of the catalogue's classes and every custom column the GUI may
write (`EH_CUSTOM_COLUMNS`, `PROCESS_CUSTOM_COLUMNS`) is either a catalogue entry or a
row here; `tests/test_catalogue_coverage.py` fails on a column in neither and on a row
here that has since become an entry.

A reason opens with its kind:
- `engine-internal`: PyPSA's own bookkeeping, never a planner's input;
- `time series`: set through a profile, not as one value on the asset;
- `unsupported`: an engine feature no workflow in the tool offers;
- `pending`: an input an engine reads that the catalogue should offer; a named follow-up
  (the Expert form adds it in a later slice without changing today's asset form).
"""

from __future__ import annotations

_INTERNAL_SIGN = "engine-internal: the sign convention PyPSA applies to the flow."
_SET = "engine-internal: a fixed-size override for PyPSA's iterative solve; the tool sizes through the size and its bounds."
_MOD = "unsupported: modular expansion (sizes in fixed blocks) is not offered."
_ACTIVE = "unsupported: an engine switch that drops the asset; the tool keeps or removes an asset instead."
_TYPE_TAG = "unsupported: a free-text engine tag no study reads; the asset type comes from the catalogue."
_QUADRATIC = "unsupported: quadratic running cost is not offered; the solve is linear."
_UC_INIT = "unsupported: the starting state of unit commitment; every solve starts from the engine default."
_UC_RAMP = (
    "unsupported: start-up and shut-down ramp limits; the ramp limits are offered."
)
_P_SET = "time series: the fixed power comes from the asset's profile."
_Q_SET = "time series: reactive power set points come from a profile; no study writes one value."
_PERIOD = (
    "unsupported: per-period storage cycling for multi-period expansion is not offered."
)
_V_ANG = "unsupported: voltage angle limits for the linear power flow are not offered."
_S_MAX_PU = (
    "pending: the usable share of the rating (thermal margin); the rating is offered."
)
_NUM_PARALLEL = "pending: parallel circuits; today one branch row is one circuit."
_TERRAIN = "unsupported: the terrain cost factor is folded into the per-km cost."

NOT_OFFERED: dict[str, dict[str, str]] = {
    "Bus": {
        "location": "unsupported: a free-text place name; the site holds the coordinates.",
        "type": _TYPE_TAG,
        "v_mag_pu_max": "pending: the voltage band for load flow; the PSS/E importer reads it (follow-up).",
        "v_mag_pu_min": "pending: the voltage band for load flow; the PSS/E importer reads it (follow-up).",
        "v_mag_pu_set": "pending: the voltage set point for load flow; the PSS/E importer reads it (follow-up).",
    },
    "Carrier": {
        "max_growth": "unsupported: a build-rate cap across investment periods is not offered.",
        "max_relative_growth": "unsupported: a build-rate cap across investment periods is not offered.",
    },
    "Generator": {
        "active": _ACTIVE,
        "down_time_before": _UC_INIT,
        "marginal_cost_quadratic": _QUADRATIC,
        "p_init": _UC_INIT,
        "p_nom_mod": _MOD,
        "p_nom_set": _SET,
        "p_set": _P_SET,
        "q_set": _Q_SET,
        "ramp_limit_shut_down": _UC_RAMP,
        "ramp_limit_start_up": _UC_RAMP,
        "sign": _INTERNAL_SIGN,
        "type": _TYPE_TAG,
        "up_time_before": _UC_INIT,
        "weight": "engine-internal: the weight PyPSA uses when it clusters a network.",
    },
    "Line": {
        "active": _ACTIVE,
        "b": "pending: line shunt susceptance for load flow; the PSS/E importer writes it (follow-up).",
        "g": "pending: line shunt conductance for load flow; the PSS/E importer writes it (follow-up).",
        "num_parallel": _NUM_PARALLEL,
        "overnight_cost": "unsupported: a line's building cost is set per km (cost per km × length).",
        "r": "pending: line resistance for load flow; the PSS/E importer writes it (follow-up).",
        "s_max_pu": _S_MAX_PU,
        "s_nom_mod": _MOD,
        "s_nom_set": _SET,
        "terrain_factor": _TERRAIN,
        "type": "pending: a standard line type that sets the impedances (follow-up).",
        "v_ang_max": _V_ANG,
        "v_ang_min": _V_ANG,
        "x": "pending: line reactance for load flow; the PSS/E importer writes it (follow-up).",
    },
    "Link": {
        "active": _ACTIVE,
        "down_time_before": _UC_INIT,
        "length": "unsupported: a link's length feeds no cost or loss in the tool.",
        "marginal_cost_quadratic": _QUADRATIC,
        "p_init": _UC_INIT,
        "p_nom_mod": _MOD,
        "p_nom_set": _SET,
        "p_set": _P_SET,
        "ramp_limit_shut_down": _UC_RAMP,
        "ramp_limit_start_up": _UC_RAMP,
        "shut_down_cost": "unsupported: a shut-down cost; the start-up cost is offered.",
        "terrain_factor": _TERRAIN,
        "type": _TYPE_TAG,
        "up_time_before": _UC_INIT,
    },
    "Load": {
        "active": _ACTIVE,
        "type": _TYPE_TAG,
    },
    "ShuntImpedance": {
        "active": _ACTIVE,
        "sign": _INTERNAL_SIGN,
    },
    "StorageUnit": {
        "active": _ACTIVE,
        "control": "engine-internal: the load-flow control type PyPSA derives from the bus.",
        "cyclic_state_of_charge_per_period": _PERIOD,
        "marginal_cost_quadratic": _QUADRATIC,
        "marginal_cost_storage": "unsupported: a cost per stored MWh per hour is not offered.",
        "overnight_cost": "pending: a storage unit's building cost is set per part (power and energy); one total is a follow-up.",
        "p_dispatch_set": _P_SET,
        "p_max_pu": "time series: the availability comes from the asset's profile.",
        "p_min_pu": "time series: the minimum output share comes from the asset's profile.",
        "p_nom_mod": _MOD,
        "p_nom_set": _SET,
        "p_set": _P_SET,
        "p_store_set": _P_SET,
        "q_set": _Q_SET,
        "sign": _INTERNAL_SIGN,
        "spill_cost": "unsupported: spilling inflow is not modelled; the tool has no hydro inflow.",
        "state_of_charge_initial_per_period": _PERIOD,
        "state_of_charge_set": "time series: a fixed state of charge comes from a profile.",
        "type": _TYPE_TAG,
    },
    "Store": {
        "active": _ACTIVE,
        "delivery_interval_h": "engine-internal: set by the process-unit builder from the delivery plan, never in a form.",
        "e_cyclic_per_period": _PERIOD,
        "e_initial_per_period": _PERIOD,
        "e_nom_mod": _MOD,
        "e_nom_set": _SET,
        "e_set": "time series: a fixed energy level comes from a profile.",
        "marginal_cost_quadratic": _QUADRATIC,
        "marginal_cost_storage": "unsupported: a cost per stored MWh per hour is not offered.",
        "p_set": _P_SET,
        "q_set": _Q_SET,
        "sign": _INTERNAL_SIGN,
        "type": _TYPE_TAG,
    },
    "Transformer": {
        "active": _ACTIVE,
        "b": "pending: magnetising susceptance for load flow; the PSS/E importer writes it (follow-up).",
        "g": "pending: magnetising conductance for load flow; the PSS/E importer writes it (follow-up).",
        "num_parallel": _NUM_PARALLEL,
        "s_max_pu": _S_MAX_PU,
        "s_nom_mod": _MOD,
        "s_nom_set": _SET,
        "tap_position": "pending: the tap step; the tap ratio is offered (follow-up).",
        "tap_side": "pending: which winding carries the tap; the tap ratio is offered (follow-up).",
        "type": "pending: a standard transformer type that sets the impedances (follow-up).",
        "v_ang_max": _V_ANG,
        "v_ang_min": _V_ANG,
    },
}

#: The reason kinds a row may open with.
REASON_KINDS = ("engine-internal", "time series", "unsupported", "pending")
