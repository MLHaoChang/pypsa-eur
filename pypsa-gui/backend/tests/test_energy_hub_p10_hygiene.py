"""
P10 hygiene — plan docs/superpowers/plans/2026-09-25-eh-post-seal-implementation.md.

P10b: DtC stress is a FIXED-PLAN re-dispatch (spec decision 8) — it must not
      re-optimise capacity under islanding; and its unserved fallbacks must
      read the Load-keyed P6(b) capture correctly.
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pytest

from models.energy_hub import DtcConfig
from services.adequacy import dtc as D
from services.solver_service import SolverConfig
from tests.test_energy_hub_dtc import _weak_network


def _dtc() -> DtcConfig:
    return DtcConfig(critical_bus_ids=["crit"],
                     islanding_contingencies=["import_poc"])


def _with_cheap_extendable(n):
    # Cheap to build; the plan never builds it because import is cheaper.
    n.add("Generator", "peaker", bus="crit", carrier="gas",
          p_nom=0.0, p_nom_extendable=True, p_nom_max=500.0,
          capital_cost=0.01, marginal_cost=60.0)
    return n


def _stress(n, cfg):
    from services.pypsa_service import PyPSAService
    PyPSAService.set_network(n)
    return D.run_dtc_stress(
        n, cfg, lock=PyPSAService.get_lock(), stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(), dtc=_dtc())


# ── P10b-2: fixed plan ──────────────────────────────────────────────────────


@pytest.mark.live_solve
def test_dtc_stress_does_not_build_capacity_under_islanding():
    n = _with_cheap_extendable(_weak_network())
    table = _stress(n, SolverConfig(voll=500.0))
    row = table["contingencies"][0]
    assert row["status"] in ("ok", "optimal")
    # Frozen plan: the peaker stays at 0 MW. Islanded, the hub has 30 MW
    # local for 100 MW of load → 70 MW × 4 h = 280 MWh unserved in total.
    # Re-optimising would build the peaker and report 0 (pre-fix).
    crit = row["critical_unserved_mwh"]
    other = row["noncritical_unserved_mwh"]
    assert crit + other == pytest.approx(280.0, rel=1e-4)
    # At equal VoLL the crit/flex split is degenerate (P16 premium owns
    # that); only the physical bounds are pinned: crit is short at least
    # 10 MW (40 load − 30 local) and never more than its own 40 MW demand.
    assert 40.0 - 1e-3 <= crit <= 160.0 + 1e-3
    assert other <= 240.0 + 1e-3


# ── P10b-3: a Load's VoLL slack never sheds more than that Load demands ────


@pytest.mark.live_solve
def test_voll_slack_is_bounded_by_its_own_load_each_snapshot():
    """P6(b) slacks were 10× peak with no per-snapshot bound, so a slack could
    'shed' more than its Load and export the surplus over Links — unserved
    energy attributed to the wrong Load/bus, above that Load's demand."""
    from services.pypsa_service import PyPSAService
    from services.solver_service import run_simulation

    n = _weak_network()
    # Island the hub by hand so shortage is forced; crit_flex couples buses.
    n.links.at["import_poc", "p_max_pu"] = 0.0
    n.loads_t.p_set["critical"] = [40.0, 20.0, 40.0, 5.0]
    PyPSAService.set_network(n)
    sink: dict = {}
    status, _ = run_simulation(
        SolverConfig(voll=500.0), n, PyPSAService.get_lock(),
        threading.Event(), queue.SimpleQueue(),
        state_update=lambda **kw: sink.update(kw))
    assert status in ("ok", "optimal")
    lost_t = sink["last_lost_load"]["lost_load_t"]
    demand = n.loads_t.p_set.reindex(columns=lost_t.columns)
    for load in lost_t.columns:
        d = demand[load] if load in n.loads_t.p_set.columns else \
            pd.Series(float(n.loads.at[load, "p_set"]), index=lost_t.index)
        assert (lost_t[load] <= d + 1e-6).all(), load
    # Energy balance: total shed = total load − 30 MW local, per snapshot.
    total_load = pd.Series([40.0, 20.0, 40.0, 5.0], index=n.snapshots) + 60.0
    assert lost_t.sum(axis=1).to_numpy() == pytest.approx(
        (total_load - 30.0).clip(lower=0).to_numpy(), rel=1e-6)


@pytest.mark.live_solve
def test_dtc_stress_strips_margin_and_ens_targets_for_the_frozen_solve():
    n = _with_cheap_extendable(_weak_network())
    cfg = SolverConfig(voll=500.0, ens_cap_permyriad=10.0,
                       reserve_margin=0.5)
    table = _stress(n, cfg)
    assert table["contingencies"][0]["status"] in ("ok", "optimal")
    # The caller's cfg is not mutated.
    assert cfg.reserve_margin == 0.5
    assert cfg.ens_cap_permyriad == 10.0


@pytest.mark.live_solve
def test_dtc_stress_solves_the_islanded_copy_with_pinned_capacity(monkeypatch):
    """The islanded solve itself must see min == solved size, max ≈ size."""
    import services.solver_service as SS

    n = _with_cheap_extendable(_weak_network())
    seen: list = []
    real = SS.run_simulation

    def spy(cfg_i, net, *a, **k):
        seen.append((net is n,
                     float(net.generators.at["peaker", "p_nom_min"]),
                     float(net.generators.at["peaker", "p_nom_max"]),
                     cfg_i.ens_zone_cap_multiple, cfg_i.reserve_margin))
        return real(cfg_i, net, *a, **k)

    monkeypatch.setattr(SS, "run_simulation", spy)
    _stress(n, SolverConfig(voll=500.0, ens_zone_cap_multiple=2.0,
                            reserve_margin=0.5))
    assert len(seen) == 1
    is_shared, pmin, pmax, zone, margin = seen[0]
    assert not is_shared                       # a private copy
    assert pmin == pytest.approx(0.0) and pmax == pytest.approx(0.0, abs=1e-5)
    assert zone is None and margin is None
    # The shared network's bounds are untouched.
    assert float(n.generators.at["peaker", "p_nom_max"]) == 500.0


@pytest.mark.live_solve
def test_dtc_stress_on_an_unsolved_network_freezes_at_nameplate():
    """PyPSA defaults p_nom_opt to 0 before any solve — freezing on it would
    stress a brownfield extendable as if it did not exist."""
    n = _weak_network()
    n.add("Generator", "brownfield", bus="crit", carrier="gas",
          p_nom=50.0, p_nom_min=0.0, p_nom_extendable=True, p_nom_max=500.0,
          capital_cost=1000.0, marginal_cost=60.0)
    assert not n.is_solved
    table = _stress(n, SolverConfig(voll=500.0))
    # 30 local + 50 brownfield ≥ 40 critical: nothing critical is unserved.
    assert table["contingencies"][0]["critical_unserved_mwh"] == \
        pytest.approx(0.0, abs=1e-6)


# ── P10b-1: Load-keyed fallbacks ────────────────────────────────────────────


def _two_loads_one_bus():
    n = _weak_network()
    n.add("Load", "crit_b", bus="crit", p_set=5.0)
    return n


def test_unserved_fallback_rolls_load_keyed_timeseries_up_to_buses():
    n = _two_loads_one_bus()
    snaps = n.snapshots
    lost_t = pd.DataFrame(
        {"critical": [1.0, 2.0, 0.0, 0.0], "crit_b": [0.5, 0.0, 0.0, 0.0],
         "comfort": [3.0, 0.0, 0.0, 0.0]}, index=snaps)
    n.snapshot_weightings.loc[:, :] = 2.0
    sink = {"last_lost_load": {"lost_load_t": lost_t}}
    assert D._bus_unserved_mwh(n, {"crit"}, sink=sink) == pytest.approx(7.0)
    assert D._bus_unserved_mwh(n, {"flex"}, sink=sink) == pytest.approx(6.0)


def test_unserved_fallback_uses_load_period_capture_when_bus_rollup_absent():
    n = _two_loads_one_bus()
    lp = pd.DataFrame([{"critical": 3.0, "crit_b": 1.0, "comfort": 4.0}],
                      index=["ALL"])
    sink = {"last_lost_load": {"lost_load_load_period_mwh": lp}}
    assert D._bus_unserved_mwh(n, {"crit"}, sink=sink) == pytest.approx(4.0)
    assert D._bus_unserved_mwh(n, {"flex"}, sink=sink) == pytest.approx(4.0)


@pytest.mark.live_solve
def test_load_keyed_fallbacks_match_the_bus_rollup_of_a_real_capture():
    """Parity: each fallback rebuilds the capture's own bus roll-up."""
    from services.pypsa_service import PyPSAService
    from services.solver_service import run_simulation

    n = _two_loads_one_bus()
    n.links.at["import_poc", "p_max_pu"] = 0.0
    n.snapshot_weightings.loc[:, :] = 3.0
    PyPSAService.set_network(n)
    sink: dict = {}
    run_simulation(SolverConfig(voll=500.0), n, PyPSAService.get_lock(),
                   threading.Event(), queue.SimpleQueue(),
                   state_update=lambda **kw: sink.update(kw))
    lost = sink["last_lost_load"]
    for buses in ({"crit"}, {"flex"}, {"crit", "flex"}):
        primary = D._bus_unserved_mwh(n, buses, sink=sink)
        by_load = {k: v for k, v in lost.items()
                   if k != "lost_load_bus_period_mwh"}
        by_t = {"lost_load_t": lost["lost_load_t"]}
        assert D._bus_unserved_mwh(
            n, buses, sink={"last_lost_load": by_load}) == pytest.approx(primary)
        assert D._bus_unserved_mwh(
            n, buses, sink={"last_lost_load": by_t}) == pytest.approx(primary)
    assert D._bus_unserved_mwh(n, {"crit", "flex"}, sink=sink) > 0


def test_bus_rollup_with_no_matching_bus_is_zero_not_a_fallthrough():
    n = _two_loads_one_bus()
    bp = pd.DataFrame([{"crit": 5.0}], index=["ALL"])
    lost_t = pd.DataFrame({"comfort": [9.0, 0, 0, 0]}, index=n.snapshots)
    sink = {"last_lost_load": {"lost_load_bus_period_mwh": bp,
                               "lost_load_t": lost_t}}
    # flex shed nothing per the authoritative roll-up — must not fall
    # through to a different (weaker) source.
    assert D._bus_unserved_mwh(n, {"flex"}, sink=sink) == 0.0


# ── P10a: DSR preflight (decision 15) ───────────────────────────────────────


def _run_eh(n, pack, cfg, monkeypatch, **kw):
    """Run the EH driver, recording the cfg each LP solve receives and the
    side-results each solve publishes (``captures``)."""
    import services.solver_service as SS
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService

    seen: list = []
    real = SS.run_simulation

    def spy(cfg_i, *a, **k):
        seen.append(cfg_i)
        inner = k.get("state_update")

        def tee(**kw_):
            if "last_lost_load" in kw_:
                _run_eh.captures.append(kw_["last_lost_load"])
            if inner is not None:
                inner(**kw_)
        k["state_update"] = tee
        return real(cfg_i, *a, **k)

    _run_eh.captures = []

    monkeypatch.setattr(SS, "run_simulation", spy)
    PyPSAService.set_network(n)
    report = S.run_eh_study(
        n, pack, cfg, lock=PyPSAService.get_lock(),
        stop_event=threading.Event(), log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "assemble"), **kw)
    return report, seen


def _weak_pack():
    from models.energy_hub import AvailabilityTarget, default_weak_flexible_pack
    return default_weak_flexible_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=5000.0)})


@pytest.mark.live_solve
def test_weak_pack_with_dsr_buses_enables_the_tier_and_warns_double_count(
        monkeypatch):
    from tests.test_energy_hub_mvp_b import _weak_mvp_b_network
    # `flex` is an endpoint of Link crit_flex → modelled flexibility there.
    report, seen = _run_eh(_weak_mvp_b_network(), _weak_pack(),
                           SolverConfig(voll=500.0), monkeypatch,
                           dsr_buses=["flex"])
    assert seen and seen[0].dsr_buses == ["flex"]
    assert seen[0].dsr_price_eur_per_mwh > 0 and seen[0].dsr_share_of_load > 0
    # The tier was actually built and dispatched in the ENS solve.
    cap = _run_eh.captures[0]
    assert cap.get("dsr_total_mwh", 0.0) > 0
    assert "flex" in cap["dsr_t"].columns
    assert any("demand response" in w for w in report.notes)
    apply = next(r for r in report.pipeline.stages if r.stage == "apply_pack")
    assert "demand response" in (apply.note or "")


@pytest.mark.live_solve
def test_weak_pack_without_dsr_buses_keeps_dsr_off_and_says_so(monkeypatch):
    from tests.test_energy_hub_mvp_b import _weak_mvp_b_network
    report, seen = _run_eh(_weak_mvp_b_network(), _weak_pack(),
                           SolverConfig(voll=500.0), monkeypatch)
    assert seen and not seen[0].dsr_buses
    assert any("DSR stays OFF" in w for w in report.notes)


@pytest.mark.live_solve
def test_strong_pack_ignores_dsr_buses(monkeypatch):
    from models.energy_hub import AvailabilityTarget, default_strong_grid_pack
    from tests.test_energy_hub_study import _ens_bind_network
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=1000.0)})
    report, seen = _run_eh(_ens_bind_network(), pack,
                           SolverConfig(voll=150.0), monkeypatch,
                           dsr_buses=["b"])
    assert seen and not seen[0].dsr_buses
    assert report.notes == []


def test_assumptions_hash_covers_dsr_price_and_share():
    from services.adequacy.eh_study import _assumptions_hash
    base = SolverConfig(voll=150.0, dsr_buses=["b"],
                        dsr_price_eur_per_mwh=100.0, dsr_share_of_load=0.1)
    pricier = SolverConfig(voll=150.0, dsr_buses=["b"],
                           dsr_price_eur_per_mwh=200.0, dsr_share_of_load=0.1)
    bigger = SolverConfig(voll=150.0, dsr_buses=["b"],
                          dsr_price_eur_per_mwh=100.0, dsr_share_of_load=0.2)
    assert len({_assumptions_hash(base), _assumptions_hash(pricier),
                _assumptions_hash(bigger)}) == 3


def test_report_notes_are_exported():
    import json
    from pathlib import Path
    from services.adequacy import eh_report as R
    keys = json.loads((Path(__file__).parent / "fixtures" / "eh_archetypes"
                       / "mvp_a_export_keys.json").read_text())["keys"]
    assert "notes" in keys
    assert "notes" in R.EXPORT_KEYS


# ── P10e: the shared network is copied under its lock ───────────────────────


@pytest.mark.live_solve
def test_eh_study_copies_the_shared_network_under_its_lock(monkeypatch):
    import time

    import pypsa

    from models.energy_hub import AvailabilityTarget, default_strong_grid_pack
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService
    from tests.test_energy_hub_study import _ens_bind_network

    copies = {"n": 0}
    real_copy = pypsa.Network.copy

    def counting_copy(self, *a, **k):
        copies["n"] += 1
        return real_copy(self, *a, **k)

    monkeypatch.setattr(pypsa.Network, "copy", counting_copy)
    n = _ens_bind_network()
    PyPSAService.set_network(n)
    lock = PyPSAService.get_lock()
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=1000.0)})
    out: dict = {}

    def worker():
        out["report"] = S.run_eh_study(
            n, pack, SolverConfig(voll=150.0), lock=lock,
            stop_event=threading.Event(), log_queue=queue.SimpleQueue(),
            stages=("apply_pack", "ens_solve", "assemble"))

    with lock:                      # an editor mid-mutation
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        time.sleep(0.5)
        assert copies["n"] == 0     # blocked, not racing the edit
    t.join(timeout=60)
    assert copies["n"] >= 1
    assert out["report"].completeness["target"] == "ok"



def test_dsr_preflight_reads_the_private_network_not_the_shared_one(
        monkeypatch):
    """P10e: every read of the shared network happens under the lock, on
    the private copy — including the DSR preflight."""
    from models.energy_hub import default_weak_flexible_pack
    from services.adequacy import archetypes as A
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService
    from tests.test_energy_hub_mvp_b import _weak_mvp_b_network

    n = _weak_mvp_b_network()
    seen: list = []
    real = A.solver_config_patch_with_preflight

    def spy(pack, *, network, **k):
        seen.append(network)
        return real(pack, network=network, **k)

    monkeypatch.setattr(A, "solver_config_patch_with_preflight", spy)
    stop = threading.Event()
    stop.set()                       # stop before any solve; preflight ran
    PyPSAService.set_network(n)
    S.run_eh_study(n, default_weak_flexible_pack(), SolverConfig(voll=500.0),
                   lock=PyPSAService.get_lock(), stop_event=stop,
                   log_queue=queue.SimpleQueue(),
                   stages=("apply_pack", "ens_solve", "assemble"))
    assert seen and all(net is not n for net in seen)


# ── P10 gate B1: the DSR tier responds with at most its bus's own load ──────


@pytest.mark.live_solve
def test_dsr_tier_is_bounded_by_its_bus_load_each_snapshot():
    import pypsa

    from services.pypsa_service import PyPSAService
    from services.solver_service import run_simulation

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "gas")
    n.add("Bus", "A", carrier="AC")
    n.add("Bus", "B", carrier="AC")
    n.add("Load", "la", bus="A", p_set=[100.0, 10.0])
    n.add("Load", "lb", bus="B", p_set=40.0)
    n.add("Generator", "ga", bus="A", carrier="gas", p_nom=30.0,
          marginal_cost=10.0)
    n.add("Link", "ab", bus0="A", bus1="B", p_nom=100.0, efficiency=1.0)
    PyPSAService.set_network(n)
    sink: dict = {}
    status, _ = run_simulation(
        SolverConfig(voll=5000.0, dsr_buses=["A"], dsr_price_eur_per_mwh=50.0,
                     dsr_share_of_load=0.5),
        n, PyPSAService.get_lock(), threading.Event(), queue.SimpleQueue(),
        state_update=lambda **kw: sink.update(kw))
    assert status in ("ok", "optimal")
    dsr = sink["last_lost_load"]["dsr_t"]["A"]
    share_of_load = 0.5 * pd.Series([100.0, 10.0], index=n.snapshots)
    assert (dsr <= share_of_load + 1e-6).all(), dsr.tolist()
