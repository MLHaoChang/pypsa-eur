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
    n = _solve(_hub(load=10.0), _with([_cut("droop_gain", 15.0, hour=1)]))
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
    _solve(n, _with([_cut("inertia", 25.0, hour=2), _cut("droop_gain", 1.0, hour=3)]))
    rows = {name: c for name, c in n.model.constraints.items() if name.startswith("Island-cut-")}
    assert sorted(rows) == ["Island-cut-droop_gain-2030-3", "Island-cut-inertia-2030-2"]
    assert all(c.data.sizes.get("snapshot", 1) == 1 for c in rows.values())
    assert n._island_facts["cuts"] == {"applied": ["Island-cut-droop_gain-2030-3", "Island-cut-inertia-2030-2"],
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


# ── the primal estimate (Q6): the cheapest candidate that meets the cut ─────


from services.solver.island import cut_estimates  # noqa: E402
from tests.test_island_rows import _a, _genset  # noqa: E402


def _estimate(cut, n=None, units=None):
    n = n if n is not None else _hub(load=10.0)
    island = _seamless(units or _units())
    (row,) = cut_estimates(n, island, [cut])
    return row


def test_the_estimate_is_the_shortfall_over_the_bess_coefficient_times_its_cost():
    """Inertia: ΔM = 25 − 12.5 at 1.25 MW·s/MW is 10 MW at 100/MW = 1000.
    Droop: ΔK = 15 − 5 at 0.5 MW/Hz per MW is 20 MW = 2000. Headroom up:
    12 − 10 = 2 MW = 200."""
    for kind, required, current, mw, cost in (("inertia", 25.0, 12.5, 10.0, 1000.0),
                                              ("droop_gain", 15.0, 5.0, 20.0, 2000.0),
                                              ("headroom_up", 12.0, 10.0, 2.0, 200.0)):
        row = _estimate({**_cut(kind, required), "current": current})
        assert row["candidate"] == "bess", kind
        assert row["added_mw"] == pytest.approx(mw), kind
        assert row["estimate"] == pytest.approx(cost), kind
        assert row["label"].startswith("estimate")


def test_the_cheapest_candidate_wins():
    """A spinning extendable genset at 120/MW gives H/cos φ = 1.5/0.8 =
    1.875 MW·s per MW: 12.5 MW·s of it is 6.667 MW = 800, below the BESS's
    1000."""
    n = _hub(load=10.0)
    n.add("Generator", "g", bus="site", p_nom_extendable=True, capital_cost=120.0, marginal_cost=1000.0)
    units = _units(gensets={"g": _genset(spinning=True, p_min_pu=_a(0.3))})
    row = _estimate({**_cut("inertia", 25.0), "current": 12.5}, n=n, units=units)
    assert row["candidate"] == "g"
    assert row["added_mw"] == pytest.approx(12.5 / 1.875)
    assert row["estimate"] == pytest.approx(800.0)


def test_a_cold_genset_gives_no_inertia_at_the_instant_and_is_no_candidate():
    n = _hub(load=10.0)
    n.add("Generator", "g", bus="site", p_nom_extendable=True, capital_cost=1.0, marginal_cost=1000.0)
    units = _units(gensets={"g": _genset()})               # not spinning
    assert _estimate({**_cut("inertia", 25.0), "current": 12.5}, n=n, units=units)["candidate"] == "bess"


def test_a_met_cut_costs_nothing_and_one_without_a_candidate_says_so():
    assert _estimate({**_cut("inertia", 10.0), "current": 12.5})["estimate"] == 0.0  # see also below
    n = _hub(load=10.0)
    n.storage_units.at["bess", "p_nom_extendable"] = False
    row = _estimate({**_cut("inertia", 25.0), "current": 12.5}, n=n)
    assert row["candidate"] is None and row["estimate"] is None
    assert "no extendable" in row["label"]


# ── the applied cuts ride on the sidecar the solve reads (owner QB) ─────────


import json  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from services.solver import island as island_mod  # noqa: E402
from tests.test_campus_island_service import _config  # noqa: E402


def _project_dir(tmp_path, monkeypatch, cuts=None):
    (tmp_path / "island_config.json").write_text(json.dumps(_config()))
    if cuts is not None:
        (tmp_path / "island_cuts.json").write_text(json.dumps({"cuts": cuts, "trials": []}))
    from services.pypsa_service import PyPSAService
    monkeypatch.setattr(PyPSAService, "solve_target_context",
                        staticmethod(lambda: SimpleNamespace(storage_dir=str(tmp_path))))


def test_the_projects_applied_cuts_reach_the_solve(tmp_path, monkeypatch):
    cuts = [_cut("inertia", 25.0, hour=2)]
    _project_dir(tmp_path, monkeypatch, cuts)
    assert island_mod.load_project_island()["cuts"] == cuts


def test_without_a_cuts_file_the_solve_has_no_cuts(tmp_path, monkeypatch):
    _project_dir(tmp_path, monkeypatch)
    assert island_mod.load_project_island()["cuts"] == []


def test_a_cuts_file_that_is_not_json_is_refused_with_its_name(tmp_path, monkeypatch):
    _project_dir(tmp_path, monkeypatch)
    (tmp_path / "island_cuts.json").write_text("{not json")
    with pytest.raises(IslandRefusal, match="island_cuts.json"):
        island_mod.load_project_island()


def test_the_bundle_carries_the_applied_cuts_and_a_template_ships_none():
    """The cuts change what the project solves, so they travel with it as
    the sidecar does; a template starts without any."""
    from routers.projects import _BUNDLE_FILES, _TEMPLATE_SIDECARS
    assert island_mod.CUTS_FILE == "island_cuts.json"
    assert island_mod.CUTS_FILE in _BUNDLE_FILES
    assert island_mod.CUTS_FILE not in _TEMPLATE_SIDECARS


# ── closing the mutation survivors ──────────────────────────────────────────


def _spinning_genset_hub():
    """The hand hub with a fixed 5 MW spinning genset at 1000/MWh, minimum
    load 0.3 (1.5 MW), and a BESS that cannot charge. The genset cuts the
    10 MW import step to 8.5 and gives 5 − 1.5 = 3.5 MW of upward headroom
    (a constant: its p_nom is fixed), none downward. So the seamless step
    alone buys a 5 MW BESS."""
    n = _hub(load=10.0, gensets=[("g", 5.0)])
    n.storage_units.at["bess", "p_min_pu"] = 0.0
    island = _seamless(_units(gensets={"g": _genset(spinning=True, p_min_pu=_a(0.3))}))
    return n, island


@pytest.mark.parametrize("kind, p_nom", [(None, 5.0), ("headroom_up", 8.5), ("headroom_down", 12.0)])
def test_upward_and_downward_headroom_are_told_apart(kind, p_nom):
    """A 12 MW cut: upward, the genset's 3.5 MW counts, so the BESS needs
    8.5; downward only the BESS absorbs, so it needs 12."""
    n, island = _spinning_genset_hub()
    island["cuts"] = [] if kind is None else [_cut(kind, 12.0, hour=1)]
    _solve(n, island)
    assert _p_nom(n) == pytest.approx(p_nom, abs=TOL)


def test_a_cut_with_dispatch_terms_is_taken_at_its_own_snapshot_only():
    n, island = _spinning_genset_hub()
    island["cuts"] = [_cut("headroom_up", 12.0, hour=1)]
    _solve(n, island)
    c = n.model.constraints["Island-cut-headroom_up-2030-1"]
    assert list(c.data.indexes["snapshot"]) == [n.snapshots[1]]


def test_of_two_cuts_at_one_hour_and_kind_the_tighter_holds():
    n = _solve(_hub(load=10.0), _with([_cut("inertia", 25.0, hour=1), _cut("inertia", 5.0, hour=1)]))
    assert _p_nom(n) == pytest.approx(20.0, abs=TOL)


def test_a_met_cut_names_no_candidate():
    row = _estimate({**_cut("inertia", 10.0), "current": 12.5})
    assert row["candidate"] is None and row["added_mw"] == 0.0
