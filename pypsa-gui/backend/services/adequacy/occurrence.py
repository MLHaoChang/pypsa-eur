"""
Occurrence data for the adequacy / solution-FMEA work: per-asset outage-rate
resolution and the per-carrier default library.

Design: docs/superpowers/specs/2026-08-27-solution-fmea-adequacy-design.md §5.4.

Three custom columns feed this module (declared on the create schemas in
``models/schemas.py``, stored as plain DataFrame columns):

* ``outage_rate_value`` — probability-like unavailability in [0, 1)
* ``outage_rate_basis`` — ``"FOR"`` (service-hours based, what NERC GADS class
  averages publish) or ``"EFORd"`` (demand-based, what adequacy mathematics
  wants). These differ materially for units with reserve-shutdown hours —
  peakers, exactly the units that matter at the margin — where FOR biases
  availability optimistic. Converting between them needs the unit's demand
  factor and service/reserve-shutdown split, which the model does not carry,
  so THE TOOL NEVER SILENTLY CONVERTS. The basis is stored, propagated, and
  reported; downstream COPT metrics are tagged with the mix of bases that
  fed them.
* ``mttr_hours`` — mean time to repair, hours.
* ``p_max_pu_includes_outages`` — Phase 12h. A boolean on Generator saying
  "the availability I typed ALREADY contains forced outages", which is the
  only thing that distinguishes the two readings of a sub-1 ``p_max_pu``: a
  typed capacity factor on a farm (apply the rate on top) versus a historical
  CF table such as PyPSA-Eur's ``nuclear_p_max_pu.csv`` (do not). Where it is
  set, ``resolve_outage_params`` returns ``rate = 0.0``, so BOTH the engines
  and the reserve margin stop applying the rate — by construction, since every
  consumer reads ``q`` from this one frame.

NaN/None means "unset": resolution falls back to the carrier default library,
and a carrier without a library entry yields ``source="missing"`` with NaN
values — an asset is EXCLUDED from occurrence-based analysis rather than
given a guessed number.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

VALID_BASES = ("FOR", "EFORd")

# Phase 12h. The flag column, its reader and its normaliser.
#
# It must be a real ``bool`` dtype column wherever it exists, and that is not
# a tidiness preference: netCDF refuses to export an ``object`` column whose
# values are all bools (`unsupported dtype for netCDF4 variable: bool`), and
# an ``object`` column is exactly what ANY ``n.add`` that omits the column
# leaves behind — including the solve's own VOLL and DSR slack rows, which are
# added and then removed, so the next project save would 500 and the undo
# snapshot would fail silently. (Measured: ``[True, False, nan]`` and
# ``[True, False, None]`` export fine; ``[True, False, True]`` raises.)
FLAG_COL = "p_max_pu_includes_outages"


def flag_is_set(v: object) -> bool:
    """The ONE reader of the flag. True only for a real affirmative: a bool,
    1/1.0, or the strings ``"true"``/``"1"``/``"yes"`` case-insensitively —
    the same set ``PATCH /_bulk``'s own boolean branch accepts. NaN (the value
    a column gains when a row is added without it), None and ``"False"`` are
    all False."""
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(f):
        return False
    return f != 0.0


def normalise_flag_column(n, component: str = "generators") -> None:
    """Make the flag column exist and be ``bool`` dtype on ``n.<component>``.

    Creates it (all ``False``) when absent — which is what lets a user
    bulk-flag an imported network whose frame has never carried the column —
    and otherwise maps every cell through :func:`flag_is_set`. Idempotent and
    cheap (0.17 ms on a 300-row frame), so it can sit at every boundary that
    needs it: the netCDF export helper, the solver's restore callback, the
    create/update/bulk routes, and every import or network-replacing path.
    """
    df = getattr(n, component, None)
    if df is None:
        return
    try:
        if FLAG_COL not in df.columns:
            df[FLAG_COL] = False
        else:
            df[FLAG_COL] = df[FLAG_COL].map(flag_is_set).astype(bool)
    except Exception:                                         # noqa: BLE001
        # A frame that cannot take the column is a frame nothing reads it
        # from; the resolver's own `flag_is_set` still answers False.
        pass

# Above this implied event frequency the (rate, MTTR) pair is almost certainly
# inconsistent — the two are sourced independently (class averages vs a
# maintenance database) and are over-determined via
#   events/yr = 8760 · rate / MTTR.
# The spec's canonical bad pair: FOR 0.10 with MTTR 24 h ⇒ 36.5 outages/yr
# for a large thermal unit.
MAX_PLAUSIBLE_EVENTS_PER_YEAR = 20.0


@dataclass(frozen=True)
class OutageParams:
    rate: float
    basis: str          # "FOR" | "EFORd"
    mttr_hours: float
    source: str         # where the number comes from — shown to the user


# Per-carrier defaults. Order-of-magnitude class averages for screening, NOT
# unit-specific data — every entry names its source so the worksheet can show
# provenance, and the resolve step marks them "carrier_default" so the UI can
# distinguish them from user-entered values. Users override per asset.
#
# Deliberately ABSENT: wind / solar / other VRE. Their availability is
# profile-borne (p_max_pu time series); adding a mechanical FOR here would
# double-count against the profile. Mechanical VRE outage is second-order and
# excluded until class A models it explicitly (spec §5.3: VRE enters the COPT
# as multi-state capacity from profiles). Branch (Line/Link) outage rates are
# per-circuit exposure statistics handled by failure class B, not this
# generation-shaped library.
CARRIER_DEFAULTS: dict[str, OutageParams] = {
    "gas": OutageParams(0.05, "EFORd", 50.0,
        "≈ NERC GADS 2019–2023 gas CT/CC fleet class average (EFORd)"),
    "ccgt": OutageParams(0.04, "EFORd", 50.0,
        "≈ NERC GADS 2019–2023 combined-cycle fleet class average (EFORd)"),
    "ocgt": OutageParams(0.06, "EFORd", 40.0,
        "≈ NERC GADS 2019–2023 simple-cycle CT fleet class average (EFORd)"),
    "coal": OutageParams(0.07, "EFORd", 60.0,
        "≈ NERC GADS 2019–2023 coal fleet class average (EFORd)"),
    "lignite": OutageParams(0.07, "EFORd", 60.0,
        "≈ coal-fleet class average applied to lignite (EFORd)"),
    "nuclear": OutageParams(0.02, "EFORd", 150.0,
        "≈ NERC GADS 2019–2023 nuclear fleet class average (EFORd)"),
    "oil": OutageParams(0.08, "EFORd", 50.0,
        "≈ NERC GADS 2019–2023 oil/petroleum fleet class average (EFORd)"),
    "biomass": OutageParams(0.06, "EFORd", 60.0,
        "≈ ENTSO-E ERAA thermal availability assumptions, biomass class"),
    "hydro": OutageParams(0.02, "EFORd", 40.0,
        "≈ NERC GADS 2019–2023 conventional hydro class average (EFORd)"),
    "battery": OutageParams(0.02, "FOR", 24.0,
        "≈ utility-scale BESS availability surveys (FOR; sparse public data)"),
}


def _is_set(v: object) -> bool:
    if v is None:
        return False
    if isinstance(v, float) and not math.isfinite(v):
        return False
    if isinstance(v, str):
        return v.strip() not in ("", "nan", "None")
    return True


def _as_float(v: object) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")


def resolve_outage_params(n, component: str) -> pd.DataFrame:
    """
    Effective per-asset occurrence params for ``getattr(n, component)``
    (e.g. ``"generators"``). Returns a DataFrame indexed like the component
    frame with columns ``rate`` / ``basis`` / ``mttr_hours`` / ``source``:

    * asset ``outage_rate_value`` set → ``source="asset"`` (basis defaults to
      "FOR" when unset — the validator warns; MTTR falls back to the carrier
      default's MTTR when unset, since MTTR is a repair property, not a
      rate-basis property);
    * unset, carrier in CARRIER_DEFAULTS → ``source="carrier_default"``;
    * otherwise → ``source="missing"`` with NaN values — excluded from
      occurrence-based analysis, never guessed.
    """
    df = getattr(n, component)
    out = pd.DataFrame(
        {"rate": float("nan"), "basis": "", "mttr_hours": float("nan"),
         "source": "missing", "outages_in_availability": False},
        index=df.index,
    )
    has_carrier = "carrier" in df.columns
    # Phase 12h: the flag is Generator-only and needs the availability to be
    # sub-1 for there to be anything to fold — see `_availability_is_sub_one`.
    has_flag = FLAG_COL in df.columns
    for name in df.index:
        row = df.loc[name]
        carrier = str(row["carrier"]).strip().lower() if has_carrier else ""
        default = CARRIER_DEFAULTS.get(carrier)
        rate = row.get("outage_rate_value")
        if _is_set(rate):
            basis = row.get("outage_rate_basis")
            mttr = row.get("mttr_hours")
            out.loc[name, ["rate", "basis", "mttr_hours", "source"]] = [
                _as_float(rate),
                str(basis) if _is_set(basis) else "FOR",
                _as_float(mttr) if _is_set(mttr)
                else (default.mttr_hours if default else float("nan")),
                "asset",
            ]
        elif default is not None:
            out.loc[name, ["rate", "basis", "mttr_hours", "source"]] = [
                default.rate, default.basis, default.mttr_hours,
                "carrier_default",
            ]
        # Phase 12h. The flag zeroes the rate at this one place, so every
        # consumer — both engines, the margin's derate, the net-load window,
        # the worksheet, the disclosures — stops applying it together, by
        # construction rather than by seven edits that must agree.
        #
        # Three conditions. `source != "missing"`: there is no rate to fold
        # otherwise. A SUB-1 availability: zeroing the rate of a unit whose
        # availability is 1 does not "have no effect", it makes the unit
        # perfectly firm — the maximal effect — so there the rate is left
        # alone and preflight says the flag was ignored.
        if has_flag and out.at[name, "source"] != "missing" \
                and flag_is_set(row.get(FLAG_COL)) \
                and _availability_is_sub_one(n, component, name, row):
            out.loc[name, ["rate", "outages_in_availability"]] = [0.0, True]
    return out


def _availability_is_sub_one(n, component: str, name, row) -> bool:
    """Whether this asset carries an availability below 1. Only then is there
    an availability for outages to be "already included in" — zeroing the
    rate of a unit whose availability is 1 does not "have no effect", it
    makes the unit perfectly firm, which is the maximal effect.

    The test MIRRORS ``copt.static_fold_factor``'s gate, and must: a
    ``p_max_pu`` COLUMN supersedes the static cell in PyPSA, in the LP, in
    the reserve margin and in the fold alike, so when a column exists it
    alone decides. A non-informative (all-ones) column beside a static 0.8
    means the availability really is 1 — reading the superseded cell there
    zeroed a live outage rate and made the unit perfectly firm, the exact
    outcome this guard exists to prevent (shipped-code review, finding 1;
    measured on the §0 fixture: LOLE 8.40 h -> 0.00, EUE 441.0 -> 0.0,
    margin derate 0.95 -> 1.00).
    """
    from services.adequacy.copt import series_is_informative

    ts = getattr(getattr(n, f"{component}_t", None), "p_max_pu", None)
    if ts is not None and name in getattr(ts, "columns", []):
        try:
            return bool(series_is_informative(ts[name]))
        except Exception:                                     # noqa: BLE001
            return False
    try:
        v = float(row.get("p_max_pu"))
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v < 1.0 - 1e-9


def validate_outage_params(params: pd.DataFrame) -> list[str]:
    """
    Consistency warnings over a ``resolve_outage_params``-shaped frame.
    Rows with ``source="missing"`` are skipped (nothing to validate).
    Returns human-readable messages; empty list = all plausible.
    """
    warnings: list[str] = []
    for name, row in params.iterrows():
        if row["source"] == "missing":
            continue
        rate = _as_float(row["rate"])
        mttr = _as_float(row["mttr_hours"])
        basis = str(row["basis"])
        if not (0.0 <= rate < 1.0):
            warnings.append(
                f"'{name}': outage rate {rate:g} is outside [0, 1) — it is a "
                "probability-like unavailability, not a percentage or count."
            )
            continue
        if not (math.isfinite(mttr) and mttr > 0):
            warnings.append(
                f"'{name}': MTTR must be a positive number of hours "
                f"(got {mttr:g})."
            )
            continue
        if basis not in VALID_BASES:
            warnings.append(
                f"'{name}': outage-rate basis '{basis}' is not one of "
                f"{VALID_BASES} — assuming FOR; set it explicitly."
            )
        if rate > 0:
            events_per_year = 8760.0 * rate / mttr
            if events_per_year > MAX_PLAUSIBLE_EVENTS_PER_YEAR:
                implied_mttf = mttr * (1.0 - rate) / rate
                warnings.append(
                    f"'{name}': rate {rate:g} with MTTR {mttr:g} h implies "
                    f"{events_per_year:.1f} outage events/yr "
                    f"(MTTF ≈ {implied_mttf:.0f} h) — the rate and MTTR are "
                    "over-determined and this pair is implausible; check "
                    "whether the rate is EFORd on an annual basis while the "
                    "MTTR describes single events."
                )
    return warnings
