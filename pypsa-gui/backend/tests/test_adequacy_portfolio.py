"""
Phase 12c — the portfolio ELCC, per period, as a second opinion on the
reserve margin (plan 2026-09-03-fmea-phase12c-portfolio-elcc-v3.md, v3.1).

Engine half: hand-built ``MCInputs`` (the ELCC test convention — every
answer is known in closed form from the fleet's 30 MW grid). Every ★ names
its bite; B3 is an anchor inside B1 (the review showed widening the bracket
cannot move a located step edge).

The two-period non-overlap fixture: 20 h per period, flat 250 MW load,
five 30 MW units at q = 0.15 (fleet states on a 30 MW grid), two 100 MW
must-take farms. In 2030 both are on in hours 0–9 (overlap, group peak
200 MW); in 2035 A is on in hours 0–9 and B in 10–19 (no overlap, group
peak 100 MW). Per period the predicate's step edges sit where
``250 − Δ`` crosses a fleet state:

* 2035 — baseline residual 150 every hour; the group removal makes it 250;
  at Δ = 100 the shortfall set is the baseline's exactly, below 100 the
  all-up state (F = 150) is short too. ELCC = **100.0**, the group's
  physical cap, exactly (the bisection's ``hi`` never moves below it).
* 2030 — baseline: hours 0–9 residual 50 (short iff F ≤ 30, ≈ 0.2 %),
  hours 10–19 residual 250 (always short). Reduced: 250 in every hour, so
  ``LOLE_red(Δ) = 20·P̂[F < 250 − Δ]`` against ``10 + 10·P̂[F ≤ 30]``.
  Holds once ``P̂[F ≤ 90] ≤ ~0.5`` (Δ ≥ 130; true probability 0.165) and
  fails at Δ = 100 while ``P̂[F ≤ 120] > ~0.5`` (true 0.556). ELCC = **130**
  to within the 0.5 MW tolerance, on any seed whose empirical
  ``P̂[F ≤ 120]`` exceeds ½ — asserted up front so the test cannot pass
  against a seed that moved the edge.
"""
from __future__ import annotations

import numpy as np
import pytest

from services.adequacy import copt as C
from services.adequacy import elcc as E
from services.adequacy import mc as M
from services.adequacy import portfolio as P

H = 20
LOAD = 250.0
CAP = 100.0
SEED, DRAWS = 0, 128


def _units(n_units=5, q=0.15):
    return tuple(C.CoptUnit(name=f"u{i}", capacity_mw=30.0, q=q, basis="EFORd",
                            mttr_hours=20.0, source="asset") for i in range(n_units))


def _two_period(*, load_2030=LOAD, overlap_2030=True, zero_2030=False,
                b_as_generator_q=None):
    """The fixture above. ``b_as_generator_q`` turns farm B into a profiled
    occurrence unit at that q (B4). ``zero_2030`` makes both farms absent
    in 2030 (B5's no_contribution). ``load_2030`` = 0 gives 2030 no shortfall
    at all (B2)."""
    a = np.zeros(2 * H); b = np.zeros(2 * H)
    if not zero_2030:
        a[0:10] = 1.0
        b[0:10 if overlap_2030 else 10] = 1.0
        if not overlap_2030:
            b[0:10] = 0.0; b[10:20] = 1.0
    a[H + 0:H + 10] = 1.0
    b[H + 10:H + 20] = 1.0
    load = np.concatenate([np.full(H, float(load_2030)), np.full(H, LOAD)])
    units = list(_units())
    profiles = {"a": a * CAP}
    if b_as_generator_q is None:
        profiles["b"] = b * CAP
        residual = load - a * CAP - b * CAP
    else:
        units.append(C.CoptUnit(name="b", capacity_mw=CAP, q=float(b_as_generator_q),
                                basis="EFORd", mttr_hours=20.0, source="asset",
                                profile=b.copy()))
        residual = load - a * CAP
    return M.MCInputs(units=tuple(units), residual=np.ascontiguousarray(residual),
                      weights=np.ones(2 * H), periods=((2030, 0, H), (2035, H, 2 * H)),
                      storage=(), nyears=1.0, vre_profiles=profiles)


def _members(inp, names=("a", "b")):
    unit_names = {u.name for u in inp.units}
    return [P.Member("generator" if nm in unit_names else "vre", nm, CAP) for nm in names]


def _empirical_share(inp, state_mw):
    """P̂[F ≤ state] over the fixture's draws and hours — the seed condition."""
    cap = M.sample_capacity(inp.units, 2 * H, DRAWS, SEED, periods=inp.periods)
    return float((cap <= state_mw + 1e-6).mean())


# ── B1 (+ B3 anchor): per-period portfolio, exact ───────────────────────

def test_B1_the_portfolio_credit_is_priced_per_period_at_the_hand_edges():
    """★ B1. 2035 = 100.0 exactly (the group's cap binds); 2030 = 130 ± tol.
    B3 as an anchor: the 2035 credit never exceeds the period's physical
    maximum. Bite (verified): compare the HORIZON LOLE for every period
    (`_lole_of` ignoring `period`) → the two periods report one number and
    neither equals its hand value."""
    inp = _two_period()
    assert _empirical_share(inp, 120.0) > 0.5, "seed moved the 2030 edge — re-derive"
    rows = {r["period"]: r for r in P.elcc_of_portfolio(
        inp, _members(inp), seed=SEED, draws=DRAWS)}
    assert rows["2035"]["status"] == "ok" and rows["2035"]["elcc_mw"] == 100.0
    assert rows["2035"]["nameplate_mw"] == 100.0
    assert rows["2035"]["elcc_mw"] <= rows["2035"]["nameplate_mw"] + E.default_tol_mw(100.0)
    assert rows["2030"]["status"] == "ok"
    assert rows["2030"]["nameplate_mw"] == 200.0
    assert abs(rows["2030"]["elcc_mw"] - 130.0) <= E.default_tol_mw(200.0), rows["2030"]
    # the baseline LOLE is the period's, not the horizon's
    base = M.mc_adequacy(inp, draws=DRAWS, seed=SEED, cov_target=0.05)
    assert rows["2030"]["baseline_lole_h"] == pytest.approx(base["by_period"][2030]["lole_hours"])
    assert rows["2035"]["baseline_lole_h"] == pytest.approx(base["by_period"][2035]["lole_hours"])


# ── B2: the floor is per period ──────────────────────────────────────────

def test_B2_a_period_with_no_shortfall_is_unidentifiable_on_its_own():
    """★ B2. 2030 at zero load has no shortfall in any draw → `unidentifiable`
    for 2030 while 2035 prices normally. Bite (verified): use the horizon
    floor and LOLE → 2030 is priced off 2035's shortfall."""
    inp = _two_period(load_2030=0.0)
    rows = {r["period"]: r for r in P.elcc_of_portfolio(
        inp, _members(inp), seed=SEED, draws=DRAWS)}
    assert rows["2030"]["status"] == "unidentifiable", rows["2030"]
    assert rows["2030"]["elcc_mw"] is None
    assert rows["2035"]["status"] == "ok" and rows["2035"]["elcc_mw"] == 100.0


# ── B4: mixed kinds ──────────────────────────────────────────────────────

def test_B4_a_mixed_removal_un_nets_the_farm_and_excludes_the_unit():
    """★ B4. Farm B as a profiled occurrence unit at q = 0 contributes the
    same megawatts at the same hours as the must-take farm B, so the mixed
    portfolio (un-net A, exclude B) must equal the all-vre portfolio, row
    for row — deterministically, since a q = 0 unit consumes no substream.
    Bite (verified): drop the `exclude` → B stays in the fleet and the
    credit is A's alone."""
    vre = _two_period()
    mixed = _two_period(b_as_generator_q=0.0)
    rows_v = {r["period"]: r for r in P.elcc_of_portfolio(vre, _members(vre), seed=SEED, draws=DRAWS)}
    rows_m = {r["period"]: r for r in P.elcc_of_portfolio(mixed, _members(mixed), seed=SEED, draws=DRAWS)}
    assert [m.kind for m in _members(mixed)] == ["vre", "generator"]
    for p in ("2030", "2035"):
        assert rows_m[p]["status"] == rows_v[p]["status"] == "ok"
        assert rows_m[p]["elcc_mw"] == rows_v[p]["elcc_mw"], (p, rows_m[p], rows_v[p])
        assert rows_m[p]["nameplate_mw"] == rows_v[p]["nameplate_mw"]


# ── B5: refusals ─────────────────────────────────────────────────────────

def test_B5_an_empty_population_and_a_no_op_period_are_refused_not_priced():
    """★ B5. No members → `no_population` and no bisection; both farms absent
    in 2030 → that period is `no_contribution` (a no-op removal) while 2035
    prices. Bite (verified): pass through to `elcc_of_removal(nameplate=0)`
    → `ok 0.0`."""
    inp = _two_period()
    calls = []
    orig = M.mc_adequacy

    def spy(*a, **kw):
        calls.append(1)
        return orig(*a, **kw)

    import services.adequacy.elcc as EM
    import services.adequacy.portfolio as PM
    EM.mc_adequacy, PM_orig = spy, None  # noqa: F841
    try:
        block = P.portfolio_block(
            inp, {"members": [], "unbuilt": ["a", "b"], "snapshot_names": {"a", "b"}},
            margin_payload=None, snapshot_fingerprint="x", seed=SEED, draws=DRAWS,
            cov_target=0.05, baseline=None, baseline_key=None)
    finally:
        EM.mc_adequacy = orig
    assert block["status"] == "no_population"
    assert block["periods"] == [] and calls == []
    with pytest.raises(ValueError):
        P.elcc_of_portfolio(inp, [], seed=SEED, draws=DRAWS)

    z = _two_period(zero_2030=True)
    rows = {r["period"]: r for r in P.elcc_of_portfolio(z, _members(z), seed=SEED, draws=DRAWS)}
    assert rows["2030"]["status"] == "no_contribution"
    assert rows["2030"]["elcc_mw"] is None and rows["2030"]["nameplate_mw"] == 0.0
    assert rows["2035"]["status"] == "ok" and rows["2035"]["elcc_mw"] == 100.0


# ── B7: the injected baseline ────────────────────────────────────────────

def test_B7_an_injected_baseline_is_bit_identical_and_a_wrong_key_raises():
    """★ B7. Rows priced against the caller's `mc_adequacy` (the `/mc`
    worker's headline) are bit-identical to rows that computed their own;
    a key from a different seed raises rather than running the replay
    against another sample set. Bite (verified): drop the key comparison."""
    inp = _two_period()
    base = M.mc_adequacy(inp, draws=DRAWS, seed=SEED, cov_target=0.05)
    key = E.baseline_key(inp, draws=DRAWS, seed=SEED, cov_target=0.05,
                         max_draws=M.MAX_DRAWS, batch=250)
    own = P.elcc_of_portfolio(inp, _members(inp), seed=SEED, draws=DRAWS)
    inj = P.elcc_of_portfolio(inp, _members(inp), seed=SEED, draws=DRAWS,
                              baseline=base, baseline_key=key)
    assert inj == own
    bad = E.baseline_key(inp, draws=DRAWS, seed=SEED + 1, cov_target=0.05,
                         max_draws=M.MAX_DRAWS, batch=250)
    with pytest.raises(ValueError):
        P.elcc_of_portfolio(inp, _members(inp), seed=SEED, draws=DRAWS,
                            baseline=base, baseline_key=bad)
    # the key covers the sim kwargs too (v3.1 A7)
    assert E.baseline_key(inp, draws=DRAWS, seed=SEED, cov_target=0.05,
                          max_draws=M.MAX_DRAWS, batch=250,
                          sim_kwargs={"initial_soc_frac": 0.5}) != key


# ── B11: the population ──────────────────────────────────────────────────

def test_B11_the_population_is_the_informative_series_rule_on_both_halves():
    """★ B11. On the 12c-pre membership fixture the portfolio is
    {hydro_const, wind_for, wind_mt}: a constant 0.8 series (occurrence
    unit), a varying series with outage data, and a varying must-take farm.
    Not gas_static (static column only), not gas_ones (all-ones column).
    Bite (verified): admit any must-take with a column → gas_ones-like
    must-takes and static-only farms enter."""
    from tests.test_adequacy_profiled_units import _m1_network

    n = _m1_network()
    # Two must-takes the plan's filter must keep OUT (v3 review, finding 3):
    # an all-ones column and a static-only value. Both remain ELCC
    # candidates on their own (`snapshot_inputs` builds a profile for either)
    # but neither carries an informative SERIES.
    n.add("Carrier", "solar")
    n.add("Generator", "solar_ones", bus="b", carrier="solar", p_nom=40.0, marginal_cost=0.0)
    n.generators_t.p_max_pu["solar_ones"] = np.ones(len(n.snapshots))
    n.add("Generator", "solar_static", bus="b", carrier="solar", p_nom=30.0,
          marginal_cost=0.0, p_max_pu=0.6)
    from services.adequacy.copt import must_take_generators
    must_take = must_take_generators(n)
    assert {"solar_ones", "solar_static"} <= set(must_take)
    inp = M.snapshot_inputs(n, vre_assets=must_take)
    assert {"solar_ones", "solar_static"} <= set(inp.vre_profiles)
    pop = P.portfolio_population(n, inp)
    assert {m.name for m in pop["members"]} == {"hydro_const", "wind_for", "wind_mt"}
    kinds = {m.name: m.kind for m in pop["members"]}
    assert kinds == {"hydro_const": "generator", "wind_for": "generator", "wind_mt": "vre"}
    assert pop["unbuilt"] == []


# ═══ the block against a REAL margin payload (B6, B10, B12, B13) ═════════

def _solved(n, **cfg_kw):
    """A real solve (HiGHS) through `run_simulation`; returns the sink."""
    from tests.test_adequacy_reserve_margin import _solve
    sink, status, cond = _solve(n, **cfg_kw)
    assert status in ("ok", "optimal", "completed"), (status, cond)
    return sink


def _block_for(n, sink, *, cfg=None, draws=32, seed=1):
    from services.adequacy.copt import must_take_generators
    inp = M.snapshot_inputs(n, vre_assets=must_take_generators(n), cfg=cfg)
    pop = P.portfolio_population(n, inp)
    return P.portfolio_block(
        inp, pop, margin_payload=sink.get("last_reserve_margin"),
        snapshot_fingerprint=P.network_fingerprint(n), seed=seed, draws=draws,
        cov_target=1.0, baseline=None, baseline_key=None), pop


def test_B6_a_vintage_plan_passes_the_by_parent_capacity_check():
    """★ B6 (v3 review, finding 4 — the real vintage shape). After the
    vintage solve the payload carries `wind: 0` + `wind@2030: 70` +
    `wind@2040: 0` while `solved_capacity(wind)` reads the parent's
    aggregated `p_nom_opt` = 70. Compared BY PARENT AGGREGATE per period the
    two agree and the block is `ok`; compared member-by-member it would
    refuse every vintage network for the wrong reason. Bite (verified):
    compare the parent row alone (0 ≠ 70) → `capacity_basis_mismatch`."""
    from tests.test_adequacy_reserve_margin import _vintage_network
    from services.solver_service import SolverConfig
    n = _vintage_network(alternating=True)
    sink = _solved(n, reserve_margin=0.5, multi_investment_periods=True)
    block, pop = _block_for(n, sink, cfg=SolverConfig(multi_investment_periods=True))
    names = {m.name: m for m in pop["members"]}
    assert "wind" in names and names["wind"].capacity_mw == pytest.approx(70.0, rel=1e-6)
    assert block["status"] == "ok", block["reason"]
    rows = {r["period"]: r for r in block["periods"]}
    assert set(rows) == {"2030", "2040"}
    assert block["margin_available"] is True


def test_B10_the_credits_are_the_margin_payloads_own_rows_for_the_members():
    """★ B10. `credit_gross_mw` per period equals Σ derate × capacity over
    the payload rows whose PARENT is a member — recomputed here from the
    served payload — and differs from the sum over every row (the thermal
    fleet is credited too but is not in the portfolio). `credit_net_mw` is
    null unless the period's net window is `ok`. Bite (verified): sum every
    row."""
    from tests.test_adequacy_reserve_margin import _vintage_network
    from services.solver_service import SolverConfig
    n = _vintage_network(alternating=True)
    sink = _solved(n, reserve_margin=0.5, multi_investment_periods=True)
    payload = sink["last_reserve_margin"]
    block, pop = _block_for(n, sink, cfg=SolverConfig(multi_investment_periods=True))
    members = {m.name for m in pop["members"]}
    for r in block["periods"]:
        P_ = r["period"]
        mine = [a for a in payload["assets"] if str(a["period"]) == P_
                and a["kind"] == "generator" and P._parent(a["name"]) in members]
        every = [a for a in payload["assets"] if str(a["period"]) == P_
                 and a["kind"] == "generator"]
        want = sum(float(a["derate"]) * float(a["capacity_mw"] or 0.0) for a in mine)
        wrong = sum(float(a["derate"]) * float(a["capacity_mw"] or 0.0) for a in every)
        assert r["credit_gross_mw"] == pytest.approx(want), (P_, r)
        assert wrong != pytest.approx(want)
        nw = next(b for b in payload["by_period"] if str(b["period"]) == P_)["net_window"]
        if nw["status"] == "ok":
            assert r["credit_net_mw"] is not None
        else:
            assert r["credit_net_mw"] is None


def test_B12_a_member_the_margin_masks_out_of_a_period_is_an_activity_mismatch():
    """★ B12 (v3 review, finding 2). A farm with `build_year=2035` is masked
    out of the 2030 margin but netted into the 2030 residual by the engines
    (which ignore activity). The block refuses with `activity_mismatch`
    naming the farm and the period, and prices nothing. Bite (verified):
    drop the membership check → the comparison runs on unequal populations."""
    from tests.test_adequacy_demand_basis import two_period_network
    from services.solver_service import SolverConfig
    n = two_period_network(wind_build_year=2035)
    sink = _solved(n, reserve_margin=0.15, multi_investment_periods=True)
    block, _pop = _block_for(n, sink, cfg=SolverConfig(multi_investment_periods=True))
    assert block["status"] == "activity_mismatch", block
    assert "wind" in block["reason"] and "2030" in block["reason"]
    assert block["periods"] == []


def test_B13_a_network_edited_after_the_solve_is_a_stale_report():
    """★ B13. The margin payload carries the network fingerprint stamped at
    the report step; editing `p_max_pu` afterwards changes the snapshot's
    and the block refuses with `stale_report`. Bite (verified): drop the
    fingerprint comparison."""
    from tests.test_adequacy_demand_basis import two_period_network
    from services.solver_service import SolverConfig
    n = two_period_network()
    sink = _solved(n, reserve_margin=0.15, multi_investment_periods=True)
    assert sink["last_reserve_margin"]["fingerprint"] == P.network_fingerprint(n)
    block, _pop = _block_for(n, sink, cfg=SolverConfig(multi_investment_periods=True))
    assert block["status"] == "ok", block["reason"]
    n.generators_t.p_max_pu["wind"] = n.generators_t.p_max_pu["wind"] * 0.9
    block2, _pop = _block_for(n, sink, cfg=SolverConfig(multi_investment_periods=True))
    assert block2["status"] == "stale_report", block2
    assert block2["periods"] == []


def test_a_solve_without_a_margin_prices_the_portfolio_and_says_so():
    """The last solve set no margin: the ELCC rows still run, the credits
    are null, and the block says `margin_unavailable` rather than inventing
    a comparison."""
    from tests.test_adequacy_demand_basis import two_period_network
    from services.solver_service import SolverConfig
    n = two_period_network()
    sink = _solved(n, multi_investment_periods=True)
    assert sink.get("last_reserve_margin") is None
    block, _pop = _block_for(n, sink, cfg=SolverConfig(multi_investment_periods=True))
    assert block["status"] == "margin_unavailable" and block["margin_available"] is False
    assert len(block["periods"]) == 2
    assert all(r["credit_gross_mw"] is None and r["credit_net_mw"] is None
               for r in block["periods"])
    assert all(r["status"] in ("ok", "unidentifiable", "not_bracketed", "no_contribution")
               for r in block["periods"])


# ═══ the route (B8, B9) ══════════════════════════════════════════════════

def test_B9_the_route_prices_the_portfolio_only_when_asked(client, install_network):
    """★ B9. `elcc_portfolio: true` → the block is present with one row per
    period; absent → `null`; the POST echoes the flag. Bite (verified):
    always compute."""
    from tests.test_adequacy_copt import _network as _copt_network
    from tests.test_adequacy_mc_endpoint import COV, DRAWS, SEED, _poll
    # the COPT fixture: two thermal units plus a must-take farm with a
    # varying column — the endpoint module's own fixture has no farm.
    install_network(_copt_network())
    r = client.post("/api/results/mc", json={"draws": DRAWS, "seed": SEED, "cov_target": COV})
    assert r.status_code == 200 and r.json()["elcc_portfolio"] is False
    body = _poll(client)
    assert body["status"] == "done" and body["result"]["elcc_portfolio"] is None

    r = client.post("/api/results/mc", json={"draws": DRAWS, "seed": SEED, "cov_target": COV,
                                             "elcc_portfolio": True})
    assert r.status_code == 200 and r.json()["elcc_portfolio"] is True
    body = _poll(client)
    assert body["status"] == "done", body
    block = body["result"]["elcc_portfolio"]
    assert block is not None
    # the copt fixture's wind farm is must-take with a varying column → the
    # population is that farm; no solve ran, so the margin is unavailable.
    assert [m["name"] for m in block["population"]["members"]] == ["windfarm"]
    assert block["status"] == "margin_unavailable"
    assert [r_["period"] for r_ in block["periods"]] == ["ALL"]
    assert block["periods"][0]["status"] in ("ok", "unidentifiable", "not_bracketed")


def test_B8_the_portfolio_row_is_never_in_the_elcc_list(client, install_network):
    """★ B8. A consumer summing `elcc_mw` over `result["elcc"]` gets the
    member rows only; the portfolio is a SIBLING key. Bite (verified):
    append the block's rows to `rows`."""
    from tests.test_adequacy_copt import _network as _copt_network
    from tests.test_adequacy_mc_endpoint import COV, DRAWS, SEED, _poll
    # the COPT fixture: two thermal units plus a must-take farm with a
    # varying column — the endpoint module's own fixture has no farm.
    install_network(_copt_network())
    r = client.post("/api/results/mc", json={
        "draws": DRAWS, "seed": SEED, "cov_target": COV, "elcc_portfolio": True,
        "elcc_assets": [{"kind": "vre", "name": "windfarm"}]})
    assert r.status_code == 200, r.text
    body = _poll(client)
    assert body["status"] == "done", body
    res = body["result"]
    assert [row["name"] for row in res["elcc"]] == ["windfarm"]
    assert all("period" not in row for row in res["elcc"])
    assert res["elcc_portfolio"]["periods"][0]["period"] == "ALL"
