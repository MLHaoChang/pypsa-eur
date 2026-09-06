"""
The fixed-capacity contingency sweep driver (Phase 4 Task 1) and the class-B
link contingencies (Task 2).

Design: spec §§4.1, 5.4, 6.2, 7.2; plan 2026-08-28-fmea-phase4-taxonomy.md.
Fixture: a 2-bus system where a Link is the ONLY path to the load —
bus_gen (200 MW cheap generator) —tie link 100 MW→ bus_load (50 MW load).
Base EUE is exactly 0; with the tie out, exactly 50 MW strands every
snapshot: ΔEUE = 50 × 2 snapshots × weight 3 = 300 MWh.
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pypsa
import pytest

from services.adequacy import sweep as S
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig

WEIGHT = 3.0
N_SNAPSHOTS = 2
LOAD_MW = 50.0
STRANDED_MWH = LOAD_MW * N_SNAPSHOTS * WEIGHT   # 300
VOLL = 3000.0
LINK_Q, LINK_MTTR = 0.02, 24.0


def _network(extendable_local_gen: bool = False) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=N_SNAPSHOTS, freq="h"))
    n.snapshot_weightings.loc[:, :] = WEIGHT
    n.add("Carrier", "gas")
    n.add("Bus", "bus_gen", carrier="AC")
    n.add("Bus", "bus_load", carrier="AC")
    n.add("Load", "l", bus="bus_load", p_set=LOAD_MW)
    n.add("Generator", "big_cheap", bus="bus_gen", carrier="gas",
          p_nom=200.0, marginal_cost=10.0)
    n.add("Link", "tie", bus0="bus_gen", bus1="bus_load", p_nom=100.0,
          efficiency=1.0,
          outage_rate_value=LINK_Q, outage_rate_basis="FOR",
          mttr_hours=LINK_MTTR)
    if extendable_local_gen:
        # Cheap enough that an UNFROZEN contingency solve would build it and
        # shed nothing — the freeze is what forces the honest 300 MWh.
        n.add("Generator", "local_option", bus="bus_load", carrier="gas",
              p_nom=0.0, p_nom_extendable=True, p_nom_max=100.0,
              capital_cost=1.0, marginal_cost=20.0)
    return n


def _cfg(**kw) -> SolverConfig:
    return SolverConfig(voll=VOLL, **kw)


def _link_out(name: str):
    """Force the link's flow to zero via p_max_pu/p_min_pu — capacity stays,
    so preflight's link_p_nom_invalid guard (p_nom must be > 0 on a fixed
    link) is respected. This is the driver's own representation too."""
    def mutate(n):
        orig_max = float(n.links.at[name, "p_max_pu"])
        orig_min = float(n.links.at[name, "p_min_pu"])
        n.links.at[name, "p_max_pu"] = 0.0
        n.links.at[name, "p_min_pu"] = 0.0
        def undo():
            n.links.at[name, "p_max_pu"] = orig_max
            n.links.at[name, "p_min_pu"] = orig_min
        return undo
    return mutate


def _run(n, contingencies, cfg=None):
    PyPSAService.set_network(n)
    return S.run_contingency_sweep(
        n, PyPSAService.get_lock(), cfg or _cfg(),
        contingencies, log_queue=queue.SimpleQueue(),
    )


def test_link_out_strands_exactly_the_load():
    n = _network()
    out = _run(n, [{"id": "tie_out", "mutate": _link_out("tie"), "meta": {}}])
    assert out["base"]["eue_mwh"] == pytest.approx(0.0, abs=1e-6)
    assert out["contingencies"]["tie_out"]["delta_eue_mwh"] == pytest.approx(
        STRANDED_MWH, rel=1e-3)


def test_capacities_are_frozen_during_contingencies():
    """With an unfrozen LP the 1 €/MW local option would be built and nothing
    shed; the frozen operational re-solve must shed the full 300 MWh."""
    n = _network(extendable_local_gen=True)
    out = _run(n, [{"id": "tie_out", "mutate": _link_out("tie"), "meta": {}}])
    assert out["contingencies"]["tie_out"]["delta_eue_mwh"] == pytest.approx(
        STRANDED_MWH, rel=1e-3)
    # The freeze is undone after the sweep: the option's bounds are back.
    assert bool(n.generators.at["local_option", "p_nom_extendable"]) is True
    assert float(n.generators.at["local_option", "p_nom_max"]) == pytest.approx(100.0)
    assert float(n.generators.at["local_option", "p_nom_min"]) == pytest.approx(0.0)


def test_network_is_left_in_base_state():
    """The closing base re-solve must leave dispatch at BASE, not at the last
    contingency — the link flows again and nothing is shed."""
    n = _network()
    _run(n, [{"id": "tie_out", "mutate": _link_out("tie"), "meta": {}}])
    assert float(n.links.at["tie", "p_nom"]) == pytest.approx(100.0)
    flows = n.links_t.p0["tie"]
    assert float(flows.min()) == pytest.approx(LOAD_MW, rel=1e-4)


def test_foreground_state_is_untouched():
    from routers.simulation import _state
    before = _state.get("last_lost_load")
    n = _network()
    _run(n, [{"id": "tie_out", "mutate": _link_out("tie"), "meta": {}}])
    assert _state.get("last_lost_load") is before


def test_budget_guard_refuses_oversize_sweeps():
    n = _network()
    too_many = [{"id": f"c{i}", "mutate": _link_out("tie"), "meta": {}}
                for i in range(S.MAX_CONTINGENCIES + 1)]
    with pytest.raises(S.SweepBudgetError):
        _run(n, too_many)


def test_sweep_requires_voll():
    n = _network()
    with pytest.raises(ValueError):
        _run(n, [{"id": "c", "mutate": _link_out("tie"), "meta": {}}],
             cfg=SolverConfig(voll=0.0))


def test_ens_cap_is_stripped_inside_the_sweep():
    """A tight ENS cap on the user's config must NOT bind the contingency
    re-solve (it would make severity read as 'the cap', or go infeasible) —
    the sweep clones the config with the target off."""
    n = _network()
    out = _run(n, [{"id": "tie_out", "mutate": _link_out("tie"), "meta": {}}],
               cfg=_cfg(ens_cap_permyriad=1.0))
    assert out["contingencies"]["tie_out"]["delta_eue_mwh"] == pytest.approx(
        STRANDED_MWH, rel=1e-3)


# ── class B: link contingencies (Task 2) ──────────────────────────────────

def test_class_b_contingencies_select_occurrence_bearing_links_only():
    n = _network()
    n.add("Bus", "bus3", carrier="AC")
    n.add("Load", "l3", bus="bus3", p_set=1.0)
    # A link with NO occurrence data must be skipped (nothing to price).
    n.add("Link", "silent_tie", bus0="bus_gen", bus1="bus3", p_nom=10.0)
    cons = S.class_b_contingencies(n)
    assert [c["id"] for c in cons] == ["link:tie:forced_outage"]
    assert cons[0]["meta"]["q"] == pytest.approx(LINK_Q)


def test_class_b_rows_price_outages_first_order():
    """criticality €/yr = q × ΔEUE_full × VoLL (the whole-horizon hold
    integrates over event timing); occurrence = 8760·q/MTTR; severity =
    criticality / occurrence — the f×S identity by construction."""
    n = _network()
    PyPSAService.set_network(n)
    rows, _restore = S.run_class_b_sweep(n, PyPSAService.get_lock(), _cfg(),
                               log_queue=queue.SimpleQueue())
    assert len(rows) == 1
    r = rows[0]
    assert r["delta_eue_mwh"] == pytest.approx(STRANDED_MWH, rel=1e-3)
    fm = r["failure_mode"]
    assert fm["failure_class"] == "B"
    assert fm["engine"] == "lp_proxy"
    assert fm["fidelity"] == "deterministic_scenario"
    assert fm["criticality_eur_per_year"] == pytest.approx(
        LINK_Q * STRANDED_MWH * VOLL, rel=1e-3)
    assert fm["occurrence_per_year"] == pytest.approx(8760 * LINK_Q / LINK_MTTR)
    assert fm["severity_eur"] * fm["occurrence_per_year"] == pytest.approx(
        fm["criticality_eur_per_year"], rel=1e-9)
    from models.adequacy import FailureModeResult
    FailureModeResult.model_validate(fm)


def test_class_b_zeroes_time_varying_availability_too():
    """A links_t.p_max_pu column overrides the static — the outage must
    zero it (and restore it) or the link keeps flowing."""
    n = _network()
    n.links_t.p_max_pu = pd.DataFrame({"tie": [1.0, 1.0]}, index=n.snapshots)
    PyPSAService.set_network(n)
    rows, _restore = S.run_class_b_sweep(n, PyPSAService.get_lock(), _cfg(),
                               log_queue=queue.SimpleQueue())
    assert rows[0]["delta_eue_mwh"] == pytest.approx(STRANDED_MWH, rel=1e-3)
    assert float(n.links_t.p_max_pu["tie"].min()) == pytest.approx(1.0)


# ── the background runner + routes ────────────────────────────────────────

def test_fmea_sweep_routes_lifecycle():
    import routers.results as R
    from routers.simulation import _state
    n = _network()
    PyPSAService.set_network(n)
    _state.pop("fmea_sweep", None)
    _state["solver_config"] = _cfg()
    resp = R.get_fmea_sweep()
    assert getattr(resp, "status_code", 200) == 204
    out = R.post_fmea_sweep()
    assert out["status"] == "running"
    _state["fmea_sweep"]["thread"].join(timeout=300)
    done = R.get_fmea_sweep()
    assert done["status"] == "done", done
    assert done["rows"][0]["failure_mode"]["failure_class"] == "B"
    assert "thread" not in done
    # A second start while one is recorded as running would 409; after done
    # it restarts cleanly.
    out2 = R.post_fmea_sweep()
    assert out2["status"] == "running"
    _state["fmea_sweep"]["thread"].join(timeout=300)
