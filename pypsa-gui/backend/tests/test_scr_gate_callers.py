"""The EH study and readiness callers of the SCR gate (GK A 30, DC walk 05).

Neither knows a project's power architecture, so neither passes ``behind``:
with one PoC, a derived inverter capacity counts every converter-coupled asset
on the network, StorageUnits included, wherever it sits. A set architecture's
units behind it count here, though the scorecard leaves them out. These pins
state that behaviour, which the release note announces.
"""

from __future__ import annotations

import queue
import threading

import pytest

from tests.eh_stage_fixtures import certifiable_weak_network
from models.energy_hub import default_weak_flexible_pack
from services.adequacy import eh_study as S
from services.adequacy.eh_readiness import eh_readiness
from services.solver_service import SolverConfig


def _network():
    n = certifiable_weak_network()
    n.buses["eh_sk_mva"] = float("nan")
    n.buses["eh_ibr_mva"] = float("nan")
    n.buses.at["grid", "eh_sk_mva"] = 300.0
    # Off the PoC bus, on the critical one: both count (10 + 20 = 30 MVA).
    n.add("Generator", "pv", bus="hub", carrier="solar", p_nom=10.0)
    n.add("StorageUnit", "bess", bus="hub", carrier="battery", p_nom=20.0)
    return n


def test_readiness_derives_the_capacity_from_every_converter_and_storage():
    scr = eh_readiness(_network(), default_weak_flexible_pack(), budget_solves=10)[
        "scr"
    ]
    assert scr["status"] == "ok" and scr["min_scr"] == pytest.approx(300.0 / 30.0)


def test_the_study_gate_derives_the_capacity_from_every_converter_and_storage():
    from services.pypsa_service import PyPSAService

    n = _network()
    PyPSAService.set_network(n)
    report = S.run_eh_study(
        n,
        default_weak_flexible_pack(),
        SolverConfig(voll=5000.0),
        lock=PyPSAService.get_lock(),
        log_queue=queue.SimpleQueue(),
        stages=["apply_pack", "ens_solve"],
        stop_event=threading.Event(),
    )
    gates = report.sections["gates"]
    assert gates.status == "ok"
    (row,) = gates.payload["buses"]
    assert row["ibr_source"] == "derived" and row["ibr_assets"] == {
        "pv": 10.0,
        "bess": 20.0,
    }
    assert gates.payload["min_scr"] == pytest.approx(300.0 / 30.0)


# GK A 31 ask 1: a cap never certifies a Pass, at the gate itself, so every
# caller withholds it, not only the scorecard.


def _with_unsized(n):
    n.add(
        "StorageUnit", "bess_new", bus="hub", carrier="battery", p_nom_extendable=True
    )
    return n


def test_readiness_withholds_the_pass_with_an_unsized_converter():
    scr = eh_readiness(
        _with_unsized(_network()), default_weak_flexible_pack(), budget_solves=10
    )["scr"]
    assert scr["status"] == "not_established"
    # UX 2: the note names the asset as a person reads it, in plain words.
    assert "BESS new is not sized yet" in scr["note"] and "bess_new" not in scr["note"]


def test_the_study_gate_withholds_the_pass_with_an_unsized_converter():
    from services.pypsa_service import PyPSAService

    n = _with_unsized(_network())
    PyPSAService.set_network(n)
    report = S.run_eh_study(
        n,
        default_weak_flexible_pack(),
        SolverConfig(voll=5000.0),
        lock=PyPSAService.get_lock(),
        log_queue=queue.SimpleQueue(),
        stages=["apply_pack", "ens_solve"],
        stop_event=threading.Event(),
    )
    gates = report.sections["gates"]
    assert gates.status == "not_established"
    assert gates.payload["capped_pocs"] == ["grid"]
    assert gates.payload["min_scr"] == pytest.approx(300.0 / 30.0)  # still shown


def test_the_dc_template_does_not_pass_the_gate_before_bess_new_is_sized():
    import pathlib
    import sys

    sys.path.insert(
        0, str(pathlib.Path(__file__).resolve().parents[1] / "project_templates")
    )
    import eh_templates as T
    from services.adequacy import scr_gate as G

    n = T.BUILDERS["eh_datacenter"]()
    gate, status, payload, note = G.evaluate_network_scr_gate(n)
    assert gate is None and status == "not_established"
    assert payload["capped_pocs"] == ["grid"]
    assert note.startswith(
        "Grid strength not confirmed: the lowest short-circuit ratio is "
    )
    assert "at Grid, BESS new is not sized yet" in note and "bess_new" not in note
    n.storage_units.at["bess_new", "p_nom_opt"] = 10.0
    assert G.evaluate_network_scr_gate(n)[1] == "ok"


def test_a_stated_capacity_below_its_converters_on_a_second_poc_withholds_the_pass():
    from services.adequacy import scr_gate as G

    n = _network()
    n.buses.at["grid", "eh_ibr_mva"] = 30.0
    n.add("Bus", "poc2")
    n.buses.at["poc2", "eh_poc"] = True
    n.buses.at["poc2", "eh_sk_mva"] = 400.0
    n.buses.at["poc2", "eh_ibr_mva"] = 10.0  # 100 MVA of converters on its bus
    n.add("Generator", "wind2", bus="poc2", carrier="wind", p_nom=100.0)
    gate, status, payload, note = G.evaluate_network_scr_gate(n)
    assert payload["min_bus"] == "grid" and status == "not_established"
    assert payload["capped_pocs"] == ["poc2"]
    assert (
        "at Poc2, the stated inverter capacity of 10 MVA is below the 100 MVA "
        "of its converters; clear it"
    ) in note
    n.buses.at["poc2", "eh_ibr_mva"] = 100.0
    assert G.evaluate_network_scr_gate(n)[1] == "ok"


def test_a_converter_the_last_solve_sized_at_zero_is_sized_not_unsized():
    # The DC study's ENS solve sizes bess_new at 0: that is a size, and the
    # gate passes. On a network never solved, a 0 p_nom_opt is PyPSA's default
    # and stays an unsized candidate (DC walk 05, GK A 31).
    from services.adequacy import scr_gate as G

    n = _with_unsized(_network())
    n.storage_units.at["bess_new", "p_nom_opt"] = 0.0
    assert G.evaluate_network_scr_gate(n)[1] == "not_established"  # never solved
    _solve_covered(n)
    gate, status, payload, _ = G.evaluate_network_scr_gate(n)
    assert status == "ok" and gate.scr == "pass" and payload["capped_pocs"] == []
    assert payload["buses"][0]["ibr_assets"]["bess_new"] == 0.0


def _solve_covered(n):
    """What optimize() leaves: the objective, and a solved-output column per asset."""
    import pandas as pd

    n._objective = 123.0
    for frame in ("generators", "storage_units"):
        names = getattr(n, frame).index
        getattr(n, f"{frame}_t")["p"] = pd.DataFrame(
            0.0, index=n.snapshots, columns=names
        )


@pytest.mark.parametrize(
    "component, name, shown, kw",
    [
        ("StorageUnit", "bess_added", "BESS added", {"carrier": "battery"}),
        ("Generator", "pv_added", "PV added", {"carrier": "solar"}),
    ],
)
def test_a_converter_added_after_the_solve_is_unsized_not_a_chosen_zero(
    component, name, shown, kw
):
    # GK A 31: PyPSA gives an extendable asset added after a solve p_nom_opt 0.
    # The solve did not cover it (no column in its solved output), so that 0
    # is not a size: the gate withholds the Pass and names the asset.
    from services.adequacy import scr_gate as G

    n = _network()
    _solve_covered(n)
    assert G.evaluate_network_scr_gate(n)[1] == "ok"
    n.add(component, name, bus="grid", p_nom_extendable=True, **kw)
    gate, status, payload, note = G.evaluate_network_scr_gate(n)
    assert gate is None and status == "not_established"
    assert payload["buses"][0]["ibr_unsized"] == [name]
    assert f"at Grid, {shown} is not sized yet" in note
