# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""A per-snapshot CO2 intensity (t per MWh of electricity) for a generator, in
``generators_t.eh_co2_t_per_mwh`` (DC template review 03: the grid's intensity
by period). One reader for every surface that counts or prices CO2 — the two
Emissions views, the solve diagnostics, the asset results and the LP's CO2
price — so they cannot disagree. A generator with no column there is counted
at its carrier's ``co2_emissions`` / efficiency, bit for bit as before."""
from __future__ import annotations

import pandas as pd

OVERRIDE_ATTR = "eh_co2_t_per_mwh"


def has_override(n, name) -> bool:
    t = n.generators_t.get(OVERRIDE_ATTR)
    return t is not None and name in t.columns


def override_series(n, name, default: float, index=None) -> pd.Series | None:
    """t CO2 per MWh el of generator ``name`` at each snapshot of ``index`` (the
    network's snapshots by default); a snapshot with no figure reads ``default``,
    the carrier's own t/MWh el. None when the generator has no override."""
    if not has_override(n, name):
        return None
    s = n.generators_t[OVERRIDE_ATTR][name]
    return s.reindex(n.snapshots if index is None else index).fillna(default).astype(float)
