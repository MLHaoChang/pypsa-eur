"""
The MC / COPT fleet must be the HUB's fleet, not the whole copper plate.

The sequential MC is single-area: every electrical bus is one plate. Before
this fix ``mc_certify`` sampled generators BEHIND the import Link too, so on
an ``off_grid`` hub — whose PoC Link the pack islands — a grid-side unit the
LP cannot reach still covered the hub's deficits and the verdict came back
``certified`` for a hub that sheds. The certification is now taken on the hub
side of the identified import Link(s), with the import counted as firm up to
its planning cap (0 MW when islanded) and disclosed as ``fleet_scope``.
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pypsa
import pytest

from models.energy_hub import (
    AvailabilityTarget,
    default_off_grid_pack,
    default_strong_grid_pack,
    default_weak_flexible_pack,
)
from services.adequacy import eh_stages as ST
from services.adequacy import eh_study as S
from services.solver_service import SolverConfig
from tests.eh_stage_fixtures import (
    HOURS,
    VOLL,
    certifiable_weak_network,
    islanded_certify_network,
)


def _run(n, pack, *, stages, cfg=None):
    from services.pypsa_service import PyPSAService

    PyPSAService.set_network(n)
    return S.run_eh_study(
        n, pack, cfg or SolverConfig(voll=VOLL),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=stages,
    )


def _islanded_hub_with_grid_behind_poc() -> pypsa.Network:
    """
    The islanded fixture PLUS a large, reliable generator on the grid side
    of the PoC Link. off_grid islands that Link, so the LP cannot use it; a
    copper-plate MC would count it and certify a hub that sheds.
    """
    n = islanded_certify_network()
    n.add("Generator", "grid_supply", bus="grid", carrier="gas", p_nom=200.0,
          marginal_cost=5.0, outage_rate_value=0.02,
          outage_rate_basis="EFORd", mttr_hours=100.0)
    return n


def _off_grid_pack(target_lole_h: float = 3.0):
    base = default_off_grid_pack()
    return base.model_copy(update={
        "availability": AvailabilityTarget(
            ens_cap_permyriad=2000.0, target_lole_h=target_lole_h,
            certification_metric="mc_lole"),
        "levers": base.levers.model_copy(update={"storage_duration": False}),
        "mc_draws": 100,
    })


@pytest.mark.live_solve
def test_off_grid_certification_ignores_generation_behind_the_islanded_poc():
    report = _run(_islanded_hub_with_grid_behind_poc(), _off_grid_pack(),
                  stages=("apply_pack", "ens_solve", "mc_certify", "assemble"))
    sec = report.sections["certification"]
    # The bug: with grid_supply in the copper plate the LOLE is ~0 and the
    # verdict reads "certified" for a hub that cannot reach it.
    assert sec.payload["verdict"] == "failed", sec.note
    assert report.mc_lole_h is not None and report.mc_lole_h > 3.0
    scope = sec.payload["fleet_scope"]
    assert scope["mode"] == "hub_side"
    assert "grid" in scope["excluded_buses"]
    assert "grid_supply" in scope["excluded_units"]
    assert scope["import_links"] == ["import_poc"]
    assert scope["import_firm_mw_max"] == pytest.approx(0.0)  # islanded
    assert scope["import_firmness"] == "planning_limit_only"
    assert "hub side" in scope["note"]


@pytest.mark.live_solve
def test_weak_flexible_counts_import_as_firm_up_to_its_planning_cap_only():
    n = certifiable_weak_network()
    pack = default_weak_flexible_pack().model_copy(update={
        "availability": AvailabilityTarget(
            ens_cap_permyriad=10.0, target_lole_h=3.0,
            certification_metric="mc_lole"),
        "mc_draws": 50,
    })
    report = _run(n, pack, stages=(
        "apply_pack", "ens_solve", "mc_certify", "fmea_top", "assemble"))
    scope = report.sections["certification"].payload["fleet_scope"]
    assert scope["mode"] == "hub_side"
    assert "grid_supply" in scope["excluded_units"]
    # apply_pack capped the PoC Link at the pack's 50 MW; that cap, not the
    # 200 MW generator behind it, is what the hub can count on.
    assert scope["import_firm_mw_max"] == pytest.approx(50.0)
    # The screened (class-A) fleet is the same hub-side fleet.
    top = report.sections["fmea_top"].payload["top"]
    names = {m["name"] for m in top if m["failure_class"] == "A"}
    assert names == {"base", "peaker"}
    assert report.sections["fmea_top"].payload["fleet_scope"] == scope
    # Undo restored the Link after the study.
    assert float(n.links.at["import_poc", "p_nom"]) == pytest.approx(100.0)


def _two_bus(*, tag: bool, extra_line: bool = False) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=HOURS, freq="h"))
    n.snapshot_weightings.loc[:, :] = 8760.0 / HOURS
    for c in ("gas", "AC"):
        n.add("Carrier", c)
    n.add("Bus", "grid", carrier="AC")
    n.add("Bus", "hub", carrier="AC")
    n.add("Load", "l", bus="hub", p_set=50.0)
    n.add("Generator", "local", bus="hub", carrier="gas", p_nom=60.0,
          marginal_cost=10.0, outage_rate_value=0.05,
          outage_rate_basis="EFORd", mttr_hours=50.0)
    n.add("Generator", "far", bus="grid", carrier="gas", p_nom=100.0,
          marginal_cost=5.0, outage_rate_value=0.02,
          outage_rate_basis="EFORd", mttr_hours=50.0)
    n.add("Link", "tie", bus0="grid", bus1="hub", p_nom=30.0, carrier="AC")
    if tag:
        n.links["eh_role"] = ""
        n.links.at["tie", "eh_role"] = "grid_import"
    if extra_line:
        n.add("Line", "parallel", bus0="grid", bus1="hub", x=0.1, r=0.01,
              s_nom=50.0)
    return n


def test_scope_is_whole_network_when_import_links_only_match_by_carrier():
    """Carrier fallback can match links INSIDE the hub — no sides to trust."""
    n = _two_bus(tag=False)
    scope = ST.hub_fleet_scope(n, default_strong_grid_pack().import_overlay)
    assert scope.mode == "whole_network"
    assert "carrier" in scope.note


def test_scope_is_whole_network_when_the_import_link_does_not_separate():
    n = _two_bus(tag=True, extra_line=True)
    scope = ST.hub_fleet_scope(n, default_strong_grid_pack().import_overlay)
    assert scope.mode == "whole_network"
    assert "do not separate" in scope.note


def test_scope_takes_bus0_as_the_grid_side_and_reads_the_link_cap():
    n = _two_bus(tag=True)
    scope = ST.hub_fleet_scope(n, default_strong_grid_pack().import_overlay)
    assert scope.mode == "hub_side"
    assert scope.excluded_buses == ["grid"]
    assert scope.excluded_units == ["far"]
    assert scope.import_firm_mw is not None
    assert float(scope.import_firm_mw.max()) == pytest.approx(30.0)


def test_scope_flips_orientation_when_bus0_is_the_critical_side():
    n = _two_bus(tag=True)
    # Reverse the Link so bus0 is the hub; tag the hub critical so the
    # side rule can tell which end the hub is on.
    n.links.at["tie", "bus0"] = "hub"
    n.links.at["tie", "bus1"] = "grid"
    n.links.at["tie", "p_min_pu"] = -1.0
    n.buses["eh_critical"] = False
    n.buses.at["hub", "eh_critical"] = True
    scope = ST.hub_fleet_scope(n, default_strong_grid_pack().import_overlay)
    assert scope.mode == "hub_side"
    assert scope.excluded_buses == ["grid"]
    # Reverse flow into bus0 is bounded by -p_min_pu × p_nom.
    assert float(scope.import_firm_mw.max()) == pytest.approx(30.0)
