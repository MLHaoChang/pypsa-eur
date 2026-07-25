"""
Shared economics / emissions primitives used by BOTH the single-network
Results endpoints (``routers/results.py``) and the cross-scenario Compare
view (``routers/compare.py``).

Why this module exists: those two files answer different questions — Results
details one network, Compare aggregates several — but they must agree on the
*inputs* to those answers. Where they each carried their own copy of a lookup,
the two views could silently disagree on the same network, and a fix applied
to one would not reach the other.

LEAF module: imports only stdlib + pandas. Must never import ``routers.*`` or
other ``services.*`` (beyond equally-leaf siblings) so anything can depend on
it without a cycle. Same contract as ``services/serialization.py`` and
``services/period_utils.py``.

Deliberately NOT here:
  * The annuity / capital-recovery factor. That already has a single home in
    ``services.solver_service._annuity`` and is imported from there — the LP
    and the reporting layer must not diverge on it.
  * The per-snapshot weighting basis (``snapshot_weightings × investment_
    period_weightings.years``) — that lives in ``services.period_utils.
    snapshot_weights``.
  * ``lp_scaled_load_frame`` / ``corrected_marginal_prices``. Both are genuinely
    shared (compare.py imports them from results.py) but they depend on
    ``routers.results._result_df``, which reads the ``routers.simulation._state``
    module global. Moving them here means untangling that global first; until
    then compare.py imports them from results.py directly, which is safe —
    verified that neither import order cycles.
"""
from __future__ import annotations

import math


def co2_intensity_map(n) -> dict[str, float]:
    """
    ``carrier_name_lower -> co2_emissions`` (tCO2 per MWh of PRIMARY energy)
    for every carrier whose value is finite.

    This is the intensity PyPSA's primary-energy global constraint reads, so
    emissions must be computed as
    ``dispatch × weight × co2_intensity / efficiency`` to match what the LP
    actually enforced.

    Keys are lower-cased because component ``carrier`` values are looked up
    case-insensitively downstream. Returns an EMPTY dict when the network has
    no carriers frame or no ``co2_emissions`` column — callers treat a missing
    carrier as zero-emitting.

    Values are coerced with ``float(v)`` rather than gated on
    ``isinstance(v, (int, float))``. routers/results.py previously used the
    isinstance form, which silently DROPPED any carrier whose co2_emissions
    arrived as a numeric string (e.g. from a CSV-imported carriers table)
    while routers/compare.py's coercing form kept it — so the Results tab and
    the Compare rail could report different emissions for the same network.
    Coercion is the superset and makes the two agree.
    """
    out: dict[str, float] = {}
    carriers = getattr(n, "carriers", None)
    if carriers is None or carriers.empty or "co2_emissions" not in carriers.columns:
        return out
    try:
        for k, v in carriers["co2_emissions"].items():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(fv):
                continue
            out[str(k).lower()] = fv
    except Exception:
        return out
    return out
