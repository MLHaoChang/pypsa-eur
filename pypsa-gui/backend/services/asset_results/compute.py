"""Per-metric computation. One function per metric; no HTTP, no serialisation."""
from __future__ import annotations

from typing import Any


def _todo(*_a: Any, **_k: Any):  # replaced wholesale in Task 2
    raise NotImplementedError


def not_yet(*_a: Any, **_k: Any) -> None:
    """Phase 2/3 placeholder metric — never invoked (always resolves `na`)."""
    return None


summary_identity = summary_params = _todo
gen_p_nom = gen_p_nom_opt = gen_p_nom_delta = gen_capex_annual = _todo
gen_p_nom_by_vintage = _todo
gen_p = gen_p_max_pu = gen_available = gen_curtailment = _todo
gen_capacity_factor = gen_status = gen_start_up = gen_shut_down = _todo
gen_energy = gen_full_load_hours = gen_mean_cf = gen_curtailed_energy = _todo
gen_peak = gen_zero_hours = gen_max_ramp_up = gen_max_ramp_down = _todo
gen_n_starts = gen_q = _todo
gen_bus_price = gen_mu_upper = gen_mu_lower = _todo
gen_capture_price = gen_capture_rate = gen_binding_hours = _todo
gen_revenue = gen_vom = gen_fixed_cost = gen_net_profit = gen_lcoe = _todo
gen_co2_rate = gen_co2_total = gen_co2_intensity = _todo
