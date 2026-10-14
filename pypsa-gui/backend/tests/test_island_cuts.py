"""
I7 of the campus island plan: frequency cuts back into sizing.

A cut is one failed I6 hour turned into a row of the hub LP: at that
(period, hour), the quantity the I2b rows already build must reach what the
frequency engine says passes (``frequency.cuts``, gridspine):

- ``inertia``: M(t) ≥ required, MW·s, M as in the RoCoF row (2M there);
- ``headroom_up`` / ``headroom_down``: the upward / downward power at 0⁺ of
  the (b↑) / (b↓) rows ≥ required, MW;
- ``droop``: ΣK(t) ≥ required, MW/Hz, ΣK as in the qss row.

The cuts ride on the sidecar the solve reads (``island["cuts"]``). The hand
case: 10 MW import, a GFM droop BESS at 100/MW and nothing else, so the
seamless headroom alone buys p_nom = 10. Per MW the BESS gives
M = τ_f/(2R) = 0.1/(2·0.04) = 1.25 MW·s and K = 1/(R·f0) = 0.5 MW/Hz.
"""
import pytest

from services.solver.island import IslandRefusal
from tests.test_island_rows import TOL, _hub, _p_nom, _seamless, _solve, _units


def _cut(kind, required, hour=0, period=2030, scenario="seamless"):
    return {"period": period, "hour": hour, "scenario": scenario, "kind": kind, "required": required}


def _with(cuts, **kw):
    island = _seamless(_units(), **kw)
    island["cuts"] = list(cuts)
    return island


def test_without_cuts_the_headroom_alone_sizes_the_bess():
    n = _solve(_hub(load=10.0), _with([]))
    assert _p_nom(n) == pytest.approx(10.0, abs=TOL)


def test_an_inertia_cut_buys_the_bess_its_inertia_needs():
    """M ≥ 25 MW·s at 1.25 MW·s per MW: p_nom = 20."""
    n = _solve(_hub(load=10.0), _with([_cut("inertia", 25.0, hour=2)]))
    assert _p_nom(n) == pytest.approx(20.0, abs=TOL)


def test_a_droop_cut_buys_the_bess_its_gain_needs():
    """ΣK ≥ 15 MW/Hz at 0.5 MW/Hz per MW: p_nom = 30."""
    n = _solve(_hub(load=10.0), _with([_cut("droop", 15.0, hour=1)]))
    assert _p_nom(n) == pytest.approx(30.0, abs=TOL)


@pytest.mark.parametrize("kind", ["headroom_up", "headroom_down"])
def test_a_headroom_cut_buys_the_headroom(kind):
    """A BESS that cannot charge (p_min_pu 0), so cyclic storage cannot
    dispatch at all and cannot buy headroom with dispatch: its headroom is
    p_nom both ways, and a 12 MW cut buys 12. (Were it free to charge, the
    cut would cost nothing: charging raises the import, so the seamless step
    grows with the headroom it buys.)"""
    n = _hub(load=10.0)
    n.storage_units.at["bess", "p_min_pu"] = 0.0
    n = _solve(n, _with([_cut(kind, 12.0, hour=1)]))
    assert _p_nom(n) == pytest.approx(12.0, abs=TOL)


def test_a_cut_is_one_row_at_its_own_hour():
    n = _hub(load=10.0)
    _solve(n, _with([_cut("inertia", 25.0, hour=2), _cut("droop", 1.0, hour=3)]))
    rows = {name: c for name, c in n.model.constraints.items() if name.startswith("Island-cut-")}
    assert sorted(rows) == ["Island-cut-droop-2030-3", "Island-cut-inertia-2030-2"]
    assert all(c.data.sizes.get("snapshot", 1) == 1 for c in rows.values())
    assert n._island_facts["cuts"] == {"applied": ["Island-cut-droop-2030-3", "Island-cut-inertia-2030-2"],
                                       "not_applied": []}


def test_the_tightest_cut_wins_and_a_slack_one_changes_nothing():
    n = _solve(_hub(load=10.0), _with([_cut("inertia", 5.0, hour=0), _cut("inertia", 25.0, hour=1)]))
    assert _p_nom(n) == pytest.approx(20.0, abs=TOL)


def test_a_cut_for_another_period_or_scenario_is_kept_out_and_said_so():
    n = _hub(load=10.0)
    _solve(n, _with([_cut("inertia", 1e6, period=2040), _cut("inertia", 1e6, scenario="planned")]))
    assert _p_nom(n) == pytest.approx(10.0, abs=TOL)
    out = n._island_facts["cuts"]
    assert out["applied"] == []
    assert [c["reason"] for c in out["not_applied"]] == [
        "no period 2040 in this solve", "planned: only seamless cuts have rows (the 0⁺ rows I2b builds)"]


def test_a_cut_past_the_periods_hours_is_refused():
    with pytest.raises(IslandRefusal, match="hour 9"):
        _solve(_hub(load=10.0), _with([_cut("inertia", 1.0, hour=9)]))


def test_a_cut_of_an_unknown_kind_is_refused():
    with pytest.raises(IslandRefusal, match="kind"):
        _solve(_hub(load=10.0), _with([_cut("damping", 1.0)]))
