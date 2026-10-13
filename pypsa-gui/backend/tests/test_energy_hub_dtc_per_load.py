"""
P16 — DtC per-Load attribution under a scoped VOLL premium (decision Q5).

Spec §10 amendment (2026-09-26). At equal VOLL the LP's split of shed between
a critical and a non-critical Load on ONE bus is degenerate, so "the same
result twice" cannot detect an arbitrary split. These tests instead pin the
priority rule: on a shared bus short by X MWh, non-critical Loads are shed
first and critical unserved = max(0, X − non-critical demand).
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pypsa
import pytest

from models.energy_hub import DtcConfig
from services.adequacy import dtc as D
from services.solver_service import SolverConfig

H = 2                  # snapshots, weight 1 → MWh = 2 × MW


def _shared_bus(local_mw: float) -> pypsa.Network:
    """One hub bus with a critical (30 MW) and a non-critical (50 MW) Load,
    `local_mw` of local generation, and an import that islanding removes."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=H, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    for c in ("gas", "AC"):
        n.add("Carrier", c)
    n.add("Bus", "hub", carrier="AC")
    n.add("Bus", "grid", carrier="AC")
    n.add("Load", "hospital", bus="hub", p_set=30.0)
    n.add("Load", "offices", bus="hub", p_set=50.0)
    n.add("Generator", "local", bus="hub", carrier="gas", p_nom=local_mw,
          marginal_cost=80.0)
    n.add("Generator", "remote", bus="grid", carrier="gas", p_nom=500.0,
          marginal_cost=10.0)
    n.add("Link", "import_poc", bus0="grid", bus1="hub", p_nom=200.0,
          efficiency=1.0, carrier="AC")
    return n


def _dtc(**kw) -> DtcConfig:
    return DtcConfig(critical_load_ids=["hospital"],
                     islanding_contingencies=["import_poc"], **kw)


def _stress(n, dtc, voll=3000.0):
    from services.pypsa_service import PyPSAService
    PyPSAService.set_network(n)
    return D.run_dtc_stress(
        n, SolverConfig(voll=voll), lock=PyPSAService.get_lock(),
        stop_event=threading.Event(), log_queue=queue.SimpleQueue(), dtc=dtc)


# ── contract ────────────────────────────────────────────────────────────────


def test_per_load_is_opt_in_and_unknown_modes_are_refused():
    assert _dtc().attribution == "bus_aggregate_not_per_load"
    assert _dtc(attribution="per_load").attribution == "per_load"
    for bad in ("auto", "per_load_shed"):
        with pytest.raises(Exception, match="attribution|per_load"):
            _dtc(attribution=bad)


def test_premium_is_not_a_solver_config_field():
    """R5: a SolverConfig field is settable by every solve request and saved
    project, so the premium must live elsewhere."""
    import dataclasses
    names = {f.name for f in dataclasses.fields(SolverConfig)}
    assert not any("premium" in n for n in names)


def test_premium_scope_is_reset_after_use():
    from services.solver import assumptions as A
    assert A.current_voll_load_premium() == {}
    with A.voll_load_premium({"hospital": 1.05}):
        assert A.current_voll_load_premium() == {"hospital": 1.05}
    assert A.current_voll_load_premium() == {}
    with pytest.raises(ValueError, match=">= 1"):
        with A.voll_load_premium({"hospital": 0.5}):
            pass


def test_per_load_refuses_a_capture_without_load_keys():
    n = _shared_bus(10.0)
    by_bus = pd.DataFrame({"hub": [140.0]}, index=[2030])
    sink = {"last_lost_load": {"lost_load_bus_period_mwh": by_bus}}
    with pytest.raises(D.DtcStressError, match="Load-keyed"):
        D._load_unserved_mwh(n, {"hospital"}, sink=sink)


def test_critical_loads_under_per_load():
    n = _shared_bus(10.0)
    n.add("Bus", "b2", carrier="AC")
    n.add("Load", "pump", bus="b2", p_set=5.0)
    n.buses["eh_critical"] = False
    n.buses.at["b2", "eh_critical"] = True
    assert D._critical_loads(n, _dtc(attribution="per_load")) == {"hospital", "pump"}


# ── the priority rule, solved ───────────────────────────────────────────────


@pytest.mark.live_solve
@pytest.mark.parametrize("local_mw,crit_mwh,noncrit_mwh", [
    (40.0, 0.0, 40.0 * H),         # short 40 < offices 50 → hospital served
    (10.0, 20.0 * H, 50.0 * H),    # short 70 → offices fully shed, hospital 20
])
def test_per_load_sheds_noncritical_first_on_a_shared_bus(
        local_mw, crit_mwh, noncrit_mwh):
    table = _stress(_shared_bus(local_mw), _dtc(attribution="per_load"))
    assert table["attribution"] == "per_load"
    assert "per_load_by_voll_priority" in table["honesty_notes"]
    assert "no_per_load_attribution" not in table["honesty_notes"]
    assert table["voll_premium_eps"] == pytest.approx(D.CRITICAL_VOLL_PREMIUM_EPS)
    row = table["contingencies"][0]
    assert row["status"] in ("ok", "optimal"), row
    assert row["critical_unserved_mwh"] == pytest.approx(crit_mwh, abs=1e-4)
    assert row["noncritical_unserved_mwh"] == pytest.approx(noncrit_mwh, abs=1e-4)
    assert row["critical_unserved_by_load"] == pytest.approx(
        {"hospital": crit_mwh}, abs=1e-4)
    assert row["critical_loads"] == ["hospital"]
    assert row["noncritical_loads"] == ["offices"]


@pytest.mark.live_solve
def test_bus_aggregate_default_is_unchanged():
    """A critical Load promotes its whole bus; offices count as critical."""
    table = _stress(_shared_bus(10.0), _dtc())
    assert table["attribution"] == "bus_aggregate_not_per_load"
    assert "no_per_load_attribution" in table["honesty_notes"]
    row = table["contingencies"][0]
    assert row["critical_unserved_mwh"] == pytest.approx(70.0 * H, abs=1e-4)
    assert row["noncritical_unserved_mwh"] == pytest.approx(0.0, abs=1e-6)
    assert "critical_unserved_by_load" not in row


@pytest.mark.live_solve
def test_premium_does_not_leak_into_a_later_solve():
    """After a per_load stress, an ordinary solve prices every slack at the
    base VOLL (the premium lived only inside the DtC re-dispatch)."""
    from services.pypsa_service import PyPSAService
    from services.solver import assumptions as A
    from services.solver_service import run_simulation

    n = _shared_bus(10.0)
    _stress(n, _dtc(attribution="per_load"))
    assert A.current_voll_load_premium() == {}
    seen: list = []
    real_add = pypsa.Network.add

    def spy(self, cls, name, *a, **kw):
        if cls == "Generator" and "marginal_cost" in kw:
            seen.append(kw["marginal_cost"])
        return real_add(self, cls, name, *a, **kw)

    n2 = _shared_bus(10.0)
    n2.links.at["import_poc", "p_max_pu"] = 0.0
    PyPSAService.set_network(n2)
    pypsa.Network.add = spy
    try:
        run_simulation(SolverConfig(voll=3000.0), n2, PyPSAService.get_lock(),
                       threading.Event(), queue.SimpleQueue())
    finally:
        pypsa.Network.add = real_add
    flat = [float(x) for v in seen for x in (v if isinstance(v, list) else [v])]
    assert flat and all(x == pytest.approx(3000.0) for x in flat)


# ── planning: retained demand by Load ───────────────────────────────────────


def test_retained_demand_by_load_zeroes_noncritical_on_a_critical_bus():
    n = _shared_bus(10.0)
    undo, mut = D.apply_retained_critical_demand(n, _dtc(attribution="per_load"))
    assert float(n.loads.at["hospital", "p_set"]) == 30.0
    assert float(n.loads.at["offices", "p_set"]) == 0.0
    assert mut["zeroed_load_ids"] == ["offices"]
    assert mut["critical_loads"] == ["hospital"]
    undo()
    assert float(n.loads.at["offices", "p_set"]) == 50.0


def test_retained_demand_bus_aggregate_keeps_the_whole_bus():
    n = _shared_bus(10.0)
    undo, mut = D.apply_retained_critical_demand(n, _dtc())
    assert float(n.loads.at["offices", "p_set"]) == 50.0
    assert mut["zeroed_load_ids"] == []
    undo()


@pytest.mark.live_solve
def test_planning_accepts_per_load_and_reports_it():
    from services.pypsa_service import PyPSAService
    n = _shared_bus(10.0)
    n.add("Generator", "peaker", bus="hub", carrier="gas", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=200.0, capital_cost=100.0,
              marginal_cost=90.0)
    PyPSAService.set_network(n)
    table = D.run_dtc_planning(
        n, SolverConfig(voll=3000.0, ens_cap_permyriad=100.0),
        lock=PyPSAService.get_lock(), stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(), dtc=_dtc(attribution="per_load"))
    assert table["attribution"] == "per_load"
    assert "per_load_by_voll_priority" not in table["honesty_notes"]
    assert "retained_critical_demand" in table["honesty_notes"]
    row = table["contingencies"][0]
    assert row["status"] in ("ok", "optimal"), row
    assert row["retained_critical"]["zeroed_load_ids"] == ["offices"]
    # only the 30 MW hospital is retained: 10 local + ~20 built (ENS-capped)
    assert 15.0 < row["built_p_nom_mw"] <= 30.0 + 1e-6


# ── the study: per_load never changes ens_solve ─────────────────────────────


@pytest.mark.live_solve
def test_ens_solve_cost_is_unchanged_by_a_per_load_dtc_stage():
    from models.energy_hub import AvailabilityTarget, default_weak_flexible_pack
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig as SC

    def run(attribution):
        n = _shared_bus(10.0)
        n.add("Generator", "peaker", bus="hub", carrier="gas", p_nom=0.0,
              p_nom_extendable=True, p_nom_max=200.0, capital_cost=100.0,
              marginal_cost=90.0)
        n.links["eh_role"] = ""
        n.links.at["import_poc", "eh_role"] = "grid_import"
        PyPSAService.set_network(n)
        pack = default_weak_flexible_pack().model_copy(update={
            "availability": AvailabilityTarget(ens_cap_permyriad=100.0)})
        return S.run_eh_study(
            n, pack, SC(voll=3000.0), lock=PyPSAService.get_lock(),
            stop_event=threading.Event(), log_queue=queue.SimpleQueue(),
            stages=["apply_pack", "ens_solve", "dtc_stress"],
            dtc_config=_dtc(attribution=attribution))

    base, per_load = run("bus_aggregate_not_per_load"), run("per_load")
    assert per_load.sections["cost"].payload == base.sections["cost"].payload
    stages = {r.stage: r.status for r in per_load.pipeline.stages}
    assert stages["dtc_stress"] == "run"
    assert per_load.sections["dtc"].payload["attribution"] == "per_load"


def test_the_runner_accepts_per_load():
    from services.adequacy.eh_study_runner import _validate_dtc_config
    n = _shared_bus(10.0)
    dtc = _validate_dtc_config({"critical_load_ids": ["hospital"],
                                "islanding_contingencies": ["import_poc"],
                                "attribution": "per_load"}, n)
    assert dtc.attribution == "per_load"


# ── P16 gate: the priority claim is exact only on loss-free paths ───────────


def _lossy(eta: float) -> pypsa.Network:
    """Critical Load on bus A, fed only through a Link (efficiency eta) from
    bus B, which holds the non-critical Load and 40 MW of generation."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=H, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    for c in ("gas", "AC"):
        n.add("Carrier", c)
    for b in ("A", "B", "grid"):
        n.add("Bus", b, carrier="AC")
    n.add("Load", "hospital", bus="A", p_set=30.0)
    n.add("Load", "offices", bus="B", p_set=50.0)
    n.add("Generator", "local", bus="B", carrier="gas", p_nom=40.0,
          marginal_cost=80.0)
    n.add("Generator", "remote", bus="grid", carrier="gas", p_nom=500.0,
          marginal_cost=10.0)
    n.add("Link", "feeder", bus0="B", bus1="A", p_nom=100.0, efficiency=eta,
          carrier="AC")
    n.add("Link", "import_poc", bus0="grid", bus1="B", p_nom=200.0,
          efficiency=1.0, carrier="AC")
    return n


@pytest.mark.live_solve
def test_a_lossy_path_can_invert_the_priority_and_is_flagged():
    """V/eta > (1+eps)·V once eta < 1/(1+eps): serving the critical Load
    costs more than shedding it. The table must say so, not claim priority."""
    table = _stress(_lossy(0.9), _dtc(attribution="per_load"))
    row = table["contingencies"][0]
    assert row["critical_unserved_mwh"] > 0            # the inversion is real
    assert table["priority_exact"] is False
    assert table["priority_caveat_links"] == ["feeder"]
    assert "priority_may_invert_on_lossy_paths" in table["honesty_notes"]


@pytest.mark.live_solve
def test_a_loss_free_network_is_flagged_exact():
    lossless = _stress(_lossy(1.0), _dtc(attribution="per_load"))
    assert lossless["priority_exact"] is True
    assert lossless["priority_caveat_links"] == []
    assert lossless["contingencies"][0]["critical_unserved_mwh"] == \
        pytest.approx(0.0, abs=1e-6)
    shared = _stress(_shared_bus(10.0), _dtc(attribution="per_load"))
    assert shared["priority_exact"] is True
    assert "priority_may_invert_on_lossy_paths" not in shared["honesty_notes"]


def test_line_losses_make_the_priority_inexact():
    n = _shared_bus(10.0)
    assert D._priority_caveats(n, SolverConfig(transmission_losses=True),
                               exclude=set()) == (False, [], True)
    assert D._priority_caveats(n, SolverConfig(), exclude=set()) == (True, [], False)


@pytest.mark.live_solve
def test_a_refusal_keeps_the_rows_and_solves_already_spent(monkeypatch):
    def refuse(*a, **kw):
        raise D.DtcStressError("per_load attribution needs Load-keyed shed data")

    monkeypatch.setattr(D, "_load_unserved_by_load", refuse)
    table = _stress(_shared_bus(10.0), _dtc(attribution="per_load"))
    assert table["solves_attempted"] == 1
    assert table["refused"].startswith("per_load attribution needs Load-keyed")
    assert table["contingencies"][0]["status"] == "refused"
    status, note = D.dtc_section_status(table)
    assert status == "not_established" and "Load-keyed" in note
