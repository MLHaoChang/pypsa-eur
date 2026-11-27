"""store-dispatch/1 (assistant-assurance 09, S5).

A hand LP, solved the way the app solves, read through the checked profile and
`/results/store_dispatch` and `/results/store_energy`. The expected figures are
literals worked out below, never read from the services under test.

Snapshots: three. Store weights w = 2, 0.5, 1; the generator weights (1, 3,
0.5) and the objective weights (3, 1, 2) differ at every snapshot, so a wrong
column gives another number. PyPSA's balance: e[t] = eff[t] e[t-1] - w[t] p[t]
with eff = (1 - sl)^w, p > 0 discharging. `e_set` pins each Store's energy, so
the solve is unique.

Bus A: generator ga (10 EUR/MWh, 1000 MW), load LA 10 MW.

- Store s, non-cyclic, e_initial 20. Time-varying standing loss 0.5, 0.19,
  0.1 (static 0.3, never read), so eff = 0.25, 0.9, 0.9. e = 12, 13.8, 10.42.
  PyPSA applies no standing loss to e_initial: e0 = 20 - 2 x 4, so p0 = 4;
  e1 = 0.9 x 12 + 0.5 x 6 (p1 = -6); e2 = 0.9 x 13.8 - 2 (p2 = 2).
  Charged 0.5 x 6 = 3 MWh, discharged 2 x 4 + 1 x 2 = 10 MWh, net 7 MWh.
  Loss 0 + 0.1 x 12 + 0.1 x 13.8 = 2.58 MWh (20 - 10.42 = 10 - 3 + 2.58).
  Peaks 6 and 4 MW, final 10.42 MWh. Extendable with e_nom 5, e_nom_min 23 and
  capital cost 1, so e_nom_opt is 23: fill 100 x 13.8 / 23 = 60 %. e_max_pu
  1, 0.7, 1 and e_min_pu 0, 0.5, 0 (static e_max_pu 0.5, never read: 0.5 x 23
  < 13.8). With the generator weights the charge would be 18, the objective's 6.
- Store c, cyclic, e_initial 5 (ignored), e_nom 12, the same standing loss.
  e = 8, 9, 6 from e[last] = 6: p = -3.25, -3.6, 2.1. Charged 6.5 + 1.8 = 8.3,
  discharged 2.1, net -6.2, loss 0.75 x 6 + 0.1 x 8 + 0.1 x 9 = 6.2 MWh.
  Peaks 3.6 and 2.1 MW, final 6 MWh, fill 75 %.
"""

# ruff: noqa: F811 -- imported shared fixtures.

import pandas as pd
import pypsa
import pytest

from harness import assurance_scope, calculations, fact_identity, profile_common
from harness import store_dispatch_calculations as store
from harness.derived import NEAR_ZERO
from services import assurance_records as records
from services import workflow_tools
from tests.assurance_acceptance.support import approval
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_workflow_tools import baseline  # noqa: F401

STAMPS = pd.date_range("2026-01-01", periods=3, freq="h")
STORE_WEIGHTS = [2.0, 0.5, 1.0]
HAND_REL = 1e-12  # relative tolerance of the hand-number comparisons below
S = {
    "store_charged_energy": 3.0,
    "store_discharged_energy": 10.0,
    "store_net_energy": 7.0,
    "store_standing_loss": 2.58,
    "peak_store_charging": 6.0,
    "peak_store_discharging": 4.0,
    "final_store_energy": 10.42,
    "peak_store_fill": 60.0,
}
C = {
    "store_charged_energy": 8.3,
    "store_discharged_energy": 2.1,
    "store_net_energy": -6.2,
    "store_standing_loss": 6.2,
    "peak_store_charging": 3.6,
    "peak_store_discharging": 2.1,
    "final_store_energy": 6.0,
    "peak_store_fill": 75.0,
}
UNITS = {m: d["unit"] for m, d in store.METRICS.items()}


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", store.PROFILE)


def _network():
    n = pypsa.Network()
    n.set_snapshots(STAMPS)
    n.snapshot_weightings["generators"] = [1.0, 3.0, 0.5]
    n.snapshot_weightings["objective"] = [3.0, 1.0, 2.0]
    n.snapshot_weightings["stores"] = STORE_WEIGHTS
    n.add("Carrier", "AC")
    n.add("Bus", "A", v_nom=110, carrier="AC")
    n.add("Generator", "ga", bus="A", carrier="AC", p_nom=1000, marginal_cost=10.0)
    n.add("Load", "LA", bus="A", p_set=10.0)
    n.add(
        "Store",
        "s",
        bus="A",
        e_initial=20.0,
        e_set=[12.0, 13.8, 10.42],
        standing_loss=[0.5, 0.19, 0.1],
        e_max_pu=[1.0, 0.7, 1.0],
        e_min_pu=[0.0, 0.5, 0.0],
        e_nom=5,
        e_nom_extendable=True,
        e_nom_min=23,
        capital_cost=1.0,
    )
    # The static values; the series win.
    n.stores.loc["s", ["standing_loss", "e_max_pu"]] = [0.3, 0.5]
    n.add(
        "Store",
        "c",
        bus="A",
        e_cyclic=True,
        e_initial=5.0,
        e_nom=12,
        e_set=[8.0, 9.0, 6.0],
        standing_loss=[0.5, 0.19, 0.1],
    )
    return n


def _solve(ctx):
    n = ctx.network
    assert n.optimize(solver_name="highs")[1] == "optimal"
    ctx.solver_state.update(status="completed", condition="optimal")
    ctx.solver_state.pop("last_lost_load", None)
    workflow_tools._record_solve_inputs(n, ctx.solver_state["solver_config"])
    return ctx


@pytest.fixture
def hand_store(baseline):
    """The hand network, unsolved: `solve_store` solves it."""
    ctx = baseline[0]
    ctx.network = _network()
    return ctx


def solve_store(ctx):
    return _solve(ctx)


@pytest.fixture
def store_case(baseline):
    ctx = baseline[0]

    def make(tweak=None):
        ctx.network = _network()
        if tweak is not None:
            tweak(ctx.network)
        return _solve(ctx)

    return make


def _scope(ctx, entity="s", window=None):
    scope = {
        "version": 1,
        "profile": store.PROFILE,
        "project_id": ctx.project_uuid,
        "entity_id": entity,
        "retain": False,
    }
    if window:
        scope.update(start=window[0], end=window[1])
    return scope


def _checked(ctx, entity="s", window=None):
    record = records.capture_scoped(records.actor_id(), _scope(ctx, entity, window))
    values = {f["metric"]: (f["value"], f["unit"]) for f in record["facts"]}
    reasons = {
        s["metric"]: s["reason"]
        for s in record["slots"]
        if s["disposition"] == "unavailable"
    }
    return values, reasons


def _raw(ctx, entity="s"):
    return calculations.scoped_source(
        ctx, {"profile": store.PROFILE, "entity_id": entity}
    )


def _slots(raw, window=None):
    scope = {"profile": store.PROFILE, "entity_id": raw["entity_id"]}
    if window:
        scope.update(start=window[0], end=window[1])
    facts, slots = store.calculate(raw, scope)
    return (
        {f["metric"]: f["value"] for f in facts},
        {s["metric"]: s["reason"] for s in slots if s["disposition"] == "unavailable"},
    )


def _hand_raw(
    p,
    sl=None,
    weights=None,
    e_initial=0.0,
    cyclic=False,
    e_nom_opt=100.0,
    e_min_pu=None,
    e_max_pu=None,
):
    """A raw record built by hand, its e following PyPSA's balance from p."""
    count = len(p)
    sl = list(sl or [0.0] * count)
    weights = list(weights or [1.0] * count)
    e, previous = [], e_initial
    for t, (w, x, loss) in enumerate(zip(weights, p, sl)):
        previous = ((1.0 - loss) ** w if t else 1.0) * previous - w * x
        e.append(previous)
    return {
        "version": 1,
        "profile": store.PROFILE,
        "entity_id": "x",
        "index": [
            s.isoformat() for s in pd.date_range("2026-01-01", periods=count, freq="h")
        ],
        "energy_weight": weights,
        "bus": "A",
        "cyclic": cyclic,
        "e_initial": e_initial,
        "p": list(p),
        "e": e,
        "standing_loss_source": "series",
        "standing_loss": sl,
        "e_min_pu": list(e_min_pu or [-10.0] * count),
        "e_max_pu": list(e_max_pu or [10.0] * count),
        "e_nom_opt": e_nom_opt,
        "valid_solve": True,
    }


def _live(monkeypatch):
    """For a network edited after its solve: the edit, not the fingerprint, is under test."""
    monkeypatch.setattr(profile_common, "lp_outputs_live", lambda ctx: True)


def _every(raw, window=None):
    return set(_slots(raw, window)[1].values())


def _at(*positions):
    return tuple(STAMPS[i].isoformat() for i in positions)


# ── the hand arithmetic ───────────────────────────────────────────────────


def test_store_figures_equal_the_hand_arithmetic(store_case):
    ctx = store_case()
    for entity, hand in (("s", S), ("c", C)):
        values, reasons = _checked(ctx, entity)
        assert reasons == {}, entity
        assert values == {
            m: (pytest.approx(v, rel=HAND_REL, abs=HAND_REL), UNITS[m])
            for m, v in hand.items()
        }, entity
    n = ctx.network
    assert n.stores.at["s", "e_nom"] != n.stores.at["s", "e_nom_opt"]
    assert list(n.stores_t.p["s"]) == pytest.approx([4.0, -6.0, 2.0], rel=HAND_REL)


def test_the_store_weights_differ_from_the_other_columns(store_case):
    """Condition 1: a generator or objective weight would give another charge."""
    weights = store_case().network.snapshot_weightings
    for other in ("generators", "objective"):
        assert (weights[other] != weights["stores"]).all(), other
    assert 3 * 6 != S["store_charged_energy"] != 1 * 6


def test_the_weight_basis_follows_the_definitions():
    rows = [m for m in fact_identity.WEIGHT_BASIS if m in store.METRICS]
    assert rows == list(store.METRICS)
    assert {m: fact_identity.WEIGHT_BASIS[m] for m in rows} == {
        "store_charged_energy": "stores",
        "store_discharged_energy": "stores",
        "store_net_energy": "derived",
        "store_standing_loss": "stores",
        "peak_store_charging": "none",
        "peak_store_discharging": "none",
        "final_store_energy": "none",
        "peak_store_fill": "none",
    }


def test_the_series_standing_loss_and_bounds_are_read_not_the_static_ones(store_case):
    raw = _raw(store_case())
    assert raw["standing_loss_source"] == "series"
    assert raw["standing_loss"] == pytest.approx([0.5, 0.19, 0.1], rel=HAND_REL)
    assert raw["e_max_pu"] == pytest.approx([1.0, 0.7, 1.0], rel=HAND_REL)
    assert raw["e_min_pu"] == pytest.approx([0.0, 0.5, 0.0], rel=HAND_REL)
    assert raw["e_nom_opt"] == pytest.approx(23.0, rel=HAND_REL)


def test_a_store_without_a_series_reads_the_static_standing_loss(store_case):
    def tweak(n):
        n.stores_t.standing_loss.drop(columns="c", inplace=True)
        n.stores.loc["c", "standing_loss"] = 0.0
        # No loss: e = 8, 9, 6 from e[last] = 6 needs p = -1, -2, 3 (weighted sum 0).

    ctx = store_case(tweak)
    raw = _raw(ctx, "c")
    assert raw["standing_loss_source"] == "static"
    assert raw["standing_loss"] == [0.0, 0.0, 0.0]
    values, reasons = _checked(ctx, "c")
    assert reasons == {}
    assert values["store_standing_loss"] == (0.0, "MWh")
    assert values["store_charged_energy"][0] == pytest.approx(3.0, rel=HAND_REL)


def test_a_nan_series_standing_loss_is_missing_data_not_the_static_value(
    store_case, monkeypatch
):
    ctx = store_case()
    _live(monkeypatch)
    ctx.network.stores_t.standing_loss.loc[STAMPS[1], "s"] = float("nan")
    raw = _raw(ctx)
    assert raw["standing_loss"][1] is None
    assert _every(raw) == {"invalid_or_missing_raw_data"}


# ── the state before the window ───────────────────────────────────────────


def test_a_window_starts_from_the_energy_before_it(store_case):
    ctx = store_case()
    values, reasons = _checked(ctx, "s", _at(1, 2))
    assert reasons == {}
    # From e0 = 12, never e_initial = 20 (which would book 0.1 x 20 at t1).
    assert values["store_standing_loss"][0] == pytest.approx(2.58, rel=HAND_REL)
    assert values["store_charged_energy"][0] == pytest.approx(3.0, rel=HAND_REL)
    assert values["store_discharged_energy"][0] == pytest.approx(2.0, rel=HAND_REL)
    assert values["store_net_energy"][0] == pytest.approx(-1.0, rel=HAND_REL)


def test_a_cyclic_store_starts_from_its_last_energy(store_case):
    """Store c at t0 alone: from e[last] = 6, loss 0.75 x 6; e_initial 5 would break the balance."""
    ctx = store_case()
    assert ctx.network.stores.at["c", "e_initial"] == 5.0
    values, reasons = _checked(ctx, "c", _at(0, 0))
    assert reasons == {}
    assert values["store_standing_loss"][0] == pytest.approx(4.5, rel=HAND_REL)
    assert values["final_store_energy"][0] == pytest.approx(8.0, rel=HAND_REL)


def test_the_final_value_and_the_fill_are_over_the_window(store_case):
    ctx = store_case()
    values, _ = _checked(ctx, "s", _at(0, 0))
    assert values["final_store_energy"][0] == pytest.approx(12.0, rel=HAND_REL)
    assert values["peak_store_fill"][0] == pytest.approx(100 * 12 / 23, rel=HAND_REL)
    values, _ = _checked(ctx, "s", _at(0, 1))
    assert values["final_store_energy"][0] == pytest.approx(13.8, rel=HAND_REL)


def test_no_loss_applies_to_e_initial():
    """PyPSA 1.1.2: e[0] = e_initial - w p[0] for a non-cyclic Store."""
    values, reasons = _slots(_hand_raw([1.0], sl=[0.5], weights=[2.0], e_initial=10.0))
    assert reasons == {}
    assert values["final_store_energy"] == 8.0
    assert values["store_standing_loss"] == 0.0


@pytest.mark.parametrize("cyclic,answered", [(True, True), (False, False)])
def test_a_nan_e_initial_is_missing_only_where_it_is_used(cyclic, answered):
    raw = _hand_raw([1.0, -2.0], sl=[0.1, 0.1], e_initial=5.0, cyclic=cyclic)
    if cyclic:
        raw["e"] = _cycle([1.0, -2.0], [0.1, 0.1])
    raw["e_initial"] = None
    assert _every(raw) == (set() if answered else {"invalid_or_missing_raw_data"})
    # A window after the first snapshot never reads e_initial.
    assert _every(raw, (raw["index"][1], raw["index"][1])) == set()


def _cycle(p, sl):
    """The unique cyclic e for unit weights: e = A e + b solved on two snapshots."""
    f0, f1 = (1.0 - sl[0]), (1.0 - sl[1])
    # e0 = f0 e1 - p0 and e1 = f1 e0 - p1.
    e0 = (-p[0] - f0 * p[1]) / (1.0 - f0 * f1)
    return [e0, f1 * e0 - p[1]]


# ── guards ────────────────────────────────────────────────────────────────


def test_the_reasons_and_refusals_are_the_named_sets():
    assert store.REASONS == {
        "unverified_solve",
        "invalid_or_missing_raw_data",
        "unsupported_standing_loss",
        "inconsistent_store_balance",
        "store_bound_violation",
        "negative_store_energy_with_losses",
        "no_store_capacity",
    }
    assert store.REFUSALS == {"unknown_store", "unsupported_snapshot_scope"}


def _base():
    return _hand_raw([1.0, -2.0, 3.0], sl=[0.1, 0.1, 0.1], e_initial=50.0)


GUARD_CASES = {
    "unverified_solve": lambda r: r.update(valid_solve=False),
    "invalid_or_missing_raw_data": lambda r: r["p"].__setitem__(1, None),
    "unsupported_standing_loss": lambda r: r["standing_loss"].__setitem__(1, 1.0),
    "inconsistent_store_balance": lambda r: r["e"].__setitem__(1, r["e"][1] + 1e-3),
    "store_bound_violation": lambda r: r["e_max_pu"].__setitem__(1, 0.4),
}


@pytest.mark.parametrize("reason", sorted(GUARD_CASES))
def test_each_guard_withholds_every_figure(reason):
    raw = _base()
    assert _every(raw) == set()
    GUARD_CASES[reason](raw)
    assert _every(raw) == {reason}
    assert set(_slots(raw)[1]) == set(store.METRICS)


def test_every_reason_has_a_case():
    covered = set(GUARD_CASES) | {
        "negative_store_energy_with_losses",
        "no_store_capacity",
    }
    assert covered == store.REASONS


def test_the_first_failing_guard_wins():
    raw = _base()
    for apply in GUARD_CASES.values():
        apply(raw)
    assert _every(raw) == {"unverified_solve"}
    raw["valid_solve"] = True
    assert _every(raw) == {"invalid_or_missing_raw_data"}
    raw["p"][1] = -2.0
    assert _every(raw) == {"unsupported_standing_loss"}
    raw["standing_loss"][1] = 0.1
    assert _every(raw) == {"inconsistent_store_balance"}
    raw["e"][1] -= 1e-3
    assert _every(raw) == {"store_bound_violation"}


@pytest.mark.parametrize("weight", [-0.5, None], ids=["negative", "non_finite"])
def test_a_negative_or_non_finite_weight_answers_nothing(weight):
    raw = _base()
    raw["energy_weight"][1] = weight
    assert _every(raw) == {"unverified_solve"}


@pytest.mark.parametrize("field", ["p", "e", "standing_loss", "e_min_pu", "e_max_pu"])
def test_a_missing_value_is_missing_data(field):
    raw = _base()
    raw[field][2] = None
    assert _every(raw) == {"invalid_or_missing_raw_data"}


@pytest.mark.parametrize("field", ["p", "e"])
def test_a_missing_series_is_missing_data(field):
    raw = _base()
    raw[field] = None
    assert _every(raw) == {"invalid_or_missing_raw_data"}


@pytest.mark.parametrize(
    "loss,supported",
    [(0.0, True), (0.999, True), (1.0, False), (-0.01, False), (1.5, False)],
    ids=["zero", "below_one", "one", "negative", "above_one"],
)
def test_a_standing_loss_outside_zero_to_one_answers_nothing(loss, supported):
    raw = _hand_raw([1.0, -2.0], sl=[0.0, loss], e_initial=50.0)
    assert _every(raw) == (set() if supported else {"unsupported_standing_loss"})


@pytest.mark.parametrize(
    "energy,offset,consistent",
    [
        (0.0, 1e-6, True),
        (0.0, 2e-6, False),
        (1024.0, 0.5 * 1024e-6, True),
        (1024.0, 2 * 1024e-6, False),
    ],
    ids=["unit_at", "unit_past", "scaled_inside", "scaled_past"],
)
def test_the_balance_tolerance_boundary(energy, offset, consistent):
    assert store.BALANCE_TOLERANCE == 1e-6
    raw = _hand_raw([0.0], e_initial=energy, e_nom_opt=None)
    raw["e"][0] += offset
    assert _every(raw) == (
        {"no_store_capacity"} if consistent else {"inconsistent_store_balance"}
    )


@pytest.mark.parametrize(
    "side,offset,within",
    [
        ("upper", 0.5e-5, True),
        ("upper", 2e-5, False),
        ("lower", -0.5e-6, True),
        ("lower", -2e-6, False),
        ("scaled_lower", -2.5e-6, True),
        ("scaled_lower", -1e-5, False),
    ],
    ids=[
        "upper_inside",
        "upper_past",
        "lower_inside",
        "lower_past",
        "scaled_lower_inside",
        "scaled_lower_past",
    ],
)
def test_the_bound_tolerance_on_both_sides(side, offset, within):
    """Capacity 10: the slack is BALANCE_TOLERANCE x max(1, |e|). Bounds 0 and 1,
    or 0.5 and 1 for the scaled lower case (slack 5e-6 at e = 5)."""
    level = {"upper": 10.0, "lower": 0.0, "scaled_lower": 5.0}[side]
    low = 0.5 if side == "scaled_lower" else 0.0
    raw = _hand_raw(
        [-(level + offset)], e_nom_opt=10.0, e_min_pu=[low], e_max_pu=[1.0]
    )
    assert _every(raw) == (set() if within else {"store_bound_violation"})


def test_a_store_outside_its_series_bound_is_caught(store_case, monkeypatch):
    """The series bound, not the static 0.5: e1 = 13.8 sits under 0.7 x 23 only."""
    ctx = store_case()
    _live(monkeypatch)
    assert _every(_raw(ctx)) == set()
    ctx.network.stores_t.e_max_pu.loc[STAMPS[1], "s"] = 0.5
    assert _every(_raw(ctx)) == {"store_bound_violation"}
    ctx.network.stores_t.e_max_pu.loc[STAMPS[1], "s"] = 0.7
    ctx.network.stores_t.e_min_pu.loc[STAMPS[1], "s"] = 0.61
    assert _every(_raw(ctx)) == {"store_bound_violation"}


def test_an_unusable_capacity_skips_the_bounds_and_keeps_seven_figures():
    raw = _base()
    raw["e_max_pu"] = [None, 0.0, 0.0]  # broken bounds go unread without a capacity
    raw["e_nom_opt"] = None
    values, reasons = _slots(raw)
    assert reasons == {"peak_store_fill": "no_store_capacity"}
    assert len(values) == 7


@pytest.mark.parametrize(
    "capacity",
    [None, 0.0, -1.0, NEAR_ZERO, float("nan"), float("inf")],
    ids=["missing", "zero", "negative", "near_zero", "nan", "inf"],
)
def test_no_capacity_withholds_the_fill_alone(capacity):
    raw = _hand_raw([-1.0, -1.0], e_nom_opt=capacity)
    assert _slots(raw)[1] == {"peak_store_fill": "no_store_capacity"}


def test_the_fill_is_not_clamped_below_zero():
    """e_min_pu < 0 lets e go negative: the fill is negative too."""
    raw = _hand_raw([2.0, -1.0], e_nom_opt=10.0, e_min_pu=[-1.0, -1.0])
    values, reasons = _slots(raw)
    assert reasons == {}
    assert values["peak_store_fill"] == -10.0


def test_a_negative_energy_with_losses_withholds_the_loss_alone():
    raw = _hand_raw([2.0, 0.0], sl=[0.0, 0.1], e_nom_opt=10.0, e_min_pu=[-1.0, -1.0])
    values, reasons = _slots(raw)
    assert reasons == {"store_standing_loss": "negative_store_energy_with_losses"}
    assert values["final_store_energy"] == pytest.approx(-1.8, rel=HAND_REL)
    # Without a loss on that snapshot the negative energy is no gain.
    raw = _hand_raw([2.0, 0.0], sl=[0.1, 0.0], e_nom_opt=10.0, e_min_pu=[-1.0, -1.0])
    assert _slots(raw)[1] == {}


def test_a_multi_period_network_is_refused(store_case):
    ctx = store_case()
    n = ctx.network
    n.set_snapshots(pd.MultiIndex.from_product([[2030], STAMPS]))
    n.set_investment_periods([2030])
    with pytest.raises(ValueError, match="unsupported_snapshot_scope"):
        _raw(ctx)


def test_an_edit_after_the_solve_withholds_every_figure(store_case):
    ctx = store_case()
    ctx.network.loads.loc["LA", "p_set"] = 1.0
    assert set(_checked(ctx)[1].values()) == {"unverified_solve"}


# ── peaks ─────────────────────────────────────────────────────────────────


def test_a_zero_weight_snapshot_still_counts_for_the_peak():
    values, reasons = _slots(
        _hand_raw([1.0, 7.0, 2.0], weights=[1.0, 0.0, 1.0], e_initial=20.0)
    )
    assert reasons == {}
    assert values["peak_store_discharging"] == 7.0
    assert values["store_discharged_energy"] == 3.0


def test_a_store_that_never_charges_has_zero_charge_exactly():
    values, _ = _slots(_hand_raw([1.0, 0.5, 2.0], e_initial=20.0))
    assert values["peak_store_charging"] == 0.0
    assert values["store_charged_energy"] == 0.0
    assert values["store_net_energy"] == 3.5


# ── refusals, identity and grammar ────────────────────────────────────────


def test_an_unknown_store_is_refused_with_its_kind(store_case):
    ctx = store_case()
    scope = assurance_scope.validate(_scope(ctx, "nope"))
    with pytest.raises(ValueError) as refused:
        records.capture_scoped(records.actor_id(), scope)
    assert refused.value.args[0] == "unknown_store"


def test_an_unknown_store_keeps_its_kind_through_the_turn(store_case, checked):
    ctx = store_case()
    frames = checked.run(
        NumericalProvider({}),
        message="Check store dispatch.",
        checked_scope=_scope(ctx, "nope"),
    )
    receipt = approval(frames).get("receipt") or {}
    assert receipt.get("request_complete") is False
    assert {s["reason"] for s in receipt["result_slots"]} == {"unknown_store"}


def test_a_record_of_another_store_is_rejected(store_case):
    raw = _raw(store_case())
    with pytest.raises(ValueError, match="store_dispatch_source_identity"):
        store.calculate(raw, {"profile": store.PROFILE, "entity_id": "c"})


def test_a_cost_or_comparison_scope_is_refused():
    base = {
        "version": 1,
        "profile": store.PROFILE,
        "project_id": "p1",
        "entity_id": "s",
    }
    with pytest.raises(ValueError, match="unsupported_result_cost_scope"):
        assurance_scope.validate({**base, "comparison_evidence_id": "0" * 36})


def test_grammar():
    scope = assurance_scope.from_message("Show the store dispatch for store s.", "p1", {})
    assert (scope["profile"], scope["entity_id"]) == (store.PROFILE, "s")
    windowed = assurance_scope.from_message(
        "show store dispatch for store H2 tank; from 2026-01-01T01:00 to 2026-01-01T02:00",
        "p1",
        {},
    )
    assert (windowed["entity_id"], windowed["start"], windowed["end"]) == (
        "H2 tank",
        "2026-01-01T01:00",
        "2026-01-01T02:00",
    )
    assert (
        assurance_scope.from_message("show the store dispatch for link s", "p1", {})
        is None
    )


def test_the_profile_is_opt_in(monkeypatch):
    from harness import rollout

    assert store.PROFILE in calculations.OPT_IN_PROFILES
    assert calculations.PROFILES[store.PROFILE] == tuple(store.METRICS)
    assert rollout.profile_enabled(store.PROFILE) is True  # listed by the autouse fixture
    monkeypatch.delenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES")
    assert rollout.profile_enabled(store.PROFILE) is False


# ── the routes: raw p and e, unweighted ───────────────────────────────────


def _route(client, path, column):
    payload = client.get(f"/api/results/{path}").json()
    position = payload["columns"].index(column)
    return [row[position] for row in payload["data"]]


def test_the_routes_show_the_same_peaks_final_value_and_fill(store_case, client):
    ctx = store_case()
    p = _route(client, "store_dispatch", "s")
    e = _route(client, "store_energy", "s")
    # Raw and unweighted: the LP's own p and e.
    assert p == pytest.approx([4.0, -6.0, 2.0], rel=HAND_REL)
    assert e == pytest.approx([12.0, 13.8, 10.42], rel=HAND_REL)
    values, _ = _checked(ctx)
    capacity = ctx.network.stores.at["s", "e_nom_opt"]
    for metric, route in (
        ("peak_store_charging", max(max(0.0, -x) for x in p)),
        ("peak_store_discharging", max(max(0.0, x) for x in p)),
        ("final_store_energy", e[-1]),
        ("peak_store_fill", 100 * max(e) / capacity),
    ):
        assert values[metric][0] == pytest.approx(route, rel=HAND_REL), metric


def test_notes_name_the_sign_the_start_and_the_capacity(store_case):
    ctx = store_case()
    lines = store.notes(_raw(ctx), {}, str)
    assert lines == [
        "Store at A: p > 0 is discharge to the bus, p < 0 charging; time-varying standing loss.",
        "The window starts from e_initial, with no standing loss applied.",
        "Fill is against the optimised capacity e_nom_opt (23 MWh).",
    ]
    assert store.notes(_raw(ctx, "c"), {}, str)[1] == (
        "The window starts from the last snapshot's energy (cyclic store)."
    )
    windowed = {"start": STAMPS[1].isoformat(), "end": STAMPS[2].isoformat()}
    assert store.notes(_raw(ctx), windowed, str)[1] == (
        "The window starts from the energy at the snapshot before the window."
    )
    raw = _hand_raw([1.0], e_nom_opt=None)
    raw["standing_loss_source"] = "static"
    assert store.notes(raw, {}, str)[0].endswith("static standing loss.")
    assert store.notes(raw, {}, str)[2].endswith("(not available).")


@pytest.mark.parametrize("field,value", [("version", 2), ("profile", "link-flow/1")])
def test_a_record_of_another_version_or_profile_is_rejected(field, value):
    raw = _hand_raw([1.0], e_initial=5.0)
    store.calculate(raw, {"profile": store.PROFILE, "entity_id": "x"})
    raw[field] = value
    with pytest.raises(ValueError, match="store_dispatch_source_identity"):
        store.calculate(raw, {"profile": store.PROFILE, "entity_id": "x"})
