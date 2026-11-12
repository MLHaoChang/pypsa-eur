"""homer-parity 03a: the best system per architecture (pure core).

`services/study/architectures.py` groups a network's assets, builds one
network per architecture by OMITTING the other groups (never zeroing them),
and tabulates each architecture's solved best system; an architecture that
does not solve keeps its row.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from services.study import architectures as A


def _net() -> pypsa.Network:
    """A site with PV, a battery, a diesel genset and a grid supply."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=4, freq="h"))
    for c in ("AC", "solar", "battery", "diesel", "grid", "grid_import"):
        n.add("Carrier", c)
    n.add("Bus", "site", carrier="AC")
    n.add("Bus", "grid", carrier="AC")
    n.add("Load", "site_load", bus="site", p_set=1.0)
    n.add("Generator", "pv", bus="site", carrier="solar", p_nom_extendable=True,
          overnight_cost=800_000.0, capital_cost=60_000.0)
    n.add("StorageUnit", "battery", bus="site", carrier="battery",
          p_nom_extendable=True, max_hours=2, overnight_cost=500_000.0)
    n.add("Generator", "genset", bus="site", carrier="diesel", p_nom_extendable=True,
          marginal_cost=250.0, capital_cost=20_000.0)
    n.add("Generator", "grid", bus="grid", carrier="grid", p_nom=5.0)
    n.add("Link", "grid_import", bus0="grid", bus1="site", carrier="grid_import", p_nom=5.0)
    return n


def test_classify_groups_assets_by_role():
    groups = A.classify(_net())
    assert groups == {
        "pv": [("Generator", "pv")],
        "battery": [("StorageUnit", "battery")],
        "genset": [("Generator", "genset")],
        "grid": [("Generator", "grid"), ("Link", "grid_import")],
    }


def test_architectures_cover_only_present_load_serving_sets():
    archs = A.architectures_for(A.classify(_net()))
    ids = [a.architecture_id for a in archs]
    assert len(ids) == len(set(ids))
    # every architecture has a source of energy; a battery alone is none
    assert all(set(a.groups) - {"battery"} for a in archs)
    assert "battery" not in ids
    # wind is absent from the network, so no architecture names it
    assert not any("wind" in a.groups for a in archs)
    for expected in ("grid", "genset", "pv+battery", "pv+genset", "pv+battery+genset",
                     "pv+battery+genset+grid"):
        assert expected in ids
    assert len(archs) <= A.MAX_ARCHITECTURES


def test_architectures_without_grid_on_an_off_grid_site():
    n = _net()
    n.remove("Link", "grid_import")
    n.remove("Generator", "grid")
    ids = [a.architecture_id for a in A.architectures_for(A.classify(n))]
    assert ids and not any("grid" in i for i in ids)


def test_omit_removes_components_never_zeroes_them():
    n = _net()
    arch = next(a for a in A.architectures_for(A.classify(n))
                if a.architecture_id == "pv+battery")
    m = A.omit(n, arch)
    assert "genset" not in m.generators.index           # omitted, not zeroed
    assert "grid" not in m.generators.index
    assert "grid_import" not in m.links.index
    assert list(m.generators.index) == ["pv"]
    assert list(m.storage_units.index) == ["battery"]
    # the kept assets are untouched and the source network is unchanged
    assert bool(m.generators.at["pv", "p_nom_extendable"]) is True
    assert "genset" in n.generators.index and "grid_import" in n.links.index


def _solved(n: pypsa.Network, *, pv, genset, grid, objective=1000.0) -> pypsa.Network:
    idx = n.snapshots
    p = {}
    if "pv" in n.generators.index:
        p["pv"] = pd.Series(pv, index=idx)
        n.generators.loc["pv", "p_nom_opt"] = 2.0
    if "genset" in n.generators.index:
        p["genset"] = pd.Series(genset, index=idx)
        n.generators.loc["genset", "p_nom_opt"] = 1.0
    if "grid" in n.generators.index:
        p["grid"] = pd.Series(grid, index=idx)
    n.generators_t.p = pd.DataFrame(p, index=idx)
    if "grid_import" in n.links.index:
        n.links_t.p0 = pd.DataFrame({"grid_import": grid}, index=idx)
    if "battery" in n.storage_units.index:
        n.storage_units.loc["battery", "p_nom_opt"] = 0.5
    n._objective = objective
    n._objective_constant = 0.0
    return n


def test_renewable_fraction_is_one_minus_non_renewable_over_served_load():
    n = _solved(_net(), pv=[1.0, 1.0, 0.0, 1.0], genset=[0.0, 0.0, 0.5, 0.0],
                grid=[0.0, 0.0, 0.5, 0.0])
    # served 4 MWh; non-renewable 0.5 genset + 0.5 grid import
    assert A.renewable_fraction(n) == pytest.approx(0.75)


def test_renewable_fraction_is_none_without_dispatch():
    assert A.renewable_fraction(_net()) is None


def test_renewable_fraction_clips_to_unit_interval():
    # surplus PV exported does not push it above 1, genset excess not below 0
    n = _solved(_net(), pv=[3.0] * 4, genset=[0.0] * 4, grid=[0.0] * 4)
    assert A.renewable_fraction(n) == 1.0
    n = _solved(_net(), pv=[0.0] * 4, genset=[2.0] * 4, grid=[0.0] * 4)
    assert A.renewable_fraction(n) == 0.0


def test_row_reads_cost_capex_and_sizes():
    n = _net()
    arch = next(a for a in A.architectures_for(A.classify(n))
                if a.architecture_id == "pv+battery+genset")
    m = _solved(A.omit(n, arch), pv=[1.0] * 4, genset=[0.0] * 4, grid=[0.0] * 4,
                objective=1234.0)
    row = A.row_of(arch, m)
    assert row["status"] == "ok"
    assert row["total_cost_eur_per_year"] == pytest.approx(1234.0)
    # overnight cost x size; the genset has none, so capex is not established
    assert row["capex_eur"] is None
    assert row["unavailable"]["capex_eur"] == "no_overnight_cost:genset"
    assert {s["asset"]: s["p_nom_opt"] for s in row["sizes"]} == {
        "pv": 2.0, "battery": 0.5, "genset": 1.0}
    assert row["renewable_fraction"] == 1.0


def test_row_capex_sums_overnight_cost_times_size():
    n = _net()
    arch = next(a for a in A.architectures_for(A.classify(n))
                if a.architecture_id == "pv+battery")
    m = _solved(A.omit(n, arch), pv=[1.0] * 4, genset=[], grid=[])
    row = A.row_of(arch, m)
    assert row["capex_eur"] == pytest.approx(800_000.0 * 2.0 + 500_000.0 * 0.5)


def test_compare_keeps_an_infeasible_architecture_and_ranks_by_cost():
    n = _net()
    costs = {"grid": 900.0, "pv+battery": 700.0}

    def solve(net, arch):
        if arch.architecture_id == "genset":
            raise A.ArchitectureUnsolved("infeasible", "LP infeasible")
        if arch.architecture_id not in costs:
            return None                                   # not reached
        return _solved(net, pv=[1.0] * 4, genset=[0.0] * 4, grid=[0.0] * 4,
                       objective=costs[arch.architecture_id])

    table = A.compare(n, solve)
    ids = [r["architecture_id"] for r in table["rows"]]
    assert set(ids) == {a.architecture_id for a in A.architectures_for(A.classify(n))}
    assert ids[:2] == ["pv+battery", "grid"]              # cheapest first
    genset = next(r for r in table["rows"] if r["architecture_id"] == "genset")
    assert genset["status"] == "infeasible"
    assert genset["total_cost_eur_per_year"] is None
    assert genset["unavailable"]["total_cost_eur_per_year"] == "infeasible"
    pending = [r for r in table["rows"] if r["status"] == "not_run"]
    assert pending and all(r["total_cost_eur_per_year"] is None for r in pending)
    # unsolved rows follow every solved one
    statuses = [r["status"] for r in table["rows"]]
    assert statuses.index("infeasible") > statuses.index("ok")
    assert table["best_architecture_id"] == "pv+battery"


def test_compare_on_a_network_with_no_source_refuses():
    n = pypsa.Network()
    n.add("Bus", "site")
    n.add("Load", "l", bus="site", p_set=1.0)
    with pytest.raises(A.ArchitectureUnsolved) as exc:
        A.compare(n, lambda net, arch: None)
    assert exc.value.code == "no_architectures"
