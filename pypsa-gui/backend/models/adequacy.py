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

ONE recorded exception to "every engine fills this", stated in full on
``AdequacyReport`` below: the engine-local STUDIES — the COPT screening and
the sequential MC — answer a question this report does not ask and are served
as sibling payloads (`/results/copt`, `/results/mc`) rather than folded in.
They still carry the same provenance vocabulary (`engine` + `fidelity`).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# "expert" / "expert_judgement": worksheet rows a person entered by hand
# (class D, and any manual annotation). Honest provenance, not a loophole —
# the UI badges them exactly like engine-computed rows (Phase 3).
Engine = Literal["lp_proxy", "copt", "mc", "pras", "antares", "expert"]
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
    # "mc_lole" stays RESERVED and UNADDED, and Phase 7 ratified that rather
    # than spending it (plan [N3]). A target expressed against the
    # sequential-MC LOLE has no constraint that could enforce it — the LP
    # still enforces an ENERGY cap — and a basis the solve cannot honour would
    # read, on this very report, as a standard the run met.
    #
    # What Phase 7 built instead is the SEAM, realised at
    # /results/coupling_loop: the loop drives this energy cap until the plan it
    # produces meets an MC-LOLE target, and reports the certified cap as
    # `eps_star` with an affordance (`restore: "final"`) that applies it to the
    # solver config. So the MC-LOLE standard is reachable end-to-end while
    # every report this module types still says, truthfully, which energy cap
    # the solve enforced.
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


class ReserveMarginAsset(BaseModel):
    """One row of the derating table: what this asset was credited with, and
    on what evidence.

    ``derate`` is never 1.0 by default (spec §2.2): it is
    ``(1 − q) × availability``, where availability is the unit's
    peak-coincidence mean ``p_max_pu`` when it has a profile and its static
    ``p_max_pu`` otherwise. ``basis`` rides with the number because
    ``1 − FOR`` is not a UCAP derate and is optimistic exactly for peakers —
    the units that matter at the margin — and ``source`` because a carrier
    class average the user never entered is an assumption that changed the
    plan.

    Rows are per (asset, PERIOD): a must-take derate is period-dependent, so
    one row per asset could only ever report one period's credit."""
    name: str
    period: str
    kind: Literal["generator", "storage"]
    # None on the LP-time stash (an extendable's capacity is a variable);
    # filled with the BUILT capacity once the solve has one.
    capacity_mw: float | None = None
    derate: float
    basis: str
    source: str
    extendable: bool = False
    firm_mw: float = 0.0
    # A reservoir with `max_hours = 2000` takes full power credit while its
    # energy limit is what actually binds it — the mirror of the failure the
    # duration haircut exists to prevent. Recorded, not fixed (plan §1.4).
    energy_limited: bool = False


class ReserveMarginPeriod(BaseModel):
    """One investment period's firm-capacity standard and what met it.

    ``binding`` is per ROW and deliberately NOT folded into
    ``TargetBlock.binding``: that field is a three-value ``Literal`` the
    frontend re-declares with an exhaustive label map, and one field cannot
    report two standards when the energy cap and the margin both bind.

    ``met`` and ``binding`` are separate questions. ``met`` is "the plan
    reaches the standard"; ``binding`` is "the standard SHAPED the plan" —
    firm capacity sitting on its lower bound. A margin the existing fleet
    already satisfies is met and not binding, and calling it binding would
    credit the margin for capacity that was always there."""
    period: str
    peak_mw: float
    required_mw: float
    firm_mw: float
    # firm_mw / peak_mw − 1, i.e. the margin the plan actually carries. None
    # when the period has no demand to take a margin over.
    margin_achieved: float | None = None
    met: bool = False
    binding: bool = False
    n_peak_hours: int = 0
    # The hours the must-take credit was measured over — a proxy nobody can
    # inspect is a number nobody can check (plan §1.3).
    peak_snapshots: list[str] = Field(default_factory=list)
    # None when an active extendable has an unbounded `p_nom_max`: "unbounded"
    # is not a number, and `inf` is not JSON (amendment 6). The flag below
    # says which case the null is.
    max_achievable_mw: float | None = None
    max_achievable_unbounded: bool = False


class ReserveMarginBlock(BaseModel):
    """The firm-capacity (planning reserve margin) standard — a SIBLING of
    ``TargetBlock``, never a fourth value of its ``binding`` field.

    ``horizon_wide`` is the honest label for what the LP can express:
    ``Generator-p_nom`` is ONE variable for the whole horizon, so when the
    active extendable set is the same in every period the per-period
    constraints share it and the system degenerates to a single standard at
    ``max_P peak_P``. Calling that "per period" would be a claim the
    constraint does not support.

    A met margin is NOT a met reliability target: it is a proxy standard
    justified by convention and by the derating factors, not by a sampler."""
    margin: float
    horizon_wide: bool
    by_period: list[ReserveMarginPeriod] = Field(default_factory=list)
    assets: list[ReserveMarginAsset] = Field(default_factory=list)
    # How many credited assets carried each basis — the same
    # never-silently-converted discipline `InputsBlock.outage_rate_bases`
    # applies to the COPT's metrics, with more force: here a wrong basis
    # moves the built plan, not a diagnostic.
    derating_bases: dict[str, int] = Field(default_factory=dict)


class MetricsBlock(BaseModel):
    ens_mwh: float
    shed_hours: float
    # Probabilistic metrics — absent until an engine that can honestly
    # produce them fills them (COPT: lole/eue; sequential MC: + CI).
    lole_hours: float | None = None
    eue_mwh: float | None = None
    # A sequential-MC LOLE without a confidence interval is not reportable;
    # these exist so the "sequential_mc" fidelity tier is usable at all.
    #
    # deprecated alias of lole_ci — kept so reports written before the MC
    # engine landed still validate. New producers fill `lole_ci`.
    confidence_interval: tuple[float, float] | None = None
    # The MC's own intervals, one per metric. A single `confidence_interval`
    # could only ever describe ONE of them, and an EUE band is not derivable
    # from a LOLE band (different per-draw statistics, different variance) —
    # so an EUE reported beside a LOLE interval would invite reading the
    # interval as covering both.
    lole_ci: tuple[float, float] | None = None
    eue_ci: tuple[float, float] | None = None
    n_samples: int | None = None
    # LOLE in hours/yr vs days/yr differ by ~24x. Never implicit.
    #
    # "hours_per_horizon" is NOT a lesser variant of "hours_per_year" — it is
    # the honest label whenever the modelled horizon is not a year. It used to
    # be hardcoded to hours_per_year at both call sites, so a 168 h
    # representative week reported 80.86 "h/yr" for a system whose annual LOLE
    # is ~4216 h. That error runs in the dangerous direction: a short horizon
    # makes the system look far MORE reliable than it is, and the number is
    # then one glance away from being read against a 3 h/yr standard.
    time_basis: Literal["hours_per_year", "hours_per_horizon", "days_per_year"]
    # Σ(snapshot weights) / 8760 — how much modelled time the numbers above
    # cover. Present so a reader (or the UI) can say "per 168 h horizon"
    # rather than having to trust the label alone. None when unknown.
    horizon_years: float | None = None


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
    """The one shape — with one recorded exception.

    ENGINE-LOCAL STUDIES RETURN SIBLING PAYLOADS AND ARE NOT FOLDED IN. The
    COPT screening (`GET /results/copt`) and the sequential MC
    (`GET|POST /results/mc`) each answer a question this report does not ask:
    they are computed on demand from the current network, they carry no
    target/cost/inputs block (no solve produced them), and the MC additionally
    carries an ELCC table and a per-run standing warning. Folding either into
    AdequacyReport would mean optional-everything blocks that every consumer
    must branch on, so both stay siblings — same provenance vocabulary
    (`engine` + `fidelity`), separate payloads. Recorded decision, spec §4: no
    report bloat for engine-local studies.
    """

    engine: Engine
    fidelity: Fidelity
    target: TargetBlock
    # The firm-capacity standard, when one was enforced AND the solve produced
    # a dispatch to judge it against. A SIBLING block rather than a fourth
    # `TargetBlock.binding` value: the two standards can bind at once, and one
    # field cannot report both (plan §3).
    reserve_margin: ReserveMarginBlock | None = None
    metrics: MetricsBlock
    cost: CostBlock
    inputs: InputsBlock
    energy: EnergyBlock
    per_mode: list[FailureModeResult] = Field(default_factory=list)
    frontier: list[TradeoffPoint] = Field(default_factory=list)
