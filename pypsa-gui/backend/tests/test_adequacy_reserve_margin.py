"""
The firm-capacity (planning reserve margin) constraint — Phase 8, §§1, 2, 5.

Spec: docs/superpowers/specs/2026-08-29-reserve-margin-spec.md (BINDING).
Plan: docs/superpowers/plans/2026-08-29-fmea-phase8-reserve-margin.md v2.

Per active investment period P the wrapper adds ONE constraint

    Σ d_g·P_g (extendable, LP var) + Σ d_g·p_nom_g (fixed, constant)
        + storage terms                ≥  (1 + m) · peak_P

with `d_g = (1 − q_g) × availability_g` derated from the SAME occurrence
chain the COPT and the MC read.

Two test styles are used deliberately:

* **live HiGHS solves** where the claim is that the constraint BINDS — the
  standard the ENS-cap suite set (`tests/test_adequacy_ens_cap.py`): a
  wrapper that composes but never moves the LP is worthless;
* **build-the-model-then-apply** where the claim is about MEMBERSHIP (which
  names carry a coefficient in which period's constraint, and what the stash
  records). Those are structural facts about the constraint, and asserting
  them through a solve would only make them fuzzier.

Every ★ below is one of the traps the spec exists to prevent; the plan's
[B*]/[S*] tags name the bug each one would otherwise reintroduce.
"""
from __future__ import annotations

import math
import queue
import threading

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.adequacy.slack import DSR_SLACK_PREFIX
from services.pypsa_service import PyPSAService
from services.solver_service import (
    SolverConfig,
    _wrap_with_reserve_margin,
    run_simulation,
)

N_SNAPSHOTS = 4
LOAD_MW = 150.0
BASE_MW = 200.0
GAS_Q = 0.05                      # occurrence.CARRIER_DEFAULTS["gas"], EFORd
GAS_DERATE = 1.0 - GAS_Q          # 0.95 — static p_max_pu is 1.0 here
BATTERY_Q = 0.02                  # CARRIER_DEFAULTS["battery"], FOR

# Firm fixed capacity of the fixture below: 0.95 × 200 = 190 MW.
FIRM_FIXED_MW = GAS_DERATE * BASE_MW
# m = 0.5 ⇒ required 225 MW ⇒ the LP must build (225 − 190)/0.95 MW of peaker.
MARGIN = 0.5
REQUIRED_MW = (1 + MARGIN) * LOAD_MW
FORCED_PEAKER_MW = (REQUIRED_MW - FIRM_FIXED_MW) / GAS_DERATE


# ── fixtures ──────────────────────────────────────────────────────────────

def _network(*, peaker: bool = True) -> pypsa.Network:
    """
    Flat 150 MW load, a 200 MW fixed gas unit that serves it outright, and an
    expensive extendable peaker the LP has no economic reason to build. The
    margin is the ONLY thing that can put the peaker into the plan — which is
    the whole point of the phase (plan §5, acceptance test 1).
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=N_SNAPSHOTS, freq="h"))
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=LOAD_MW)
    n.add("Generator", "base", bus="b", carrier="gas", p_nom=BASE_MW,
          marginal_cost=10.0)
    if peaker:
        n.add("Generator", "peaker", bus="b", carrier="gas", p_nom=0.0,
              p_nom_extendable=True, p_nom_max=500.0,
              capital_cost=5_000_000.0, marginal_cost=500.0)
    return n


def _apply(n: pypsa.Network, **cfg_kw) -> tuple[pypsa.Network, list[str]]:
    """Build the LP (no solve) and run the reserve-margin wrapper over it."""
    cfg = SolverConfig(**cfg_kw)
    n.optimize.create_model(
        multi_investment_periods=bool(cfg.multi_investment_periods))
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    fn = _wrap_with_reserve_margin(n, None, cfg, log_queue=log_q)
    assert fn is not None
    fn(n, n.snapshots)
    lines: list[str] = []
    while True:
        try:
            lines.append(str(log_q.get_nowait()))
        except queue.Empty:
            break
    return n, lines


def _stash(n: pypsa.Network) -> dict:
    st = getattr(n, "_reserve_margin_targets", None)
    assert st is not None, "the wrapper did not stash `_reserve_margin_targets`"
    return st


def _assets(n: pypsa.Network, period: str | None = None) -> dict:
    rows = _stash(n)["assets"]
    if period is not None:
        rows = [r for r in rows if str(r.get("period", period)) == period]
    return {r["name"]: r for r in rows}


def _names_in_constraint(n: pypsa.Network, cname: str) -> set[str]:
    """The component names carrying a coefficient in constraint ``cname``."""
    con = n.model.constraints[cname]
    label_to_name: dict[int, str] = {}
    for var_name in ("Generator-p_nom", "StorageUnit-p_nom"):
        if var_name not in n.model.variables:
            continue
        v = n.model.variables[var_name]
        for lab, nm in zip(np.ravel(v.labels.values), list(v.indexes["name"])):
            label_to_name[int(lab)] = str(nm)
    out: set[str] = set()
    for lab in np.ravel(con.vars.values):
        if int(lab) in label_to_name:
            out.add(label_to_name[int(lab)])
    return out


def _solve(n: pypsa.Network, **cfg_kw) -> tuple[dict, str, str]:
    PyPSAService.set_network(n)
    sink: dict = {}
    cfg = SolverConfig(**cfg_kw)
    status, condition = run_simulation(
        cfg, n, PyPSAService.get_lock(), threading.Event(),
        queue.SimpleQueue(), state_update=lambda **kw: sink.update(kw),
    )
    return sink, status, condition


# ── the wrapper is a no-op without a margin ───────────────────────────────

@pytest.mark.parametrize("margin", [None, 0.0, -1.0, float("nan"), float("inf")])
def test_no_margin_returns_user_fn_unchanged(margin):
    """A run that did not ask for a margin must not pay for one, and must not
    have its LP touched — the same contract `_wrap_with_ens_cap` honours."""
    sentinel = object()
    got = _wrap_with_reserve_margin(
        _network(), sentinel, SolverConfig(reserve_margin=margin))
    assert got is sentinel


def test_margin_wrapper_chains_the_user_callback():
    calls: list[str] = []
    n = _network()
    n.optimize.create_model()
    fn = _wrap_with_reserve_margin(
        n, lambda nn, sns: calls.append("user"),
        SolverConfig(reserve_margin=MARGIN), log_queue=queue.SimpleQueue())
    fn(n, n.snapshots)
    assert calls == ["user"]


# ── ★ the constraint BINDS (the phase's whole point) ──────────────────────

def test_margin_builds_capacity_that_a_zero_margin_solve_does_not():
    """
    ★ A live solve at m = 0 builds nothing (the 200 MW fixed unit covers the
    150 MW load and the peaker is priced far above any dispatch value); the
    SAME LP at m = 0.5 must build exactly (225 − 190)/0.95 MW of peaker.

    If this test can pass with the constraint absent, nothing else in this
    file means anything.
    """
    sink0, st0, cond0 = _solve(_network(), reserve_margin=0.0)
    assert st0 in ("ok", "optimal"), (st0, cond0)

    n = _network()
    sink1, st1, cond1 = _solve(n, reserve_margin=MARGIN)
    assert st1 in ("ok", "optimal"), (st1, cond1)

    built = float(n.generators.at["peaker", "p_nom_opt"])
    assert built == pytest.approx(FORCED_PEAKER_MW, rel=1e-4), (
        f"m={MARGIN} must force {FORCED_PEAKER_MW:.3f} MW of peaker; got {built}"
    )

    n0 = _network()
    _solve(n0, reserve_margin=0.0)
    assert float(n0.generators.at["peaker", "p_nom_opt"]) == pytest.approx(
        0.0, abs=1e-6), "m=0 must not build — otherwise the lever proves nothing"


def test_margin_is_a_constraint_not_a_price():
    """The LP may not buy its way out (plan §1.6): the built capacity is the
    constraint's minimum, not whatever the objective prefers."""
    n = _network()
    _solve(n, reserve_margin=MARGIN)
    assert float(n.generators.at["peaker", "p_nom_opt"]) == pytest.approx(
        FORCED_PEAKER_MW, rel=1e-4)


# ── ★ trap 1: capacity never comes from `solved_capacity` ─────────────────

def test_unbuilt_extendable_is_on_the_lhs():
    """
    ★ [B1/B3] `solved_capacity` reads `p_nom_opt == 0.0` for an extendable at
    LP-BUILD time, so a walk taken at the default `keep_zero_capacity=False`
    drops the peaker — the one asset the margin exists to force into being.
    The LHS would then be the fixed fleet alone and no margin above 0.173
    could ever be met.
    """
    n, _ = _apply(_network(), reserve_margin=MARGIN)
    assert "peaker" in _names_in_constraint(n, "reserve_margin_ALL")
    row = _assets(n)["peaker"]
    assert row["extendable"] is True
    assert row["capacity_mw"] is None, (
        "an extendable's capacity is an LP variable, not a number")
    assert row["derate"] == pytest.approx(GAS_DERATE)


# ── ★ trap 2: activity masks BOTH sides ───────────────────────────────────

def _two_period_network() -> pypsa.Network:
    n = pypsa.Network()
    sns = pd.MultiIndex.from_product(
        [[2030, 2040],
         pd.date_range("2030-01-01", periods=N_SNAPSHOTS, freq="h")],
        names=["period", "timestep"])
    n.set_snapshots(sns)
    n.investment_periods = [2030, 2040]
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=LOAD_MW)
    # build_year 2000 / lifetime 100 — NOT `build_year=0`, which PyPSA reads
    # as "retires in year 100" and would leave `base` inactive in both periods.
    n.add("Generator", "base", bus="b", carrier="gas", p_nom=BASE_MW,
          marginal_cost=10.0, build_year=2000, lifetime=100)
    n.add("Generator", "now", bus="b", carrier="gas", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=500.0, capital_cost=5e6,
          marginal_cost=500.0, build_year=2030, lifetime=100)
    n.add("Generator", "future", bus="b", carrier="gas", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=500.0, capital_cost=1.0,
          marginal_cost=1.0, build_year=2040, lifetime=100)
    n.add("Generator", "fixed_2040", bus="b", carrier="gas", p_nom=400.0,
          marginal_cost=10.0, build_year=2040, lifetime=100)
    return n


def test_future_vintage_cannot_satisfy_an_earlier_period():
    """
    ★ [B2] `Generator-p_nom` is ONE horizon-wide variable with coords
    `(name,)`. The variable for a `build_year=2040` extendable EXISTS while
    the 2030 constraint is built, so without
    `get_active_assets(2030)` the LP would satisfy the 2030 margin with 2040
    capacity — and it would prefer to, because `future` here is 5e6× cheaper
    than `now`. The fixed side needs the same mask in the other direction:
    a 400 MW unit not built until 2040 must not appear as a 2030 constant.
    """
    n, _ = _apply(_two_period_network(), reserve_margin=MARGIN,
                  multi_investment_periods=True)
    in_2030 = _names_in_constraint(n, "reserve_margin_2030")
    in_2040 = _names_in_constraint(n, "reserve_margin_2040")
    assert "now" in in_2030
    assert "future" not in in_2030, (
        "a 2040 vintage carried a coefficient in the 2030 margin constraint")
    assert {"now", "future"} <= in_2040

    per = _stash(n)["periods"]
    # 2030's fixed side is `base` alone (190 MW); 2040's adds fixed_2040.
    assert per["2030"]["firm_fixed_mw"] == pytest.approx(GAS_DERATE * BASE_MW)
    assert per["2040"]["firm_fixed_mw"] == pytest.approx(
        GAS_DERATE * (BASE_MW + 400.0))


def test_multi_period_is_not_labelled_horizon_wide():
    n, _ = _apply(_two_period_network(), reserve_margin=MARGIN,
                  multi_investment_periods=True)
    assert _stash(n)["horizon_wide"] is False


def test_single_period_is_labelled_horizon_wide():
    """§2.1: with no vintage split the per-period constraints share one
    variable and the standard is horizon-wide. The panel must be able to say
    so rather than claim a per-period standard the LP cannot express."""
    n, _ = _apply(_network(), reserve_margin=MARGIN)
    assert _stash(n)["horizon_wide"] is True


# ── ★ trap 3: coord membership before `.sel` ──────────────────────────────

def test_inactive_extendable_does_not_raise_keyerror():
    """
    ★ [B2] `v.sel(name=X)` raises `KeyError` when X is absent from the
    variable's coords, and PyPSA builds `Generator-p_nom` on
    `extendables ∩ active_assets` — so an `active=False` extendable is in the
    generators frame and NOT in the coords. `_wrap_with_capex_budget` guards
    only the variable's existence and raises here; this wrapper must not.
    """
    n = _network()
    n.add("Generator", "shelved", bus="b", carrier="gas", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=500.0, capital_cost=1.0,
          marginal_cost=1.0, active=False)
    n.optimize.create_model()
    # The trap, pinned: the name IS in the frame and IS NOT in the coords.
    assert "shelved" in n.generators.index
    assert "shelved" not in n.model.variables["Generator-p_nom"].indexes["name"]
    with pytest.raises(KeyError):
        n.model.variables["Generator-p_nom"].sel(name="shelved")

    fn = _wrap_with_reserve_margin(
        n, None, SolverConfig(reserve_margin=MARGIN),
        log_queue=queue.SimpleQueue())
    fn(n, n.snapshots)          # must not raise
    assert "shelved" not in _names_in_constraint(n, "reserve_margin_ALL")
    assert "shelved" not in _assets(n)


def test_inactive_fixed_generator_is_not_firm_capacity():
    """The mask runs in the other direction too — an asset the LP will not
    dispatch cannot be a constant on the LHS."""
    n = _network(peaker=False)
    n.add("Generator", "mothballed", bus="b", carrier="gas", p_nom=1000.0,
          marginal_cost=10.0, active=False)
    n, _ = _apply(n, reserve_margin=MARGIN)
    assert "mothballed" not in _assets(n)
    assert _stash(n)["periods"]["ALL"]["firm_fixed_mw"] == pytest.approx(
        FIRM_FIXED_MW)


# ── ★ trap 4: no derate may default to 1.0 ────────────────────────────────

def test_generator_without_outage_data_or_profile_is_excluded():
    """
    ★ [B4] `resolve_outage_params` returns `source="missing"` for any carrier
    outside the 10-entry defaults library. Such a unit has no profile either,
    so a `p_max_pu`-shaped fallback credits it at PyPSA's default 1.0 — the
    tool would give MORE firm credit to a unit it knows NOTHING about than to
    a gas unit on a carrier class average (0.95). It is excluded from the LHS
    instead; §3's preflight (a later wave) errors on it.
    """
    n = _network()
    n.add("Generator", "geo", bus="b", carrier="geothermal", p_nom=1000.0,
          marginal_cost=5.0)
    n, lines = _apply(n, reserve_margin=MARGIN)
    assert "geo" not in _assets(n), (
        "a unit with no outage data and no availability profile was credited")
    assert "geo" not in _names_in_constraint(n, "reserve_margin_ALL")
    assert _stash(n)["periods"]["ALL"]["firm_fixed_mw"] == pytest.approx(
        FIRM_FIXED_MW), "the unpriceable unit leaked into the fixed constant"
    assert any("geo" in ln for ln in lines), (
        "an excluded generator must be named in the solver log, not dropped "
        "silently")


def test_static_p_max_pu_is_part_of_the_derate():
    """[S3] PyPSA caps dispatch at `p_max_pu × p_nom`, so a thermal unit with
    static `p_max_pu = 0.9` can never deliver nameplate."""
    n = _network(peaker=False)
    n.generators.loc["base", "p_max_pu"] = 0.9
    n, _ = _apply(n, reserve_margin=MARGIN)
    assert _assets(n)["base"]["derate"] == pytest.approx(GAS_DERATE * 0.9)


def test_derating_rows_carry_basis_and_source():
    """[S1] The basis rides with the number — `1 − FOR` is not a UCAP derate
    and the tool never silently converts."""
    n, _ = _apply(_network(), reserve_margin=MARGIN)
    row = _assets(n)["base"]
    assert row["basis"] == "EFORd"
    assert row["source"] == "carrier_default"


# ── ★ trap 5: the peak is an UNWEIGHTED MW maximum ────────────────────────

@pytest.mark.parametrize("weight", [1.0, 50.0])
def test_peak_is_unweighted(weight):
    """
    ★ [S4] The ENS cap's denominator is a weighted ENERGY sum; copying that
    shape here would report a 50× peak on a representative-week run and force
    50× the capacity. A margin is a POWER standard.
    """
    n = _network()
    n.snapshot_weightings.loc[:, :] = weight
    n, _ = _apply(n, reserve_margin=MARGIN)
    per = _stash(n)["periods"]["ALL"]
    assert per["peak_mw"] == pytest.approx(LOAD_MW)
    assert per["required_mw"] == pytest.approx(REQUIRED_MW)


def test_peak_uses_the_time_series_when_one_exists():
    n = _network(peaker=False)
    n.loads_t.p_set = pd.DataFrame(
        {"l": [10.0, 10.0, 10.0, 400.0]}, index=n.snapshots)
    n, _ = _apply(n, reserve_margin=MARGIN)
    assert _stash(n)["periods"]["ALL"]["peak_mw"] == pytest.approx(400.0)


# ── ★ trap 6: must-take credit includes every tied peak snapshot ──────────

def test_must_take_credit_includes_all_snapshots_tied_at_the_nth():
    """
    ★ [S5] On a flat-demand fixture every snapshot ties for "highest demand".
    `N = min(100, max(1, round(0.01·|P|)))` is 1 here, and an `nlargest`-style
    selection would resolve the 24-way tie by INDEX ORDER — turning the credit
    into "the mean over the first snapshot", deterministic and meaningless.
    The credit must be the profile's mean over the WHOLE period (0.5), not
    over the first N (1.0).
    """
    n = pypsa.Network()
    sns = pd.date_range("2030-01-01", periods=24, freq="h")
    n.set_snapshots(sns)
    n.add("Carrier", "gas")
    n.add("Carrier", "wind")            # deliberately absent from the library
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=LOAD_MW)     # flat ⇒ a 24-way tie
    n.add("Generator", "base", bus="b", carrier="gas", p_nom=BASE_MW,
          marginal_cost=10.0)
    n.add("Generator", "wind1", bus="b", carrier="wind", p_nom=100.0,
          p_max_pu=pd.Series([1.0] * 12 + [0.0] * 12, index=sns),
          marginal_cost=0.0)
    n, _ = _apply(n, reserve_margin=MARGIN)
    per = _stash(n)["periods"]["ALL"]
    assert per["n_peak_hours"] == 24, (
        f"the tie must pull in all 24 snapshots, got {per['n_peak_hours']}")
    assert len(per["peak_snapshots"]) == 24
    row = _assets(n)["wind1"]
    assert row["source"] == "missing"
    assert row["derate"] == pytest.approx(0.5), (
        "0.5 is the mean over the whole tied period; 1.0 is the first-N bug")


def test_peak_hours_scale_and_are_capped_at_100():
    """N = min(100, max(1, round(0.01·|P|))): 1 on a 24-hour horizon, 100 on
    a very long one — never the single-hour draw a 'top 0.1 %' rule floors to
    on every horizon under 1000 snapshots."""
    n = pypsa.Network()
    sns = pd.date_range("2030-01-01", periods=200, freq="h")
    n.set_snapshots(sns)
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    # Strictly descending demand ⇒ no ties, so the count is exactly N = 2.
    n.loads_t.p_set = pd.DataFrame(
        {"l": np.linspace(400.0, 10.0, 200)}, index=sns)
    n.add("Load", "l", bus="b", p_set=0.0)
    n.add("Generator", "base", bus="b", carrier="gas", p_nom=BASE_MW,
          marginal_cost=10.0)
    n, _ = _apply(n, reserve_margin=MARGIN)
    assert _stash(n)["periods"]["ALL"]["n_peak_hours"] == 2


def test_prm_peak_hours_overrides_the_rule():
    n = pypsa.Network()
    sns = pd.date_range("2030-01-01", periods=200, freq="h")
    n.set_snapshots(sns)
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.loads_t.p_set = pd.DataFrame(
        {"l": np.linspace(400.0, 10.0, 200)}, index=sns)
    n.add("Load", "l", bus="b", p_set=0.0)
    n.add("Generator", "base", bus="b", carrier="gas", p_nom=BASE_MW,
          marginal_cost=10.0)
    n, _ = _apply(n, reserve_margin=MARGIN, prm_peak_hours=7)
    assert _stash(n)["periods"]["ALL"]["n_peak_hours"] == 7


# ── ★ trap 7: DSR slacks are not firm capacity ────────────────────────────

def test_dsr_slack_is_not_firm_capacity():
    """
    ★ [S9] The ENS cap's `involuntary_slack_mask` excludes only the VoLL tier
    BY DESIGN, so a `__dsr_*` generator survives it — and a DSR slack's
    `p_nom` is `share × bus peak load`, potentially enough to satisfy any
    margin by itself. Membership must use `slack_generator_mask` (both tiers).

    The `__dsr_` row here carries a REAL carrier so that it would otherwise be
    credited at 0.95: a DSR slack on its own synthetic carrier is dropped by
    the no-outage-data rule anyway, which would make this test pass with the
    wrong mask.
    """
    n = _network(peaker=False)
    n.add("Generator", f"{DSR_SLACK_PREFIX}b", bus="b", carrier="gas",
          p_nom=1000.0, marginal_cost=300.0)
    n, _ = _apply(n, reserve_margin=MARGIN)
    assert f"{DSR_SLACK_PREFIX}b" not in _assets(n)
    assert _stash(n)["periods"]["ALL"]["firm_fixed_mw"] == pytest.approx(
        FIRM_FIXED_MW), "a demand-response slack was counted as firm capacity"


def test_live_dsr_run_still_builds_the_margin_capacity():
    """End-to-end through `run_simulation`'s own DSR tier: 75 MW of voluntary
    demand response must buy exactly zero margin."""
    n = _network()
    _sink, status, cond = _solve(
        n, reserve_margin=MARGIN, voll=3000.0,
        dsr_price_eur_per_mwh=50.0, dsr_share_of_load=0.5, dsr_buses=["b"])
    assert status in ("ok", "optimal"), (status, cond)
    assert float(n.generators.at["peaker", "p_nom_opt"]) == pytest.approx(
        FORCED_PEAKER_MW, rel=1e-4)


def test_voll_slack_is_not_firm_capacity():
    n = _network(peaker=False)
    n.add("Generator", "__voll_b", bus="b", carrier="gas", p_nom=1000.0,
          marginal_cost=3000.0)
    n, _ = _apply(n, reserve_margin=MARGIN)
    assert "__voll_b" not in _assets(n)


# ── ★ storage: the duration haircut, and `Store` is excluded ──────────────

def test_storage_takes_the_duration_haircut():
    """★ [S10] `d_s = min(1, max_hours / prm_storage_duration_h) × (1 − q_s)`
    — a 2-hour battery is worth half a 4-hour one against a 4-hour standard,
    and the haircut is what stops a 15-minute battery counting as firm."""
    n = _network(peaker=False)
    n.add("Carrier", "battery")
    n.add("StorageUnit", "bat2h", bus="b", carrier="battery", p_nom=100.0,
          max_hours=2.0)
    n.add("StorageUnit", "bat8h", bus="b", carrier="battery", p_nom=100.0,
          max_hours=8.0)
    n, _ = _apply(n, reserve_margin=MARGIN, prm_storage_duration_h=4.0)
    rows = _assets(n)
    assert rows["bat2h"]["derate"] == pytest.approx(0.5 * (1 - BATTERY_Q))
    assert rows["bat8h"]["derate"] == pytest.approx(1.0 * (1 - BATTERY_Q)), \
        "the haircut is a min(1, ·) — a long battery is not credited above 1"
    assert rows["bat2h"]["kind"] == "storage"
    assert _stash(n)["periods"]["ALL"]["firm_fixed_mw"] == pytest.approx(
        FIRM_FIXED_MW + 0.5 * (1 - BATTERY_Q) * 100.0
        + 1.0 * (1 - BATTERY_Q) * 100.0)


def test_storage_duration_knob_is_read_from_the_config():
    """The convention lives on `SolverConfig`, never as a module constant —
    `InputsBlock.assumptions_hash` is computed from `asdict(cfg)`, so a
    constant would let two reports made under different conventions carry the
    same hash."""
    n = _network(peaker=False)
    n.add("Carrier", "battery")
    n.add("StorageUnit", "bat2h", bus="b", carrier="battery", p_nom=100.0,
          max_hours=2.0)
    n, _ = _apply(n, reserve_margin=MARGIN, prm_storage_duration_h=1.0)
    assert _assets(n)["bat2h"]["derate"] == pytest.approx(1.0 - BATTERY_Q)


def test_extendable_storage_is_an_lp_term():
    n = _network(peaker=False)
    n.add("Carrier", "battery")
    n.add("StorageUnit", "bat", bus="b", carrier="battery", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=300.0, max_hours=4.0,
          capital_cost=1e6)
    n, _ = _apply(n, reserve_margin=MARGIN)
    assert "bat" in _names_in_constraint(n, "reserve_margin_ALL")
    assert _assets(n)["bat"]["capacity_mw"] is None


def test_store_is_excluded():
    """★ [S10] A `Store` has no power rating, so it cannot carry a firm-power
    credit — the MC's recorded rationale, applied here."""
    n = _network(peaker=False)
    n.add("Carrier", "battery")
    n.add("Store", "tank", bus="b", carrier="battery", e_nom=5000.0)
    n, _ = _apply(n, reserve_margin=MARGIN)
    assert "tank" not in _assets(n)
    assert _stash(n)["periods"]["ALL"]["firm_fixed_mw"] == pytest.approx(
        FIRM_FIXED_MW)


def test_storage_with_inflow_is_flagged_energy_limited():
    """A reservoir with `max_hours = 2000` takes full power credit while its
    energy limit is what actually binds it — recorded as a limitation, not
    silently fixed (§2.4)."""
    n = _network(peaker=False)
    n.add("Carrier", "hydro")
    sns = n.snapshots
    n.add("StorageUnit", "res", bus="b", carrier="hydro", p_nom=100.0,
          max_hours=2000.0,
          inflow=pd.Series([5.0] * len(sns), index=sns))
    n.add("StorageUnit", "bat", bus="b", carrier="hydro", p_nom=100.0,
          max_hours=4.0)
    n, _ = _apply(n, reserve_margin=MARGIN)
    rows = _assets(n)
    assert rows["res"]["energy_limited"] is True
    assert rows["bat"]["energy_limited"] is False


# ── the stash ─────────────────────────────────────────────────────────────

def test_stash_shape():
    """§2.6 — the contract §4's report and §3's diagnoser read.

    Amendment v1.3 (Phase 12b): the period row also carries `demand_mw` —
    the scaled demand Series itself, so the payload can select a net-load
    window on the SAME demand the constraint was built on — and
    `peak_hours_override`; the asset row carries `q`, `profile_kind`,
    `nettable` and `profile`. All in memory only; none reach the wire.
    """
    n, _ = _apply(_network(), reserve_margin=MARGIN)
    st = _stash(n)
    assert set(st) == {"margin", "horizon_wide", "periods", "assets"}
    assert st["margin"] == pytest.approx(MARGIN)
    assert isinstance(st["horizon_wide"], bool)
    assert set(st["periods"]) == {"ALL"}
    per = st["periods"]["ALL"]
    assert set(per) == {
        "peak_mw", "peak_snapshots", "n_peak_hours", "required_mw",
        "firm_fixed_mw", "max_achievable_mw",
        "demand_mw", "peak_hours_override"}
    assert isinstance(per["demand_mw"], pd.Series)
    assert list(per["demand_mw"].index) == list(n.snapshots)
    assert per["peak_mw"] == pytest.approx(LOAD_MW)
    assert per["required_mw"] == pytest.approx(REQUIRED_MW)
    assert per["firm_fixed_mw"] == pytest.approx(FIRM_FIXED_MW)
    assert all(isinstance(s, str) for s in per["peak_snapshots"])
    for row in st["assets"]:
        assert set(row) >= {
            "name", "kind", "capacity_mw", "derate", "basis", "source",
            "extendable", "energy_limited",
            "q", "profile_kind", "nettable", "profile"}
        assert row["profile_kind"] in ("none", "constant", "varying")
        assert row["nettable"] == (row["profile_kind"] == "varying")
        assert (row["profile"] is None) == (row["profile_kind"] != "varying")


def test_stash_records_max_achievable_capacity():
    """§2.6 — `max_achievable_mw` is what §3's preflight and
    `_diagnose_infeasibility` compare against `required_mw`; without it the
    user gets "check binding capacity bounds, ramp limits, or a too-tight
    global constraint", three wrong places."""
    n, _ = _apply(_network(), reserve_margin=MARGIN)
    per = _stash(n)["periods"]["ALL"]
    assert per["max_achievable_mw"] == pytest.approx(
        FIRM_FIXED_MW + GAS_DERATE * 500.0)
    assert per["max_achievable_mw"] > per["required_mw"]


def test_stash_is_cleaned_up_after_a_solve():
    """Like `_ens_cap_targets`: a solve-time stash that outlives its solve
    would have the NEXT run's report read this run's targets."""
    n = _network()
    _sink, status, _ = _solve(n, reserve_margin=MARGIN)
    assert status in ("ok", "optimal")
    assert getattr(n, "_reserve_margin_targets", None) is None


def test_stash_is_cleaned_up_after_a_failed_solve():
    """
    The fixture must be infeasible with the margin CONSTRAINT INSTALLED, and
    infeasible for a reason §3's preflight cannot decide.

    Its first form (a peaker capped at 100 MW against a 750 MW requirement)
    stopped testing anything the moment §3 shipped: an unreachable fleet is
    now a PREFLIGHT ERROR, so that network never reaches the LP at all and the
    cleanup path under test is never entered — the test would have passed
    while asserting nothing. `_transmission_limited_network` keeps the claim
    real: the fleet CAN reach the margin (a system-wide power standard has no
    network in it), so preflight passes and `reserve_margin_ALL` is a live
    constraint, but the load sits behind a 10 MW line and the dispatch is
    infeasible.
    """
    n = _transmission_limited_network()
    _sink, status, condition = _solve(n, reserve_margin=MARGIN)
    assert status not in ("ok", "optimal"), (
        f"fixture is meant to be infeasible, got {status}/{condition}")
    assert condition != "validation_failed", (
        "the fixture must fail in the LP, not at preflight — otherwise the "
        "cleanup path this test exists for is never reached")
    assert "reserve_margin_ALL" in n.model.constraints
    assert getattr(n, "_reserve_margin_targets", None) is None


def test_unreachable_fleet_does_not_crash_the_lp_build():
    """[plan §2] Linopy raises `TypeError` on a constant constraint and
    `Generator-p_nom` does not exist when nothing extendable is active. The
    wrapper must skip and SAY SO (§3's preflight owns the error); it must not
    take the solve down with a linopy exception."""
    n = _network(peaker=False)
    n, lines = _apply(n, reserve_margin=5.0)
    assert "reserve_margin_ALL" not in n.model.constraints
    joined = "\n".join(lines)
    assert "reserve margin" in joined.lower()
    per = _stash(n)["periods"]["ALL"]
    assert per["max_achievable_mw"] < per["required_mw"]


# ── ★ §5: the contingency sweep strips the margin ─────────────────────────

def test_sweep_strips_the_reserve_margin():
    """
    ★ [B7] `freeze_capacities` pins bounds while KEEPING
    `p_nom_extendable=True`, so a surviving margin constraint stays pinned
    against a fleet that cannot grow: the base solve goes infeasible and the
    WHOLE sweep fails with "base operational solve failed". The margin is a
    standing standard, not the question the sweep asks.
    """
    from services.adequacy import sweep as S

    n = _network()
    PyPSAService.set_network(n)
    cfg = SolverConfig(voll=3000.0, reserve_margin=MARGIN)

    def _base_out(nn):
        orig = float(nn.generators.at["base", "p_max_pu"])
        nn.generators.at["base", "p_max_pu"] = 0.5

        def undo():
            nn.generators.at["base", "p_max_pu"] = orig
        return undo

    out = S.run_contingency_sweep(
        n, PyPSAService.get_lock(), cfg,
        [{"id": "base_derate", "mutate": _base_out, "meta": {}}],
        log_queue=queue.SimpleQueue(),
    )
    assert out["base"]["status"] in ("ok", "optimal")
    assert out["contingencies"]["base_derate"]["status"] in ("ok", "optimal")
    assert out["contingencies"]["base_derate"]["delta_eue_mwh"] > 0.0


def test_sweep_config_drops_the_margin_field():
    """The one-line rationale, pinned at the seam so a future field addition
    cannot silently leave the margin in."""
    import dataclasses

    from services.adequacy import sweep as S  # noqa: F401  (import parity)

    src = dataclasses.replace(
        SolverConfig(voll=1.0, reserve_margin=MARGIN,
                     ens_cap_permyriad=10.0, ens_zone_cap_multiple=2.0),
        ens_cap_permyriad=None, ens_zone_cap_multiple=None,
        reserve_margin=None)
    assert src.reserve_margin is None


# ── §1: the bounds live on the SCHEMA ─────────────────────────────────────

from pydantic import ValidationError  # noqa: E402

from models.schemas import SolverConfigSchema  # noqa: E402


@pytest.mark.parametrize(
    "field, value",
    [
        # A negative margin is a typo, and `_wrap_with_reserve_margin` reads
        # `<= 0` as "no margin" — so without the bound the user who types -1
        # gets a solve with no standard at all, no report and no warning
        # (the Phase-1 QA lesson, applied at the boundary where it is entered).
        ("reserve_margin", -1.0),
        ("reserve_margin", -0.0001),
        # 600 % is not a margin, it is a units mistake (15 for 0.15).
        ("reserve_margin", 6.0),
        ("prm_peak_hours", 0),
        ("prm_peak_hours", -5),
        # A zero or negative reference duration divides the storage haircut
        # by zero.
        ("prm_storage_duration_h", 0.0),
        ("prm_storage_duration_h", -4.0),
    ],
)
def test_schema_rejects_nonsense_margin_inputs(field, value):
    with pytest.raises(ValidationError):
        SolverConfigSchema(**{field: value})


@pytest.mark.parametrize(
    "field, value",
    [
        ("reserve_margin", None),
        ("reserve_margin", 0.0),
        ("reserve_margin", 0.15),
        ("reserve_margin", 5.0),
        ("prm_peak_hours", None),
        ("prm_peak_hours", 1),
        ("prm_peak_hours", 100),
        ("prm_storage_duration_h", 4.0),
        ("prm_storage_duration_h", 0.25),
    ],
)
def test_schema_accepts_the_meaningful_margin_range(field, value):
    cfg = SolverConfigSchema(**{field: value})
    assert getattr(cfg, field) == value


def test_solver_config_carries_the_margin_defaults():
    cfg = SolverConfig()
    assert cfg.reserve_margin is None
    assert cfg.prm_peak_hours is None
    assert cfg.prm_storage_duration_h == pytest.approx(4.0)


# ══════════════════════════════════════════════════════════════════════════
# §3 — preflight / validation, and the infeasibility diagnosis
# ══════════════════════════════════════════════════════════════════════════
#
# The unreachable-fleet ERROR is what REPLACES "let the LP go infeasible":
# linopy raises `TypeError` on a constant constraint and `Generator-p_nom`
# does not exist when nothing extendable is active, so the only place that
# answer can be given is BEFORE the solve — where it is also the more useful
# one ("no plan built from your candidate set can reach this margin").

from services.validation_service import validate_for_run  # noqa: E402


def _issues(n: pypsa.Network, **cfg_kw):
    return validate_for_run(n, SolverConfig(**cfg_kw))


def _by_code(issues, code: str):
    return [i for i in issues if i.code == code]


def _unpriceable_network() -> pypsa.Network:
    """`geo` has no outage data (carrier outside the defaults library) and no
    availability profile — the wrapper excludes it, so 1000 MW of nameplate
    silently vanishes from the standard unless preflight says so."""
    n = _network()
    n.add("Carrier", "geothermal")
    n.add("Generator", "geo", bus="b", carrier="geothermal", p_nom=1000.0,
          marginal_cost=5.0)
    return n


def _must_take_network() -> pypsa.Network:
    """The other half of the evidence split: no outage data, but a profile."""
    n = _network()
    n.add("Carrier", "wind")
    n.add("Generator", "wind1", bus="b", carrier="wind", p_nom=100.0,
          marginal_cost=0.0,
          p_max_pu=pd.Series(0.4, index=n.snapshots))
    return n


def test_preflight_errors_on_generators_it_cannot_price():
    """
    ★ §3 — an excluded unit is a silent 1000 MW hole in the standard. The
    wrapper logs it, but a log line is not a decision point: the user is
    committing to a plan built against a fleet the tool could not price.
    """
    issues = _issues(_unpriceable_network(), reserve_margin=MARGIN)
    errs = _by_code(issues, "reserve_margin_unpriceable_assets")
    assert len(errs) == 1, [i.code for i in issues]
    e = errs[0]
    assert e.severity == "error"
    assert "geo" in e.message, e.message
    assert "1" in e.message, e.message


def test_preflight_does_not_error_on_a_must_take_profile():
    """The evidence split, at the preflight boundary: a unit with a
    `p_max_pu` series IS priceable (§2.3 credits it at its peak-coincidence
    mean). Erroring here would block every VRE network."""
    issues = _issues(_must_take_network(), reserve_margin=MARGIN)
    assert not _by_code(issues, "reserve_margin_unpriceable_assets")


def test_preflight_is_silent_without_a_margin():
    """No margin ⇒ no standard ⇒ nothing to say. A network the margin would
    reject must still solve when nobody asked for a margin."""
    for iss in _issues(_unpriceable_network()):
        assert not iss.code.startswith("reserve_margin_"), iss.message


def test_preflight_errors_when_no_plan_can_reach_the_margin():
    """
    ★ §3 / plan §2 — the fixed fleet tops out at 190 MW derated against a
    900 MW requirement and nothing extendable is active. Both numbers must be
    in the message: "unreachable" without them is a slogan.
    """
    issues = _issues(_network(peaker=False), reserve_margin=5.0)
    errs = _by_code(issues, "reserve_margin_unreachable")
    assert len(errs) == 1, [i.code for i in issues]
    msg = errs[0].message
    assert errs[0].severity == "error"
    assert "900" in msg, msg
    assert "190" in msg, msg


def test_preflight_accepts_a_margin_an_extendable_can_reach():
    """The peaker's `p_nom_max` is part of what the fleet can reach — an
    error here would block the exact case the phase exists to serve."""
    assert not _by_code(
        _issues(_network(), reserve_margin=MARGIN), "reserve_margin_unreachable")


def test_preflight_never_calls_an_unbounded_fleet_unreachable():
    """Amendment 6: an unbounded `p_nom_max` makes `max_achievable_mw` `inf`,
    so the comparison is always False. Pinned because the alternative (a
    clamp to some large number) would make the test fire by accident."""
    n = _network()
    n.generators.at["peaker", "p_nom_max"] = float("inf")
    assert not _by_code(
        _issues(n, reserve_margin=5.0), "reserve_margin_unreachable")


def test_preflight_warns_on_carrier_default_derating():
    """★ [S2] The derate is a hidden assumption that changes what gets BUILT,
    and `source` in the post-solve table is visible too late to act on."""
    warns = _by_code(
        _issues(_network(), reserve_margin=MARGIN),
        "reserve_margin_carrier_default_derating")
    assert len(warns) == 1
    assert warns[0].severity == "warning"
    assert "2" in warns[0].message, warns[0].message
    assert "base" in warns[0].message and "peaker" in warns[0].message


def test_preflight_does_not_warn_when_the_user_entered_the_rates():
    n = _network()
    for g in ("base", "peaker"):
        n.generators.at[g, "outage_rate_value"] = 0.07
        n.generators.at[g, "outage_rate_basis"] = "EFORd"
        n.generators.at[g, "mttr_hours"] = 24.0
    assert not _by_code(
        _issues(n, reserve_margin=MARGIN),
        "reserve_margin_carrier_default_derating")


def test_preflight_blocks_the_run_it_rejects():
    """The error is only worth anything if `run_simulation` refuses on it."""
    n = _network(peaker=False)
    _sink, status, condition = _solve(n, reserve_margin=5.0)
    assert (status, condition) == ("error", "validation_failed")


# ── the rolling / myopic adjudication (§3, last bullet) ───────────────────
#
# ADJUDICATED: rolling is an ERROR (mirroring `_check_ens_cap_coherence`),
# myopic is a WARNING (diverging from it), and the reason is the DENOMINATOR.
# `optimize_with_rolling_horizon` calls `extra_functionality` once per WINDOW
# with that window's snapshots, so the standard silently becomes
# "(1+m) x the window peak" — a weaker standard than the one asked for, and
# reported as met. A myopic iteration's snapshots ARE one investment period,
# which is exactly the denominator §2.5 specifies, so the standard is enforced
# correctly; what breaks is only the REPORT (each iteration overwrites the
# stash, so the published block covers the last period solved). A correct
# standard with an incomplete report is a warning, not a refusal.

def test_margin_under_rolling_horizon_is_an_error():
    errs = _by_code(
        _issues(_network(), reserve_margin=MARGIN, solve_strategy="rolling"),
        "reserve_margin_unsupported_strategy")
    assert len(errs) == 1 and errs[0].severity == "error"
    assert "window" in errs[0].message.lower()


def test_margin_under_myopic_foresight_warns_but_does_not_block():
    issues = _issues(
        _two_period_network(), reserve_margin=MARGIN, solve_strategy="myopic",
        multi_investment_periods=True)
    assert not _by_code(issues, "reserve_margin_unsupported_strategy")
    warns = _by_code(issues, "reserve_margin_myopic_report_is_partial")
    assert len(warns) == 1 and warns[0].severity == "warning"


# ── §3: `_diagnose_infeasibility` can see this constraint ─────────────────

def _diagnose(n, stash, **cfg_kw) -> list[str]:
    from services.solver_service import _diagnose_infeasibility
    q: queue.SimpleQueue = queue.SimpleQueue()
    _diagnose_infeasibility(n, SolverConfig(**cfg_kw), q, margin_targets=stash)
    out: list[str] = []
    while True:
        try:
            out.append(str(q.get_nowait()))
        except queue.Empty:
            return out


def test_diagnosis_names_the_margin_and_both_numbers():
    """
    ★ [S11] Today a PRM-infeasible run says "No obvious structural cause
    found. Check binding capacity bounds, ramp limits, or a too-tight global
    constraint" — three wrong places. Its peak-vs-buildable hint is gated on
    `voll <= 0`, so with a VoLL set it never fires at all.
    """
    n = _network(peaker=False)
    n, _ = _apply(n, reserve_margin=5.0)
    lines = _diagnose(n, _stash(n), voll=3000.0)
    joined = "\n".join(lines)
    assert "reserve margin requires" in joined.lower(), joined
    assert "900" in joined and "190" in joined, joined
    assert "No obvious structural cause" not in joined, (
        "the fallback fired even though the margin explained the infeasibility")


def _transmission_limited_network() -> pypsa.Network:
    """
    Infeasible for a reason the PREFLIGHT cannot decide: the fleet can reach
    the margin (a system-wide power standard has no network in it), but the
    load sits behind a 10 MW line. So the margin constraint IS installed, the
    LP IS infeasible, and the diagnoser must still be able to read the stash —
    which it can only do if `run_simulation` captures it before the cleanup
    `delattr`.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=N_SNAPSHOTS, freq="h"))
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Bus", "b2", carrier="AC")
    n.add("Line", "l1", bus0="b", bus1="b2", x=0.1, r=0.01, s_nom=10.0)
    n.add("Load", "l", bus="b2", p_set=500.0)
    n.add("Generator", "base", bus="b", carrier="gas", p_nom=BASE_MW,
          marginal_cost=10.0)
    n.add("Generator", "peaker", bus="b", carrier="gas", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=1000.0,
          capital_cost=5_000_000.0, marginal_cost=500.0)
    return n


def test_an_infeasible_solve_diagnoses_the_margin_end_to_end():
    """
    ★ The ordering trap: the stash is deleted in the report step, and
    `_diagnose_infeasibility` runs AFTER it. Reading the attribute off the
    network there finds nothing, and the user gets the three-wrong-places
    fallback on a run whose margin is a live constraint.
    """
    n = _transmission_limited_network()
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    PyPSAService.set_network(n)
    status, condition = run_simulation(
        SolverConfig(reserve_margin=MARGIN), n, PyPSAService.get_lock(),
        threading.Event(), log_q, state_update=lambda **kw: None,
    )
    assert status not in ("ok", "optimal"), (status, condition)
    lines = []
    while True:
        try:
            lines.append(str(log_q.get_nowait()))
        except queue.Empty:
            break
    joined = "\n".join(lines)
    assert "[INFEASIBLE]" in joined, joined[-2000:]
    assert "reserve margin requires" in joined.lower(), joined[-2000:]


# ══════════════════════════════════════════════════════════════════════════
# §4 — the report block
# ══════════════════════════════════════════════════════════════════════════

import json  # noqa: E402
import typing  # noqa: E402

from models.adequacy import AdequacyReport, TargetBlock  # noqa: E402


def _report(sink: dict) -> AdequacyReport:
    raw = sink.get("adequacy_report")
    assert raw is not None, (
        "a solve that enforced a standard emitted no adequacy_report")
    return AdequacyReport.model_validate(raw)


def test_a_margin_only_run_produces_a_report():
    """
    ★ [B5/6.5] `build_adequacy_report` fires only when `_ens_cap_targets` is
    set, so a margin-only run produces NO report at all — the margin
    invisible exactly when it is the only standard, and every Phase-9 iterate
    reading `no_report` → failed.
    """
    sink, status, _ = _solve(_network(), reserve_margin=MARGIN)
    assert status in ("ok", "optimal")
    r = _report(sink)
    assert r.reserve_margin is not None, (
        "the report fired but carries no reserve-margin block")
    assert r.reserve_margin.margin == pytest.approx(MARGIN)
    assert [p.period for p in r.reserve_margin.by_period] == ["ALL"]


def test_the_margin_block_reports_achieved_against_required():
    sink, _s, _c = _solve(_network(), reserve_margin=MARGIN)
    row = _report(sink).reserve_margin.by_period[0]
    assert row.peak_mw == pytest.approx(LOAD_MW)
    assert row.required_mw == pytest.approx(REQUIRED_MW)
    # The LP builds exactly enough peaker to reach the standard, so the
    # constraint sits on its bound: met AND binding.
    assert row.firm_mw == pytest.approx(REQUIRED_MW, rel=1e-6)
    assert row.met is True
    assert row.binding is True
    assert row.margin_achieved == pytest.approx(MARGIN, rel=1e-6)


def test_a_margin_the_fixed_fleet_already_meets_is_not_binding():
    """`binding` must mean "this standard shaped the plan", not "a margin was
    set" — otherwise the panel credits the margin for capacity that was
    always there."""
    sink, _s, _c = _solve(_network(peaker=False), reserve_margin=0.2)
    row = _report(sink).reserve_margin.by_period[0]
    assert row.met is True
    assert row.binding is False
    assert row.firm_mw == pytest.approx(FIRM_FIXED_MW)


def test_the_margin_block_carries_the_derating_table():
    sink, _s, _c = _solve(_network(), reserve_margin=MARGIN)
    block = _report(sink).reserve_margin
    rows = {a.name: a for a in block.assets}
    assert set(rows) == {"base", "peaker"}
    assert rows["base"].basis == "EFORd"
    assert rows["base"].source == "carrier_default"
    # The BUILT capacity, not the LP-time `None`: a table that reports the
    # variable rather than its solution cannot be checked against the plan.
    assert rows["peaker"].capacity_mw == pytest.approx(
        FORCED_PEAKER_MW, rel=1e-6)
    assert rows["peaker"].firm_mw == pytest.approx(
        GAS_DERATE * FORCED_PEAKER_MW, rel=1e-6)
    assert block.derating_bases == {"EFORd": 2}
    assert block.horizon_wide is True


def test_the_ens_cap_report_still_fires_and_gains_a_margin_block():
    """The trigger became an OR — the AND it replaced must not have been
    broken in the process, and BOTH standards must be reportable at once
    (which is why the margin is a sibling block, not a fourth `binding`)."""
    sink, status, _ = _solve(
        _network(), reserve_margin=MARGIN, voll=3000.0, ens_cap_permyriad=10.0)
    assert status in ("ok", "optimal")
    r = _report(sink)
    assert r.target.system.cap_mwh > 0
    assert r.reserve_margin is not None


def test_target_block_binding_is_still_three_valued():
    """★ §4 — the frontend re-declares this `Literal` with an exhaustive label
    `Record` and `NEVER_BOUND_COPY_V1` tests `binding == "system_cap"`; a
    fourth value renders `undefined` and misdiagnoses the loop."""
    assert set(typing.get_args(
        TargetBlock.model_fields["binding"].annotation)) == {
            "system_cap", "zone_cap", "voll"}


def test_a_margin_only_report_leaves_the_energy_target_at_voll():
    """With no ENS cap there is no energy standard to report; `binding`
    falls to its only admissible value rather than growing a fourth."""
    sink, _s, _c = _solve(_network(), reserve_margin=MARGIN)
    r = _report(sink)
    assert r.target.binding == "voll"
    assert r.target.system.cap_mwh == 0.0


def test_the_margin_block_is_not_published_on_a_failed_solve():
    """
    ★ The identical guard the ENS cap got after QA round 2, which found an
    infeasible solve publishing a "target met" report. Without it an
    infeasible run republishes the PREVIOUS solve's margin as if this plan
    had met it.
    """
    from services.adequacy.report import (
        build_adequacy_report,
        reserve_margin_payload,
    )

    n = _network()
    n2, _ = _apply(n, reserve_margin=MARGIN)
    payload = reserve_margin_payload(n2, _stash(n2))
    ok = build_adequacy_report(n2, SolverConfig(reserve_margin=MARGIN), {}, {},
                               margin_payload=payload, status="ok")
    assert ok["reserve_margin"] is not None
    bad = build_adequacy_report(n2, SolverConfig(reserve_margin=MARGIN), {}, {},
                                margin_payload=payload, status="infeasible")
    assert bad["reserve_margin"] is None


def test_an_unbounded_extendable_leaves_a_json_serialisable_report():
    """
    ★ Amendment 6 — `max_achievable_mw` is `inf` for an unbounded
    `p_nom_max`. Mathematically right, and NOT JSON-serialisable: Starlette
    dumps with `allow_nan=False`, so the report endpoint 500s on it.
    """
    from services.adequacy.report import (
        build_adequacy_report,
        reserve_margin_payload,
    )

    n = _network()
    n.generators.at["peaker", "p_nom_max"] = float("inf")
    n2, _ = _apply(n, reserve_margin=MARGIN)
    stash = _stash(n2)
    assert stash["periods"]["ALL"]["max_achievable_mw"] == float("inf"), (
        "the fixture no longer produces the unbounded case it exists to pin")
    rep = build_adequacy_report(
        n2, SolverConfig(reserve_margin=MARGIN), {}, {},
        margin_payload=reserve_margin_payload(n2, stash), status="ok")
    row = rep["reserve_margin"]["by_period"][0]
    assert row["max_achievable_mw"] is None
    assert row["max_achievable_unbounded"] is True
    json.dumps(rep, allow_nan=False)   # what Starlette does to it


def test_the_margin_result_is_a_persisted_state_key():
    from services.project_context import RESULT_STATE_KEYS, ProjectSolverState

    assert "last_reserve_margin" in RESULT_STATE_KEYS
    assert hasattr(ProjectSolverState(), "last_reserve_margin")


def test_a_solve_emits_the_margin_result_into_solver_state():
    """Emitted like `last_lost_load` — the endpoint serves the PERSISTED
    payload, so a solve that does not write it leaves the panel empty."""
    sink, _s, _c = _solve(_network(), reserve_margin=MARGIN)
    payload = sink.get("last_reserve_margin")
    assert payload is not None
    assert payload["by_period"][0]["required_mw"] == pytest.approx(REQUIRED_MW)


def test_a_failed_solve_emits_no_margin_result():
    n = _transmission_limited_network()
    sink, status, _c = _solve(n, reserve_margin=MARGIN)
    assert status not in ("ok", "optimal")
    assert not sink.get("last_reserve_margin")


# ── a margin-only run must not report zero unserved energy ────────────────

def test_a_margin_only_run_reports_the_energy_it_actually_shed():
    """★ The report's headline ENS must be true when no ENS TARGET is set.

    Found by the Phase-9 review and confirmed live: `build_adequacy_report`
    accumulates `sys_achieved_total` INSIDE the loop over the ENS cap's target
    periods. Phase 8 made the report fire when either standard was enforced —
    so on a margin-only solve that loop never runs, and the report shipped
    `metrics.ens_mwh = 0.0` beside `metrics.shed_hours = 24.0`: the system shed
    in every hour and shed no energy. That is not a rounding disagreement, it
    is a self-contradiction, and it is the same class QA round 2 caught when an
    infeasible solve published a "target met" report.

    The cap's own numbers stay zero and are FLAGGED (`energy_target_set`) —
    a cap of 0.0 must never read as a target that was met — but the metrics
    block, which is what every consumer quotes, tells the truth.

    Bite (verified): compute `ens_mwh` from `sys_achieved_total` again.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=24, freq="h"))
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=200.0)
    n.add("Generator", "g", bus="b", carrier="gas", p_nom=120.0,
          marginal_cost=10.0, outage_rate_value=0.05,
          outage_rate_basis="EFORd", mttr_hours=12.0)
    # Buildable enough to satisfy the margin, but priced ABOVE VoLL so the LP
    # builds it and still sheds — the state that exposes the bug.
    n.add("Generator", "cand", bus="b", carrier="gas", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=300.0, capital_cost=1e6,
          marginal_cost=5000.0, outage_rate_value=0.05,
          outage_rate_basis="EFORd", mttr_hours=12.0)

    sink, status, condition = _solve(
        n, solver_name="highs", voll=3000.0,
        ens_cap_permyriad=None,          # NO energy target
        reserve_margin=0.05)
    assert condition == "optimal", (status, condition)
    rep = sink.get("adequacy_report")
    assert rep is not None, "the margin alone must still produce a report"

    shed_hours = float(rep["metrics"]["shed_hours"])
    ens = float(rep["metrics"]["ens_mwh"])
    assert shed_hours > 0, (
        "vacuous fixture: nothing was shed, so the contradiction cannot show")
    assert ens > 0, (
        f"the report claims {shed_hours} shed hours and {ens} MWh of unserved "
        "energy — a system cannot lose load for hours without losing energy")

    # And the cap's own block must not masquerade as a target that was met.
    assert rep["target"]["energy_target_set"] is False


# ── ★ the payload must report what a VINTAGE-EXPANDED plan built ────────────
#
# Found by the Phase 12b (v3) review while checking a premise of that plan,
# not by looking for it. On a multi-period network with per-period capacity
# bounds, `apply_vintage_bounds` expands `wind` into transient rows
# `wind@2030` / `wind@2040`, the wrapper stashes those names, and the restore
# drops the rows BEFORE the payload runs. `_built()` then finds no row and
# credits zero — so a plan that built 35 MW of wind and met the margin was
# reported as `met=False`. The built size is recoverable from the per-vintage
# breakdown the restore persists into `n.meta["vintage_results"]`.

def _vintage_network(*, alternating: bool = False) -> pypsa.Network:
    """Two periods, one load, a cheap must-take wind candidate with per-period
    bounds and an absurdly expensive peaker, so the margin is met by wind.

    ``alternating=True`` gives wind the profile 1,0,1,0 across each period's
    four hours (Phase 12b B3b): the gross window is all four tied hours, so
    the derate is 0.5 and the LP must build 70 MW of `wind@2030` for 35 MW of
    firm capacity; the net-load window is then exactly the two hours wind is
    absent, and its `derate_net` is 0.0."""
    from services.vintage_service import set_bounds_for_asset

    sns = pd.MultiIndex.from_product(
        [[2030, 2040], pd.date_range("2030-01-01", periods=4, freq="h")],
        names=["period", "timestep"])
    n = pypsa.Network()
    n.set_snapshots(sns)
    n.investment_periods = [2030, 2040]
    n.add("Carrier", "gas")
    n.add("Carrier", "wind")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=150.0)
    n.add("Generator", "base", bus="b", carrier="gas", p_nom=200.0,
          marginal_cost=10.0, build_year=2000, lifetime=100)
    n.add("Generator", "peaker", bus="b", carrier="gas", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=500.0, capital_cost=5e6,
          marginal_cost=500.0, build_year=2030, lifetime=100)
    # must-take (carrier absent from the defaults library), flat profile 1.0
    n.add("Generator", "wind", bus="b", carrier="wind", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=500.0, capital_cost=1000.0,
          marginal_cost=0.0, build_year=2030, lifetime=100,
          p_max_pu=pd.Series(
              ([1.0, 0.0, 1.0, 0.0] * 2) if alternating else 1.0, index=sns))
    set_bounds_for_asset(n, "Generator", "wind",
                         {"2030": {"p_nom_min": 0.0, "p_nom_max": 100.0},
                          "2040": {"p_nom_min": 0.0, "p_nom_max": 100.0}})
    return n


VINTAGE_REQUIRED_MW = 1.5 * 150.0          # (1 + 0.5) × peak
VINTAGE_BASE_FIRM_MW = GAS_DERATE * 200.0  # 190
VINTAGE_WIND_MW = VINTAGE_REQUIRED_MW - VINTAGE_BASE_FIRM_MW   # 35, derate 1.0


def test_a_vintage_expanded_plan_reports_the_capacity_it_built():
    """★ The LP builds exactly 35 MW of `wind@2030` to reach the standard.
    The payload must say so — in BOTH periods, since a 2030 vintage with a
    100-year lifetime is active in 2040 too — and name the vintage row with
    its built size.

    BROKEN VARIANT (bite): `_built()` without the vintage fallback — the row
    is gone from the network, the lookup returns None, firm capacity reads
    190 < 225 and `met` is False in both periods.
    """
    sink, status, _ = _solve(_vintage_network(), reserve_margin=0.5,
                             multi_investment_periods=True)
    assert status == "ok", sink
    block = _report(sink).reserve_margin
    by_period = {r.period: r for r in block.by_period}
    assert set(by_period) == {"2030", "2040"}
    for P, row in by_period.items():
        assert row.met is True, (P, row)
        assert row.firm_mw == pytest.approx(VINTAGE_REQUIRED_MW, rel=1e-6), (P, row)
    rows = {(a.name, a.period): a for a in block.assets}
    assert rows[("wind@2030", "2030")].capacity_mw == pytest.approx(VINTAGE_WIND_MW, rel=1e-6)
    assert rows[("wind@2030", "2040")].capacity_mw == pytest.approx(VINTAGE_WIND_MW, rel=1e-6)
    # the 2040 vintage was not needed and reads as built-to-zero, not None
    assert rows[("wind@2040", "2040")].capacity_mw == pytest.approx(0.0, abs=1e-9)


# ── ★ a failed margin run must not leak its targets into the next solve ─────
#
# Same review. The stash is deleted at the report step, inside the try body;
# an exception between the wrapper and that line lands in the outer handlers,
# which never touch the attribute, and the wrapper is a no-op without a
# margin so nothing overwrites it. The NEXT solve — one that set no margin —
# then publishes a margin result built on the dead run's targets.

def test_a_failed_margin_run_does_not_leak_its_targets_into_the_next_solve():
    """★ Run 1 sets a margin and fails after optimize. Run 2 sets NO margin.
    Run 2 must publish no reserve-margin result.

    BROKEN VARIANT (bite): do not clear `_reserve_margin_targets` at solve
    start — run 2 finds run 1's stash and publishes `margin 0.5, met=False`
    for a run that never asked for one.
    """
    import services.solver_service as S

    n = _network()
    orig = S._capture_extendable_p_nom_opt_to_frozen_store

    def boom(*_a, **_k):
        raise RuntimeError("simulated post-optimize failure")

    S._capture_extendable_p_nom_opt_to_frozen_store = boom
    try:
        sink1, status1, _ = _solve(n, reserve_margin=MARGIN)
    finally:
        S._capture_extendable_p_nom_opt_to_frozen_store = orig
    assert status1 != "ok", "the fixture did not fail run 1"
    assert "last_reserve_margin" not in sink1 or sink1["last_reserve_margin"] is None

    sink2, status2, _ = _solve(n)          # no margin at all
    assert status2 == "ok", sink2
    assert sink2.get("last_reserve_margin") is None, (
        "a run that set no margin published a margin result: "
        f"{sink2.get('last_reserve_margin')}")


def test_a_failed_ens_cap_run_does_not_leak_its_targets_into_the_next_solve():
    """★ The ENS-cap stash (`_ens_cap_targets`) is read and deleted on the
    same try body as the margin's, so it has the same leak. Run 1 sets a cap
    and fails after optimize; run 2 sets nothing and must publish no adequacy
    report at all — a report fires only when a standard's stash is present.

    BROKEN VARIANT (bite): clear only `_reserve_margin_targets` at solve
    start — run 2 then finds run 1's ENS stash and publishes a report for a
    standard it never set.
    """
    import services.solver_service as S

    n = _network()
    orig = S._capture_extendable_p_nom_opt_to_frozen_store

    def boom(*_a, **_k):
        raise RuntimeError("simulated post-optimize failure")

    S._capture_extendable_p_nom_opt_to_frozen_store = boom
    try:
        _sink1, status1, _ = _solve(n, voll=3000.0, ens_cap_permyriad=10.0)
    finally:
        S._capture_extendable_p_nom_opt_to_frozen_store = orig
    assert status1 != "ok", "the fixture did not fail run 1"

    sink2, status2, _ = _solve(n)          # no standard at all
    assert status2 == "ok", sink2
    assert sink2.get("adequacy_report") is None, (
        "a run that set no standard published an adequacy report: "
        f"{sink2.get('adequacy_report')}")



# ═══════════════════════════════════════════════════════════════════════════
# Phase 12b — the net-load window (plan v5 + v5.1). Every ★ names its bite.
# ═══════════════════════════════════════════════════════════════════════════

def _payload(n):
    from services.adequacy.report import reserve_margin_payload
    return reserve_margin_payload(n, _stash(n))


def _add_profiled(n, name, values, *, p_nom=100.0, carrier="wind",
                  extendable=False):
    """A generator with a TIME-SERIES `p_max_pu`. Carrier `wind` has no
    entry in the defaults library → must-take, priced by its profile alone."""
    kw = dict(bus="b", carrier=carrier, marginal_cost=0.0,
              p_max_pu=pd.Series(list(values), index=n.snapshots))
    if extendable:
        kw.update(p_nom=0.0, p_nom_extendable=True, p_nom_max=500.0,
                  capital_cost=1000.0)
    else:
        kw.update(p_nom=p_nom)
    if carrier not in n.carriers.index:
        n.add("Carrier", carrier)
    n.add("Generator", name, **kw)
    return n


def _row(payload, name, period="ALL"):
    for a in payload["assets"]:
        if a["name"] == name and a["period"] == period:
            return a
    raise KeyError((name, period))


def _snaps(n, *positions):
    return [str(n.snapshots[i]) for i in positions]


# ── ★ B1: the net window's CONTENT, on a fixture whose ordering is known ──

def test_the_net_window_is_the_hours_the_profile_is_absent():
    """★ Flat 150 MW load, one 100 MW farm on hours 0 and 2, off 1 and 3.
    Net load is [50,150,50,150]; N=1; the ≥-threshold rule returns BOTH
    tied hours 1 and 3. The gross window is all four (flat), so the overlap
    is 2 and the two windows are different objects.

    BROKEN VARIANTS (bites): (a) select the net window on gross demand —
    flat, so all four hours; (b) `nlargest(1)` — one hour.
    """
    n = _add_profiled(_network(peaker=False), "wind", [1, 0, 1, 0])
    _apply(n, reserve_margin=0.2)
    nw = _payload(n)["by_period"][0]["net_window"]
    assert nw["status"] == "ok", nw
    assert nw["netted_assets"] == ["wind"]
    assert nw["snapshots"] == _snaps(n, 1, 3)
    assert nw["n_hours"] == 2
    assert nw["overlap_hours"] == 2
    assert nw["net_peak_mw"] == pytest.approx(150.0)
    assert nw["gross_at_net_peak_mw"] == pytest.approx(150.0)
    assert nw["netted_mw"] == pytest.approx(50.0)     # 100 × mean(1,0,1,0)
    # the two totals, pinned (review finding 4): only rows WITH a profile
    # count — `base` has none — so gross = 0.5 × 100, net = 0.0 × 100.
    assert nw["firm_gross_mw"] == pytest.approx(50.0)
    assert nw["firm_net_mw"] == pytest.approx(0.0)


# ── ★ B2: the demand comes from the STASH, never from the network ─────────

def test_the_net_window_is_selected_on_the_stashed_demand_not_a_reread():
    """★ v1's BLOCKER 2 as a regression test. After the wrapper stashes a
    flat-150 demand, the network's load is overwritten with a spike that
    would put the net window on hour 2 alone. The payload must still report
    the stashed window (hours 1 and 3).

    Guard (the Phase-9 lesson): the two candidate windows are asserted to
    DIFFER first, so the fixture can see the defect.

    BROKEN VARIANT (bite): rebuild the demand series from `n.loads` inside
    the payload — the spike then selects hour 2.
    """
    n = _add_profiled(_network(peaker=False), "wind", [1, 0, 1, 0])
    _apply(n, reserve_margin=0.2)
    # simulate the restore leaving DIFFERENT loads behind
    n.loads.at["l", "p_set"] = 0.0
    n.loads_t.p_set["l"] = pd.Series([0.0, 0.0, 300.0, 0.0], index=n.snapshots)
    from services.adequacy.window import peak_window
    reread_net = n.loads_t.p_set["l"] - 100.0 * pd.Series([1, 0, 1, 0], index=n.snapshots)
    assert [str(x) for x in peak_window(reread_net)] == _snaps(n, 2)   # differs
    nw = _payload(n)["by_period"][0]["net_window"]
    assert nw["snapshots"] == _snaps(n, 1, 3), nw


# ── ★ B3: built capacity through `_built`, not nameplate ───────────────────

def test_an_extendable_is_netted_at_its_built_capacity():
    """★ An extendable farm with `p_nom = 0` and a hand-set `p_nom_opt = 80`
    (what a solve writes) is netted at 80: `netted_mw = 80 × 0.5 = 40`.

    BROKEN VARIANT (bite): read `p_nom` — 0 — so the farm is not built, not
    netted, and the block reads `nothing_netted`.
    """
    n = _add_profiled(_network(peaker=False), "wind", [1, 0, 1, 0], extendable=True)
    _apply(n, reserve_margin=0.2)
    n.generators["p_nom_opt"] = 0.0
    n.generators.at["wind", "p_nom_opt"] = 80.0
    p = _payload(n)
    row = _row(p, "wind")
    assert row["nettable"] is True and row["netted"] is True, row
    assert row["capacity_mw"] == pytest.approx(80.0)
    nw = p["by_period"][0]["net_window"]
    assert nw["status"] == "ok" and nw["netted_mw"] == pytest.approx(40.0), nw


# ── ★ B3b: a VINTAGE row on the netting path (v3 BLOCKER 1, pinned) ────────

VINTAGE_ALT_WIND_MW = (VINTAGE_REQUIRED_MW - VINTAGE_BASE_FIRM_MW) / 0.5   # 70


def test_a_vintage_row_is_netted_at_the_capacity_the_plan_built():
    """★ The alternating-profile vintage fixture. The expansion clones the
    1,0,1,0 profile onto `wind@2030`; the gross window is all four tied
    hours so the derate is 0.5; the LP builds exactly 70 MW of `wind@2030`.
    The restore then DROPS that row and its cloned column — so the profile
    the payload nets with must be the stashed one and the capacity must come
    through the vintage-aware `_built`.

    Per period: `wind@2030` netted at 70 (`netted_mw = 35`); net load is
    [80,150,80,150] so the net window is hours 1 and 3; `derate_net = 0.0` —
    the wind is available only when load is already served, which is this
    phase's whole point on one fixture. The zero-capacity rows are pinned
    too: the parent `wind` (capacity 0) and `wind@2040` (built 0) are
    `nettable` and NOT `netted`, so a bite that drops the `> 0` test flips
    those flags even though it changes no number.

    BROKEN VARIANT (bite): look the vintage up in the live `p_nom_opt` table
    by name → None → not netted → `nothing_netted` for a plan that built it.
    """
    sink, status, _ = _solve(_vintage_network(alternating=True),
                             reserve_margin=0.5, multi_investment_periods=True)
    assert status == "ok", sink
    from services.adequacy.report import sanitize_reserve_margin_payload
    p = sanitize_reserve_margin_payload(sink["last_reserve_margin"])
    per = {r["period"]: r for r in p["by_period"]}
    assert set(per) == {"2030", "2040"}
    for P in ("2030", "2040"):
        assert per[P]["met"] is True, per[P]
        nw = per[P]["net_window"]
        assert nw["status"] == "ok", nw
        assert nw["netted_assets"] == ["wind@2030"], nw
        assert nw["netted_mw"] == pytest.approx(0.5 * VINTAGE_ALT_WIND_MW)   # 35
        assert nw["n_hours"] == 2 and nw["net_peak_mw"] == pytest.approx(150.0)
        v = _row(p, "wind@2030", P)
        assert v["capacity_mw"] == pytest.approx(VINTAGE_ALT_WIND_MW, rel=1e-6)
        assert v["nettable"] is True and v["netted"] is True
        assert v["derate"] == pytest.approx(0.5)
        assert v["derate_net"] == pytest.approx(0.0)
        parent = _row(p, "wind", P)
        assert parent["nettable"] is True and parent["netted"] is False, parent
    v40 = _row(p, "wind@2040", "2040")
    assert v40["capacity_mw"] == pytest.approx(0.0, abs=1e-9)
    assert v40["nettable"] is True and v40["netted"] is False, v40


# ── ★ B4: no profile ⇒ derate_net is null, not a zero delta ────────────────

def test_a_profile_less_member_reports_no_net_derate():
    """★ A storage unit has a duration haircut, not a profile: its credit is
    window-independent, and a numeric "delta = 0" there would read as a
    clean bill for exactly the asset most sensitive to which hours the
    window picks. Null, with `profile_kind = "none"`.

    BROKEN VARIANT (bite): compute `derate_net` for every row — a number
    equal to `derate` appears.
    """
    n = _add_profiled(_network(peaker=False), "wind", [1, 0, 1, 0])
    n.add("Carrier", "battery")
    n.add("StorageUnit", "batt", bus="b", carrier="battery", p_nom=50.0,
          max_hours=4.0)
    _apply(n, reserve_margin=0.2)
    row = _row(_payload(n), "batt")
    assert row["profile_kind"] == "none"
    assert row["nettable"] is False and row["netted"] is False
    assert row["derate_net"] is None, row


# ── ★ B5′: the empty case is a STATUS, and the block is present ────────────

def test_nothing_netted_is_reported_as_a_status_not_as_a_zero_delta_window():
    """★ No profile-bearing capacity at all. The block is present with
    `status = "nothing_netted"`, an empty window and null numbers — never a
    net window identical to the gross one with every delta at zero, which
    would render as an all-clear.

    BROKEN VARIANT (bite): publish the gross window as the net window.
    """
    n = _network()
    _apply(n, reserve_margin=MARGIN)
    nw = _payload(n)["by_period"][0]["net_window"]
    assert nw is not None
    assert nw["status"] == "nothing_netted", nw
    assert nw["netted_assets"] == [] and nw["snapshots"] == [] and nw["n_hours"] == 0
    for k in ("net_peak_mw", "gross_at_net_peak_mw", "netted_mw",
              "overlap_hours", "firm_gross_mw", "firm_net_mw"):
        assert nw[k] is None, (k, nw[k])


# ── ★ B6: a SHADOWED farm is netted too (all of M, not the MC's residual) ──

def test_a_shadowed_farm_is_netted_alongside_a_must_take_one():
    """★ Two identical 100 MW farms with the 1,0,1,0 profile. `w_hydro` sits
    on a carrier the defaults library prices (q = 0.02), so it is
    occurrence-bearing — the Phase-12a shadowed case — while `w_wind` is
    must-take. Both are netted: the window asks when the SYSTEM runs short,
    and both farms' output reduces that. `netted_mw` is asserted EXACTLY as
    both farms' contribution, 100.

    BROKEN VARIANT (bite): net only `source == "missing"` — 50.
    """
    n = _add_profiled(_network(peaker=False), "w_wind", [1, 0, 1, 0])
    _add_profiled(n, "w_hydro", [1, 0, 1, 0], carrier="hydro")
    _apply(n, reserve_margin=0.2)
    p = _payload(n)
    assert _row(p, "w_hydro")["source"] == "carrier_default"
    assert _row(p, "w_hydro")["netted"] is True
    assert _row(p, "w_wind")["netted"] is True
    nw = p["by_period"][0]["net_window"]
    assert sorted(nw["netted_assets"]) == ["w_hydro", "w_wind"]
    assert nw["netted_mw"] == 100.0, nw["netted_mw"]


# ── ★ B8: the netting PREDICATE — a flat column cannot move a window ──────

def test_only_a_varying_profile_is_netted():
    """★ Three 100 MW gas units, all with a `p_max_pu` COLUMN: all-ones, a
    flat 0.9, and a maintenance schedule (off at hour 2). The first two are
    constant — subtracting a constant changes no ordering, and netting one
    would let the panel report a gas unit as netted capacity (v3 BLOCKER 2).
    The schedule varies and IS netted, as a stated decision.

    BROKEN VARIANT (bite): use Phase 12a's `_profile_is_informative`, which
    accepts a flat 0.9 — `gas_flat` is then netted and `netted_mw` reads 165.
    """
    n = _network(peaker=False)
    _add_profiled(n, "gas_ones", [1, 1, 1, 1], carrier="gas")
    _add_profiled(n, "gas_flat", [0.9, 0.9, 0.9, 0.9], carrier="gas")
    _add_profiled(n, "gas_maint", [1, 1, 0, 1], carrier="gas")
    _apply(n, reserve_margin=0.2)
    p = _payload(n)
    assert _row(p, "gas_ones")["profile_kind"] == "constant"
    assert _row(p, "gas_flat")["profile_kind"] == "constant"
    assert _row(p, "gas_maint")["profile_kind"] == "varying"
    assert _row(p, "gas_ones")["netted"] is False
    assert _row(p, "gas_flat")["netted"] is False
    assert _row(p, "gas_maint")["netted"] is True
    nw = p["by_period"][0]["net_window"]
    assert nw["netted_assets"] == ["gas_maint"]
    assert nw["netted_mw"] == pytest.approx(75.0)          # 100 × mean(1,1,0,1)
    assert nw["snapshots"] == _snaps(n, 2)                 # the outage hour


# ── ★ B9: the profile comes from the STASH — the column may be gone ────────

def test_derate_net_survives_the_profile_column_being_dropped():
    """★ What the restore does to a vintage's cloned column, done by hand:
    after the wrapper stashes, the farm's `p_max_pu` column is deleted from
    the network. The payload must still compute `derate_net` from the
    stashed series (v3 BLOCKER 1(b)).

    BROKEN VARIANT (bite): read `generators_t.p_max_pu[name]` in the payload
    — the column is gone.
    """
    n = _add_profiled(_network(peaker=False), "wind", [1, 0, 1, 0])
    _apply(n, reserve_margin=0.2)
    n.generators_t.p_max_pu = n.generators_t.p_max_pu.drop(columns=["wind"])
    assert "wind" not in n.generators_t.p_max_pu.columns
    row = _row(_payload(n), "wind")
    assert row["netted"] is True
    assert row["derate_net"] == pytest.approx(0.0), row      # absent on hours 1, 3


# ── ★ B10: NaN — two rules, both pinned ────────────────────────────────────

def test_a_nan_availability_hour_is_netted_as_unavailable_and_stays_in_the_window():
    """★ Rule 1 (window): profile [1, NaN, 1, 0]. NaN nets as 0, so net load
    is [50,150,50,150] and hour 1 — the NaN hour — is IN the window with
    hour 3. Rule 2 (derate_net): the guarded skipna mean over {1, 3} is
    mean(NaN, 0) = 0.0, finite.

    BROKEN VARIANT (bite): skip `fillna` — pandas skips the NaN hour in the
    comparison and hour 1 silently drops out of the window.
    """
    n = _add_profiled(_network(peaker=False), "wind", [1.0, float("nan"), 1.0, 0.0])
    _apply(n, reserve_margin=0.2)
    p = _payload(n)
    nw = p["by_period"][0]["net_window"]
    assert nw["status"] == "ok"
    assert nw["snapshots"] == _snaps(n, 1, 3), nw
    row = _row(p, "wind")
    assert row["derate_net"] == pytest.approx(0.0) and math.isfinite(row["derate_net"])


def test_an_all_nan_net_window_reads_zero_not_nan_and_serialises():
    """★ Profile [1, NaN, 0.5, 1]. It VARIES over its finite values (so it
    is netted — a profile that is 1.0 everywhere except one NaN is constant
    over its finite values and correctly is not, which is what the first
    draft of this test got wrong). Net load is [50,150,100,50], the window
    is hour 1 alone, and the only profile value there is NaN. The guarded
    mean reads 0.0 — "unknown = unavailable", consistent with rule 1 — and
    the sanitised payload serialises with `allow_nan=False`.

    BROKEN VARIANT (bite): drop the `_finite` guard — `derate_net` is NaN and
    the serialisation assertion fails.
    """
    import json
    from services.adequacy.report import sanitize_reserve_margin_payload

    n = _add_profiled(_network(peaker=False), "wind", [1.0, float("nan"), 0.5, 1.0])
    _apply(n, reserve_margin=0.2)
    p = _payload(n)
    nw = p["by_period"][0]["net_window"]
    assert nw["snapshots"] == _snaps(n, 1), nw
    row = _row(p, "wind")
    assert row["derate_net"] == 0.0, row
    json.dumps(sanitize_reserve_margin_payload(p), allow_nan=False)


# ── ★ B12: a `None` capacity on a nettable row must not lose the block ─────

def test_a_missing_built_capacity_is_not_netted_and_does_not_lose_the_block():
    """★ The vintage fixture solved, then its per-vintage breakdown removed
    from `n.meta`, so `_built` returns None for `wind@2030`. The row is
    `netted = False`, the block is present, and no exception escapes.

    BROKEN VARIANT (bite): `cap > 0` without the `None` test — `TypeError`,
    caught by the solver's broad except, and the whole margin block is gone.
    """
    from services.adequacy.report import reserve_margin_payload
    import services.adequacy.report as R

    n = _vintage_network(alternating=True)
    captured = {}
    orig = R.reserve_margin_payload

    def spy(net, targets, **kw):
        captured["targets"] = targets
        return orig(net, targets, **kw)

    R.reserve_margin_payload = spy
    try:
        sink, status, _ = _solve(n, reserve_margin=0.5, multi_investment_periods=True)
    finally:
        R.reserve_margin_payload = orig
    assert status == "ok"
    n.meta.pop("vintage_results", None)
    p = reserve_margin_payload(n, captured["targets"])       # must not raise
    v = _row(p, "wind@2030", "2030")
    assert v["capacity_mw"] is None
    assert v["nettable"] is True and v["netted"] is False
    assert p["by_period"][0]["net_window"]["status"] == "nothing_netted"


# ── ★ B13: non-finite demand degrades to a status on BOTH surfaces ─────────

def test_non_finite_demand_yields_a_status_and_the_report_still_builds():
    """★ A static `p_set` of NaN passes the facts loop (`float(nan or 0.0)`
    is nan — the recorded wart) and the peak is NaN. Preflight refuses this
    live, so the harness is the wrapper + payload directly. The route
    surface nulls the peak; the report surface must ACCEPT that null rather
    than throw the whole adequacy report away, which it did before
    `ReserveMarginPeriod.peak_mw` was widened (v5 review SERIOUS 2).

    BROKEN VARIANT (bite): `peak_mw: float` (non-Optional) — the report
    validation raises.
    """
    from models.adequacy import ReserveMarginBlock
    from services.adequacy.report import sanitize_reserve_margin_payload

    n = _add_profiled(_network(peaker=False), "wind", [1, 0, 1, 0])
    n.loads.at["l", "p_set"] = float("nan")
    _apply(n, reserve_margin=0.2)
    p = _payload(n)
    nw = p["by_period"][0]["net_window"]
    assert nw["status"] == "no_finite_demand", nw
    san = sanitize_reserve_margin_payload(p)
    assert san["by_period"][0]["peak_mw"] is None
    block = ReserveMarginBlock.model_validate(san)            # must not raise
    assert block.by_period[0].net_window.status == "no_finite_demand"


# ── ★ B14: the report surface and the route surface AGREE, field for field ─

def test_the_report_block_equals_the_route_payload_field_for_field():
    """★ Amendment v1.2(7): the adequacy report and `/results/reserve_margin`
    are two views of one payload. `net_window` is compared on
    `.model_dump()` against the SANITISED sink payload (the route sanitises
    at serve time; the report is built from the sanitised dict), so every
    key the block emits must be one the model declares, and vice versa.

    BROKEN VARIANT (bite): leave the pydantic models unchanged —
    `AttributeError: 'ReserveMarginPeriod' object has no attribute
    'net_window'`.
    """
    from services.adequacy.report import sanitize_reserve_margin_payload

    sink, status, _ = _solve(_vintage_network(alternating=True),
                             reserve_margin=0.5, multi_investment_periods=True)
    assert status == "ok", sink
    san = sanitize_reserve_margin_payload(sink["last_reserve_margin"])
    rep = _report(sink).reserve_margin
    assert rep is not None
    for i, row in enumerate(rep.by_period):
        assert row.net_window is not None
        assert row.net_window.model_dump() == san["by_period"][i]["net_window"]
    for i, a in enumerate(rep.assets):
        for k in ("profile_kind", "nettable", "netted", "derate_net"):
            assert getattr(a, k) == san["assets"][i][k], (a.name, k)


# ── B11: the sanitiser descends (contract pin, not a bite) ─────────────────

def test_the_sanitiser_nulls_non_finite_values_inside_the_net_window_and_asset_rows():
    import json
    from services.adequacy.report import sanitize_reserve_margin_payload

    payload = {
        "margin": 0.2, "horizon_wide": True,
        "by_period": [{
            "period": "ALL", "peak_mw": 150.0, "required_mw": 180.0,
            "firm_mw": 190.0, "margin_achieved": 0.27, "met": True,
            "binding": False, "n_peak_hours": 4, "peak_snapshots": [],
            "max_achievable_mw": float("inf"),
            "net_window": {"status": "ok", "netted_assets": ["w"],
                           "snapshots": ["x"], "n_hours": 1,
                           "net_peak_mw": float("nan"),
                           "gross_at_net_peak_mw": 150.0,
                           "netted_mw": float("inf"), "overlap_hours": 1,
                           "firm_gross_mw": 1.0, "firm_net_mw": float("nan")},
        }],
        "assets": [{"name": "w", "period": "ALL", "kind": "generator",
                    "capacity_mw": 100.0, "derate": 0.5, "basis": "",
                    "source": "missing", "extendable": False,
                    "firm_mw": 50.0, "energy_limited": False,
                    "profile_kind": "varying", "nettable": True,
                    "netted": True, "derate_net": float("nan")}],
        "derating_bases": {},
    }
    san = sanitize_reserve_margin_payload(payload)
    nw = san["by_period"][0]["net_window"]
    assert nw["net_peak_mw"] is None and nw["netted_mw"] is None and nw["firm_net_mw"] is None
    assert nw["gross_at_net_peak_mw"] == 150.0
    assert san["assets"][0]["derate_net"] is None
    json.dumps(san, allow_nan=False)
    # The sanitiser touches ONLY what the model declares Optional. `derate`
    # and `firm_mw` are `float` on `ReserveMarginAsset`; nulling them would
    # make the route ship a null the report surface rejects (review, 2).
    payload["assets"][0]["derate"] = float("nan")
    payload["assets"][0]["firm_mw"] = float("nan")
    san2 = sanitize_reserve_margin_payload(payload)
    assert math.isnan(san2["assets"][0]["derate"])
    assert math.isnan(san2["assets"][0]["firm_mw"])


# ── review findings 1 and 3: the enum never lies, and myopic is said ───────

def test_an_empty_window_with_finite_demand_is_its_own_status_not_no_finite_demand():
    """Demand [150, NaN, NaN, NaN] with `prm_peak_hours = 3`: the peak IS
    150, but the window's threshold lands on a NaN and the window is empty.
    Labelling that `no_finite_demand` beside `peak_mw = 150` would be false.
    Not reachable from the facts loop today; the enum is the contract.

    BROKEN VARIANT (bite): report `no_finite_demand` for the empty window.
    """
    from services.adequacy.report import reserve_margin_payload

    n = _add_profiled(_network(peaker=False), "wind", [1, 0, 1, 0])
    _apply(n, reserve_margin=0.2)
    st = _stash(n)
    per = st["periods"]["ALL"]
    per["demand_mw"] = pd.Series([150.0, float("nan"), float("nan"), float("nan")],
                                 index=n.snapshots)
    per["peak_hours_override"] = 3
    nw = reserve_margin_payload(n, st)["by_period"][0]["net_window"]
    assert nw["status"] == "empty_window", nw
    assert nw["snapshots"] == [] and nw["net_peak_mw"] is None


def test_the_payload_says_when_it_describes_the_last_period_only():
    """★ Under the myopic strategy each iteration overwrites the stash, so the
    payload describes ONE period. That was always true of every margin
    field; Phase 12b's review found the spec promised the panel would say
    so and nothing carried the fact. `partial_periods` does.

    BROKEN VARIANT (bite): drop the flag — the model default hides it.
    """
    from models.adequacy import ReserveMarginBlock
    from services.adequacy.report import reserve_margin_payload, sanitize_reserve_margin_payload

    n = _add_profiled(_network(peaker=False), "wind", [1, 0, 1, 0])
    _apply(n, reserve_margin=0.2)
    st = _stash(n)
    assert reserve_margin_payload(n, st)["partial_periods"] is False
    p = reserve_margin_payload(n, st, partial=True)
    assert p["partial_periods"] is True
    assert ReserveMarginBlock.model_validate(sanitize_reserve_margin_payload(p)).partial_periods is True
