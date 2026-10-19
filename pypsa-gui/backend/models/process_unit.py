"""
Grid-to-process custom network columns (ticket 02): the process-unit columns on Link and the tonne factor on a
product Bus.

Not PyPSA attributes and not Energy Hub tags, so they have their own registry beside `EH_CUSTOM_COLUMNS`. They ride
the same lazy-column machinery (`services/adequacy/eh_columns.py`): a project that never writes one never gets the
column. Kinds are those of `eh_columns`: "bool", "float_nonneg", "tier" (one of CRITICALITY_TIERS or "") and "text".
"""
from __future__ import annotations

# How a process unit's electrical supply is ranked when it must be cut (CONTEXT.md, Criticality tier). The empty
# string is "not set".
CRITICALITY_TIERS: tuple[str, ...] = ("safety", "process_critical", "process_flexible", "deferrable")

PROCESS_CUSTOM_COLUMNS: dict[str, dict[str, str]] = {
    "Bus": {"mwh_per_t": "float_nonneg"},
    "Link": {"eh_process": "bool", "criticality_tier": "tier", "throughput_unit": "text",
             "specific_energy_kwh_per_t": "float_nonneg"},
}
