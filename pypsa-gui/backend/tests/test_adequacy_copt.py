"""
COPT engine (Phase 2 Tasks 1–2): convolution core, hourly screening
adequacy, and the leave-one-out outage-attribution criticality.

Design: spec §§3.1, 3.3, 5.3; plan docs/superpowers/plans/
2026-08-28-fmea-phase2-copt.md. The test standard here is EXACT
hand-computed arithmetic on two-unit systems, not statistical assertions.

The canonical fixture: units 60 MW (q=0.1) and 40 MW (q=0.2), load 70 MW.
Capacity states: 0 (0.02), 40 (0.08), 60 (0.18), 100 (0.72).
  LOLP = P[cap < 70] = 0.28
  EUE/h = 0.02·70 + 0.08·30 + 0.18·10 = 5.6 MWh
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from services.adequacy import copt as C

Q1, CAP1 = 0.1, 60.0
Q2, CAP2 = 0.2, 40.0
LOAD = 70.0
EXACT_LOLP = 0.28
EXACT_EUE_H = 5.6


def _units():
    return [C.CoptUnit(name="u1", capacity_mw=CAP1, q=Q1),
            C.CoptUnit(name="u2", capacity_mw=CAP2, q=Q2)]


# ── the convolution ───────────────────────────────────────────────────────

def test_two_unit_distribution_is_exact():
    dist = C.build_copt(_units(), delta_mw=1.0)
    # {state_mw: probability}; probabilities over the four states.
    assert dist.probability_of(0.0) == pytest.approx(Q1 * Q2)
    assert dist.probability_of(40.0) == pytest.approx(Q1 * (1 - Q2))
    assert dist.probability_of(60.0) == pytest.approx((1 - Q1) * Q2)
    assert dist.probability_of(100.0) == pytest.approx((1 - Q1) * (1 - Q2))
    assert dist.total_probability == pytest.approx(1.0)


def test_survival_function():
    dist = C.build_copt(_units(), delta_mw=1.0)
    # P[available ≥ 70] = P[100] = 0.72; P[available ≥ 41] = P[60]+P[100].
    assert dist.survival(LOAD) == pytest.approx(1 - EXACT_LOLP)
    assert dist.survival(41.0) == pytest.approx((1 - Q1) * Q2 + (1 - Q1) * (1 - Q2))
    assert dist.survival(0.0) == pytest.approx(1.0)


def test_rounding_apportions_and_preserves_the_mean():
    """A 2.5 MW unit on a 1 MW grid must split between the 2 and 3 MW
    states so the expected available capacity is exactly (1−q)·2.5."""
    dist = C.build_copt([C.CoptUnit(name="frac", capacity_mw=2.5, q=0.1)],
                        delta_mw=1.0)
    assert dist.mean() == pytest.approx(0.9 * 2.5)
    assert dist.probability_of(2.0) == pytest.approx(0.45)
    assert dist.probability_of(3.0) == pytest.approx(0.45)


def test_empty_fleet_has_zero_capacity():
    dist = C.build_copt([], delta_mw=1.0)
    assert dist.survival(0.1) == 0.0
    assert dist.probability_of(0.0) == pytest.approx(1.0)


# ── hourly adequacy ───────────────────────────────────────────────────────

def _residual(values, weight=3.0):
    idx = pd.date_range("2030-01-01", periods=len(values), freq="h")
    return pd.Series(values, index=idx), pd.Series(weight, index=idx)


def test_hourly_adequacy_matches_the_exact_case():
    dist = C.build_copt(_units(), delta_mw=1.0)
    residual, w = _residual([LOAD, LOAD])
    out = C.hourly_adequacy(dist, residual, weights=w)
    assert out["lole_hours"] == pytest.approx(EXACT_LOLP * 3.0 * 2)
    assert out["eue_mwh"] == pytest.approx(EXACT_EUE_H * 3.0 * 2)
    assert out["lolp_max"] == pytest.approx(EXACT_LOLP)
    assert out["by_period"]["ALL"]["lole_hours"] == pytest.approx(out["lole_hours"])


def test_hourly_adequacy_zero_and_negative_residual_hours_are_safe():
    dist = C.build_copt(_units(), delta_mw=1.0)
    residual, w = _residual([0.0, -5.0, LOAD])
    out = C.hourly_adequacy(dist, residual, weights=w)
    # Only the third hour contributes.
    assert out["lole_hours"] == pytest.approx(EXACT_LOLP * 3.0)
    assert out["eue_mwh"] == pytest.approx(EXACT_EUE_H * 3.0)


def test_hourly_adequacy_splits_by_period():
    dist = C.build_copt(_units(), delta_mw=1.0)
    base = pd.date_range("2030-01-01", periods=2, freq="h")
    idx = pd.MultiIndex.from_product([[2030, 2035], base],
                                     names=["period", "timestep"])
    residual = pd.Series([LOAD, LOAD, 0.0, LOAD], index=idx)
    w = pd.Series(3.0, index=idx)
    out = C.hourly_adequacy(dist, residual, weights=w)
    assert out["by_period"][2030]["lole_hours"] == pytest.approx(EXACT_LOLP * 6.0)
    assert out["by_period"][2035]["lole_hours"] == pytest.approx(EXACT_LOLP * 3.0)


# ── fleet membership + residual load from a network ───────────────────────

def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.snapshot_weightings.loc[:, :] = 3.0
    n.add("Carrier", "gas"); n.add("Carrier", "wind")
    n.add("Bus", "b", carrier="AC")
    n.add("Bus", "b_h2", carrier="H2")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Load", "l_h2", bus="b_h2", p_set=30.0)
    # Two-state COPT units: explicit occurrence data.
    n.add("Generator", "thermal1", bus="b", carrier="gas", p_nom=CAP1,
          outage_rate_value=Q1, outage_rate_basis="EFORd", mttr_hours=50.0)
    n.add("Generator", "thermal2", bus="b", carrier="gas", p_nom=CAP2,
          outage_rate_value=Q2, outage_rate_basis="EFORd", mttr_hours=40.0)
    # Must-take: wind has NO occurrence data (and deliberately no library
    # default) → netted from load at its hourly availability.
    n.add("Generator", "windfarm", bus="b", carrier="wind", p_nom=50.0)
    n.generators_t.p_max_pu = pd.DataFrame(
        {"windfarm": [0.6, 0.0]}, index=n.snapshots)
    # An H2 generator must be excluded entirely (electrical scope).
    n.add("Generator", "electrolyser_h2", bus="b_h2", carrier="gas", p_nom=10.0,
          outage_rate_value=0.1, outage_rate_basis="FOR", mttr_hours=10.0)
    return n


def test_fleet_and_residual_apply_the_membership_rule():
    units, residual, w = C.fleet_and_residual(_network())
    names = sorted(u.name for u in units)
    assert names == ["thermal1", "thermal2"], names
    # Residual = 100 − wind availability (0.6·50=30, then 0): [70, 100].
    assert list(residual.values) == pytest.approx([70.0, 100.0])
    assert list(w.values) == pytest.approx([3.0, 3.0])


def test_fleet_uses_carrier_defaults_where_asset_data_is_absent():
    n = _network()
    n.add("Generator", "coalplant", bus="b", carrier="coal", p_nom=20.0)
    units, _, _ = C.fleet_and_residual(n)
    from services.adequacy.occurrence import CARRIER_DEFAULTS
    coal = next(u for u in units if u.name == "coalplant")
    assert coal.q == pytest.approx(CARRIER_DEFAULTS["coal"].rate)


# ── solved-capacity semantics (coupling-loop spec §1.1) ───────────────────

def _solved_network(p_nom_opt: float, *, extendable: bool = True) -> pypsa.Network:
    """The two-unit fixture plus ONE candidate whose nameplate and solved size
    disagree — the whole point of §1.1. ``thermal1``/``thermal2`` are the
    untouched control group."""
    n = _network()
    n.add("Generator", "peaker", bus="b", carrier="gas", p_nom=100.0,
          p_nom_extendable=extendable, p_nom_max=200.0,
          outage_rate_value=0.08, outage_rate_basis="EFORd", mttr_hours=20.0)
    n.generators.loc["peaker", "p_nom_opt"] = p_nom_opt
    return n


def test_an_extendable_asset_the_lp_declined_is_not_in_the_fleet():
    """★ §1.1 (plan [B2]). ``p_nom_opt = 0.0`` on an EXTENDABLE row is the LP
    saying "I declined to build this" — the capacity does not exist and the
    COPT must not convolve it in. The old rule took the first finite value
    ``> 0`` from ``(p_nom_opt, p_nom)``, so the zero fell through to the
    pre-solve nameplate and the engine scored a plan containing 100 MW of
    imaginary capacity (optimistic bias, silently).

    BROKEN VARIANT (bite): restore the ``> 0`` fallthrough in
    ``copt._firm_capacity`` (i.e. drop the extendable branch of
    ``solved_capacity``) — ``peaker`` reappears in the fleet at 100 MW.
    """
    units = C.fleet_and_residual(_solved_network(0.0))[0]
    assert sorted(u.name for u in units) == ["thermal1", "thermal2"], \
        [(u.name, u.capacity_mw) for u in units]


def test_a_built_extendable_asset_is_sized_by_the_solve_not_the_nameplate():
    """★ §1.1, the other side of the same rule: a finite ``p_nom_opt`` is
    AUTHORITATIVE, not merely a positivity-gated preference. 37.5 MW built
    against a 100 MW nameplate must be simulated at 37.5.

    BROKEN VARIANT (bite): the same ``> 0`` fallthrough — here it still
    returns 37.5 (it is positive), so this clause alone does not bite; it is
    the ``p_nom_opt = 0.0`` clause above that does. Kept as the pin that the
    fix did not invert the rule.
    """
    units = C.fleet_and_residual(_solved_network(37.5))[0]
    peaker = next(u for u in units if u.name == "peaker")
    assert peaker.capacity_mw == pytest.approx(37.5)


def test_a_non_extendable_row_keeps_the_nameplate_fallback():
    """The ``p_nom`` fallback survives exactly where the plan says it must:
    a NON-extendable row (PyPSA leaves ``p_nom_opt`` at its 0.0 default until
    a solve writes it) must still be its nameplate, or every unsolved network
    would report an empty fleet."""
    units = C.fleet_and_residual(_solved_network(0.0, extendable=False))[0]
    peaker = next(u for u in units if u.name == "peaker")
    assert peaker.capacity_mw == pytest.approx(100.0)


# ── superset fleet (coupling-loop spec §1.2) ──────────────────────────────

def test_keep_zero_capacity_defaults_to_false_and_changes_nothing():
    """§1.2's standing obligation: the flag is opt-in. The default call and
    the explicit ``False`` call must produce the SAME fleet, or every existing
    surface (COPT, MC study, ELCC, the pinned benchmark anchors) is at risk."""
    n = _solved_network(0.0)
    a = C.fleet_and_residual(n)
    b = C.fleet_and_residual(n, keep_zero_capacity=False)
    assert [(u.name, u.capacity_mw, u.q) for u in a[0]] == \
           [(u.name, u.capacity_mw, u.q) for u in b[0]]
    assert list(a[1].values) == pytest.approx(list(b[1].values))


def test_keep_zero_capacity_keeps_the_declined_unit_in_the_fleet_at_zero():
    """★ §1.2 (plan [B1]). ``sample_capacity`` keys each unit's RNG substream
    to its POSITION in the fleet tuple, so a unit entering or leaving between
    iterates shifts every downstream unit's whole outage path. With the flag
    the occurrence-bearing generator that clears every scope test EXCEPT
    ``cap > 0`` stays in the fleet at 0.0 MW — same names, same order,
    stable positions.

    BROKEN VARIANT (bite): revert to dropping ``cap <= 0`` under the flag
    (``if cap <= 0: continue`` unconditionally in ``_membership_walk``) —
    ``peaker`` vanishes and membership is no longer invariant.
    """
    units = C.fleet_and_residual(_solved_network(0.0), keep_zero_capacity=True)[0]
    assert [u.name for u in units] == ["thermal1", "thermal2", "peaker"]
    peaker = next(u for u in units if u.name == "peaker")
    assert peaker.capacity_mw == 0.0
    # It is a real unit, not a placeholder: its outage params came through, so
    # it draws its chain normally and consumes its substream.
    assert peaker.q == pytest.approx(0.08)
    assert peaker.mttr_hours == pytest.approx(20.0)
    # and the untouched units are byte-for-byte the ones the default produces
    built = C.fleet_and_residual(_solved_network(80.0), keep_zero_capacity=True)[0]
    assert [u.name for u in built] == [u.name for u in units]


def test_a_zero_capacity_generator_is_never_a_vre_candidate():
    """★ §1.2's trap. ``must_take_generators`` feeds the ELCC candidate
    picker's ``kind="vre"`` list, so whatever it names the UI may ask for a
    capacity credit on. Two clauses:

    * the zero-capacity OCCURRENCE-BEARING ``peaker`` is a sampled unit, never
      must-take, under EITHER flag value — that one is structural (the
      ``source == "missing"`` branch excludes it whatever the walk yields);
    * ``unbuilt_wind`` is the biteable one: an extendable must-take generator
      the LP declined. Under the superset walk it would be offered as a VRE
      candidate whose profile × 0 MW is the zero series — a capacity credit
      priced on an asset that does not exist. The superset fleet is a SAMPLING
      device; it must not leak into who the UI may ask about.

    BROKEN VARIANT (bite): take the walk at ``keep_zero_capacity=True`` inside
    ``must_take_generators`` — ``unbuilt_wind`` joins the candidate list.
    """
    for opt in (0.0, 80.0):
        n = _solved_network(opt)
        n.add("Generator", "unbuilt_wind", bus="b", carrier="wind", p_nom=40.0,
              p_nom_extendable=True, p_nom_max=200.0)
        n.generators.loc["unbuilt_wind", "p_nom_opt"] = 0.0
        assert C.must_take_generators(n) == ["windfarm"]


# ── outage-attribution criticality (Task 2) ───────────────────────────────

def test_deconvolution_round_trips():
    units = _units()
    dist = C.build_copt(units, delta_mw=1.0)
    without_u2 = C.deconvolve(dist, capacity_mw=CAP2, q=Q2)
    ref = C.build_copt([units[0]], delta_mw=1.0)
    for k in range(len(ref.probs)):
        assert without_u2.probs[k] == pytest.approx(ref.probs[k], abs=1e-9)


def test_perfectly_reliable_unit_attributes_zero():
    units = _units() + [C.CoptUnit(name="perfect", capacity_mw=30.0, q=0.0)]
    dist = C.build_copt(units, delta_mw=1.0)
    residual, w = _residual([LOAD, LOAD])
    rows = C.attribute_criticality(units, dist, residual, weights=w, voll=1000.0)
    perfect = next(r for r in rows if r["name"] == "perfect")
    assert perfect["delta_eue_mwh"] == pytest.approx(0.0, abs=1e-9)


def test_attribution_prices_outages_not_capacity():
    """ΔEUE for unit i = EUE(as-is) − EUE(i perfectly available). For the
    two-unit fixture with load 70:
      u1 perfect: states 60(only? u1=60 always up) → cap 60+{0 or 40}:
        {60: q2=0.2 shortfall 10, 100: 0.8} → EUE/h = 2.0 ⇒ Δu1 = 3.6
      u2 perfect: {40: q1=0.1 shortfall 30, 100: 0.9} → EUE/h = 3.0 ⇒ Δu2 = 2.6
    """
    units = _units()
    dist = C.build_copt(units, delta_mw=1.0)
    residual, w = _residual([LOAD, LOAD])   # weight 3 × 2 snapshots = 6 h
    rows = C.attribute_criticality(units, dist, residual, weights=w, voll=1000.0)
    by = {r["name"]: r for r in rows}
    assert by["u1"]["delta_eue_mwh"] == pytest.approx((EXACT_EUE_H - 2.0) * 6)
    assert by["u2"]["delta_eue_mwh"] == pytest.approx((EXACT_EUE_H - 3.0) * 6)
    # Ranking: u1's outages cost more → first.
    assert rows[0]["name"] == "u1"
    assert by["u1"]["criticality_eur_per_year"] == pytest.approx(
        (EXACT_EUE_H - 2.0) * 6 * 1000.0)


def test_failure_mode_rows_validate_against_the_contract():
    from models.adequacy import FailureModeResult
    n = _network()
    units, residual, w = C.fleet_and_residual(n)
    dist = C.build_copt(units, delta_mw=1.0)
    rows = C.attribute_criticality(units, dist, residual, weights=w, voll=3000.0)
    assert rows, "no FMECA rows produced"
    for r in rows:
        fm = FailureModeResult.model_validate(r["failure_mode"])
        assert fm.engine == "copt"
        assert fm.fidelity == "analytic_convolution"
        assert fm.failure_class == "A"
        assert fm.in_metric_scope is True
        assert fm.criticality_eur_per_year >= 0
        # occurrence = cycle frequency 8760·q/MTTR (occurrence.py semantics).
    t1 = next(r for r in rows if r["name"] == "thermal1")
    assert t1["failure_mode"]["occurrence_per_year"] == pytest.approx(
        8760 * Q1 / 50.0)
    # severity × occurrence = criticality (the f×S factorisation).
    fm1 = t1["failure_mode"]
    assert fm1["severity_eur"] * fm1["occurrence_per_year"] == pytest.approx(
        fm1["criticality_eur_per_year"], rel=1e-9)
