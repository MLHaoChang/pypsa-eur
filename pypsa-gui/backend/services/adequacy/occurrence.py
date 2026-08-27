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
         "source": "missing"},
        index=df.index,
    )
    has_carrier = "carrier" in df.columns
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
    return out


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
