"""homer-parity 03a: the best system per supply architecture (pure core).

`services/study/supply_architectures.py` groups a network's assets, builds
one network per architecture by OMITTING the other groups (never zeroing
them), and tabulates each architecture's solved best system; one that does
not solve keeps its row. Gate letters are GK B's plan conditions (G1-G12).
"""

from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from services.study import supply_architectures as A

# Buses stay (an emptied bus is harmless); every other component is checked.
ALL = ("Generator", "StorageUnit", "Store", "Link", "Load", "Line")


def _net() -> pypsa.Network:
    """A site with PV, a battery, a diesel genset and a grid supply."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=4, freq="h"))
    for c in ("AC", "solar", "battery", "diesel", "grid", "grid_import"):
        n.add("Carrier", c)
    n.add("Bus", "site", carrier="AC")
    n.add("Bus", "grid", carrier="AC")
    n.add("Load", "site_load", bus="site", p_set=1.0)
    n.add(
        "Generator",
        "pv",
        bus="site",
        carrier="solar",
        p_nom_extendable=True,
        overnight_cost=800_000.0,
    )
    n.add(
        "StorageUnit",
        "battery",
        bus="site",
        carrier="battery",
        p_nom_extendable=True,
        max_hours=2,
        overnight_cost=500_000.0,
    )
    n.add(
        "Generator",
        "genset",
        bus="site",
        carrier="diesel",
        p_nom_extendable=True,
        marginal_cost=250.0,
        overnight_cost=300_000.0,
    )
    n.add("Generator", "grid", bus="grid", carrier="grid", p_nom=5.0)
    n.add(
        "Link",
        "grid_import",
        bus0="grid",
        bus1="site",
        carrier="grid_import",
        p_nom=5.0,
    )
    return n


def _arch(n, aid: str) -> A.SupplyArchitecture:
    return next(a for a in A.architectures_for(n) if a.architecture_id == aid)


def _names(n) -> set[str]:
    return {str(x) for c in ALL for x in n.components[c].static.index}


# ── classify / architectures_for ──────────────────────────────────────────


def test_classify_groups_assets_by_role():
    assert A.classify(_net()) == {
        "pv": [("Generator", "pv")],
        "battery": [("StorageUnit", "battery")],
        "genset": [("Generator", "genset")],
        "grid": [("Generator", "grid"), ("Link", "grid_import")],
    }


def test_architectures_cover_only_present_load_serving_sets():
    archs = A.architectures_for(_net())
    ids = [a.architecture_id for a in archs]
    assert len(ids) == len(set(ids)) == 14  # 15 combinations less the battery alone
    assert "battery" not in ids
    assert not any("wind" in a.groups for a in archs)
    for expected in (
        "grid",
        "genset",
        "pv",
        "pv+battery",
        "pv+genset",
        "pv+battery+genset",
        "pv+battery+genset+grid",
    ):
        assert expected in ids
    assert ids[0] == "pv" and ids[-1] == "pv+battery+genset+grid"
    assert _arch(_net(), "pv+battery").label == "PV + Battery"


def test_off_grid_site_has_no_grid_architecture():
    n = _net()
    n.remove("Link", "grid_import")
    n.remove("Generator", "grid")
    ids = [a.architecture_id for a in A.architectures_for(n)]
    assert ids and not any("grid" in i for i in ids)


def test_a_slack_is_never_a_member_nor_a_supply():
    """G2: a VoLL or lost-production slack stays in every fork and does not
    make a battery-only set load-serving."""
    n = pypsa.Network()
    n.add("Bus", "site")
    n.add("Load", "l", bus="site", p_set=1.0)
    n.add(
        "StorageUnit", "battery", bus="site", carrier="battery", p_nom_extendable=True
    )
    n.add("Generator", "__voll_l", bus="site", carrier="load_shedding", p_nom=10.0)
    n.add("Generator", "__lost_site", bus="site", carrier="diesel", p_nom=10.0)
    n.add(
        "Generator",
        "short",
        bus="site",
        carrier="diesel",
        p_nom=1.0,
        process_role="shortfall",
    )
    assert A.classify(n) == {"battery": [("StorageUnit", "battery")]}
    assert A.architectures_for(n) == []
    n.add("Generator", "pv", bus="site", carrier="solar", p_nom_extendable=True)
    m = A.omit(n, _arch(n, "pv"))
    assert {"__voll_l", "__lost_site", "short"} <= set(m.generators.index)
    assert "battery" not in m.storage_units.index


def test_a_load_bus_with_no_supply_path_is_not_listed():
    """G3: a genset on a bus not connected to the load serves no architecture."""
    n = pypsa.Network()
    n.add("Bus", "site")
    n.add("Bus", "island")
    n.add("Load", "l", bus="site", p_set=1.0)
    n.add("Generator", "genset", bus="island", carrier="diesel", p_nom_extendable=True)
    n.add("Generator", "pv", bus="site", carrier="solar", p_nom_extendable=True)
    assert [a.architecture_id for a in A.architectures_for(n)] == ["pv", "pv+genset"]


# ── omit: removed, never zeroed (acceptance) ──────────────────────────────


def test_omit_removes_components_never_zeroes_them():
    n = _net()
    m = A.omit(n, _arch(n, "pv+battery"))
    gone = {"genset", "grid", "grid_import"}
    assert not gone & _names(m)  # omitted, not zeroed
    assert list(m.generators.index) == ["pv"]
    assert list(m.storage_units.index) == ["battery"]
    assert bool(m.generators.at["pv", "p_nom_extendable"]) is True
    assert gone <= _names(n)  # the source is unchanged


def _store_net() -> pypsa.Network:
    """A battery built as a Store with charger and discharger links, and a
    grid reached through an export link too."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=2, freq="h"))
    for b in ("site", "batt", "grid"):
        n.add("Bus", b)
    n.add("Load", "l", bus="site", p_set=1.0)
    n.add("Generator", "pv", bus="site", carrier="solar", p_nom_extendable=True)
    n.add("Store", "li-ion", bus="batt", carrier="li-ion", e_nom_extendable=True)
    n.add(
        "Link",
        "charger",
        bus0="site",
        bus1="batt",
        carrier="battery charger",
        p_nom_extendable=True,
    )
    n.add(
        "Link",
        "discharger",
        bus0="batt",
        bus1="site",
        carrier="battery discharger",
        p_nom_extendable=True,
    )
    n.add(
        "Generator",
        "grid",
        bus="grid",
        carrier="grid",
        p_nom=5.0,
        eh_role="grid_supply",
    )
    n.add("Link", "imp", bus0="grid", bus1="site", carrier="AC", p_nom=5.0)
    n.add("Link", "exp", bus0="site", bus1="grid", carrier="AC", p_nom=5.0)
    return n


def test_omit_takes_the_dependants_with_their_member():
    """G3: the store, charger and discharger go with the battery; the import
    and export links go with the grid supply."""
    n = _store_net()
    assert A.classify(n)["battery"] == [("Link", "charger"), ("Link", "discharger")]
    m = A.omit(n, _arch(n, "pv"))
    assert not {"li-ion", "charger", "discharger", "grid", "imp", "exp"} & _names(m)
    assert list(m.generators.index) == ["pv"] and list(m.loads.index) == ["l"]
    kept = A.omit(n, _arch(n, "pv+battery"))
    assert {"li-ion", "charger", "discharger"} <= _names(kept)
    assert not {"grid", "imp", "exp"} & _names(kept)


def test_omit_keeps_a_hub_that_still_joins_two_buses():
    n = pypsa.Network()
    for b in ("a", "hub", "b"):
        n.add("Bus", b)
    n.add("Load", "la", bus="a", p_set=1.0)
    n.add("Load", "lb", bus="b", p_set=1.0)
    n.add("Generator", "genset", bus="hub", carrier="diesel", p_nom_extendable=True)
    n.add("Generator", "pv", bus="a", carrier="solar", p_nom_extendable=True)
    n.add("Link", "ha", bus0="hub", bus1="a", p_nom=5.0)
    n.add("Link", "hb", bus0="hub", bus1="b", p_nom=5.0)
    m = A.omit(n, _arch(n, "pv"))
    assert {"ha", "hb"} <= set(m.links.index)


# ── renewable fraction (G5) ───────────────────────────────────────────────


def _solved(n: pypsa.Network, *, pv=None, genset=None, grid=None, objective=1000.0):
    idx = n.snapshots
    p = {}
    for name, series in (("pv", pv), ("genset", genset), ("grid", grid)):
        if name in n.generators.index and series is not None:
            p[name] = pd.Series(series, index=idx, dtype=float)
    n.generators_t.p = pd.DataFrame(p, index=idx)
    for name, size in (("pv", 2.0), ("genset", 1.0)):
        if name in n.generators.index:
            n.generators.loc[name, "p_nom_opt"] = size
    if "battery" in n.storage_units.index:
        n.storage_units.loc["battery", "p_nom_opt"] = 0.5
    n._objective = objective
    n._objective_constant = 0.0
    return n


def test_renewable_fraction_is_one_minus_non_renewable_over_served_load():
    n = _solved(
        _net(),
        pv=[1.0, 1.0, 0.0, 1.0],
        genset=[0.0, 0.0, 0.5, 0.0],
        grid=[0.0, 0.0, 0.5, 0.0],
    )
    # served 4 MWh; non-renewable 0.5 genset + 0.5 grid import
    assert A.renewable_fraction(n) == pytest.approx(0.75)


def test_renewable_fraction_weights_uneven_snapshots():
    n = _solved(
        _net(), pv=[1.0, 1.0, 0.0, 1.0], genset=[0.0, 0.0, 1.0, 0.0], grid=[0.0] * 4
    )
    n.snapshot_weightings.loc[:, "generators"] = [1.0, 1.0, 6.0, 2.0]
    # served 10 MWh, genset 6 MWh: 0.4 (0.75 unweighted)
    assert A.renewable_fraction(n) == pytest.approx(0.4)


def test_renewable_fraction_weights_investment_periods():
    n = pypsa.Network()
    n.set_snapshots(
        pd.MultiIndex.from_product(
            [[2030, 2040], pd.date_range("2030-01-01", periods=1, freq="h")]
        )
    )
    n.investment_periods = [2030, 2040]
    n.investment_period_weightings.loc[:, "years"] = [1.0, 3.0]
    n.add("Bus", "site")
    n.add("Load", "l", bus="site", p_set=1.0)
    n.add("Generator", "pv", bus="site", carrier="solar")
    n.add("Generator", "genset", bus="site", carrier="diesel")
    n.generators_t.p = pd.DataFrame(
        {"pv": [1.0, 0.0], "genset": [0.0, 1.0]}, index=n.snapshots
    )
    assert A.renewable_fraction(n) == pytest.approx(0.25)


def test_renewable_fraction_counts_grid_import_gross_and_slack_as_unserved():
    n = _solved(
        _net(), pv=[2.0, 2.0, 0.0, 0.0], genset=[0.0] * 4, grid=[-1.0, -1.0, 1.0, 0.5]
    )
    n.add("Generator", "__voll_site_load", bus="site", carrier="load_shedding")
    n.generators_t.p["__voll_site_load"] = [0.0, 0.0, 0.0, 0.5]
    # served 4 - 0.5 = 3.5; grid import 1.5 gross (the export does not net)
    assert A.renewable_fraction(n) == pytest.approx(1 - 1.5 / 3.5)


def test_renewable_fraction_is_none_without_dispatch_or_served_load():
    assert A.renewable_fraction(_net()) is None
    n = _solved(_net(), pv=[0.0] * 4, genset=[0.0] * 4, grid=[0.0] * 4)
    n.loads.loc["site_load", "p_set"] = 0.0
    assert A.renewable_fraction(n) is None  # 0/0 is not 1.0 or NaN


def test_renewable_fraction_clips_to_unit_interval():
    n = _solved(_net(), pv=[3.0] * 4, genset=[0.0] * 4, grid=[0.0] * 4)
    assert A.renewable_fraction(n) == 1.0
    n = _solved(_net(), pv=[0.0] * 4, genset=[2.0] * 4, grid=[0.0] * 4)
    assert A.renewable_fraction(n) == 0.0


# ── rows (G4) ─────────────────────────────────────────────────────────────


def test_row_reads_cost_capex_and_sizes():
    n = _net()
    arch = _arch(n, "pv+battery+genset")
    m = _solved(A.omit(n, arch), pv=[1.0] * 4, genset=[0.0] * 4, objective=1234.0)
    m._objective_constant = 66.0
    row = A.row_of(arch, m)
    assert row["status"] == "ok" and row["unavailable"] == {}
    assert row["total_cost_eur_per_year"] == pytest.approx(1300.0)
    assert row["capex_eur"] == pytest.approx(
        800_000 * 2.0 + 500_000 * 0.5 + 300_000 * 1.0
    )
    assert {s["asset"]: s["size"] for s in row["sizes"]} == {
        "pv": 2.0,
        "battery": 0.5,
        "genset": 1.0,
    }
    assert row["renewable_fraction"] == 1.0


def test_capex_is_unavailable_when_any_extendable_member_has_no_overnight_cost():
    n = _net()
    n.generators.loc["genset", "overnight_cost"] = float("nan")
    arch = _arch(n, "pv+genset")
    m = _solved(A.omit(n, arch), pv=[1.0] * 4, genset=[0.0] * 4)
    m.generators.loc["genset", "p_nom_opt"] = 0.0  # even unbuilt: no partial sum
    row = A.row_of(arch, m)
    assert row["capex_eur"] is None
    assert row["unavailable"]["capex_eur"] == "no_overnight_cost:genset"


def test_row_with_no_objective_flags_the_cost():
    n = _net()
    arch = _arch(n, "pv+battery")
    m = A.omit(n, arch)
    row = A.row_of(arch, m)
    assert row["total_cost_eur_per_year"] is None
    assert row["unavailable"]["total_cost_eur_per_year"] == "no_objective"
    assert row["unavailable"]["renewable_fraction"] == "no_served_load"


# ── compare: every architecture listed, ranked (G6) ───────────────────────


def test_compare_lists_every_status_and_ranks_them():
    n = _net()
    costs = {"grid": 900.0, "pv+battery": 700.0, "genset+grid": 900.0}

    def solve(net, arch):
        aid = arch.architecture_id
        if aid == "genset":
            raise A.ArchitectureUnsolved("infeasible", "LP infeasible")
        if aid == "pv":
            raise A.ArchitectureUnsolved("failed", "preflight_failed")
        if aid not in costs:
            return None  # stopped / no budget
        return _solved(
            net, pv=[1.0] * 4, genset=[0.0] * 4, grid=[0.0] * 4, objective=costs[aid]
        )

    table = A.compare(n, solve)
    rows = table["rows"]
    assert {r["architecture_id"] for r in rows} == {
        a.architecture_id for a in A.architectures_for(n)
    }
    # cheapest first; the 900 tie keeps architecture order (grid before genset+grid)
    assert [r["architecture_id"] for r in rows[:3]] == [
        "pv+battery",
        "grid",
        "genset+grid",
    ]
    statuses = [r["status"] for r in rows]
    assert statuses[:3] == ["ok"] * 3
    assert statuses[3:5] == ["infeasible", "failed"]
    assert set(statuses[5:]) == {"not_reached"}
    for r in rows[3:]:
        assert r["total_cost_eur_per_year"] is None and r["capex_eur"] is None
        assert r["unavailable"]["total_cost_eur_per_year"] == r["status"]
    assert rows[3]["error"] == "LP infeasible"
    assert table["best_architecture_id"] == "pv+battery"


def test_compare_with_nothing_solved_names_no_winner():
    table = A.compare(_net(), lambda net, arch: None)
    assert table["best_architecture_id"] is None
    assert {r["status"] for r in table["rows"]} == {"not_reached"}


def test_compare_solves_omitted_networks_and_named_architectures_only():
    seen = {}

    def solve(net, arch):
        seen[arch.architecture_id] = _names(net)
        return None

    A.compare(_net(), solve, ["genset", "pv+battery"])
    assert set(seen) == {"genset", "pv+battery"}
    assert not {"pv", "battery", "grid", "grid_import"} & seen["genset"]
    with pytest.raises(A.ArchitectureUnsolved) as exc:
        A.compare(_net(), solve, ["wind"])
    assert exc.value.code == "unknown_architecture"


def test_compare_on_a_network_with_no_source_refuses():
    n = pypsa.Network()
    n.add("Bus", "site")
    n.add("Load", "l", bus="site", p_set=1.0)
    with pytest.raises(A.ArchitectureUnsolved) as exc:
        A.compare(n, lambda net, arch: None)
    assert exc.value.code == "no_architectures"


def test_too_many_architectures_needs_a_selection(monkeypatch):
    monkeypatch.setattr(A, "MAX_ARCHITECTURES", 3)
    with pytest.raises(A.ArchitectureUnsolved) as exc:
        A.select(_net())
    assert exc.value.code == "too_many_architectures"
    assert len(A.select(_net(), ["grid", "genset", "pv"])) == 3


def test_unsolved_row_refuses_an_unknown_status():
    with pytest.raises(ValueError):
        A.unsolved_row(A.SupplyArchitecture("grid", ("grid",)), "ok")
