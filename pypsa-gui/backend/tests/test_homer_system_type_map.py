"""homer-parity 03b: the optimal system type map (pure core).

`services/study/system_type_map.py` applies two multipliers to a copy of the
base network per cell and takes the 03a comparison's cheapest architecture as
the cell's winner, with the runner-up's cost gap.
"""

from __future__ import annotations

import pytest

from services.study import supply_architectures as SA
from services.study import system_type_map as M
from tests.test_homer_supply_architectures import _net, _solved


def _axes(n, x=("genset_fuel_price", [0.5, 1.0, 2.0]), y=("load", [1.0, 2.0, 3.0])):
    return M.check_axes(
        {"variable": x[0], "values": x[1]}, {"variable": y[0], "values": y[1]}
    )


def _stub(net, arch):
    """Cost by hand, per unit of load: PV+battery 700, grid 900, any other
    1000 (with PV) or 3000, plus 4 x the genset's marginal cost for a genset."""
    load = float(net.loads.p_set.iloc[0])
    gen_price = (
        float(net.generators.marginal_cost.get("genset", 0.0))
        if "genset" in net.generators.index
        else 0.0
    )
    base = {"pv+battery": 700.0, "grid": 900.0}.get(arch.architecture_id)
    if base is None:
        base = 1000.0 if "pv" in arch.groups else 3000.0
        base += gen_price * 4.0 if "genset" in arch.groups else 0.0
    return _solved(
        net, pv=[1.0] * 4, genset=[0.0] * 4, grid=[0.0] * 4, objective=base * load
    )


def test_each_cell_names_the_cheapest_architecture_and_the_runner_up_gap():
    n = _net()
    archs = SA.select(n)
    out = M.build_map(n, *_axes(n), _stub, archs=archs)
    assert len(out["cells"]) == 9
    assert [a["label"] for a in out["axes"]] == ["Genset fuel price", "Load"]
    for c in out["cells"]:
        load = [1.0, 2.0, 3.0][c["j"]]
        solved = sorted(
            r["total_cost_eur_per_year"] for r in c["rows"] if r["status"] == "ok"
        )
        assert c["winner_architecture_id"] == "pv+battery"
        assert c["rows"][0]["architecture_id"] == "pv+battery"
        assert c["runner_up_gap_eur_per_year"] == pytest.approx(solved[1] - solved[0])
        assert solved[0] == pytest.approx(700.0 * load)


def test_the_winner_flips_when_the_fuel_price_falls_below_break_even():
    n = _net()

    def cheap_genset(net, arch):
        # genset alone: marginal cost x 2.0 per unit of load
        if arch.architecture_id == "genset":
            price = float(net.generators.marginal_cost["genset"])
            return _solved(net, genset=[1.0] * 4, objective=price * 2.0)
        return _stub(net, arch)

    out = M.build_map(
        n,
        *_axes(n, x=("genset_fuel_price", [0.5, 1.0]), y=("load", [1.0, 2.0])),
        cheap_genset,
        archs=SA.select(n),
    )
    by = {(c["i"], c["j"]): c["winner_architecture_id"] for c in out["cells"]}
    # 250 x 0.5 x 2 = 250 < 700 at load 1; 250 x 1 x 2 = 500 < 700 too; at load 2 the stub scales pv
    assert by[(0, 0)] == "genset"
    assert by[(1, 0)] == "genset"


def test_a_cell_where_nothing_solved_has_no_winner_and_keeps_every_status():
    n = _net()
    out = M.build_map(n, *_axes(n), lambda net, arch: None, archs=SA.select(n))
    for c in out["cells"]:
        assert (
            c["winner_architecture_id"] is None
            and c["runner_up_gap_eur_per_year"] is None
        )
        assert len(c["rows"]) == len(SA.select(n))
        assert {r["status"] for r in c["rows"]} == {"not_reached"}


def test_a_single_solved_architecture_has_no_gap():
    n = _net()

    def only_grid(net, arch):
        return _stub(net, arch) if arch.architecture_id == "grid" else None

    out = M.build_map(n, *_axes(n), only_grid, archs=SA.select(n))
    assert {c["winner_architecture_id"] for c in out["cells"]} == {"grid"}
    assert {c["runner_up_gap_eur_per_year"] for c in out["cells"]} == {None}


def test_a_cell_is_a_copy_and_the_base_is_untouched():
    n = _net()
    seen = []

    def solve(net, arch):
        if "genset" in arch.groups:
            seen.append(
                (
                    float(net.generators.marginal_cost["genset"]),
                    float(net.loads.p_set.iloc[0]),
                )
            )
        return None

    M.build_map(n, *_axes(n), solve, archs=SA.select(n))
    assert (
        float(n.generators.marginal_cost["genset"]) == 250.0
        and float(n.loads.p_set.iloc[0]) == 1.0
    )
    assert {s for s in seen} == {
        (p * 250.0, l) for p in (0.5, 1.0, 2.0) for l in (1.0, 2.0, 3.0)
    }


def test_the_omitted_assets_stay_removed_in_every_cell():
    n = _net()
    names = []

    def solve(net, arch):
        if arch.architecture_id == "genset":
            names.append(set(net.generators.index) | set(net.storage_units.index))
        return None

    M.build_map(n, *_axes(n), solve, archs=SA.select(n))
    assert names and all(s == {"genset"} for s in names)


def test_a_variable_with_nothing_to_scale_is_refused():
    n = _net()  # the grid supply is free and PV has no capital cost: zero base values
    for var in ("grid_energy_price", "pv_capital_cost", "wind_resource"):
        x = M.check_axes({"variable": var}, {"variable": "load"})
        with pytest.raises(M.MapRefused) as exc:
            M.build_map(n, *x, lambda net, arch: None, archs=SA.select(n))
        assert exc.value.code == "variable_not_in_network"


def test_a_resource_multiplier_is_clipped_at_nameplate_and_the_clip_is_recorded():
    """V2, static and time series."""
    n = _net()
    n.generators.loc["pv", "p_max_pu"] = 0.8
    probe = n.copy()
    got = M.apply_variable(probe, M.VARIABLES["solar_resource"], 1.5)
    assert float(probe.generators.p_max_pu["pv"]) == 1.0  # 0.8 x 1.5 = 1.2, clipped
    assert got.scaled == 1 and got.clipped_share == pytest.approx(0.2 / 1.2)
    n.generators_t.p_max_pu["pv"] = [0.0, 0.5, 1.0, 0.5]
    probe = n.copy()
    got = M.apply_variable(probe, M.VARIABLES["solar_resource"], 2.0)
    assert list(probe.generators_t.p_max_pu["pv"]) == [0.0, 1.0, 1.0, 1.0]
    # the static value (0.8) is scaled and clipped too, as the load's two are
    assert got.scaled == 2
    # unclipped energy 0+1+2+1 = 4, kept 0+1+1+1 = 3 (equal weights), plus the static column
    assert 0.2 < got.clipped_share < 0.4
    probe = n.copy()
    assert (
        M.apply_variable(probe, M.VARIABLES["solar_resource"], 0.5).clipped_share == 0.0
    )
    assert list(probe.generators_t.p_max_pu["pv"]) == [0.0, 0.25, 0.5, 0.25]


def test_a_cell_names_its_clipped_resource():
    n = _net()
    n.generators.loc["pv", "p_max_pu"] = 0.8
    x = M.check_axes(
        {"variable": "solar_resource", "values": [0.5, 1.5]}, {"variable": "load"}
    )
    out = M.build_map(n, *x, lambda net, arch: None, archs=SA.select(n))
    by = {(c["i"], c["j"]): c["clipped"] for c in out["cells"]}
    assert by[(0, 0)] == []
    assert by[(1, 0)][0]["variable"] == "solar_resource"
    assert by[(1, 0)][0]["share"] == pytest.approx(0.2 / 1.2)


def test_a_capital_cost_counts_only_assets_the_optimiser_sizes():
    """V3."""
    n = _net()
    n.generators.loc["pv", ["capital_cost", "p_nom_extendable"]] = [100.0, False]
    var = M.VARIABLES["pv_capital_cost"]
    assert M.apply_variable(n.copy(), var, 2.0).scaled == 0  # fixed: no effect, refused
    x = M.check_axes({"variable": "pv_capital_cost"}, {"variable": "load"})
    with pytest.raises(M.MapRefused) as exc:
        M.build_map(n, *x, lambda net, arch: None, archs=SA.select(n))
    assert exc.value.code == "variable_not_in_network"
    n.generators.loc["pv", "p_nom_extendable"] = True
    probe = n.copy()
    assert M.apply_variable(probe, var, 2.0).scaled == 1
    assert float(probe.generators.capital_cost["pv"]) == 200.0


def test_load_scales_its_static_and_time_series_values():
    """V3."""
    n = _net()
    n.loads_t.p_set["site_load"] = [1.0, 2.0, 1.0, 0.0]
    probe = n.copy()
    assert M.apply_variable(probe, M.VARIABLES["load"], 2.0).scaled == 2
    assert float(probe.loads.p_set["site_load"]) == 2.0
    assert list(probe.loads_t.p_set["site_load"]) == [2.0, 4.0, 2.0, 0.0]


def test_the_fuel_price_never_touches_a_shortfall_slack():
    """V3: the genset group is the 03a reader's, which leaves slacks out."""
    n = _net()
    n.add(
        "Generator",
        "__voll_site",
        bus="site",
        carrier="diesel",
        p_nom=100.0,
        marginal_cost=10_000.0,
    )
    probe = n.copy()
    assert M.apply_variable(probe, M.VARIABLES["genset_fuel_price"], 2.0).scaled == 1
    assert float(probe.generators.marginal_cost["__voll_site"]) == 10_000.0
    assert float(probe.generators.marginal_cost["genset"]) == 500.0


def test_a_multiplier_makes_a_negative_price_more_negative_and_keeps_zero():
    """V4."""
    n = _net()
    n.generators.loc["grid", "marginal_cost"] = -20.0
    probe = n.copy()
    assert M.apply_variable(probe, M.VARIABLES["grid_energy_price"], 1.5).scaled == 1
    assert float(probe.generators.marginal_cost["grid"]) == -30.0
    n.generators.loc["grid", "marginal_cost"] = 0.0
    assert M.apply_variable(n.copy(), M.VARIABLES["grid_energy_price"], 1.5).scaled == 0


def test_the_solve_budget_boundary_is_120_allowed_121_refused():
    """V5."""
    assert M.solves_refusal(10, 12, 120) is None
    text = M.solves_refusal(11, 11, 120)
    assert text and "121 solves" in text and "120" in text
    assert M.solves_refusal(25, 5, 120) and "125 solves" in M.solves_refusal(25, 5, 120)


def test_a_cell_says_whether_every_architecture_solved():
    """V7: `all_solved` is false when a row is not ok; the gap is null below two ok rows."""
    n = _net()

    def one_fails(net, arch):
        if arch.architecture_id == "genset":
            raise SA.ArchitectureUnsolved("infeasible", "no")
        return _stub(net, arch)

    out = M.build_map(n, *_axes(n), one_fails, archs=SA.select(n))
    assert {c["all_solved"] for c in out["cells"]} == {False}
    ok = M.build_map(n, *_axes(n), _stub, archs=SA.select(["pv", "grid"] and n))
    assert {c["all_solved"] for c in ok["cells"]} == {True}


@pytest.mark.parametrize(
    "x,y,code",
    [
        ({"variable": "bogus"}, {"variable": "load"}, "unknown_variable"),
        ({"variable": "load"}, {"variable": "load"}, "axes_not_different"),
        (
            {"variable": "load", "values": [1.0]},
            {"variable": "genset_fuel_price"},
            "axis_values_invalid",
        ),
        (
            {"variable": "load", "values": [1, 2, 3, 4, 5, 6]},
            {"variable": "genset_fuel_price"},
            "axis_values_invalid",
        ),
        (
            {"variable": "load", "values": [1, 1, 2]},
            {"variable": "genset_fuel_price"},
            "axis_values_invalid",
        ),
        (
            {"variable": "load", "values": [0, 1, 2]},
            {"variable": "genset_fuel_price"},
            "axis_values_invalid",
        ),
        (
            {"variable": "load", "values": [1, float("nan")]},
            {"variable": "genset_fuel_price"},
            "axis_values_invalid",
        ),
        (
            {"variable": "load", "values": [True, 2]},
            {"variable": "genset_fuel_price"},
            "axis_values_invalid",
        ),
        (
            {"variable": "load", "values": [1, 10.5]},
            {"variable": "genset_fuel_price"},
            "axis_values_invalid",
        ),
        (
            {"variable": "load", "values": [1, float("inf")]},
            {"variable": "genset_fuel_price"},
            "axis_values_invalid",
        ),
        (
            {"variable": "load", "values": [-1, 2]},
            {"variable": "genset_fuel_price"},
            "axis_values_invalid",
        ),
    ],
)
def test_bad_axes_are_refused_with_a_kind(x, y, code):
    with pytest.raises(M.MapRefused) as exc:
        M.check_axes(x, y)
    assert exc.value.code == code


def test_ten_times_the_base_is_the_largest_multiplier():
    (_, xv), _ = M.check_axes(
        {"variable": "load", "values": [1, 10]}, {"variable": "genset_fuel_price"}
    )
    assert xv == [1.0, 10.0]


def test_default_values_make_a_three_by_three_grid():
    (_, xv), (_, yv) = M.check_axes(
        {"variable": "load"}, {"variable": "genset_fuel_price"}
    )
    assert len(xv) == len(yv) == 3


def test_five_values_make_a_five_by_five_grid():
    (_, xv), (_, yv) = M.check_axes(
        {"variable": "load", "values": [1, 2, 3, 4, 5]},
        {"variable": "genset_fuel_price", "values": [1, 2, 3, 4, 5]},
    )
    assert len(xv) == len(yv) == 5


def test_the_progress_names_the_cell_and_the_total():
    n = _net()
    seen = []
    M.build_map(
        n,
        *_axes(n),
        lambda net, arch: None,
        archs=SA.select(n)[:1],
        progress=lambda **kw: seen.append(kw["current"]),
    )
    assert seen[0] == "cell 1 of 9" and seen[-1] == "cell 9 of 9" and len(seen) == 9


def test_two_solved_architectures_give_a_gap():
    n = _net()
    two = [a for a in SA.select(n) if a.architecture_id in ("grid", "pv+battery")]
    out = M.build_map(n, *_axes(n), _stub, archs=two)
    assert {c["winner_architecture_id"] for c in out["cells"]} == {"pv+battery"}
    for c in out["cells"]:
        assert c["runner_up_gap_eur_per_year"] == pytest.approx(
            200.0 * [1, 2, 3][c["j"]]
        )
