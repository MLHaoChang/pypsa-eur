"""
AdequacyReport contract stub (Phase 0 Task 5).

Design: docs/superpowers/specs/2026-08-27-solution-fmea-adequacy-design.md §10.
The contract is what keeps Phases 1–5 and the optional PRAS/Antares engines
plugging into one shape: later phases FILL fields, they do not negotiate
shape. No endpoint yet — models + serialization behaviour only.
"""
from __future__ import annotations

import pydantic
import pytest

from models import adequacy as A


def _minimal_report() -> A.AdequacyReport:
    """What Phase 1 will emit before Phase 2 adds per_mode: a target run's
    achieved numbers, no failure modes, no frontier."""
    return A.AdequacyReport(
        engine="lp_proxy",
        fidelity="deterministic_scenario",
        target=A.TargetBlock(
            basis="energy",
            system=A.SystemTarget(
                cap_mwh=100.0, achieved_ens_mwh=99.7, achieved_shed_hours=6.0),
            binding="system_cap",
            zone_field_populated=False,
        ),
        metrics=A.MetricsBlock(ens_mwh=99.7, shed_hours=6.0, time_basis="hours_per_year"),
        cost=A.CostBlock(total_system_cost_eur=1.2e9, period_basis="single_period"),
        inputs=A.InputsBlock(
            weather_years=["2013"],
            voll=A.VollBlock(default_eur_per_mwh=3000.0),
            assumptions_hash="deadbeef",
            outage_rate_bases={"FOR": 2, "EFORd": 14},
        ),
        energy=A.EnergyBlock(involuntary_mwh=99.7, demand_response_mwh=0.0),
    )


def test_minimal_report_constructs_and_round_trips():
    r = _minimal_report()
    j = r.model_dump_json()
    r2 = A.AdequacyReport.model_validate_json(j)
    assert r2 == r
    assert r2.per_mode == [] and r2.frontier == []


def test_excludes_shed_cost_is_unfalsifiable():
    """The cost axis must be the cost of the SYSTEM, excluding VoLL x ENS —
    including it puts the x-axis inside the y-axis and makes the frontier
    self-referential. Enforced by type: a consumer can never receive a
    report where the flag is False."""
    ok = A.CostBlock(total_system_cost_eur=1.0, period_basis="single_period")
    assert ok.excludes_shed_cost is True
    with pytest.raises(pydantic.ValidationError):
        A.CostBlock(total_system_cost_eur=1.0, period_basis="single_period",
                    excludes_shed_cost=False)


def test_binding_names_the_real_standard():
    with pytest.raises(pydantic.ValidationError):
        A.TargetBlock(
            basis="energy",
            system=A.SystemTarget(cap_mwh=1.0, achieved_ens_mwh=1.0,
                                  achieved_shed_hours=0.0),
            binding="something_else",
            zone_field_populated=True,
        )


def test_sequential_mc_metrics_carry_uncertainty_fields():
    m = A.MetricsBlock(
        ens_mwh=10.0, shed_hours=2.0, lole_hours=2.9, eue_mwh=10.4,
        confidence_interval=(2.5, 3.3), n_samples=5000,
        time_basis="hours_per_year",
    )
    j = A.MetricsBlock.model_validate_json(m.model_dump_json())
    assert j.confidence_interval == (2.5, 3.3) and j.n_samples == 5000


def test_per_mode_and_frontier_carry_their_own_provenance():
    fm = A.FailureModeResult(
        mode_id="gen:gas:forced_outage", component_class="Generator",
        name="ocgt1", failure_class="A",
        occurrence_per_year=8.76, occurrence_basis="EFORd",
        severity_eur=1.0e5, criticality_eur_per_year=8.76e5,
        engine="copt", fidelity="analytic_convolution",
        in_metric_scope=True,
    )
    tp = A.TradeoffPoint(
        cap_mwh=50.0, achieved_ens_mwh=49.9, achieved_shed_hours=3.0,
        total_system_cost_eur=1.3e9,
        engine="lp_proxy", fidelity="deterministic_scenario",
    )
    r = _minimal_report().model_copy(update={"per_mode": [fm], "frontier": [tp]})
    r2 = A.AdequacyReport.model_validate_json(r.model_dump_json())
    assert r2.per_mode[0].fidelity == "analytic_convolution"
    assert r2.frontier[0].engine == "lp_proxy"


def test_out_of_scope_modes_are_flagged_not_negative():
    """Spec §4.3: on an electricity-only metric a P2X asset outage REDUCES
    electrical demand. Such rows must render as out-of-scope, never as
    negative criticality — the contract forbids the negative number."""
    with pytest.raises(pydantic.ValidationError):
        A.FailureModeResult(
            mode_id="link:electrolyser:forced_outage", component_class="Link",
            name="elz1", failure_class="A",
            occurrence_per_year=4.0, occurrence_basis="FOR",
            severity_eur=-5.0e4, criticality_eur_per_year=-2.0e5,
            engine="lp_proxy", fidelity="deterministic_scenario",
            in_metric_scope=False,
        )
