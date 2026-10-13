"""
I2c of the campus island plan (2026-10-07): linearised unit commitment,
the islanding variants as separate solves, and the cost of island
capability (``services/solver/island.py``).

Every case is a tiny hand network (the I2a/I2b fixtures) with the answer
worked by hand:

- linearised UC commits the fixed-size gensets; their online capacity
  u_g(t)·p_nom_g replaces the ``spinning`` flag at the instant of islanding
  (headroom, RoCoF inertia, GFM MVA at 0⁺), and PyPSA's own
  p ≥ u·p_min_pu·p_nom makes it cost minimum-load fuel;
- ``island_study`` solves the project without the island rows and once per
  scenario, and reports each cost, their differences, and the binding rows
  with their hours and duals.
"""
from __future__ import annotations

import pytest

from gridspine.schema.island import validate_island_config
from services.solver.island import (
    IslandRefusal,
    _wrap_with_island,
    island_extra_functionality,
    island_study,
    linearised_uc,
)
from services.solver_service import SolverConfig
from tests.test_island_rows import (
    SLACK,
    TOL,
    _VSM,
    T,
    _a,
    _extendable_genset,
    _genset,
    _hub,
    _island,
    _net,
    _p_nom,
    _run,
    _seamless,
    _units,
)


def _uc(units, **kw):
    """A seamless sidecar with linearised UC on."""
    island = _seamless(units, **kw)
    island["requirements"]["linearised_uc"] = True
    return island


def _solve_uc(n, island, **kw):
    with linearised_uc(n, island) as uc_kw:
        status, cond = n.optimize(
            solver_name="highs", extra_functionality=island_extra_functionality(island, **kw),
            **uc_kw)
    assert (status, cond) == ("ok", "optimal")
    return n


def _status(n, g="g"):
    return n.generators_t.status[g]


def _committed_genset(**extra):
    return _genset(p_min_pu=_a(0.3), **extra)


# ── linearised UC (Q7) ──────────────────────────────────────────────────────


def test_linearised_uc_buys_exactly_the_online_capacity_headroom_needs():
    """10 MW load, the BESS capped at 7 MW. A committed 6 MW genset at
    p_min_pu 0.3 runs at p ≥ 1.8·u, which the import no longer carries, so
    the step is 10 − p and the row is 7 + 6u − p ≥ 10 − p: u = 1/2, and it
    burns its minimum load p = 0.3·6·u = 0.9 MW."""
    n = _hub(load=10.0, gensets=[("g", 6.0)])
    n.storage_units.at["bess", "p_nom_max"] = 7.0
    n = _solve_uc(n, _uc(_units(gensets={"g": _committed_genset()})))
    assert _p_nom(n) == pytest.approx(7.0, abs=TOL)
    assert _status(n).to_numpy() == pytest.approx([0.5] * 4, abs=TOL)
    assert n.generators_t.p["g"].to_numpy() == pytest.approx([0.9] * 4, abs=TOL)
    uc = n._island_facts["uc"]
    assert uc["committed"] == ["g"] and uc["status"].startswith("linearised")


def test_without_uc_the_genset_must_run_to_cut_the_step():
    """The same case with UC off: the cold genset is not online at 0⁺, so it
    can only help by running 3 MW to cut the import to the 7 MW the BESS
    covers. UC buys the same cover at 0.9 MW of minimum load, cheaper."""
    def case():
        n = _hub(load=10.0, gensets=[("g", 6.0)])
        n.storage_units.at["bess", "p_nom_max"] = 7.0
        return n
    cold = case()
    status, _ = cold.optimize(solver_name="highs", extra_functionality=island_extra_functionality(
        _seamless(_units(gensets={"g": _committed_genset()}))))
    assert status == "ok"
    assert cold.generators_t.p["g"].to_numpy() == pytest.approx([3.0] * 4, abs=TOL)
    warm = _solve_uc(case(), _uc(_units(gensets={"g": _committed_genset()})))
    # 4 h × (3 − 0.9) MW × (1000 − 10) of fuel over import.
    assert cold.objective - warm.objective == pytest.approx(4 * 2.1 * 990.0, abs=1e-3)


def test_a_committed_gensets_headroom_is_scaled_by_its_availability():
    """p_max_pu 0.5: online capacity is u·0.5·6, so with the BESS capped at
    8 MW the row 8 + 3u − p ≥ 10 − p gives u = 2/3 (1/3 if p_max_pu were
    ignored)."""
    n = _hub(load=10.0, gensets=[("g", 6.0)])
    n.generators.at["g", "p_max_pu"] = 0.5
    n.storage_units.at["bess", "p_nom_max"] = 8.0
    n = _solve_uc(n, _uc(_units(gensets={"g": _committed_genset()})))
    assert _status(n).to_numpy() == pytest.approx([2 / 3] * 4, abs=TOL)


def test_linearised_uc_counts_the_committed_inertia_in_rocof():
    """4 MW load at 1 Hz/s: 2M ≥ 50·ΔP⁺. A VSM BESS (H_v 4 s, pf 1) capped at
    23.5 MW gives 188; a committed 6 MW genset (H 1.5 s, cos φ 0.8) adds
    2·1.5·6/0.8·u = 22.5·u and, at its minimum load p = 1.8·u, cuts the step
    to 4 − 1.8·u. So 188 + 22.5·u ≥ 200 − 90·u: u = 12/112.5 = 8/75."""
    n = _hub(load=4.0, gensets=[("g", 6.0)])
    n.storage_units.at["bess", "p_nom_max"] = 23.5
    n = _solve_uc(n, _uc(_units(bess=_VSM, gensets={"g": _committed_genset()}), rocof=1.0))
    assert _status(n).to_numpy() == pytest.approx([8 / 75] * 4, abs=TOL)


def test_linearised_uc_counts_the_committed_mva_at_the_instant():
    """10 MW critical load at a 0 margin, the step overridden to 0: the BESS
    (pf 0.95) capped at 4.75 MW gives 5 MVA, and the committed genset adds
    u·6/0.8 = 7.5·u, so u = 2/3."""
    n = _hub(crit=10.0, gensets=[("g", 6.0)])
    n.storage_units.at["bess", "p_nom_max"] = 4.75
    n = _solve_uc(n, _uc(_units(gensets={"g": _committed_genset()})), delta_p_plus=0.0)
    assert _status(n).to_numpy() == pytest.approx([2 / 3] * 4, abs=TOL)


def test_a_committed_genset_takes_its_minimum_load_from_pypsa_not_the_spinning_row():
    """UC replaces the spinning flag: no Island-min-load row, and PyPSA's own
    p ≥ u·p_min_pu·p_nom holds instead."""
    n = _hub(load=10.0, gensets=[("g", 6.0)])
    n.storage_units.at["bess", "p_nom_max"] = 7.0
    n = _solve_uc(n, _uc(_units(gensets={"g": _committed_genset(spinning=True)})))
    assert not any(c.startswith("Island-min-load") for c in n.model.constraints)
    assert _status(n).to_numpy() == pytest.approx([0.5] * 4, abs=TOL)


def test_the_sidecar_p_min_pu_is_written_for_the_solve_and_restored():
    n = _hub(load=10.0, gensets=[("g", 6.0)])
    n.storage_units.at["bess", "p_nom_max"] = 7.0
    _solve_uc(n, _uc(_units(gensets={"g": _committed_genset()})))
    assert not bool(n.generators.at["g", "committable"])
    assert float(n.generators.at["g", "p_min_pu"]) == 0.0


def test_a_committed_genset_needs_a_minimum_load():
    """Without p_min_pu a committed genset's headroom is free (review B4)."""
    n = _hub(load=10.0, gensets=[("g", 6.0)])
    with pytest.raises(IslandRefusal, match="p_min_pu"):
        with linearised_uc(n, _uc(_units(gensets={"g": _genset()}))):
            pass


def test_an_extendable_genset_is_not_committed():
    """PyPSA models committable-extendable units with big-M, so u·p_nom is
    bilinear: an extendable genset is never committed, and the facts say so.
    It may still be spinning (I2b)."""
    n = _hub(load=10.0, gensets=[("g", 6.0)])
    _extendable_genset(n, "gx", p_nom_max=10.0)
    units = _units(gensets={"g": _committed_genset(),
                            "gx": _committed_genset(unit_mw=_a(5.0), spinning=True)})
    n = _solve_uc(n, _uc(units))
    uc = n._island_facts["uc"]
    assert uc["committed"] == ["g"]
    assert "extendable" in uc["not_committed"]["gx"]
    assert "Island-min-load-gx" in n.model.constraints
    assert not bool(n.generators.at["gx", "committable"])


def test_uc_on_without_a_committed_genset_is_refused_at_the_rows():
    """The rows read u from the model: a fixed genset left uncommitted (the
    solve did not go through ``linearised_uc``) is a refusal, not a silent
    fall-back to the spinning flag."""
    n = _hub(load=10.0, gensets=[("g", 6.0)])
    with pytest.raises(IslandRefusal, match="committ"):
        n.optimize(solver_name="highs", extra_functionality=island_extra_functionality(
            _uc(_units(gensets={"g": _committed_genset()}))))


def test_a_committed_link_genset_counts_its_electrical_output():
    """A Link genset (fuel → site, η 0.4, p_nom 15 on the fuel side = 6 MW
    out): u·6 of online output, as for the Generator, so u = 1/2."""
    n = _hub(load=10.0)
    n.add("Bus", "fuel")
    n.add("Generator", "diesel", bus="fuel", p_nom=1000.0, marginal_cost=400.0)
    n.add("Link", "g", bus0="fuel", bus1="site", p_nom=15.0, efficiency=0.4)
    n.links.at["g", "eh_role"] = ""
    n.storage_units.at["bess", "p_nom_max"] = 7.0
    n = _solve_uc(n, _uc(_units(gensets={"g": _committed_genset()})))
    assert n.links_t.status["g"].to_numpy() == pytest.approx([0.5] * 4, abs=TOL)


@pytest.mark.parametrize("strategy,sclopf", [("rolling", False), ("full", True),
                                             ("myopic", False)])
def test_uc_on_rolling_scopf_and_myopic_is_refused_with_a_reason(strategy, sclopf,
                                                                  monkeypatch):
    from services.solver import island as island_mod
    monkeypatch.setattr(island_mod, "_effective_path",
                        lambda n, cfg: {"solve_strategy": strategy, "sclopf": sclopf})
    with pytest.raises(IslandRefusal, match="linearised unit commitment"):
        _wrap_with_island(_net(), None, SolverConfig(island_constraints=True),
                          island=_uc(_units()))


def test_run_simulation_passes_linearised_uc_and_restores_the_network(monkeypatch):
    n = _hub(load=10.0, gensets=[("g", 6.0)])
    n.storage_units.at["bess", "p_nom_max"] = 7.0
    island = _uc(_units(gensets={"g": _committed_genset()}))
    status, condition, _ = _run(n, SolverConfig(island_constraints=True), monkeypatch, island)
    assert status in ("ok", "optimal"), condition
    assert _status(n).to_numpy() == pytest.approx([0.5] * 4, abs=TOL)
    assert not bool(n.generators.at["g", "committable"])
    assert float(n.generators.at["g", "p_min_pu"]) == 0.0


# ── the variants and the cost of island capability ─────────────────────────


def _both(units, **kw):
    """Both unplanned scenarios; blocks small enough that a 20 MW genset
    passes ups_bridged on its own."""
    return _island(units=units, k=0, scenarios=("ups_bridged", "seamless"), blocks=[1.0],
                   sustained_h=1.0, bridge_s=1.0, **{**SLACK, **kw})


def test_the_cost_of_seamless_is_the_extra_bess_headroom():
    """10 MW import, a cold 20 MW genset. ups_bridged: the genset carries the
    1 MW block and no BESS is bought. seamless: the BESS needs 10 MW of
    headroom at 100/MW, so seamless costs 1000 more."""
    n = _hub(load=10.0, gensets=[("g", 20.0)])
    out = island_study(n, _both(_units(gensets={"g": _genset()})))
    v = out["variants"]
    assert v["ups_bridged"]["objective"] == pytest.approx(v["baseline"]["objective"], abs=TOL)
    assert out["cost_of_island_capability"]["ups_bridged"] == pytest.approx(0.0, abs=TOL)
    assert out["cost_of_island_capability"]["seamless"] == pytest.approx(1000.0, abs=TOL)
    assert out["cost_of_seamless"] == pytest.approx(1000.0, abs=TOL)
    assert v["seamless"]["facts"]["instant"]["scenarios"] == ["seamless"]
    assert _p_nom(n) == 0.0                       # the study solves copies


def test_the_cost_of_island_capability_is_the_reserve():
    """The I2a energy case: 20 MW of BESS at 100/MW that the unconstrained
    solve does not buy."""
    out = island_study(_net(), _island())
    assert out["cost_of_island_capability"]["planned"] == pytest.approx(2000.0, abs=TOL)
    assert out["cost_of_seamless"] is None


def test_binding_rows_report_their_hours_and_duals():
    """The seamless headroom row binds, and its duals sum to the BESS's
    capital cost (p_nom sits in every hour's row; the LP may put the whole
    dual on one hour, since every hour asks for the same 10 MW)."""
    n = _hub(load=10.0, gensets=[("g", 20.0)])
    out = island_study(n, _both(_units(gensets={"g": _genset()})))
    rows = {r["row"]: r for r in out["variants"]["seamless"]["binding"]}
    up = rows["Island-headroom-up"]
    assert up["hours"] and len(up["hours"]) == len(up["duals"])
    assert sum(up["duals"]) == pytest.approx(100.0, abs=TOL)
    assert all(not r["row"].startswith("Island-headroom")
               for r in out["variants"]["ups_bridged"]["binding"])


def test_a_static_row_reports_its_dual_without_hours():
    """ups_bridged pickup against an extendable genset: Σ ls·p_nom ≥ block is
    one static row; its dual is the genset's capital cost per MW of block."""
    n = _hub(load=1.0)
    _extendable_genset(n, "gx", p_nom_max=100.0)
    units = _units(bess=None, gensets={"gx": _genset(unit_mw=_a(50.0))})
    island = _island(units=units, k=0, scenarios=("ups_bridged",), blocks=[10.0],
                     sustained_h=1.0, bridge_s=1.0, **SLACK)
    out = island_study(n, island)
    rows = {r["row"]: r for r in out["variants"]["ups_bridged"]["binding"]}
    assert rows["Island-pickup"]["hours"] is None
    # ls 50 %: 0.5·p ≥ 10 → p = 20 at capital 1/MW, so the dual is 2.
    assert rows["Island-pickup"]["duals"] == pytest.approx([2.0], abs=TOL)
    assert out["cost_of_island_capability"]["ups_bridged"] == pytest.approx(20.0, abs=TOL)


def test_a_refused_variant_is_reported_and_has_no_cost():
    """seamless without a tagged import Link is refused; the study records
    the reason and keeps the other variant."""
    n = _net()
    island = _island(scenarios=("planned", "seamless"), **SLACK)
    out = island_study(n, island)
    assert out["variants"]["seamless"]["status"] == "refused"
    assert "grid_import" in out["variants"]["seamless"]["reason"]
    assert "seamless" not in out["cost_of_island_capability"]
    assert out["cost_of_island_capability"]["planned"] == pytest.approx(2000.0, abs=TOL)


def test_the_study_can_compare_scenarios_the_sidecar_does_not_ship():
    n = _hub(load=10.0, gensets=[("g", 20.0)])
    island = _island(units=_units(gensets={"g": _genset()}), k=0, scenarios=("ups_bridged",),
                     blocks=[1.0], sustained_h=1.0, bridge_s=1.0, **SLACK)
    out = island_study(n, island, scenarios=("ups_bridged", "seamless"))
    assert out["cost_of_seamless"] == pytest.approx(1000.0, abs=TOL)


def test_the_template_cost_of_island_capability():
    """The Data Center template ships ups_bridged. With no island rows it
    solves at 3.754e7 and buys no genset_new; with the ups_bridged rows
    genset_new is 36.7 MW and the objective 3.981e7 (I2b as built; its
    3.882e7 was the I2a rows alone, which is the planned variant)."""
    island = validate_island_config(T.ISLAND_CONFIG["eh_datacenter"])
    out = island_study(T.build_eh_datacenter(), island)
    v = out["variants"]
    assert v["baseline"]["objective"] == pytest.approx(3.754e7, rel=1e-3)
    assert v["ups_bridged"]["objective"] == pytest.approx(3.981e7, rel=1e-3)
    assert out["cost_of_island_capability"]["ups_bridged"] == pytest.approx(
        v["ups_bridged"]["objective"] - v["baseline"]["objective"])
    assert "Island-rocof-block" in {r["row"] for r in v["ups_bridged"]["binding"]}


def test_the_template_cost_of_seamless_with_the_bess_cap_lifted():
    """seamless runs the site on its gensets: about 6.87e7 against 3.981e7."""
    n = T.build_eh_datacenter()
    n.storage_units.at["bess_new", "p_nom_max"] = 1e4
    island = validate_island_config(T.ISLAND_CONFIG["eh_datacenter"])
    out = island_study(n, island, scenarios=("ups_bridged", "seamless"))
    assert out["variants"]["seamless"]["objective"] == pytest.approx(6.87e7, rel=5e-3)
    assert out["cost_of_seamless"] == pytest.approx(6.87e7 - 3.981e7, rel=1e-2)
