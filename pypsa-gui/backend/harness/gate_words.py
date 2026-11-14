# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Plain words for result-gate rule ids (study harness issue 44).

One map, rule id -> a short noun for a table cell. The report's gates table
reads it so no raw id such as ``mg.fuel.autonomy`` reaches the reader. An id
with no entry shows as ``unnamed check (<id>)``: visible, never silent.
A test enumerates every rule of every run profile so none lacks a noun.
"""

from __future__ import annotations

RULE_WORDS: dict[str, str] = {
    "dc.campus.contingency": "Campus survives a failure",
    "dc.campus.study": "Campus grid study",
    "dc.current_distortion": "Current distortion",
    "dc.dynamic": "Dynamic behaviour",
    "dc.energization_peak": "Energisation peak",
    "dc.flicker": "Voltage flicker",
    "dc.frequency_support": "Frequency support",
    "dc.grid_former": "Grid-forming ability",
    "dc.gridcode.frequency": "Grid frequency match",
    "dc.gridcode.published": "Grid-code profile published",
    "dc.gridcode.unchanged": "Grid-code profile unchanged",
    "dc.harmonic_distortion": "Harmonic distortion",
    "dc.ic.counterfactual": "Comparison case",
    "dc.ic.returns": "Investment returns",
    "dc.motor_start": "Motor start",
    "dc.oscillation": "Oscillation",
    "dc.ramp_rate": "Ramp rate",
    "dc.rapid_voltage_change": "Rapid voltage change",
    "dc.rapid_voltage_change_infrequent": "Rare rapid voltage change",
    "dc.reactive_range": "Reactive power range",
    "dc.ride_through": "Fault ride-through",
    "dc.scr": "Grid strength (SCR)",
    "dc.voltage_stability_pv": "Voltage stability (power)",
    "dc.voltage_stability_qv": "Voltage stability (reactive)",
    "dc.voltage_unbalance": "Voltage unbalance",
    "eh.adequacy.lole": "Reliability goal",
    "eh.energization.min_mw": "Grid connection size",
    "eh.readiness.ready": "Study readiness",
    "eh.review.blockers": "Review blockers",
    "eh.site.context": "Site map context",
    "eh.study.status": "Study finished",
    "ex.cases.settled": "What-if cases settled",
    "h2.electrolyser.utilisation": "Electrolyser use",
    "h2.ic.counterfactual": "Comparison case",
    "h2.offtake.met": "Hydrogen demand met",
    "ho.kpis_read": "Headline results read back",
    "in.diagnose.unserved_islands": "Every load connected",
    "in.readiness.ready": "Model ready to solve",
    "in.validate.errors": "Model errors",
    "ipp.export.limit": "Export limit",
    "ipp.ic.min_dscr": "Debt cover",
    "ipp.ic.pre_tax_irr": "Pre-tax return",
    "kpi.unserved_mwh": "Unserved energy",
    "mg.critical.tagged": "Critical loads tagged",
    "mg.fuel.autonomy": "Fuel autonomy",
    "mg.ic.baseline_keeps": "Comparison keeps baseline",
    "mg.ic.counterfactual": "Comparison case",
    "mg.island.critical_served": "Critical loads served",
    "mg.island.frequency": "Island frequency",
    "mg.island.survival": "Island survival",
    "mg.tie.open": "Grid tie open",
    "nh3.binary_check": "Process on/off check",
    "nh3.delivery.shortfall": "Ammonia delivery shortfall",
    "nh3.ic.counterfactual": "Comparison case",
    "nh3.process.units": "Process units",
    "res.fresh": "Results match inputs",
    "res.solve.status": "Solution found",
    "rp.report.status": "Report saved",
    "rp.report.unsupported_listed": "Unsupported chapters named",
    "set.project.exists": "Project exists",
    "set.project.template": "Project type",
    "sv.solve.state": "Solve finished",
}


def rule_words(rule_id: str) -> str:
    """The table-cell noun for `rule_id`; an unknown id falls back visibly."""
    return RULE_WORDS.get(rule_id) or f"unnamed check ({rule_id})"
