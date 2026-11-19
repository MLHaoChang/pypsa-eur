"""
A must-stay-on bus fed by one Link inside the hub loses supply whenever that
Link is out, whatever the hub's generation does (asset-investment walkthrough
2026-11-17, issue 02).

``hub_fleet_scope`` merges every bus on the hub side of the import Link into
one copper plate and samples import Links only, so the outages of an internal
feeder to an ``eh_critical`` bus were never drawn. On the Island Microgrid
template the study certified 0.0 h/yr while its own FMEA ranked the
hospital's only feeder first; the hand check is 0.01 × 8,760 h = 87.6 h/yr.
"""

from __future__ import annotations

import queue
import threading

import pandas as pd
import pypsa
import pytest

from models.energy_hub import AvailabilityTarget, default_off_grid_pack
from services.adequacy import eh_study as S
from services.solver_service import SolverConfig

HOURS = 168
VOLL = 150.0


def _pack(target_lole_h: float = 3.0):
    base = default_off_grid_pack()
    return base.model_copy(
        update={
            "availability": AvailabilityTarget(
                ens_cap_permyriad=2000.0,
                target_lole_h=target_lole_h,
                certification_metric="mc_lole",
            ),
            "levers": base.levers.model_copy(update={"storage_duration": False}),
            "mc_draws": 200,
        }
    )


def _hub_with_one_critical_feeder(*, feeder_rate: float = 0.01) -> pypsa.Network:
    """
    An island whose own fleet is never short (two firm, outage-free units of
    500 MW against 60 MW of load), with a must-stay-on bus reached only
    through ``crit_feeder`` (unavailability ``feeder_rate``, MTTR 8 h). The
    hub alone gives LOLE 0; every hour lost is a feeder hour.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=HOURS, freq="h"))
    n.snapshot_weightings.loc[:, :] = 8760.0 / HOURS
    n.add("Carrier", "gas")
    n.add("Carrier", "AC")
    n.add("Bus", "grid", carrier="AC")
    n.add("Bus", "island", carrier="AC")
    n.add("Bus", "crit", carrier="AC")
    n.add("Load", "town", bus="island", p_set=50.0)
    n.add("Load", "hospital", bus="crit", p_set=10.0)
    for g in ("gen_a", "gen_b"):
        n.add(
            "Generator",
            g,
            bus="island",
            carrier="gas",
            p_nom=500.0,
            marginal_cost=10.0,
            outage_rate_value=1e-9,
            outage_rate_basis="EFORd",
            mttr_hours=1.0,
        )
    n.add(
        "Link",
        "import_poc",
        bus0="grid",
        bus1="island",
        p_nom=50.0,
        efficiency=1.0,
        carrier="AC",
    )
    n.add(
        "Link",
        "crit_feeder",
        bus0="island",
        bus1="crit",
        p_nom=20.0,
        efficiency=1.0,
        carrier="AC",
    )
    n.links["eh_role"] = ""
    n.links.at["import_poc", "eh_role"] = "grid_import"
    n.links["outage_rate_value"] = float("nan")
    n.links["mttr_hours"] = float("nan")
    n.links.at["crit_feeder", "outage_rate_value"] = feeder_rate
    n.links.at["crit_feeder", "mttr_hours"] = 8.0
    n.buses["eh_critical"] = False
    n.buses.at["crit", "eh_critical"] = True
    return n


def _run(n, pack):
    from services.pypsa_service import PyPSAService

    PyPSAService.set_network(n)
    return S.run_eh_study(
        n,
        pack,
        SolverConfig(voll=VOLL),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "mc_certify", "assemble"),
    )


def test_outages_of_the_only_feeder_to_a_critical_bus_count_in_the_certified_lole():
    report = _run(_hub_with_one_critical_feeder(), _pack())
    sec = report.sections["certification"]
    # Hand check: 0.01 unavailability × 8,760 h = 87.6 h/yr the hospital is
    # cut off; the island itself never sheds.
    assert report.mc_lole_h == pytest.approx(87.6, rel=0.01), sec.note
    assert sec.payload["verdict"] == "fail", sec.note
    lo, hi = sec.payload["lole_ci"]
    assert lo == pytest.approx(87.6, rel=0.01)
    # EUE: 10 MW × 87.6 h = 876 MWh/yr from the feeder.
    assert sec.payload["eue_mwh"] == pytest.approx(876.0, rel=0.01)
    feeders = sec.payload["fleet_scope"]["critical_feeders"]
    assert [f["name"] for f in feeders] == ["crit_feeder"]
    assert feeders[0]["buses"] == ["crit"]
    assert feeders[0]["lole_h"] == pytest.approx(87.6, rel=0.01)


def test_a_reliable_feeder_still_certifies():
    # 1e-5 × 8,760 h ≈ 0.09 h/yr: under the 3 h/yr goal, so the fix must
    # not turn every hub with an internal feeder into a miss.
    report = _run(_hub_with_one_critical_feeder(feeder_rate=1e-5), _pack())
    sec = report.sections["certification"]
    assert sec.payload["verdict"] == "pass", sec.note
    assert report.mc_lole_h == pytest.approx(0.0876, rel=0.02)


def test_a_second_feeder_in_parallel_needs_both_out():
    n = _hub_with_one_critical_feeder()
    n.add(
        "Link",
        "crit_feeder_2",
        bus0="island",
        bus1="crit",
        p_nom=20.0,
        efficiency=1.0,
        carrier="AC",
    )
    n.links.at["crit_feeder_2", "eh_role"] = ""
    n.links.at["crit_feeder_2", "outage_rate_value"] = 0.01
    n.links.at["crit_feeder_2", "mttr_hours"] = 8.0
    report = _run(n, _pack())
    # Independent: 0.01 × 0.01 × 8,760 h = 0.876 h/yr.
    assert report.mc_lole_h == pytest.approx(0.876, rel=0.01)
    names = sorted(
        f["name"]
        for f in report.sections["certification"].payload["fleet_scope"][
            "critical_feeders"
        ]
    )
    assert names == ["crit_feeder", "crit_feeder_2"]


def test_a_feeder_without_outage_data_adds_nothing():
    n = _hub_with_one_critical_feeder()
    n.links.at["crit_feeder", "outage_rate_value"] = float("nan")
    n.links.at["crit_feeder", "mttr_hours"] = float("nan")
    report = _run(n, _pack())
    assert report.mc_lole_h == pytest.approx(0.0, abs=1e-6)
    pay = report.sections["certification"].payload
    assert pay["verdict"] == "pass"
    # F5: not silent — the Link is named as taken never to fail.
    assert [(f["name"], f["model"]) for f in pay["critical_feeders"]] == [
        ("crit_feeder", "no_outage_data")
    ]
    assert "crit_feeder" in pay["critical_feeder_note"]
    assert "never failing" in pay["critical_feeder_note"]


def test_a_parallel_line_is_redundancy_and_adds_nothing():
    n = _hub_with_one_critical_feeder()
    n.add("Line", "crit_cable", bus0="island", bus1="crit", x=0.1, r=0.01, s_nom=50.0)
    report = _run(n, _pack())
    pay = report.sections["certification"].payload
    assert report.mc_lole_h == pytest.approx(0.0, abs=1e-6)
    assert pay["critical_feeders"] == [] and pay["critical_feeder_note"] is None


def _with_backup(n):
    n.add(
        "Generator",
        "crit_backup",
        bus="crit",
        carrier="gas",
        p_nom=5.0,
        marginal_cost=50.0,
        outage_rate_value=1e-9,
        outage_rate_basis="EFORd",
        mttr_hours=1.0,
    )
    return n


def test_a_critical_bus_with_its_own_units_is_bounded_not_composed():
    # GK A F6': the pocket's own units are not simulated, so its composed
    # figure (units ignored) is an upper bound: 87.6 h/yr > 3 h/yr, so the
    # verdict is "inconclusive", never "fail", and nothing is added.
    report = _run(_with_backup(_hub_with_one_critical_feeder()), _pack())
    pay = report.sections["certification"].payload
    note = report.sections["certification"].note
    [row] = pay["critical_feeders"]
    assert (row["name"], row["model"], row["upper_bound"]) == (
        "crit_feeder",
        "unsampled",
        True,
    )
    assert row["lole_h"] == pytest.approx(87.6, rel=0.01)
    assert report.mc_lole_h == pytest.approx(0.0, abs=1e-6)  # nothing added
    assert pay["verdict"] == "inconclusive", note
    assert pay["confident"] is False
    assert "crit" in note and "87.6 h/yr" in note
    assert "own units not counted" in note and "second feeder" in note
    assert "upper bound" in pay["critical_feeder_note"]
    [pk] = pay["feeder_bound"]["pockets"]
    assert pk["buses"] == ["crit"] and pk["bound_h_per_year"] == pytest.approx(
        87.6, rel=0.01
    )
    assert "issue" not in note


def test_a_bound_under_the_goal_does_not_block_a_pass():
    # 1e-5 × 8,760 ≈ 0.09 h/yr even with the backup ignored: under 3 h/yr.
    report = _run(
        _with_backup(_hub_with_one_critical_feeder(feeder_rate=1e-5)), _pack()
    )
    pay = report.sections["certification"].payload
    assert pay["verdict"] == "pass", report.sections["certification"].note
    assert pay["critical_feeders"][0]["lole_h"] == pytest.approx(0.0876, rel=0.02)


def test_the_bound_is_the_composed_figure_for_the_same_feeder():
    composed = _run(_hub_with_one_critical_feeder(), _pack())
    bounded = _run(_with_backup(_hub_with_one_critical_feeder()), _pack())
    [c] = composed.sections["certification"].payload["critical_feeders"]
    [b] = bounded.sections["certification"].payload["critical_feeders"]
    assert (c["model"], b["model"]) == ("composed", "unsampled")
    assert b["lole_h"] == pytest.approx(c["lole_h"], rel=1e-9)
    assert b["eue_mwh"] == pytest.approx(c["eue_mwh"], rel=1e-9)


def test_a_chained_feeder_has_no_bound_and_withholds_a_pass():
    # island → mid (no supply) → crit: no closed-form bound, so a reliable
    # pair of feeders still cannot certify.
    n = _hub_with_one_critical_feeder(feeder_rate=1e-5)
    n.add("Bus", "mid", carrier="AC")
    n.links.at["crit_feeder", "bus0"] = "mid"
    n.add(
        "Link",
        "mid_feeder",
        bus0="island",
        bus1="mid",
        p_nom=20.0,
        efficiency=1.0,
        carrier="AC",
    )
    n.links.at["mid_feeder", "eh_role"] = ""
    n.links.at["mid_feeder", "outage_rate_value"] = 1e-5
    n.links.at["mid_feeder", "mttr_hours"] = 8.0
    report = _run(n, _pack())
    pay = report.sections["certification"].payload
    assert [(f["name"], f["chained"]) for f in pay["critical_feeders"]] == [
        ("crit_feeder", True)
    ]
    assert pay["verdict"] == "inconclusive"
    assert "crit_feeder" in report.sections["certification"].note


def _dc_pack(target_lole_h: float = 25.0):
    # The template's recommended pack. The hub alone is about 15 h/yr (CI
    # upper ≈ 21 h/yr) at these draws, so a 25 h/yr goal isolates the bound.
    from models.energy_hub import default_weak_flexible_pack

    base = default_weak_flexible_pack()
    return base.model_copy(
        update={
            "availability": AvailabilityTarget(
                ens_cap_permyriad=10.0,
                target_lole_h=target_lole_h,
                certification_metric="mc_lole",
            ),
            "levers": base.levers.model_copy(update={"storage_duration": False}),
            "mc_draws": 200,
        }
    )


def _dc(rate=None):
    from project_templates.eh_templates import build_eh_datacenter

    n = build_eh_datacenter()
    if rate is not None:
        n.links.at["site_transformer", "outage_rate_value"] = rate
    return n


@pytest.mark.live_solve
def test_data_centre_transformer_bound_as_shipped_is_inconclusive():
    # site_transformer q = 0.005: 0.005 × 8,760 = 43.8 h/yr bound; with the
    # hub's ≈ 21 h/yr that is over the 25 h/yr goal.
    report = _run(_dc(), _dc_pack())
    sec = report.sections["certification"]
    [row] = sec.payload["critical_feeders"]
    assert (row["name"], row["model"], row["upper_bound"]) == (
        "site_transformer",
        "unsampled",
        True,
    )
    assert row["lole_h"] == pytest.approx(43.8, rel=0.01)
    assert sec.payload["verdict"] == "inconclusive", sec.note
    assert "it_bus" in sec.note


@pytest.mark.live_solve
def test_data_centre_transformer_bound_under_the_goal_passes():
    # 1e-5 × 8,760 ≈ 0.09 h/yr: the hub's ≈ 21 h/yr stays under 25 h/yr.
    report = _run(_dc(rate=1e-5), _dc_pack())
    sec = report.sections["certification"]
    assert sec.payload["critical_feeders"][0]["lole_h"] == pytest.approx(
        0.0876, rel=0.02
    )
    assert sec.payload["verdict"] == "pass", sec.note


@pytest.mark.live_solve
def test_island_microgrid_certifies_the_hospital_feeder():
    # The walkthrough's own case: Island Microgrid, its shipped 3 h/yr goal.
    from project_templates.eh_templates import build_eh_microgrid

    report = _run(build_eh_microgrid(), _pack())
    sec = report.sections["certification"]
    # Hand check: 0.01 × 8,760 h = 87.6 h/yr; the island fleet adds little.
    assert report.mc_lole_h == pytest.approx(87.6, rel=0.05), sec.note
    assert sec.payload["verdict"] == "fail"
    [row] = sec.payload["critical_feeders"]
    assert (row["name"], row["model"], row["buses"]) == (
        "hospital_feeder",
        "composed",
        ["hospital"],
    )
    assert row["lole_h"] == pytest.approx(87.6, rel=0.01)


def test_the_copper_plate_views_say_they_leave_the_feeder_out():
    # GK A F8: the loops and Expert's Monte Carlo read `loop_scope`.
    from services.adequacy.link_supply import assemble_fleet, loop_scope

    n = _hub_with_one_critical_feeder()
    notes = assemble_fleet(n, loop_scope(n)).fleet_notes
    assert any("crit_feeder" in x and "certification does" in x for x in notes)
    m = _hub_with_one_critical_feeder()
    m.add("Line", "crit_cable", bus0="island", bus1="crit", x=0.1, s_nom=50.0)
    assert not any(
        "certification does" in x for x in assemble_fleet(m, loop_scope(m)).fleet_notes
    )
