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
    # both ways (F6): pv on `a` reaches `lb` only back through the hub
    n.add("Link", "ha", bus0="hub", bus1="a", p_nom=5.0, p_min_pu=-1.0)
    n.add("Link", "hb", bus0="hub", bus1="b", p_nom=5.0, p_min_pu=-1.0)
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


# ── gaps the mutation run found ───────────────────────────────────────────


def test_classify_reads_wind_and_a_grid_asset_by_its_eh_role():
    n = _net()
    n.add("Carrier", "onwind")
    n.add("Generator", "wt", bus="site", carrier="onwind")
    n.add("Generator", "utility", bus="grid", carrier="AC", eh_role="grid_supply")
    groups = A.classify(n)
    assert groups["wind"] == [("Generator", "wt")]
    assert ("Generator", "utility") in groups["grid"]


def test_capex_is_unavailable_for_a_zero_cost_or_unsized_member_and_skips_fixed():
    n = _net()
    n.generators.loc["genset", "overnight_cost"] = 0.0
    arch = _arch(n, "pv+genset")
    m = _solved(A.omit(n, arch), pv=[1.0] * 4, genset=[0.0] * 4)
    assert A.row_of(arch, m)["unavailable"]["capex_eur"] == "no_overnight_cost:genset"

    n = _net()
    arch = _arch(n, "pv+genset")
    m = _solved(A.omit(n, arch), pv=[1.0] * 4, genset=[0.0] * 4)
    m.generators.loc["pv", "p_nom_opt"] = float("nan")
    assert A.row_of(arch, m)["unavailable"]["capex_eur"] == "not_sized:pv"

    n = _net()
    n.generators.loc["genset", ["p_nom_extendable", "p_nom"]] = [False, 1.0]
    n.generators.loc["genset", "overnight_cost"] = float("nan")  # fixed: ignored
    arch = _arch(n, "pv+genset")
    m = _solved(A.omit(n, arch), pv=[1.0] * 4, genset=[0.0] * 4)
    row = A.row_of(arch, m)
    assert row["capex_eur"] == pytest.approx(800_000 * 2.0)
    assert {s["asset"]: s["extendable"] for s in row["sizes"]}["genset"] is False


def test_renewable_fraction_falls_back_to_p_set_when_load_dispatch_is_missing():
    n = _solved(
        _net(), pv=[1.0, 1.0, 0.0, 1.0], genset=[0.0, 0.0, 1.0, 0.0], grid=[0.0] * 4
    )
    n.loads_t.p = pd.DataFrame({"other": [9.0] * 4}, index=n.snapshots)
    assert A.renewable_fraction(n) == pytest.approx(0.75)


def test_unsolved_row_shape_and_best_of_skips_unsolved_and_uncosted():
    n = _net()
    a, b, c = (_arch(n, x) for x in ("pv+battery", "genset", "pv+genset"))
    row = A.unsolved_row(b, "failed", "boom")
    assert row == {
        "architecture_id": "genset",
        "label": b.label,
        "groups": ["genset"],
        "status": "failed",
        "sizes": [],
        "unavailable": {k: "failed" for k in A._FIGURES},
        **{k: None for k in A._FIGURES},
        "error": "boom",
    }
    uncosted = {**A.unsolved_row(a, "infeasible"), "status": "ok"}
    cheap_failed = {**row, "total_cost_eur_per_year": 1.0}
    costed = {
        **A.unsolved_row(c, "infeasible"),
        "status": "ok",
        "total_cost_eur_per_year": 50.0,
    }
    assert A.best_of([uncosted, cheap_failed, costed]) == "pv+genset"
    assert A.best_of([uncosted, cheap_failed]) is None


def test_renewable_fraction_counts_a_grid_import_link_once_by_its_delivery():
    # eh_setup's shape: the import is a Link (eh_role=grid_import) and the
    # upstream generator has an ordinary carrier; count the Link's delivery
    # into the site, never the upstream generator as well.
    n = _net()
    n.generators.loc["grid", "carrier"] = "AC"
    n.links.loc["grid_import", ["carrier", "eh_role"]] = ["AC", "grid_import"]
    assert A.classify(n)["grid"] == [("Link", "grid_import"), ("Generator", "grid")]
    n = _solved(n, pv=[1.0, 1.0, 0.0, 1.0], genset=[0.0] * 4, grid=[0.0, 0.0, 2.0, 0.0])
    n.links_t.p1 = pd.DataFrame(
        {"grid_import": [0.0, 0.0, -1.0, 0.0]}, index=n.snapshots
    )
    # served 4 MWh; 1 MWh delivered by the import link (2 MWh upstream ignored)
    assert A.renewable_fraction(n) == pytest.approx(0.75)


# ── GK B round 2 (F1-F6) ──────────────────────────────────────────────────


def _with_export(n: pypsa.Network) -> pypsa.Network:
    n.add("Carrier", "grid_export")
    n.add("Bus", "sink", carrier="AC")
    n.add(
        "Link", "grid_export", bus0="site", bus1="sink", carrier="grid_export", p_nom=5
    )
    return n


def test_renewable_fraction_never_counts_export_and_counts_each_import_once():
    """F1: only energy delivered into the site counts; export never does."""
    # _net() plus an export link: 2 MWh imported, 4 MWh exported, 4 MWh served
    n = _solved(
        _with_export(_net()),
        pv=[2.0] * 4,
        genset=[0.0] * 4,
        grid=[0.0, 0.0, 1.0, 1.0],
    )
    n.links_t.p1 = pd.DataFrame({"grid_export": [-1.0] * 4}, index=n.snapshots)
    assert A.renewable_fraction(n) == pytest.approx(0.5)  # 1 - 2/4
    # the import link's delivery stands for the generator behind it
    n.links_t.p1["grid_import"] = [0.0, 0.0, -1.0, -1.0]
    assert A.renewable_fraction(n) == pytest.approx(0.5)

    # a grid Generator on the site bus with only an export link
    n = _with_export(_net())
    n.remove("Link", "grid_import")
    n.generators.loc["grid", "bus"] = "site"
    n = _solved(n, pv=[2.0] * 4, genset=[0.0] * 4, grid=[0.0, 0.0, 1.0, 1.0])
    n.links_t.p1 = pd.DataFrame({"grid_export": [-1.0] * 4}, index=n.snapshots)
    assert A.renewable_fraction(n) == pytest.approx(0.5)  # the 2 MWh generated


def _ocgt_net() -> pypsa.Network:
    """PV on the site and a genset modelled as a gas Generator feeding an
    OCGT Link at efficiency 0.4."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=4, freq="h"))
    n.add("Bus", "site", carrier="AC")
    n.add("Bus", "gas", carrier="gas")
    n.add("Load", "l", bus="site", p_set=1.0)
    n.add("Generator", "pv", bus="site", carrier="solar", p_nom_extendable=True)
    n.add("Generator", "fuel", bus="gas", carrier="gas", p_nom=10.0)
    n.add(
        "Link",
        "ocgt",
        bus0="gas",
        bus1="site",
        carrier="OCGT",
        efficiency=0.4,
        p_nom_extendable=True,
    )
    return n


def test_a_link_genset_counts_its_electricity_not_its_fuel():
    """F2: 2.5 MWh of gas through an OCGT at 0.4 delivers 1 MWh of 2 served."""
    n = _ocgt_net()
    assert set(A.classify(n)["genset"]) == {("Generator", "fuel"), ("Link", "ocgt")}
    m = A.omit(n, _arch(n, "pv"))
    assert not {"fuel", "ocgt"} & _names(m)
    n.snapshot_weightings.loc[:, "generators"] = [1.0, 1.0, 0.0, 0.0]
    n.generators_t.p = pd.DataFrame(
        {"pv": [1.0, 0.0, 1.0, 1.0], "fuel": [0.0, 2.5, 0.0, 0.0]}, index=n.snapshots
    )
    n.links_t.p0 = pd.DataFrame({"ocgt": [0.0, 2.5, 0.0, 0.0]}, index=n.snapshots)
    n.links_t.p1 = pd.DataFrame({"ocgt": [0.0, -1.0, 0.0, 0.0]}, index=n.snapshots)
    assert A.renewable_fraction(n) == pytest.approx(0.5)


def test_a_link_genset_reads_a_time_varying_efficiency():
    n = _ocgt_net()
    n.links_t.efficiency = pd.DataFrame(
        {"ocgt": [0.4, 0.4, 0.4, 0.5]}, index=n.snapshots
    )
    n.generators_t.p = pd.DataFrame(
        {"pv": [1.0, 1.0, 0.0, 0.0], "fuel": [0.0, 0.0, 2.5, 2.0]}, index=n.snapshots
    )
    n.links_t.p0 = pd.DataFrame({"ocgt": [0.0, 0.0, 2.5, 2.0]}, index=n.snapshots)
    # delivered 0.4 * 2.5 + 0.5 * 2.0 = 2 MWh of 4 served (1.8 at a fixed 0.4)
    assert A.renewable_fraction(n) == pytest.approx(0.5)


def test_served_load_is_electrical_only_and_nan_reads_none():
    """F3: a heat Load does not dilute the fraction; NaN dispatch is None."""
    n = _solved(_net(), pv=[1.0, 1.0, 0.0, 1.0], genset=[0.0, 0.0, 1.0, 0.0])
    before = A.renewable_fraction(n)
    assert before == pytest.approx(0.75)
    n.add("Bus", "heat", carrier="heat")
    n.add("Load", "heat_load", bus="heat", p_set=5.0)
    assert A.renewable_fraction(n) == pytest.approx(before)
    n = _solved(_net(), pv=[1.0, 1.0, 0.0, 1.0], genset=[0.0, 0.0, float("nan"), 0.0])
    assert A.renewable_fraction(n) is None


def _eh_setup_net() -> pypsa.Network:
    """eh_setup's shape: a fixed grid Generator with an ordinary carrier
    behind an `eh_role = grid_import` Link."""
    n = _net()
    n.generators.loc["grid", ["carrier", "capital_cost"]] = ["AC", 5_000.0]
    n.links.loc["grid_import", ["carrier", "eh_role"]] = ["AC", "grid_import"]
    return n


def test_a_generator_reached_only_through_a_member_link_goes_with_it():
    """F5: the no-grid fork does not keep a stranded grid Generator (and its
    fixed capital cost)."""
    n = _eh_setup_net()
    assert A.classify(n)["grid"] == [("Link", "grid_import"), ("Generator", "grid")]
    arch = _arch(n, "pv+genset")
    assert ("Generator", "grid") in A.removed_components(n, arch)
    m = A.omit(n, arch)
    assert "grid" not in m.generators.index and "grid_import" not in m.links.index
    # a generator that also feeds a non-member link is not stranded
    n.add("Link", "other", bus0="grid", bus1="site", carrier="AC", p_nom=1.0)
    assert ("Generator", "grid") not in A.classify(n)["grid"]


def test_a_one_way_link_away_from_the_load_supplies_nothing():
    """F6: a grid behind a site->g one-way link is no grid architecture."""
    n = pypsa.Network()
    n.add("Bus", "site")
    n.add("Bus", "g")
    n.add("Load", "l", bus="site", p_set=1.0)
    n.add("Generator", "pv", bus="site", carrier="solar", p_nom_extendable=True)
    n.add("Generator", "grid", bus="g", carrier="grid", p_nom=5.0)
    n.add("Link", "out", bus0="site", bus1="g", p_nom=5.0, p_min_pu=0.0)
    ids = [a.architecture_id for a in A.architectures_for(n)]
    assert "grid" not in ids and "pv" in ids
    n.links.loc["out", "p_min_pu"] = -1.0  # runs both ways: the grid supplies
    assert "grid" in [a.architecture_id for a in A.architectures_for(n)]


def test_a_member_link_vacates_its_bus2_too():
    n = _ocgt_net()
    n.add("Bus", "heat", carrier="heat")
    n.add("Store", "tank", bus="heat", carrier="heat", e_nom=1.0)
    n.links.loc["ocgt", "bus2"] = "heat"
    assert "tank" not in A.omit(n, _arch(n, "pv")).stores.index


def test_unsolved_rows_of_one_status_keep_list_order():
    archs = [A.SupplyArchitecture(a, (a,)) for a in ("pv", "wind", "grid")]
    rows = [A.unsolved_row(a, "failed") for a in archs]
    assert [r["architecture_id"] for r in A.ranked(rows)] == ["pv", "wind", "grid"]


def test_row_cost_is_the_runners_system_cost(monkeypatch):
    """F4: one system-cost reading, the runner's."""
    from services.study import runner as R

    n = _net()
    arch = _arch(n, "pv+battery+genset")
    m = _solved(A.omit(n, arch), pv=[1.0] * 4, genset=[0.0] * 4, objective=1.0)
    monkeypatch.setattr(R, "system_cost", lambda net: 42.0)
    assert A.row_of(arch, m)["total_cost_eur_per_year"] == 42.0


# ── GK B round 3 (F1', F6') ───────────────────────────────────────────────


def test_an_uncounted_non_renewable_generator_makes_the_fraction_none():
    """F1'(a): a grid Generator that also feeds a non-member Link is neither
    counted nor a slack, so the fraction is unknown, never 1.0."""
    n = _eh_setup_net()
    n.add("Bus", "aux", carrier="AC")
    n.add("Link", "aux_link", bus0="grid", bus1="aux", carrier="AC", p_nom=1.0)
    n = _solved(n, pv=[1.0] * 4, genset=[0.0] * 4, grid=[0.0, 0.0, 2.0, 0.0])
    assert A.renewable_fraction(n) is None
    row = A.row_of(_arch(_eh_setup_net(), "pv+genset"), n)
    assert row["unavailable"]["renewable_fraction"] == "uncounted_non_renewable"


@pytest.mark.parametrize(
    "carrier", ["gas turbine", "Diesel genset", "urban central gas CHP"]
)
def test_a_link_genset_is_recognised_by_its_fuel_or_technology_token(carrier):
    """F1'(b): 1.5 MWh delivered by the genset Link of 4 MWh served."""
    n = _ocgt_net()
    n.links.loc["ocgt", "carrier"] = carrier
    assert set(A.classify(n)["genset"]) == {("Generator", "fuel"), ("Link", "ocgt")}
    n.generators_t.p = pd.DataFrame(
        {"pv": [1.0, 1.0, 0.0, 0.5], "fuel": [0.0, 0.0, 2.5, 1.25]},
        index=n.snapshots,
    )
    n.links_t.p1 = pd.DataFrame({"ocgt": [0.0, 0.0, -1.0, -0.5]}, index=n.snapshots)
    assert A.renewable_fraction(n) == pytest.approx(1 - 1.5 / 4)


def test_a_fuel_generator_behind_a_counted_genset_link_is_covered():
    """F1': the Link's delivery stands for its fuel Generator (a non-renewable
    carrier no genset token names), so the fraction stays a number."""
    n = _ocgt_net()
    n.generators.loc["fuel", "carrier"] = "methane"
    assert A._group_of("Generator", "methane", "") is None
    n.generators_t.p = pd.DataFrame(
        {"pv": [1.0, 1.0, 1.0, 0.0], "fuel": [0.0, 0.0, 0.0, 2.5]}, index=n.snapshots
    )
    n.links_t.p1 = pd.DataFrame({"ocgt": [0.0, 0.0, 0.0, -1.0]}, index=n.snapshots)
    assert A.renewable_fraction(n) == pytest.approx(0.75)


def test_a_link_that_runs_back_in_some_snapshot_supplies_the_load():
    """F6': a static p_min_pu of 0 with a time-varying -1 runs both ways."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=2, freq="h"))
    n.add("Bus", "site")
    n.add("Bus", "g")
    n.add("Load", "l", bus="site", p_set=1.0)
    n.add("Generator", "pv", bus="site", carrier="solar", p_nom_extendable=True)
    n.add("Generator", "grid", bus="g", carrier="grid", p_nom=5.0)
    n.add("Link", "out", bus0="site", bus1="g", p_nom=5.0, p_min_pu=0.0)
    n.links_t.p_min_pu = pd.DataFrame({"out": [0.0, -1.0]}, index=n.snapshots)
    assert "grid" in [a.architecture_id for a in A.architectures_for(n)]


def test_a_fuel_generator_feeding_only_heat_does_not_blank_the_fraction():
    """F1' bound: a gas Generator whose only Link runs to a heat bus never
    reaches electricity, so it is outside the (electrical) fraction."""
    n = _solved(
        _net(), pv=[1.0, 1.0, 0.0, 1.0], genset=[0.0, 0.0, 1.0, 0.0], grid=[0.0] * 4
    )
    for b, c in (("gas", "gas"), ("heat", "heat")):
        n.add("Carrier", c)
        n.add("Bus", b, carrier=c)
    n.add("Generator", "gas_supply", bus="gas", carrier="gas", p_nom=5.0)
    n.add("Link", "boiler", bus0="gas", bus1="heat", carrier="gas boiler", p_nom=5.0)
    n.add("Load", "heat_load", bus="heat", p_set=1.0)
    n.generators_t.p["gas_supply"] = [1.0] * 4
    genset = set(A.classify(n)["genset"])
    assert not {("Generator", "gas_supply"), ("Link", "boiler")} & genset
    assert A.renewable_fraction(n) == pytest.approx(0.75)


# ── GK B round 4 (F1″) ────────────────────────────────────────────────────


def _two_hop_net(chain: str, *, store: bool = False) -> pypsa.Network:
    """`_net`, solved at a 0.75 fraction, plus a gas Generator two Links away
    from the site: (a) gas → "gas" Link → `mid` → "xyz" Link (η 0.4) → site,
    or (b) gas → SMR → H2 → fuel cell → site. ``store`` puts a Store on the
    middle bus."""
    n = _solved(
        _net(), pv=[1.0, 1.0, 0.0, 1.0], genset=[0.0, 0.0, 1.0, 0.0], grid=[0.0] * 4
    )
    mid, first, second = {
        "a": ("mid", ("gas", "gas"), ("xyz", "xyz")),
        "b": ("H2", ("SMR", "SMR"), ("fuel cell", "fuel cell")),
    }[chain]
    for b, c in (("gas", "gas"), (mid, mid)):
        n.add("Carrier", c)
        n.add("Bus", b, carrier=c)
    n.add("Generator", "gas_supply", bus="gas", carrier="gas", p_nom=5.0)
    n.add("Link", first[0], bus0="gas", bus1=mid, carrier=first[1], p_nom=5.0)
    n.add(
        "Link",
        second[0],
        bus0=mid,
        bus1="site",
        carrier=second[1],
        efficiency=0.4,
        p_nom=5.0,
    )
    if store:
        n.add("Store", "mid_store", bus=mid, carrier=mid, e_nom=10.0)
    n.generators_t.p["gas_supply"] = [0.0, 0.0, 0.0, 2.5]
    return n


@pytest.mark.parametrize("chain", ["a", "b"])
def test_a_fuel_generator_two_links_from_the_site_makes_the_fraction_none(chain):
    """F1″: the gas reaches the site's electricity through a chain of
    non-member Links, so its energy is uncounted: None, never 1.0 or 0.75."""
    n = _two_hop_net(chain)
    genset = set(A.classify(n).get("genset", ()))
    assert ("Generator", "gas_supply") not in genset
    assert A.renewable_fraction(n) is None


def test_storage_on_the_chain_does_not_launder_the_gas():
    """F1″: an H2 Store on the H2 bus does not end the walk; the gas still
    reaches the site's electricity, so the fraction stays None."""
    n = _two_hop_net("b", store=True)
    assert A.renewable_fraction(n) is None


def test_the_reach_walk_follows_link_direction():
    """F1″ bound: a one-way Link from the site into the gas bus carries no gas
    to electricity, so the gas Generator is outside the fraction."""
    n = _solved(
        _net(), pv=[1.0, 1.0, 0.0, 1.0], genset=[0.0, 0.0, 1.0, 0.0], grid=[0.0] * 4
    )
    n.add("Carrier", "gas")
    n.add("Bus", "gas", carrier="gas")
    n.add("Generator", "gas_supply", bus="gas", carrier="gas", p_nom=5.0)
    n.add("Link", "p2g", bus0="site", bus1="gas", carrier="methanation", p_nom=5.0)
    n.generators_t.p["gas_supply"] = [1.0] * 4
    assert A.renewable_fraction(n) == pytest.approx(0.75)
    n.links_t.p_min_pu = pd.DataFrame({"p2g": [0.0, -1.0, 0.0, 0.0]}, index=n.snapshots)
    assert A.renewable_fraction(n) is None


def test_a_time_varying_p_min_pu_overrides_a_negative_static_value():
    """A static p_min_pu of -1 with a time-varying column of all 0 runs one
    way: the column, when present, is the Link's p_min_pu."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=2, freq="h"))
    n.add("Bus", "site")
    n.add("Bus", "g")
    n.add("Load", "l", bus="site", p_set=1.0)
    n.add("Generator", "pv", bus="site", carrier="solar", p_nom_extendable=True)
    n.add("Generator", "grid", bus="g", carrier="grid", p_nom=5.0)
    n.add("Link", "out", bus0="site", bus1="g", p_nom=5.0, p_min_pu=-1.0)
    assert "grid" in [a.architecture_id for a in A.architectures_for(n)]
    n.links_t.p_min_pu = pd.DataFrame({"out": [0.0, 0.0]}, index=n.snapshots)
    assert "grid" not in [a.architecture_id for a in A.architectures_for(n)]


def test_electric_carriers_match_the_commercial_engine():
    from services.commercial.participants import _ELECTRIC

    assert A._ELECTRIC == _ELECTRIC
