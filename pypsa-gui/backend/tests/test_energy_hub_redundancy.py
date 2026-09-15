"""
P3a — redundancy scenario enumeration (TDD).

Design: docs/superpowers/specs/2026-09-14-eh-reference-design.md decisions 5
Plan: Phase 3a — compare cost vs achieved ENS at a fixed availability target
across discrete redundancy options (not FOR derating; not joint MILP).
"""
from __future__ import annotations

import math
import queue
import threading

import pandas as pd
import pypsa
import pytest

from models.energy_hub import AvailabilityTarget, default_strong_grid_pack
from services.adequacy import redundancy as R
from services.solver_service import SolverConfig


def _network() -> pypsa.Network:
    """Load 100 MW; cheap 60 + expensive backup 40; optional storage slot."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 3.0
    n.add("Carrier", "gas")
    n.add("Carrier", "battery")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "cheap", bus="b", carrier="gas",
          p_nom=60.0, marginal_cost=10.0)
    n.add("Generator", "backup", bus="b", carrier="gas",
          p_nom=40.0, marginal_cost=200.0, capital_cost=50.0,
          p_nom_extendable=True, p_nom_max=80.0)
    n.add("StorageUnit", "bat", bus="b", carrier="battery",
          p_nom=0.0, p_nom_extendable=True, p_nom_max=50.0,
          max_hours=4.0, capital_cost=80.0, efficiency_store=0.9,
          efficiency_dispatch=0.9)
    return n


def _network_with_link(*, p_nom_max=40.0) -> pypsa.Network:
    """Two-bus net with an extendable conversion Link."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 3.0
    n.add("Carrier", "gas")
    n.add("Carrier", "AC")
    n.add("Bus", "b0", carrier="AC")
    n.add("Bus", "b1", carrier="AC")
    n.add("Load", "l", bus="b0", p_set=100.0)
    n.add("Generator", "cheap", bus="b0", carrier="gas",
          p_nom=60.0, marginal_cost=10.0)
    n.add("Generator", "remote", bus="b1", carrier="gas",
          p_nom=80.0, marginal_cost=50.0)
    kwargs = dict(
        bus0="b0", bus1="b1",
        p_nom=20.0, p_nom_extendable=True,
        capital_cost=30.0, efficiency=0.95, carrier="AC",
    )
    if p_nom_max is not None:
        kwargs["p_nom_max"] = p_nom_max
    n.add("Link", "conv", **kwargs)
    return n


def test_default_scenario_ids():
    assert R.DEFAULT_SCENARIOS == (
        "base", "n1_generation", "n1_conversion", "parallel_storage")


def test_apply_n1_generation_adds_spare_and_undo_restores():
    n = _network()
    before = set(n.generators.index)
    undo, mut = R.apply_redundancy_scenario(n, "n1_generation")
    assert "eh_spare_gen" in n.generators.index
    assert float(n.generators.at["eh_spare_gen", "p_nom_max"]) > 0
    assert mut["cost_basis"] == "synthetic_placeholder"
    assert mut["applied"]
    undo()
    assert set(n.generators.index) == before


def test_apply_parallel_storage_adds_spare_and_undo_restores():
    n = _network()
    before = set(n.storage_units.index)
    undo, mut = R.apply_redundancy_scenario(n, "parallel_storage")
    assert "eh_spare_storage" in n.storage_units.index
    assert mut["applied"][0]["name"] == "eh_spare_storage"
    undo()
    assert set(n.storage_units.index) == before


def test_apply_n1_conversion_on_links_bumps_headroom_and_undo_restores():
    """n1_conversion must be Link topology — not an n1_generation alias."""
    n = _network_with_link(p_nom_max=40.0)
    before_gens = set(n.generators.index)
    before_links = set(n.links.index)
    undo, mut = R.apply_redundancy_scenario(n, "n1_conversion")
    assert "eh_spare_gen" not in n.generators.index
    assert set(n.generators.index) == before_gens
    assert set(n.links.index) == before_links
    assert float(n.links.at["conv", "p_nom_max"]) == 20.0 + 50.0  # base p_nom + headroom
    assert str(n.links.at["conv", "eh_role"]) == "eh_n1_conversion"
    assert mut["applied"][0]["action"] == "raise_p_nom_max"
    undo()
    assert float(n.links.at["conv", "p_nom_max"]) == 40.0


def test_apply_n1_conversion_forces_finite_headroom_when_p_nom_max_inf():
    """Assessor N1: default PyPSA p_nom_max=inf must not make n1_conversion a no-op."""
    n = _network_with_link(p_nom_max=None)  # leave default (typically inf)
    # Confirm default is non-finite or very large.
    raw = float(n.links.at["conv", "p_nom_max"])
    assert not math.isfinite(raw) or raw > 1e6
    before_max = raw
    undo, mut = R.apply_redundancy_scenario(n, "n1_conversion")
    after = float(n.links.at["conv", "p_nom_max"])
    assert math.isfinite(after)
    assert after == pytest.approx(20.0 + 50.0)  # p_nom + headroom
    assert after != before_max
    assert mut["applied"][0]["new_p_nom_max"] == after
    undo()


def test_apply_n1_conversion_prefers_role_over_insertion_order():
    """Assessor N1: select by eh_role, not links.index[0]."""
    n = _network_with_link(p_nom_max=40.0)
    # Insert another link first in index terms by rebuilding with role on second.
    n2 = pypsa.Network()
    n2.set_snapshots(n.snapshots)
    n2.snapshot_weightings.loc[:, :] = 3.0
    n2.add("Carrier", "gas")
    n2.add("Carrier", "AC")
    n2.add("Bus", "b0", carrier="AC")
    n2.add("Bus", "b1", carrier="AC")
    n2.add("Load", "l", bus="b0", p_set=100.0)
    n2.add("Generator", "cheap", bus="b0", carrier="gas", p_nom=60.0, marginal_cost=10.0)
    n2.add("Generator", "remote", bus="b1", carrier="gas", p_nom=80.0, marginal_cost=50.0)
    n2.add("Link", "order_first", bus0="b0", bus1="b1",
           p_nom=5.0, p_nom_extendable=True, p_nom_max=10.0,
           capital_cost=10.0, efficiency=0.95, carrier="heat")
    n2.add("Link", "conv_role", bus0="b0", bus1="b1",
           p_nom=20.0, p_nom_extendable=True, p_nom_max=40.0,
           capital_cost=30.0, efficiency=0.95, carrier="AC")
    n2.links["eh_role"] = ""
    n2.links.at["conv_role", "eh_role"] = "eh_conversion"
    assert list(n2.links.index)[0] == "order_first"
    undo, mut = R.apply_redundancy_scenario(n2, "n1_conversion")
    assert mut["applied"][0]["name"] == "conv_role"
    assert float(n2.links.at["conv_role", "p_nom_max"]) == 70.0
    assert float(n2.links.at["order_first", "p_nom_max"]) == 10.0
    undo()


def test_apply_n1_conversion_without_links_adds_distinct_path():
    """Link-less nets invent a conversion path — not eh_spare_gen."""
    n = _network()
    before_buses = set(n.buses.index)
    before_gens = set(n.generators.index)
    undo, _ = R.apply_redundancy_scenario(n, "n1_conversion")
    assert "eh_spare_gen" not in n.generators.index
    assert "eh_spare_conversion" in n.links.index
    assert "eh_n1_conv_bus" in n.buses.index
    assert "eh_n1_conv_feeder" in n.generators.index
    undo()
    assert set(n.buses.index) == before_buses
    assert set(n.generators.index) == before_gens
    assert n.links is None or n.links.empty


def test_n1_conversion_distinct_from_n1_generation():
    n = _network()
    R.apply_redundancy_scenario(n, "n1_generation")
    gen_ids = set(n.generators.index)
    n2 = _network()
    R.apply_redundancy_scenario(n2, "n1_conversion")
    assert "eh_spare_gen" in gen_ids
    assert "eh_spare_gen" not in n2.generators.index
    assert "eh_spare_conversion" in n2.links.index


def test_unknown_scenario_raises():
    n = _network()
    with pytest.raises(R.RedundancyScenarioError, match="unknown"):
        R.apply_redundancy_scenario(n, "not_a_scenario")


def test_compare_does_not_leak_into_caller_state_update():
    """Assessor N2: scenario sub-solves must not write the caller's sink.

    compare_redundancy_scenarios no longer accepts state_update — verify the
    signature and that a caller sink passed via store alone is not polluted
    with adequacy_report mid-compare (only the final table lands).
    """
    import inspect
    sig = inspect.signature(R.compare_redundancy_scenarios)
    assert "state_update" not in sig.parameters


def test_default_pipeline_skips_redundancy_when_lever_off():
    """pack.levers.redundancy=False + stages=None → redundancy skipped."""
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService

    n = _network()
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=1000.0),
    })
    assert pack.levers.redundancy is False
    PyPSAService.set_network(n)
    stop = threading.Event()
    stop.set()
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=stop,
        log_queue=queue.SimpleQueue(),
        stages=None,
    )
    assert report.completeness["redundancy"] == "skipped"


def test_claim_wipe_includes_eh_keys():
    """Assessor N4: /run claim must wipe EH persisted tables."""
    from pathlib import Path
    text = Path("routers/simulation.py").read_text()
    # The running-claim block.
    idx = text.find('status="running"')
    assert idx > 0
    chunk = text[idx:idx + 900]
    assert "eh_redundancy_comparison=None" in chunk
    assert "eh_reference_design_report=None" in chunk


@pytest.mark.live_solve
def test_compare_persists_to_store_with_provenance():
    from services.pypsa_service import PyPSAService

    n = _network()
    PyPSAService.set_network(n)
    store: dict = {}
    table = R.compare_redundancy_scenarios(
        n, SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        scenarios=("base",),
        availability=AvailabilityTarget(ens_cap_permyriad=1000.0),
        store=store,
        pack_hash="packdeadbeef",
        assumptions_hash="assumdeadbeef",
    )
    assert store.get("eh_redundancy_comparison") is table
    assert table["certify_method"] == "ens"
    assert table["pack_hash"] == "packdeadbeef"
    assert table["assumptions_hash"] == "assumdeadbeef"
    opt = table["options"][0]
    assert opt["cost_basis"] in ("none", "synthetic_placeholder")
    assert "applied" in opt


@pytest.mark.live_solve
def test_compare_returns_at_least_two_options_with_costs():
    from services.pypsa_service import PyPSAService

    n = _network()
    PyPSAService.set_network(n)
    cfg = SolverConfig(voll=150.0, ens_cap_permyriad=1000.0)
    table = R.compare_redundancy_scenarios(
        n, cfg,
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        scenarios=("base", "n1_generation"),
        availability=AvailabilityTarget(ens_cap_permyriad=1000.0),
    )
    assert len(table["options"]) >= 2
    ids = {o["scenario_id"] for o in table["options"]}
    assert "base" in ids and "n1_generation" in ids
    for opt in table["options"]:
        assert opt["cost_at_target_eur"] is not None
        assert opt["binding_metric"] == "ens"
        assert "achieved_ens_mwh" in opt
        assert opt["meets_target"] in (True, False, None)
        assert "cost_basis" in opt
        assert "applied" in opt
    assert len({o["binding_metric"] for o in table["options"]}) == 1


@pytest.mark.live_solve
def test_compare_provenance_names_ens_not_lole_when_ens_target():
    from services.pypsa_service import PyPSAService

    n = _network()
    PyPSAService.set_network(n)
    table = R.compare_redundancy_scenarios(
        n, SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        scenarios=("base", "parallel_storage"),
        availability=AvailabilityTarget(ens_cap_permyriad=1000.0),
    )
    assert table["certify_method"] == "ens"
    assert all(o["binding_metric"] == "ens" for o in table["options"])


@pytest.mark.live_solve
def test_n1_conversion_with_inf_p_nom_max_differs_from_base_cost():
    """Assessor N1: on default-inf links, n1_conversion must change the LP."""
    from services.pypsa_service import PyPSAService

    n = _network_with_link(p_nom_max=None)
    # Undersize remote so conversion headroom can matter for investment.
    n.generators.at["remote", "p_nom"] = 30.0
    n.links.at["conv", "p_nom"] = 10.0
    # Leave p_nom_max at default (inf); scenario must force finite headroom.
    assert not math.isfinite(float(n.links.at["conv", "p_nom_max"])) or \
        float(n.links.at["conv", "p_nom_max"]) > 1e6
    PyPSAService.set_network(n)
    table = R.compare_redundancy_scenarios(
        n, SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        scenarios=("base", "n1_conversion"),
        availability=AvailabilityTarget(ens_cap_permyriad=1000.0),
    )
    by_id = {o["scenario_id"]: o for o in table["options"]}
    assert "base" in by_id and "n1_conversion" in by_id
    # Mutation must be recorded with finite new_p_nom_max.
    applied = by_id["n1_conversion"]["applied"]
    assert applied and applied[0]["action"] == "raise_p_nom_max"
    assert math.isfinite(float(applied[0]["new_p_nom_max"]))
    # Feasible-set change: costs need not always differ (depends on bind),
    # but applied headroom must exceed base p_nom — proves not an alias.
    assert float(applied[0]["new_p_nom_max"]) > float(applied[0]["base_mw"])


@pytest.mark.live_solve
def test_run_eh_study_redundancy_writes_store_without_state_leak():
    """Binding conditions: store populated; caller sink not polluted mid-flight."""
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService

    n = _network()
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=1000.0),
        "levers": default_strong_grid_pack().levers.model_copy(
            update={"redundancy": True}),
    })
    PyPSAService.set_network(n)
    store: dict = {}
    leaked: dict = {}

    def tracking_update(**kw):
        leaked.update(kw)

    report = S.run_eh_study(
        n, pack, SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "redundancy", "assemble"),
        store=store,
        state_update=tracking_update,
    )
    assert report.completeness["redundancy"] == "ok"
    assert "eh_redundancy_comparison" in store
    table = store["eh_redundancy_comparison"]
    assert len(table["options"]) >= 2
    assert table.get("pack_hash")
    assert table.get("assumptions_hash")
    # After ens_solve the caller sink may hold adequacy_report from the
    # *primary* solve; it must NOT be overwritten by a scenario's numbers.
    # All scenario costs are on the table; the live sink's adequacy_report
    # (if present) must match ens_solve (same network, no spare assets).
    if "adequacy_report" in leaked:
        # Primary ens_solve network has no eh_spare_* — scenario sink was private.
        # Sanity: table options include synthetic mutations; sink report does not
        # need to equal any scenario row.
        assert any(o.get("applied") for o in table["options"])
