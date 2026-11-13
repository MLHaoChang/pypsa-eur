"""
Edge Investment Case — flexibility archetype specs (Phase 0, WP0.1).

Design: docs/superpowers/specs/2026-09-26-edge-investment-case-design.md §8
Plan:   docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP0.1

Specs only — the builders that emit PyPSA components land in P5. Nothing
here imports ``services``.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_serializer, model_validator

from models.commercial import TimeSeriesRef
from models.design_intent import DesignIntentSpec

RedundancyTier = Literal["N", "N+1", "2N"]
UtilisationKind = Literal["ai_training", "inference", "mixed", "flat"]


class DcPhase(BaseModel):
    """One COD phase. ``it_mw`` is the IT load in service from ``cod`` on
    (cumulative, not the increment), as the staged template counts it."""
    cod: date
    it_mw: float = Field(gt=0)
    #: Rack power density of this phase (kW per rack), so a later phase can
    #: carry denser racks. Carried into the dynamic envelope; not in the LP.
    rack_kw: float | None = Field(default=None, gt=0)


class GensetSpec(BaseModel):
    power_mw: float = Field(gt=0)
    fuel: Literal["diesel", "gas", "hvo", "hydrogen"] = "diesel"
    run_hours_cap_per_year: float | None = Field(default=None, ge=0)
    fuel_price_ref: TimeSeriesRef | None = None
    heat_rate_mwh_fuel_per_mwh_el: float = Field(default=2.8, gt=0)


class AiWorkloadSpec(BaseModel):
    """A synchronised AI-training workload, as parameters (DC grid readiness
    01). Shares are of the phase's IT MW. Nothing here is simulated below the
    hour: the hourly profile takes the mean (checkpoint duty, idle hours), the
    sub-hourly terms go to the dynamic envelope only. Every default is
    assumed; `services.dc_load_profile.SOURCES` says where each comes from."""
    full_share: float = Field(default=0.95, gt=0, le=1)
    idle_share: float = Field(default=0.30, ge=0, le=1)
    #: Idle-to-full (and full-to-idle) ramp time of a job-synchronised step.
    ramp_s: float = Field(default=1.0, gt=0)
    checkpoint_interval_s: float = Field(default=1800.0, gt=0)
    #: Each checkpoint drops the load to ``idle_share`` for this long.
    checkpoint_duration_s: float = Field(default=60.0, ge=0)
    #: Peak (not peak-to-peak) amplitude of the training-step oscillation.
    oscillation_amplitude_share: float = Field(default=0.10, ge=0, le=1)
    oscillation_period_s: float = Field(default=2.0, gt=0)
    #: Reactive swing of the load as tan φ of its real swing (S + A); assumed until vendor data.
    tan_phi_swing: float = Field(default=0.1, ge=0)
    #: Whole hours per day at ``idle_share`` (job restarts, maintenance),
    #: from ``idle_start_hour`` on the site clock.
    idle_hours_per_day: int = Field(default=1, ge=0, le=24)
    idle_start_hour: int = Field(default=3, ge=0, le=23)

    @model_validator(mode="after")
    def _steps(self) -> "AiWorkloadSpec":
        if self.idle_share > self.full_share:
            raise ValueError("idle_share must be ≤ full_share")
        if self.checkpoint_duration_s > self.checkpoint_interval_s:
            raise ValueError("checkpoint_duration_s must be ≤ checkpoint_interval_s")
        if self.oscillation_amplitude_share > self.full_share:
            raise ValueError("oscillation_amplitude_share must be ≤ full_share")
        return self


BackupMode = Literal["genset", "bess", "hybrid"]
#: The power architecture from the MV bus to the rack (DC grid readiness 05):
#: LV double-conversion UPS, MV UPS, 800 VDC from a central rectifier, or a
#: solid-state transformer to 800 VDC (``services.dc_architecture``).
DcArchitecture = Literal["ac", "mv_ups", "dc_800v", "sst"]


def _assumed_backup_bess() -> "BessSpec":
    """The backup BESS's assumed defaults (DC 03): Li-ion RTE and DoD, 2 %/yr
    calendar fade, 0.005 %/full cycle, restored to nameplate every year."""
    return BessSpec(power_mw=1.0, energy_mwh=1.0, round_trip_efficiency=0.88,
                    depth_of_discharge=0.9, calendar_fade_per_year=0.02,
                    cycle_fade_per_full_cycle=0.00005,
                    augmentation_policy="restore_to_nameplate")


class BackupSpec(BaseModel):
    """How the critical load is backed on loss of grid (DC grid readiness 03).

    ``genset`` keeps the template's genset fleet (today's model, bit for bit);
    ``bess`` replaces it with battery strings sized for ``ride_through_s``
    plus ``autonomy_h`` of the critical load; ``hybrid`` keeps the gensets
    and sizes the strings on the critical load the firm gensets leave
    uncovered. The strings honour ``DataCentreLoadSpec.redundancy``. The
    ``bess`` spec's ``power_mw``/``energy_mwh`` are placeholders: sizing
    sets them; its efficiency, DoD, fade and augmentation are read. Every
    default is assumed (``services.adequacy.dc_backup.SOURCES``)."""
    mode: BackupMode = "genset"
    autonomy_h: float = Field(default=4.0, gt=0, le=72)
    ride_through_s: float = Field(default=600.0, ge=0)
    string_mw: float = Field(default=5.0, gt=0)
    #: Full-depth test discharges a year, the cycle fade's clock.
    test_cycles_per_year: float = Field(default=12.0, ge=0)
    bess: BessSpec = Field(default_factory=lambda: _assumed_backup_bess())
    #: Genset leg of the cost delta: run hours a year (testing) when the
    #: genset spec gives no cap, and the fuel price per MWh of fuel.
    genset_run_hours_per_year: float = Field(default=50.0, ge=0, le=8760)
    fuel_price_per_mwh_fuel: float = Field(default=45.0, ge=0)
    #: Hours of ``string_mw`` each string carries on top of its backup
    #: energy, for reserve products to commit (DC grid readiness 06). 0
    #: sizes the backup alone; the backup energy stays the floor either way.
    reserve_headroom_h: float = Field(default=0.0, ge=0, le=24, allow_inf_nan=False)

    @model_serializer(mode="wrap")
    def _omit_no_headroom(self, handler):
        """A spec without headroom dumps as it did before the field existed,
        so stored specs, sidecars and hashes stay byte-equal (GK A R4)."""
        data = handler(self)
        if isinstance(data, dict) and not self.reserve_headroom_h:
            data.pop("reserve_headroom_h", None)
        return data

    @model_validator(mode="after")
    def _fade_given(self) -> "BackupSpec":
        """A ``bess`` the spec gives must give both fades: BessSpec's own
        default (0) would read as no fade (GK B K5). Without a ``bess`` the
        labelled assumed defaults apply."""
        if "bess" in self.model_fields_set:
            missing = [k for k in ("calendar_fade_per_year", "cycle_fade_per_full_cycle")
                       if k not in self.bess.model_fields_set]
            if missing:
                raise ValueError(f"backup.bess: give {' and '.join(missing)} (a missing fade "
                                 "is not read as 0; omit bess for the assumed defaults)")
        return self


class DataCentreLoadSpec(BaseModel):
    phases: list[DcPhase] = Field(min_length=1)
    utilisation: UtilisationKind = "mixed"
    #: PUE at or below ``pue_design_temp_c``; above it the facility load
    #: rises by ``pue_temperature_slope_per_k`` per kelvin. Not an annual mean.
    pue_at_design: float = Field(ge=1.0)
    pue_temperature_slope_per_k: float = Field(default=0.0, ge=0)
    pue_design_temp_c: float = 20.0
    #: The ambient the PUE slope reads, keyed to the snapshot dates: annual
    #: mean, a seasonal cosine peaking on day 200 and a diurnal cosine
    #: peaking at 15:00 site clock (assumed temperate site).
    ambient_annual_mean_c: float = 10.0
    ambient_seasonal_amplitude_k: float = Field(default=9.0, ge=0)
    ambient_diurnal_amplitude_k: float = Field(default=5.0, ge=0)
    #: The AI-training workload model; None keeps the spec's load as given.
    ai_workload: AiWorkloadSpec | None = None
    ups_distribution_loss_share: float = Field(default=0.03, ge=0, le=0.2)
    redundancy: RedundancyTier
    critical_share: float = Field(ge=0, le=1)
    curtailable_share: float = Field(ge=0, le=1)
    curtail_max_duration_h: float | None = Field(default=None, gt=0)
    #: DC-07 S2: what curtailing the curtailable share costs (EUR/MWh). None
    #: (the default) keeps curtailment off; 0 only when given.
    curtailment_price_eur_per_mwh: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    workload_shift_window_h: float | None = Field(default=None, gt=0)
    gensets: list[GensetSpec] = Field(default_factory=list)
    waste_heat_export: bool = False
    #: Asset-sizing design intent A-E (AS-01); None = a backup_mode-only project.
    design_intent: DesignIntentSpec | None = None
    #: Backup on loss of grid (DC 03); the default keeps the genset fleet.
    backup: BackupSpec = Field(default_factory=BackupSpec)
    #: Power architecture (DC 05). ``pue_at_design`` is the PUE of this
    #: architecture; the default "ac" is assumed when unset and moves nothing.
    architecture: DcArchitecture = "ac"
    #: DC 27: the project's start, the time-to-power months' origin; None = not stated.
    project_start: date | None = None
    #: DC 27: the grid connection's energisation date; when set it is first power.
    #: Neither date is read by a solve.
    connection_date: date | None = None

    @model_serializer(mode="wrap")
    def _omit_unstated_dates(self, handler):
        """A spec without the DC 27 dates dumps as it did before they existed,
        so stored specs, sidecars and hashes stay byte-equal (GK A G8)."""
        data = handler(self)
        if isinstance(data, dict):
            for key in ("project_start", "connection_date"):
                if data.get(key) is None:
                    data.pop(key, None)
        return data

    @model_validator(mode="after")
    def _shares(self) -> "DataCentreLoadSpec":
        if self.critical_share + self.curtailable_share > 1.0 + 1e-9:
            raise ValueError("critical_share + curtailable_share must be ≤ 1")
        cods = [p.cod for p in self.phases]
        if cods != sorted(cods):
            raise ValueError("phases must be ordered by cod")
        return self


class BessSpec(BaseModel):
    power_mw: float = Field(gt=0)
    energy_mwh: float = Field(gt=0)
    round_trip_efficiency: float = Field(gt=0, le=1)
    depth_of_discharge: float = Field(default=0.9, gt=0, le=1)
    cycles_per_year_cap: float | None = Field(default=None, gt=0)
    throughput_mwh_cap: float | None = Field(default=None, gt=0)
    calendar_fade_per_year: float = Field(default=0.0, ge=0, le=1)
    cycle_fade_per_full_cycle: float = Field(default=0.0, ge=0, le=1)
    augmentation_policy: Literal["none", "restore_to_nameplate", "fixed_years"] = "none"
    augmentation_years: list[int] = Field(default_factory=list)
    warranty_years: int | None = Field(default=None, ge=0)
    coupling: Literal["ac", "dc"] = "ac"


class EvFleetSpec(BaseModel):
    fleet_size: int = Field(ge=1)
    charger_mw: float = Field(gt=0)
    daily_energy_mwh: float = Field(gt=0)
    arrival_hour: int = Field(ge=0, le=23)
    departure_hour: int = Field(ge=0, le=23)
    smart_charging: bool = True
    v2x: bool = False
    public_utilisation_ref: TimeSeriesRef | None = None

    @model_validator(mode="after")
    def _window_not_empty(self) -> "EvFleetSpec":
        if self.arrival_hour == self.departure_hour:
            raise ValueError("arrival_hour and departure_hour must differ")
        return self


class ThermalFlexSpec(BaseModel):
    heat_demand_mw_peak: float = Field(gt=0)
    heat_demand_ref: TimeSeriesRef | None = None
    heat_pump_cop_design: float | None = Field(default=None, gt=1)
    thermal_store_mwh: float | None = Field(default=None, ge=0)
    e_boiler_mw: float | None = Field(default=None, ge=0)
    gas_backup_mw: float | None = Field(default=None, ge=0)
    interruptible_mw: float | None = Field(default=None, ge=0)
    interruptible_notice_h: float | None = Field(default=None, ge=0)
    interruptible_max_duration_h: float | None = Field(default=None, gt=0)
