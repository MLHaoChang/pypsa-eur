"""
The AdequacyReport contract — the one shape every adequacy engine fills.

Design: docs/superpowers/specs/2026-08-27-solution-fmea-adequacy-design.md §10.
Phases 1–5 (LP proxy, COPT, worksheet, frontier) and the optional PRAS/
Antares exporters all emit THIS; later phases fill fields, they do not
negotiate shape. Every number carries provenance (engine + fidelity), because
the LP proxy understates LOLE (perfect foresight, one realisation) and the
COPT screening is storage-blind — no number produced by Phases 0–4 may be
compared to a statutory standard, and the UI must be able to say so at the
point of display.

No endpoint yet (Phase 0 stub): models + serialization behaviour only.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# "expert" / "expert_judgement": worksheet rows a person entered by hand
# (class D, and any manual annotation). Honest provenance, not a loophole —
# the UI badges them exactly like engine-computed rows (Phase 3).
Engine = Literal["lp_proxy", "copt", "pras", "antares", "expert"]
Fidelity = Literal["deterministic_scenario", "analytic_convolution",
                   "sequential_mc", "expert_judgement"]


class PeriodTarget(BaseModel):
    """One investment period's slice of the system target.

    The cap is enforced PER PERIOD, so the summed headline can be actively
    misleading on a multi-period run: with two periods at caps of 1800 MWh
    each, one period exactly on its limit and the other at zero reads as
    "ENS 1800 / cap 3600" — 50% headroom — when the binding period has none
    at all. These rows are what let the reader see which period bound.

    Single-period runs carry exactly one row, so a consumer never needs to
    branch on the model being multi-period."""
    period: str
    cap_mwh: float
    achieved_ens_mwh: float
    binding: bool = False


class SystemTarget(BaseModel):
    """The system-wide reliability target and what the solve achieved.

    With a binding cap, achieved ENS ≈ the cap by construction and carries
    little information — ``achieved_shed_hours`` is the number that still
    tells the user something (spec §5.1), which is why both are mandatory.

    ``cap_mwh`` / ``achieved_ens_mwh`` are SUMS over investment periods;
    ``by_period`` carries the per-period rows the constraint actually
    operates on. Read the rows, not the sums, to judge headroom."""
    cap_mwh: float
    achieved_ens_mwh: float
    achieved_shed_hours: float
    by_period: list[PeriodTarget] = []


class ZoneTarget(BaseModel):
    """Per-zone ceiling row (zone = the bus ``country`` field, spec §5.1)."""
    zone: str
    cap_mwh: float
    achieved_ens_mwh: float
    binding: bool = False


class TargetBlock(BaseModel):
    basis: Literal["energy", "shed_hours"]
    system: SystemTarget
    zones: list[ZoneTarget] = Field(default_factory=list)
    # Whichever binds first is the REAL standard, and the user cannot see
    # which without this (spec §5.5): a high VoLL sheds less than the cap
    # allows and quietly becomes the effective standard.
    binding: Literal["system_cap", "zone_cap", "voll"]
    # Exposes the empty-`country` degeneracy: on a GUI-built network every
    # bus lands in one unnamed zone and a per-zone ceiling silently collapses
    # into a second copy of the system cap. A ceiling must never LOOK
    # enforced when it is not.
    zone_field_populated: bool


class MetricsBlock(BaseModel):
    ens_mwh: float
    shed_hours: float
    # Probabilistic metrics — absent until an engine that can honestly
    # produce them fills them (COPT: lole/eue; sequential MC: + CI).
    lole_hours: float | None = None
    eue_mwh: float | None = None
    # A sequential-MC LOLE without a confidence interval is not reportable;
    # these exist so the "sequential_mc" fidelity tier is usable at all.
    confidence_interval: tuple[float, float] | None = None
    n_samples: int | None = None
    # LOLE in hours/yr vs days/yr differ by ~24x. Never implicit.
    time_basis: Literal["hours_per_year", "days_per_year"]


class CostBlock(BaseModel):
    """The cost axis of the trade-off (spec §5.2): CapEx + FOM + variable
    OpEx + CO2, EXCLUDING load-shedding cost. Including VoLL x ENS puts the
    x-axis inside the y-axis and makes the frontier self-referential — so
    the exclusion is a ``Literal[True]``: a consumer can never receive a
    report where it is false."""
    total_system_cost_eur: float
    excludes_shed_cost: Literal[True] = True
    # Multi-period runs discount via investment_period_weightings.objective,
    # making the figure an NPV — not the annualised number a reliability
    # standard implies. The axis label depends on knowing which.
    period_basis: Literal["single_period", "npv_multi_period"]


class VollBlock(BaseModel):
    """Single value now, shaped for ACER's segment-weighted VoLL later
    (spec §5.5). Only ``default`` is populated until the slack geometry can
    attribute which load was shed."""
    default_eur_per_mwh: float
    by_segment_eur_per_mwh: dict[str, float] | None = None


class InputsBlock(BaseModel):
    """What fed the run — without these, two reports are not comparable and
    the Compare tab would silently diff incomparable numbers."""
    weather_years: list[str]
    voll: VollBlock
    seed: int | None = None
    assumptions_hash: str
    # How many assets entered occurrence data on each basis (FOR vs EFORd,
    # never silently converted — spec §5.4). Tags any COPT metric with the
    # mix of bases behind it.
    outage_rate_bases: dict[str, int] = Field(default_factory=dict)


class EnergyBlock(BaseModel):
    """The two slack tiers, split so no consumer can re-merge demand
    response into unserved energy by accident (spec §4.4). Only
    ``involuntary_mwh`` counts against the target."""
    involuntary_mwh: float
    demand_response_mwh: float


class FailureModeResult(BaseModel):
    """One worksheet row, carrying its own provenance — the FMECA and the
    frontier are different analyses and must not share one fidelity tag."""
    mode_id: str
    component_class: str
    name: str
    failure_class: Literal["A", "B", "C", "D"]
    occurrence_per_year: float = Field(ge=0)
    occurrence_basis: str            # "FOR" / "EFORd" / "expert" / climate-year freq
    # Severity/criticality are >= 0 by contract: on the electricity-only
    # metric a P2X outage REDUCES electrical demand (spec §4.3), and such
    # rows must be flagged out-of-scope (in_metric_scope=False, values 0),
    # never rendered as negative criticality — a worksheet that ranks
    # "break the electrolyser" as beneficial is wrong, not insightful.
    severity_eur: float = Field(ge=0)
    criticality_eur_per_year: float = Field(ge=0)
    in_metric_scope: bool = True
    mitigability: str | None = None  # expert-entered (worksheet, Phase 3)
    engine: Engine
    fidelity: Fidelity


class TradeoffPoint(BaseModel):
    """One point on the cost-vs-availability frontier (spec §5.6)."""
    cap_mwh: float
    achieved_ens_mwh: float
    achieved_shed_hours: float
    total_system_cost_eur: float
    engine: Engine
    fidelity: Fidelity


class AdequacyReport(BaseModel):
    engine: Engine
    fidelity: Fidelity
    target: TargetBlock
    metrics: MetricsBlock
    cost: CostBlock
    inputs: InputsBlock
    energy: EnergyBlock
    per_mode: list[FailureModeResult] = Field(default_factory=list)
    frontier: list[TradeoffPoint] = Field(default_factory=list)
