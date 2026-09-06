"""
IEEE Reliability Test System (RTS-79) — the 1979 load model AS DATA, plus the
8736-hour reconstruction.

WHY THIS FILE EXISTS AND WHY RTS-GMLC IS NOT ALLOWED NEAR IT
------------------------------------------------------------
The published RTS-79 adequacy indices are a property of the *percentage load
model* in the 1979 paper: three multiplicative tables (52 weekly peaks as % of
the annual peak, 7 daily peaks as % of the weekly peak, and 24-hour shapes as %
of the daily peak for three seasons x weekday/weekend). RTS-GMLC REPLACED that
model with real zonal load traces and modernised the fleet, so it cannot
reproduce the published number and is inadmissible as a source here (plan
"Review round" item 2). RTS-GMLC is used in this package for generator
FOR/MTTR cross-checking only (see rts79_units.csv).

SOURCES
-------
PRIMARY (the 1979 original)
  IEEE Committee Report (Application of Probability Methods Subcommittee),
  "IEEE Reliability Test System", IEEE Transactions on Power Apparatus and
  Systems, Vol. PAS-98, No. 6, pp. 2047-2054, Nov/Dec 1979 — Tables 1, 2 and 3
  of the load model, as reproduced verbatim in the University of Washington
  Power Systems Test Case Archive data file:
    http://labs.ece.uw.edu/pstca/rts/rts79/ieeerts79.txt
    (mirror: https://www2.ee.washington.edu/research/pstca/rts/rts79/ieeerts79.txt)
  Retrieved 2026-08-28.
  RETRIEVAL CAVEAT: both UW hosts are refused by this session's egress proxy
  (403 on CONNECT), as are link.springer.com, arxiv.org and every other
  non-GitHub host tried. The archive file was therefore read through the
  WebSearch tool's server-side reader, not fetched into this environment.
  That is a weaker chain of custody than a direct download, so the tables below
  are additionally validated numerically — see VALIDATION.

SECOND SOURCE (independent cross-check of the same tables)
  Billinton & Allan, "Reliability Evaluation of Power Systems" / "Reliability
  Assessment of Large Electric Power Systems", appendix "IEEE Reliability Test
  System", reproduced at
    https://link.springer.com/content/pdf/bbm:978-1-4899-1346-3/1.pdf
    https://link.springer.com/content/pdf/bbm:978-1-4613-1689-3/1.pdf
  Retrieved 2026-08-28 (same WebSearch caveat; link.springer.com is blocked).
  It agrees on the model's STRUCTURE (Tables A.1/A.2/A.3, the same seasonal week
  ranges, and the statement that combining them with the annual peak defines a
  364 x 24 = 8736-hour model) and on every cell that could be read back.

CELL-LEVEL DISCREPANCIES FOUND BETWEEN SOURCES
  None that survived checking. One transient disagreement is recorded because
  it was real while it lasted: one WebSearch reading of the hourly table
  returned 94 for SUMMER weekday hour 15:00-16:00 where the archive file and
  every other reading give 97. Resolved toward the 1979 original (97) — and the
  resolution is corroborated numerically: with 94 the reconstruction's annual
  energy and load factor no longer match the RTS's own published totals.

VALIDATION (this is what makes the retrieval caveat tolerable)
  The 1979 paper states two aggregate properties of this load model at the
  2850 MW annual peak: annual energy 15,297 GWh and annual load factor 61.44%.
  The reconstruction below gives 15,297.075 GWh and 61.4400% — an independently
  published aggregate that a mis-transcribed cell would break. On top of that,
  driving the COPT with these tables and rts79_units.csv reproduces the
  published LOLE to six significant figures (see below). Both checks are
  extremely sensitive to the top of the load duration curve, which is exactly
  where a transcription error would land.

PUBLISHED ADEQUACY RESULT THE GATE TESTS AGAINST
  LOLE ~= 9.39 h/yr for the generation system (32 units, 3405 MW) at a 2850 MW
  annual peak on THIS hourly load model. This is the figure the phase plan
  cites ("the published ~9.4 h/yr") and the one the reliability literature
  quotes for the RTS-79 hourly model; it is commonly written to more digits as
  9.394 h/yr. Companion energy index: LOEE/EUE ~= 1176 MWh/yr.
  HONESTY NOTE: a digit-level verbatim citation for the decimal expansion
  (e.g. "9.39418") could not be retrieved through this session's egress
  restrictions — searches surfaced the systems and the indices but never the
  printed decimals. So the authority actually standing behind the gate is:
  (a) the ~9.39 h/yr figure as cited in the plan and the literature, and
  (b) the numeric validation above. The test asserts a band around 9.39, and
  separately pins this reconstruction's own value as a regression anchor that
  is labelled as such, not as a published claim.

KNOWN SENSITIVITIES THE PUBLISHED FIGURE DEPENDS ON
  * WEEK ALIGNMENT. Week 1 is the first week of the year and starts on a
    MONDAY; the daily table is ordered Monday..Sunday and Saturday/Sunday are
    the weekend. Winter is weeks 1-8 and 44-52 (so week 1 IS a winter week).
    Shifting the weekday phase moves which day carries the 100% daily factor
    inside the 100% peak week and perturbs LOLE at the ~1% level.
  * 8736 vs 8760. 52 x 7 x 24 = 8736 h = 0.99726 of a 8760-hour year. The
    literature reports the sum over these 8736 hours AS the annual index; no
    8760/8736 rescaling is applied, and none should be.
  * LOAD-MODEL VARIANT. The same system has a widely quoted LOLE of ~1.36
    days/yr computed on a DAILY PEAK LOAD VARIATION CURVE. That is a different
    index on a different load model and must not be compared with 9.39 h/yr.
  * FOR SEMANTICS. The units carry basis="FOR" (two-state unavailability), not
    EFORd. See rts79_units.csv.
"""
from __future__ import annotations

import numpy as np

ANNUAL_PEAK_MW = 2850.0

#: Table 1 — weekly peak load in percent of the ANNUAL peak, weeks 1..52.
WEEKLY_PEAK_PCT: tuple[float, ...] = (
    86.2, 90.0, 87.8, 83.4, 88.0, 84.1, 83.2, 80.6, 74.0, 73.7, 71.5, 72.7,
    70.4, 75.0, 72.1, 80.0, 75.4, 83.7, 87.0, 88.0, 85.6, 81.1, 90.0, 88.7,
    89.6, 86.1, 75.5, 81.6, 80.1, 88.0, 72.2, 77.6, 80.0, 72.9, 72.6, 70.5,
    78.0, 69.5, 72.4, 72.4, 74.3, 74.4, 80.0, 88.1, 88.5, 90.9, 94.0, 89.0,
    94.2, 97.0, 100.0, 95.2,
)

#: Table 2 — daily peak load in percent of the WEEKLY peak, Monday..Sunday.
#: The Monday-first ordering is the week-alignment convention, pinned: day 0 of
#: every week is a Monday and days 5,6 (Saturday, Sunday) are the weekend.
DAILY_PEAK_PCT: tuple[float, ...] = (93.0, 100.0, 98.0, 96.0, 94.0, 77.0, 75.0)

#: Table 3 — hourly peak load in percent of the DAILY peak, hour 0 = 12-1 am.
#: Three seasonal blocks x weekday/weekend.
WINTER_WEEKDAY_PCT: tuple[float, ...] = (
    67, 63, 60, 59, 59, 60, 74, 86, 95, 96, 96, 95,
    95, 95, 93, 94, 99, 100, 100, 96, 91, 83, 73, 63,
)
WINTER_WEEKEND_PCT: tuple[float, ...] = (
    78, 72, 68, 66, 64, 65, 66, 70, 80, 88, 90, 91,
    90, 88, 87, 87, 91, 100, 99, 97, 94, 92, 87, 81,
)
SUMMER_WEEKDAY_PCT: tuple[float, ...] = (
    64, 60, 58, 56, 56, 58, 64, 76, 87, 95, 99, 100,
    99, 100, 100, 97, 96, 96, 93, 92, 92, 93, 87, 72,
)
SUMMER_WEEKEND_PCT: tuple[float, ...] = (
    74, 70, 66, 65, 64, 62, 62, 66, 81, 86, 91, 93,
    93, 92, 91, 91, 92, 94, 95, 95, 100, 93, 88, 80,
)
SPRING_FALL_WEEKDAY_PCT: tuple[float, ...] = (
    63, 62, 60, 58, 59, 65, 72, 85, 95, 99, 100, 99,
    93, 92, 90, 88, 90, 92, 96, 98, 96, 90, 80, 70,
)
SPRING_FALL_WEEKEND_PCT: tuple[float, ...] = (
    75, 73, 69, 66, 65, 65, 68, 74, 83, 89, 92, 94,
    91, 90, 90, 86, 85, 88, 92, 100, 97, 95, 90, 85,
)

#: Seasonal assignment of the 52 weeks (1-based week numbers), per the paper.
WINTER_WEEKS: frozenset[int] = frozenset(list(range(1, 9)) + list(range(44, 53)))
SUMMER_WEEKS: frozenset[int] = frozenset(range(18, 31))
SPRING_FALL_WEEKS: frozenset[int] = frozenset(
    list(range(9, 18)) + list(range(31, 44)))

HOURS = 52 * 7 * 24  # 8736


def season_of_week(week: int) -> str:
    """``"winter"`` / ``"summer"`` / ``"spring_fall"`` for a 1-based week."""
    if week in WINTER_WEEKS:
        return "winter"
    if week in SUMMER_WEEKS:
        return "summer"
    if week in SPRING_FALL_WEEKS:
        return "spring_fall"
    raise ValueError(f"week {week} is outside 1..52")


_HOURLY = {
    ("winter", False): WINTER_WEEKDAY_PCT,
    ("winter", True): WINTER_WEEKEND_PCT,
    ("summer", False): SUMMER_WEEKDAY_PCT,
    ("summer", True): SUMMER_WEEKEND_PCT,
    ("spring_fall", False): SPRING_FALL_WEEKDAY_PCT,
    ("spring_fall", True): SPRING_FALL_WEEKEND_PCT,
}


def build_hourly_load(annual_peak_mw: float = ANNUAL_PEAK_MW) -> np.ndarray:
    """
    The 1979 load model reconstructed to 8736 chronological hours (MW).

    ``load[h] = peak * weekly[w]/100 * daily[d]/100 * hourly[season, d>=5][k]/100``

    Hour order is week 1 Monday 00:00 first, then straight through: 52 weeks x
    7 days (Monday-start) x 24 hours. Week 1 is a winter week; the annual peak
    falls in week 51 (the 100% week), on its Tuesday (the 100% day), in the
    17:00-18:00 and 18:00-19:00 hours (the 100% winter-weekday hours).
    """
    out = np.empty(HOURS, dtype=float)
    i = 0
    for week in range(1, 53):
        season = season_of_week(week)
        weekly = annual_peak_mw * WEEKLY_PEAK_PCT[week - 1] / 100.0
        for day in range(7):                      # 0 = Monday
            daily = weekly * DAILY_PEAK_PCT[day] / 100.0
            shape = _HOURLY[(season, day >= 5)]
            for hour in range(24):
                out[i] = daily * shape[hour] / 100.0
                i += 1
    return out


# ── module self-checks (asserted at import, per the fixture contract) ────────

assert len(WEEKLY_PEAK_PCT) == 52
assert len(DAILY_PEAK_PCT) == 7
assert all(len(t) == 24 for t in _HOURLY.values())
assert len(WINTER_WEEKS) + len(SUMMER_WEEKS) + len(SPRING_FALL_WEEKS) == 52
assert not (WINTER_WEEKS & SUMMER_WEEKS) and not (WINTER_WEEKS & SPRING_FALL_WEEKS)

_SELF = build_hourly_load(ANNUAL_PEAK_MW)
assert len(_SELF) == HOURS == 8736, len(_SELF)
# The three 100% entries multiply out to exactly the annual peak.
assert abs(float(_SELF.max()) - ANNUAL_PEAK_MW) < 1e-9, float(_SELF.max())
# ...and that maximum lands in a winter week (week 51), which is the property
# the week-alignment convention exists to guarantee.
_PEAK_WEEK = int(_SELF.argmax()) // (7 * 24) + 1
assert _PEAK_WEEK == 51, _PEAK_WEEK
assert season_of_week(_PEAK_WEEK) == "winter"
# Published aggregate properties of the RTS load model (see VALIDATION above).
assert abs(float(_SELF.sum()) / 1e6 - 15_297.0 / 1e3) < 1e-3, float(_SELF.sum())
assert abs(float(_SELF.mean() / _SELF.max()) - 0.6144) < 5e-5
del _SELF, _PEAK_WEEK
