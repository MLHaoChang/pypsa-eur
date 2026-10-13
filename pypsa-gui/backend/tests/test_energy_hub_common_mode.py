"""
Common-mode import outages (plan
docs/superpowers/plans/2026-09-28-eh-zonal-mc-open-items.md, WP4).

The 2026-09-27 engine drew Link and grid outages independently. An event
that takes the PoC Link AND the grid behind it down together (a substation
fire, a storm on the shared corridor) was not modelled. It is now, from
opt-in data on the Link — ``common_mode_rate`` / ``common_mode_mttr_hours``
(``common_mode_basis`` optional, default ``FOR``) — and never defaulted.
"""
from __future__ import annotations

import threading

import numpy as np
import pytest

from models.energy_hub import default_strong_grid_pack
from services.adequacy import eh_stages as ST
from services.adequacy import mc as MC
from services.adequacy import mc_zonal as Z
from services.solver_service import SolverConfig
from tests.eh_stage_fixtures import VOLL, certifiable_weak_network
from tests.zonal_oracle import legacy_zonal_blocks

OVERLAY = default_strong_grid_pack().import_overlay
DRAWS = 400
SEED = 9


def _freeze(n, **kw):
    return ST.freeze_fixed_plan(n, SolverConfig(voll=VOLL), threading.Lock(),
                                overlay=OVERLAY, **kw)


def _weak(*, rate=None, mttr=None, basis=None, grid_sampled=True,
          firm_link=False, islanded=False):
    n = certifiable_weak_network()
    n.links.at["import_poc", "p_nom"] = 50.0
    if rate is not None:
        n.links["common_mode_rate"] = np.nan
        n.links.at["import_poc", "common_mode_rate"] = rate
    if mttr is not None:
        n.links["common_mode_mttr_hours"] = np.nan
        n.links.at["import_poc", "common_mode_mttr_hours"] = mttr
    if basis is not None:
        n.links["common_mode_basis"] = ""
        n.links.at["import_poc", "common_mode_basis"] = basis
    if not grid_sampled:
        n.add("Carrier", "grid_mix")
        n.generators.at["grid_supply", "carrier"] = "grid_mix"
        for c in ("outage_rate_value", "mttr_hours"):
            n.generators.at["grid_supply", c] = np.nan
    if firm_link:
        for c in ("outage_rate_value", "mttr_hours"):
            n.links[c] = np.nan
        n.links["outage_rate_basis"] = ""
    if islanded:
        n.links.at["import_poc", "p_max_pu"] = 0.0
    return n


def _blocks(z):
    return Z.simulate_zonal_blocks(z, draws=DRAWS, seed=SEED)


def _eue(blocks):
    return sum(v[1] for v in blocks.values())


def _oracle(z):
    a = Z.single_area(z)
    return legacy_zonal_blocks(z.hub, a.grid, a.import_idx, a.firm_import_mw,
                               a.delivery_ratio, draws=DRAWS, seed=SEED)


def _same(a, b):
    for k in a:
        np.testing.assert_array_equal(a[k][0], b[k][0])
        np.testing.assert_array_equal(a[k][1], b[k][1])


def test_no_common_mode_data_changes_nothing():
    frozen = _freeze(_weak())
    assert frozen.scope["import_common_mode"] == []
    _same(_blocks(frozen.zonal_inputs), _oracle(frozen.zonal_inputs))


def test_a_common_mode_event_raises_eue_draw_by_draw():
    base = _freeze(_weak())
    cm = _freeze(_weak(rate=0.05, mttr=24.0))
    [entry] = cm.scope["import_common_mode"]
    assert entry == {"link": "import_poc", "rate": 0.05, "mttr_hours": 24.0,
                     "basis": "FOR", "area": 0, "applied": True,
                     "reason": None}
    e_cm, e_base = _eue(_blocks(cm.zonal_inputs)), _eue(_blocks(base.zonal_inputs))
    assert np.all(e_cm >= e_base - 1e-9)
    assert e_cm.mean() > e_base.mean()


def test_the_basis_is_carried_when_given():
    cm = _freeze(_weak(rate=0.05, mttr=24.0, basis="EFORd"))
    assert cm.scope["import_common_mode"][0]["basis"] == "EFORd"


def test_a_zero_rate_draws_nothing_and_changes_nothing():
    """
    Q = 0 consumes no stream (the ``sample_capacity`` contract), so the
    run is bit-identical to having no common-mode data at all.
    """
    frozen = _freeze(_weak(rate=0.0, mttr=24.0))
    assert frozen.scope["import_common_mode"][0]["applied"] is True
    _same(_blocks(frozen.zonal_inputs), _oracle(frozen.zonal_inputs))


def test_a_rate_without_mttr_is_reported_not_guessed():
    frozen = _freeze(_weak(rate=0.05))
    [entry] = frozen.scope["import_common_mode"]
    assert entry["applied"] is False and "MTTR" in entry["reason"]
    _same(_blocks(frozen.zonal_inputs), _oracle(frozen.zonal_inputs))


def test_an_unusable_rate_refuses_the_snapshot_naming_the_link():
    frozen = _freeze(_weak(rate=1.5, mttr=24.0))
    assert frozen.mc_inputs is None
    assert "import_poc" in frozen.mc_error and "[0, 1)" in frozen.mc_error


def test_an_islanded_link_does_not_apply_its_common_mode():
    frozen = _freeze(_weak(rate=0.05, mttr=24.0, islanded=True))
    [entry] = frozen.scope["import_common_mode"]
    assert entry["applied"] is False and "islanded" in entry["reason"]
    assert frozen.zonal_inputs is None


def test_a_v1_hub_gains_the_event_through_the_two_area_engine():
    """
    Grid without occurrence data: no sampled area, but the common-mode
    chain still needs the two-area engine (the area is unbounded).
    """
    v1 = _freeze(_weak(grid_sampled=False))
    cm = _freeze(_weak(grid_sampled=False, rate=0.05, mttr=24.0))
    assert v1.zonal_inputs is None
    assert cm.zonal_inputs is not None
    assert cm.scope["import_model"] == "sampled_unit"
    assert cm.zonal_inputs.areas[0].grid is None
    e_cm = _eue(_blocks(cm.zonal_inputs))
    e_v1 = _eue(MC._simulate_blocks(v1.mc_inputs, draws=DRAWS, seed=SEED))
    assert np.all(e_cm >= e_v1 - 1e-9) and e_cm.mean() > e_v1.mean()


def test_the_copt_mixes_the_event_and_ranks_it_as_its_own_mode():
    """
    Review of WP4 (R1): folding q_cm into each Link unit treats a SHARED
    event as independent per unit. The screening now mixes the area's event
    states exactly and gives the event its own class-A row.
    """
    cm = _freeze(_weak(rate=0.05, mttr=24.0))
    base = _freeze(_weak())
    assert cm.copt_metrics["lole_hours"] > base.copt_metrics["lole_hours"]
    link = next(u for u in cm.screening_units if u.name == "link:import_poc")
    assert link.q == pytest.approx(0.03)          # the Link's own rate only
    row = next(r for r in cm.copt_rows
               if r["failure_mode"]["component_class"] == "CommonMode")
    fm = row["failure_mode"]
    assert fm["name"] == "common_mode:import_poc"
    assert fm["failure_class"] == "A"
    assert fm["occurrence_per_year"] == pytest.approx(8760.0 * 0.05 / 24.0)
    assert row["delta_eue_mwh"] > 0.0
    assert cm.scope["copt_common_mode"] == "event_mixture"


def test_a_firm_link_with_common_mode_is_mixed_not_converted():
    cm = _freeze(_weak(firm_link=True, rate=0.05, mttr=24.0))
    base = _freeze(_weak(firm_link=True))
    assert cm.copt_metrics["lole_hours"] > base.copt_metrics["lole_hours"]
    # No invented unit: the firm block stays firm in the "event up" state.
    assert "link:import_poc" not in [u.name for u in cm.screening_units]
    assert "link:import_poc" not in [u.name for u in cm.mc_inputs.units]
    assert cm.scope["import_common_mode_sampled"] is True
    # R2: on the event-only path (no sampled grid area) a firm block under a
    # sampled event is not "planning_limit_only" any more.
    only = _freeze(_weak(firm_link=True, grid_sampled=False, rate=0.05,
                         mttr=24.0))
    assert only.scope["import_model"] == "firm_block"
    assert only.scope["import_firmness"] == "common_mode_sampled"
    assert only.scope["copt_common_mode"] == "event_mixture"
    assert "common-mode events are MIXED" in only.scope["copt_import_note"]


def _two_firm_links_one_event(q_cm=0.2):
    """
    Review of WP4 (R1): two 25 MW firm Links into one ungridded area,
    one event q_cm. Independent per-unit q_cm gave 985 h vs 1190 h exact.
    """
    n = _weak(grid_sampled=False, firm_link=True, rate=q_cm, mttr=24.0)
    n.links.at["import_poc", "p_nom"] = 25.0
    n.add("Link", "poc_2", bus0="grid", bus1="hub", p_nom=25.0, carrier="AC")
    n.links.at["poc_2", "eh_role"] = "grid_import"
    return n


def test_the_event_takes_every_link_of_its_area_down():
    frozen = _freeze(_two_firm_links_one_event())
    exact = frozen.copt_metrics["import_exact"]["lole_hours"]
    # Grid unbounded: the event mixture is EXACT in the screening too.
    assert frozen.copt_metrics["lole_hours"] == pytest.approx(exact, rel=1e-9)


@pytest.mark.parametrize("kw", [
    {"rate": 0.05, "mttr": 24.0},
    {"rate": 0.05, "mttr": 24.0, "grid_sampled": False},
    {"rate": 0.05, "mttr": 24.0, "firm_link": True},
])
def test_the_exact_metric_with_common_mode_is_the_mc_expectation(kw):
    frozen = _freeze(_weak(**kw))
    exact = frozen.copt_metrics["import_exact"]
    res = Z.zonal_mc_adequacy(frozen.zonal_inputs, draws=4000, seed=21,
                              cov_target=0.0, max_draws=4000)
    lo, hi = res["lole_ci"]
    half = (hi - lo) / 2.0
    assert lo - half <= exact["lole_hours"] <= hi + half, (exact, res["lole_hours"])
    lo, hi = res["eue_ci"]
    half = (hi - lo) / 2.0
    assert lo - half <= exact["eue_mwh"] <= hi + half, (exact, res["eue_mwh"])


# ── review of WP4 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["sampled_unit", "firm_block"])
def test_an_event_is_not_reported_applied_on_a_path_that_ignores_it(mode):
    """B1: only import_model='auto' builds the chains."""
    frozen = _freeze(_weak(rate=0.05, mttr=24.0), import_model=mode)
    [entry] = frozen.scope["import_common_mode"]
    assert entry["applied"] is False and mode in entry["reason"]
    # The note is written after the flag is cleared, so it states the same
    # firmness as the payload (it said common_mode_sampled before).
    firmness = frozen.scope["import_firmness"]
    assert firmness != "common_mode_sampled"
    assert f"import_firmness={firmness}" in frozen.scope["note"]


def test_an_implied_mttf_under_one_hour_refuses_at_snapshot():
    """
    B2: rate 0.9 with MTTR 1 h implies MTTF 0.11 h — refused when the
    plan is frozen, naming the Link, not at certify time.
    """
    frozen = _freeze(_weak(rate=0.9, mttr=1.0))
    assert frozen.mc_inputs is None
    assert "import_poc" in frozen.mc_error and "MTTF" in frozen.mc_error


def test_common_mode_data_on_a_non_import_link_is_disclosed():
    n = _weak()
    n.add("Bus", "far", carrier="AC")
    n.add("Link", "internal", bus0="hub", bus1="far", p_nom=10.0, carrier="AC")
    n.links["common_mode_rate"] = np.nan
    n.links.at["internal", "common_mode_rate"] = 0.1
    frozen = _freeze(n)
    [entry] = frozen.scope["import_common_mode"]
    assert entry["link"] == "internal" and entry["applied"] is False
    assert "not an identified import Link" in entry["reason"]


def test_a_zero_rate_event_does_not_switch_a_v1_hub_to_the_two_area_engine():
    frozen = _freeze(_weak(grid_sampled=False, rate=0.0, mttr=24.0))
    assert frozen.zonal_inputs is None


def test_the_grid_does_not_charge_during_an_event():
    """
    While the event holds the grid down, its surplus is 0: an empty grid
    battery charges strictly less than without the event (a kernel that
    only zeroed the Link offer would let it charge MORE).
    """
    def net(rate):
        n = _weak(firm_link=True, rate=rate, mttr=24.0)
        n.generators.at["grid_supply", "outage_rate_value"] = 0.0
        n.add("StorageUnit", "grid_bat", bus="grid", carrier="battery",
              p_nom=100.0, max_hours=100.0, outage_rate_value=0.0,
              outage_rate_basis="FOR", mttr_hours=24.0)
        return _freeze(n)

    t_cm, t_no = {}, {}
    Z.simulate_zonal_blocks(net(0.3).zonal_inputs, draws=DRAWS, seed=SEED,
                            initial_soc_frac=0.0, trace=t_cm)
    Z.simulate_zonal_blocks(net(None).zonal_inputs, draws=DRAWS, seed=SEED,
                            initial_soc_frac=0.0, trace=t_no)
    assert t_cm["areas"][0]["charge_mwh"] < t_no["areas"][0]["charge_mwh"]


def test_two_events_in_one_area_draw_from_their_own_streams():
    n = _weak(rate=0.3, mttr=24.0)
    n.add("Link", "poc_2", bus0="grid", bus1="hub", p_nom=10.0, carrier="AC",
          common_mode_rate=0.3, common_mode_mttr_hours=24.0)
    n.links.at["poc_2", "eh_role"] = "grid_import"
    z = _freeze(n).zonal_inputs
    [area] = z.areas
    assert [c.stream for c in area.common_mode] == [0, 1]
    st = Z._AreaState(area, z.hub, tuple(z.hub.units),
                      np.random.SeedSequence(1), len(z.hub.residual), 20000,
                      True, 1.0)
    # Two independent chains: P(up) = 0.7² = 0.49 (one shared stream: 0.7).
    assert float(st.cm_up.mean()) == pytest.approx(0.49, abs=0.02)


def test_events_in_two_areas_match_the_mc_expectation():
    from tests.test_energy_hub_zonal_areas import two_grid_hub

    n = two_grid_hub()
    n.links["common_mode_rate"] = 0.1
    n.links["common_mode_mttr_hours"] = 24.0
    frozen = _freeze(n)
    assert [len(a.common_mode) for a in frozen.zonal_inputs.areas] == [1, 1]
    exact = frozen.copt_metrics["import_exact"]
    res = Z.zonal_mc_adequacy(frozen.zonal_inputs, draws=4000, seed=23,
                              cov_target=0.0, max_draws=4000)
    lo, hi = res["lole_ci"]
    half = (hi - lo) / 2.0
    assert lo - half <= exact["lole_hours"] <= hi + half, (exact, res["lole_hours"])


def test_two_events_in_one_area_match_the_mc_expectation():
    """Both chains of one area enter the exact metric (P(up) = Π(1 − q_j))."""
    n = _weak(rate=0.2, mttr=24.0)
    n.add("Link", "poc_2", bus0="grid", bus1="hub", p_nom=10.0, carrier="AC",
          common_mode_rate=0.2, common_mode_mttr_hours=24.0)
    n.links.at["poc_2", "eh_role"] = "grid_import"
    frozen = _freeze(n)
    assert len(frozen.zonal_inputs.areas[0].common_mode) == 2
    exact = frozen.copt_metrics["import_exact"]
    res = Z.zonal_mc_adequacy(frozen.zonal_inputs, draws=4000, seed=29,
                              cov_target=0.0, max_draws=4000)
    lo, hi = res["lole_ci"]
    half = (hi - lo) / 2.0
    assert lo - half <= exact["lole_hours"] <= hi + half, (exact, res["lole_hours"])
