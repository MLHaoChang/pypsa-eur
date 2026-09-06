"""
Roy Billinton Test System (RBTS) — the load model AS DATA, plus the 8736-hour
reconstruction at a 185 MW annual peak.

THE LOAD MODEL IS THE IEEE-RTS ONE — HOW WELL THAT IS ESTABLISHED
-----------------------------------------------------------------
The RBTS does not define a load model of its own: it takes the same
hierarchical percentage model as the IEEE RTS (52 weekly peaks as % of annual,
7 daily peaks as % of weekly, seasonal 24-hour shapes as % of daily) and
applies it to a 185 MW annual system peak, giving the same 52 x 7 x 24 = 8736
chronological hours. What was actually retrievable on 2026-08-28 was (a) the
structural statement that the RBTS uses that same weekly/daily/hourly
percentage structure over 8736 h, and (b) the published indices below. A
verbatim sentence from the 1989 paper saying "the IEEE RTS load model is
adopted" was NOT retrievable through this session's egress restrictions, so the
adoption is established here structurally plus numerically, not by quotation —
be aware of that if this fixture is ever the thing under suspicion.
This module re-exports the tables from ``rts79_load`` rather than duplicating them — one set of numbers, one place to
audit, and no risk of the two fixtures silently drifting apart. The tables'
provenance, retrieval caveats and cross-check record live in ``rts79_load``'s
header and apply here unchanged, including the Monday-start / week-1-is-winter
alignment convention and the 8736-vs-8760 note.

The numerical corroboration is strong: driving the COPT with these units and
this load model reproduces the RBTS's published hourly-model indices (below) to
within 0.03% on LOLE and 0.001% on LOEE. A different load model would not land
on a 9.8613 MWh/yr energy index by coincidence.

SOURCES
-------
PRIMARY (units + system definition)
  R. Billinton et al., "A reliability test system for educational purposes -
  basic data", IEEE Transactions on Power Systems, Vol. 4, No. 3, pp. 1238-1244,
  August 1989. See rbts_units.csv for the retrieval record and cross-checks.
PRIMARY (the published indices)
  R. Billinton, S. Kumar, N. Chowdhury, K. Chu, L. Goel, E. Khan, P. Kos,
  G. Nourbakhsh, J. Oteng-Adjei, "A reliability test system for educational
  purposes - basic results", IEEE Transactions on Power Systems, Vol. 5, No. 1,
  pp. 319-325, February 1990.
    record: https://eprints.qut.edu.au/57041/
    open reproduction consulted:
      https://www.academia.edu/127155017/A_reliability_test_system_for_educational_purposes_basic_results
  Retrieved 2026-08-28 (WebSearch server-side reader; all these hosts are
  blocked by this session's egress proxy for direct fetch).
LOAD MODEL
  inherited from rts79_load (IEEE Committee Report, IEEE Trans. PAS-98, 1979).

PUBLISHED ADEQUACY RESULT THE GATE TESTS AGAINST
  Generation-level indices on the HOURLY load model at a 185 MW annual peak:
    LOLE  = 1.0919 h/yr   (some reproductions print 1.0917 h/yr)
    LOEE  = 9.8613 MWh/yr
  Both figures were read back from the RBTS "basic results" literature on
  2026-08-28. The 1.0917/1.0919 spread is itself the honest measure of how
  precisely this number is quoted, and the gate's tolerance is set from it.

KNOWN SENSITIVITIES
  * FOR ROUNDING is the dominant one here (see rbts_units.csv): rounded FORs
    give 1.09156 h/yr, exact MTTR/(MTTF+MTTR) gives 1.08805 h/yr — a 0.3% move,
    an order of magnitude larger than the 1.0917/1.0919 citation spread.
  * LOAD-MODEL VARIANT: the same paper also publishes indices on a constant
    load and on a daily-peak load model; those are different numbers and must
    not be compared with the hourly-model figures above.
  * Week alignment and 8736 vs 8760: identical to rts79_load; see there.
"""
from __future__ import annotations

import numpy as np

from tests.benchmark_data import rts79_load

ANNUAL_PEAK_MW = 185.0

# Re-exported for callers that want to see the tables the RBTS results use.
WEEKLY_PEAK_PCT = rts79_load.WEEKLY_PEAK_PCT
DAILY_PEAK_PCT = rts79_load.DAILY_PEAK_PCT
WINTER_WEEKDAY_PCT = rts79_load.WINTER_WEEKDAY_PCT
WINTER_WEEKEND_PCT = rts79_load.WINTER_WEEKEND_PCT
SUMMER_WEEKDAY_PCT = rts79_load.SUMMER_WEEKDAY_PCT
SUMMER_WEEKEND_PCT = rts79_load.SUMMER_WEEKEND_PCT
SPRING_FALL_WEEKDAY_PCT = rts79_load.SPRING_FALL_WEEKDAY_PCT
SPRING_FALL_WEEKEND_PCT = rts79_load.SPRING_FALL_WEEKEND_PCT

HOURS = rts79_load.HOURS  # 8736


def build_hourly_load(annual_peak_mw: float = ANNUAL_PEAK_MW) -> np.ndarray:
    """The RBTS 8736-hour chronological load (MW). See the module docstring."""
    return rts79_load.build_hourly_load(annual_peak_mw)


# ── module self-checks (asserted at import, per the fixture contract) ────────

_SELF = build_hourly_load(ANNUAL_PEAK_MW)
assert len(_SELF) == HOURS == 8736, len(_SELF)
assert abs(float(_SELF.max()) - ANNUAL_PEAK_MW) < 1e-9, float(_SELF.max())
_PEAK_WEEK = int(_SELF.argmax()) // (7 * 24) + 1
assert _PEAK_WEEK == 51, _PEAK_WEEK
assert rts79_load.season_of_week(_PEAK_WEEK) == "winter"
del _SELF, _PEAK_WEEK
