"""Increment-2 Task 3: the RES-augmented fixture `case39_res`.

Siting and capacity are LEDGER ASSUMPTIONS, not measured data — they are
recorded in `RES_LEDGER` so a reader can see what was invented and what came
from the IEEE 39-bus case. The tests below pin the ledger, the registry
extension it drives, and the fact that the vanilla `load_case39()` path is
untouched by any of it.
"""
import pandas as pd
import pandapower as pp
import pytest

from gridspine.ingest.pandapower_source import (
    RES_LEDGER,
    load_case39,
    load_case39_res,
    registry_from_net,
)
from gridspine.schema.contracts import ContractError

EXPECTED = [
    ("W_BUS_33", "BUS_33", 600.0, "wind"),
    ("W_BUS_35", "BUS_35", 600.0, "wind"),
    ("W_BUS_37", "BUS_37", 600.0, "wind"),
    ("S_BUS_34", "BUS_34", 500.0, "solar"),
    ("S_BUS_36", "BUS_36", 500.0, "solar"),
]


def test_ledger_records_the_assumed_sites_and_sizes():
    assert [(e["name"], e["bus"], e["p_mw"], e["tech"]) for e in RES_LEDGER] == EXPECTED


def test_case39_res_adds_five_sgens_at_the_ledger_sites():
    net = load_case39_res()
    bus_name = net.bus["name"]
    got = [
        (r["name"], bus_name.at[r["bus"]], float(r["p_mw"]))
        for _, r in net.sgen.iterrows()
    ]
    assert got == [(n, b, mw) for n, b, mw, _ in EXPECTED]
    assert (net.sgen["q_mvar"] == 0.0).all()
    assert net.sgen["in_service"].all()


def test_case39_res_leaves_the_conventional_fleet_alone():
    net = load_case39_res()
    assert len(net.bus) == 39
    assert (len(net.gen) + len(net.ext_grid)) == 10
    assert list(net.bus["name"])[:2] == ["BUS_01", "BUS_02"]


def test_res_registry_has_fifteen_rows_with_res_kind():
    reg = registry_from_net(load_case39_res())
    assert len(reg) == 15
    assert reg["kind"].value_counts().to_dict() == {"gen": 9, "ext_grid": 1, "res": 5}
    assert list(reg.index[-5:]) == [n for n, _, _, _ in EXPECTED]
    assert reg.loc["W_BUS_33", "bus"] == "BUS_33"
    assert set(reg["bus"]).issubset(set(load_case39_res().bus["name"]))


def test_vanilla_registry_is_unchanged_by_the_res_extension():
    """Increment-1 regression guard. `registry_from_net` grew an sgen branch;
    callers passing a vanilla case39 must see byte-identical output — same
    10 rows, same column set, no 'res' kind. Asserted as whole-frame equality
    against a direct `unit_registry` call, so a dtype or index-name drift in the
    new sgen path fails here rather than downstream."""
    from gridspine.schema.network import unit_registry

    net = load_case39()
    bus_name = net.bus["name"]
    reference = unit_registry(
        gen_names=net.gen["name"],
        gen_buses=[bus_name.at[b] for b in net.gen["bus"]],
        ext_names=net.ext_grid["name"],
        ext_buses=[bus_name.at[b] for b in net.ext_grid["bus"]],
    )
    reg = registry_from_net(net)
    pd.testing.assert_frame_equal(reg, reference)
    assert len(reg) == 10
    assert "res" not in set(reg["kind"])
    assert reg["kind"].value_counts().to_dict() == {"gen": 9, "ext_grid": 1}
    assert list(reg.columns) == ["bus", "kind"]
    assert reg.index.name == "unit_id"


def test_case39_res_load_flow_converges_at_derated_output():
    """The ledger's `p_mw` is INSTALLED capacity. Dispatching all 2800 MW of it
    against a case39 whose conventional units are already fixed at their
    original setpoints leaves the single ext_grid slack absorbing the surplus,
    which is not a state the case was tuned for. The smoke therefore derates to
    0.3x installed — a plausible simultaneous RES capacity factor — so the test
    answers "is the augmented net electrically well-formed" rather than "does
    an arbitrary over-injection happen to solve"."""
    net = load_case39_res()
    net.sgen["scaling"] = 0.3
    pp.runpp(net)
    assert net.converged


def test_load_case39_res_actually_validates(monkeypatch):
    """Same wiring proof as increment-1's `test_load_case39_actually_validates`:
    shrinking the cap makes every canonical name illegal, so reaching the end
    without raising proves the validate_canonical call is missing."""
    import gridspine.schema.network as netmod

    monkeypatch.setattr(netmod, "MAX_NAME_LEN", 5)
    with pytest.raises(ContractError):
        load_case39_res()


def test_res_names_reach_the_canonical_validator(monkeypatch):
    """Coverage proof for the sgen half of the validate_canonical call. Every
    RES name is 8 chars, so a length cap cannot discriminate them from the
    increment-1 names — instead plant an illegal name in the ledger and require
    the load to fail. A validate_canonical call that omits net.sgen["name"]
    lets this through."""
    import gridspine.ingest.pandapower_source as src

    bad = tuple(
        {**e, "name": "W BUS 33"} if e["name"] == "W_BUS_33" else e for e in RES_LEDGER
    )
    monkeypatch.setattr(src, "RES_LEDGER", bad)
    with pytest.raises(ContractError, match="characters outside"):
        src.load_case39_res()
