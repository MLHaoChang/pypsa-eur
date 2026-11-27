"""link-flow/1 (assistant-assurance 09, S4).

A hand LP, solved the way the app solves, read through the checked profile and
`/results/links`. The expected figures are literals worked out below, never
read from the services under test.

Snapshots: three, generator weights 1, 3 and 0.5; the objective weights
(2, 1, 1) and store weights differ, so a wrong column gives another number.

Buses A, B and C. Generator ga at A (10 EUR/MWh, 1000 MW). Generator gc at C
(5 EUR/MWh, 40 MW, p_max_pu 1, 1, 0). Load LB at B: 9, 18, 4.5 MW. Load LC at
C: 0, 0, 6 MW.

- Link ab, A -> B: time-varying efficiency 0.9, 0.75, 0.5 (static 0.6, never
  read). Extendable with p_nom 5, p_nom_min 30 and capital cost 1, so
  p_nom_opt is 30. p0 = LB / eta = 10, 24, 9; p1 = -9, -18, -4.5.
  Input 1 x 10 + 3 x 24 + 0.5 x 9 = 86.5 MWh. Output 9 + 54 + 2.25 =
  65.25 MWh. Loss 21.25 MWh. Peak 24 MW, loading 100 x 24 / 30 = 80 %.
  With the objective weights the input would be 20 + 24 + 9 = 53 MWh.
- Link ca, C -> A: static efficiency 1, p_nom 50, p_min_pu -1. gc is cheaper,
  so it carries ab's input at t0 and t1 (10, 24). At t2 gc is off and LC is
  fed from A, in reverse: p0 = 10, 24, -6; p1 = -10, -24, 6. Input = output
  = 10 + 72 - 3 = 79 MWh, loss exactly 0. Peak 24 MW, loading 48 %.
"""

# ruff: noqa: F811 -- imported shared fixtures.

import math

import pandas as pd
import pypsa
import pytest

from harness import assurance_scope, calculations, profile_common
from harness import link_flow_calculations as link
from harness.derived import NEAR_ZERO
from services import assurance_records as records
from services import workflow_tools
from tests.assurance_acceptance.support import approval
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_workflow_tools import baseline  # noqa: F401

STAMPS = pd.date_range("2026-01-01", periods=3, freq="h")
WEIGHTS = [1.0, 3.0, 0.5]
HAND_REL = 1e-12  # relative tolerance of the hand-number comparisons below
AB = {
    "link_input_energy": 86.5,
    "link_output_energy": 65.25,
    "link_conversion_loss": 21.25,
    "peak_link_flow": 24.0,
    "peak_link_loading": 80.0,
}
CA = {
    "link_input_energy": 79.0,
    "link_output_energy": 79.0,
    "link_conversion_loss": 0.0,
    "peak_link_flow": 24.0,
    "peak_link_loading": 48.0,
}
UNITS = {m: d["unit"] for m, d in link.METRICS.items()}


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", link.PROFILE)


def _network(ca_efficiency=1.0):
    n = pypsa.Network()
    n.set_snapshots(STAMPS)
    n.snapshot_weightings["generators"] = WEIGHTS
    n.snapshot_weightings["objective"] = [2.0, 1.0, 1.0]
    n.snapshot_weightings["stores"] = [0.25, 2.0, 3.0]
    n.add("Carrier", "AC")
    n.add("Bus", ["A", "B", "C"], v_nom=110, carrier="AC")
    n.add("Generator", "ga", bus="A", carrier="AC", p_nom=1000, marginal_cost=10.0)
    n.add(
        "Generator",
        "gc",
        bus="C",
        carrier="AC",
        p_nom=40,
        p_max_pu=[1.0, 1.0, 0.0],
        marginal_cost=5.0,
    )
    n.add("Load", "LB", bus="B", p_set=[9.0, 18.0, 4.5])
    n.add("Load", "LC", bus="C", p_set=[0.0, 0.0, 6.0])
    n.add(
        "Link",
        "ab",
        bus0="A",
        bus1="B",
        efficiency=[0.9, 0.75, 0.5],
        p_nom=5,
        p_nom_extendable=True,
        p_nom_min=30,
        capital_cost=1.0,
    )
    n.links.loc["ab", "efficiency"] = 0.6  # the static value; the series wins
    n.add(
        "Link",
        "ca",
        bus0="C",
        bus1="A",
        efficiency=ca_efficiency,
        p_nom=50,
        p_min_pu=-1.0,
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
def hand_link(baseline):
    """The hand network, unsolved: `solve_link` solves it."""
    ctx = baseline[0]
    ctx.network = _network()
    return ctx


def solve_link(ctx):
    return _solve(ctx)


@pytest.fixture
def link_case(baseline):
    ctx = baseline[0]

    def make(tweak=None, **kw):
        ctx.network = _network(**kw)
        if tweak is not None:
            tweak(ctx.network)
        return _solve(ctx)

    return make


def _scope(ctx, entity="ab", window=None):
    scope = {
        "version": 1,
        "profile": link.PROFILE,
        "project_id": ctx.project_uuid,
        "entity_id": entity,
        "retain": False,
    }
    if window:
        scope.update(start=window[0], end=window[1])
    return scope


def _checked(ctx, entity="ab", window=None):
    record = records.capture_scoped(records.actor_id(), _scope(ctx, entity, window))
    values = {f["metric"]: (f["value"], f["unit"]) for f in record["facts"]}
    reasons = {
        s["metric"]: s["reason"]
        for s in record["slots"]
        if s["disposition"] == "unavailable"
    }
    return values, reasons


def _raw(ctx, entity="ab"):
    return calculations.scoped_source(
        ctx, {"profile": link.PROFILE, "entity_id": entity}
    )


def _slots(raw, window=None):
    scope = {"profile": link.PROFILE, "entity_id": raw["entity_id"]}
    if window:
        scope.update(start=window[0], end=window[1])
    facts, slots = link.calculate(raw, scope)
    return (
        {f["metric"]: f["value"] for f in facts},
        {s["metric"]: s["reason"] for s in slots if s["disposition"] == "unavailable"},
    )


def _hand_raw(p0, eta, weights=None, p1=None, p_nom_opt=10.0):
    """A raw record built by hand, for the guard and boundary cases."""
    count = len(p0)
    return {
        "version": 1,
        "profile": link.PROFILE,
        "entity_id": "x",
        "index": [
            s.isoformat() for s in pd.date_range("2026-01-01", periods=count, freq="h")
        ],
        "energy_weight": list(weights or [1.0] * count),
        "bus0": "A",
        "bus1": "B",
        "extra_ports": [],
        "p0": list(p0),
        "p1": list(p1) if p1 is not None else [-e * a for a, e in zip(p0, eta)],
        "efficiency_source": "series",
        "efficiency": list(eta),
        "p_nom_opt": p_nom_opt,
        "valid_solve": True,
    }


def _live(monkeypatch):
    """For a network edited after its solve: the edit, not the fingerprint, is under test."""
    monkeypatch.setattr(profile_common, "lp_outputs_live", lambda ctx: True)


# ── the hand arithmetic ───────────────────────────────────────────────────


def test_link_figures_equal_the_hand_arithmetic(link_case):
    ctx = link_case()
    for entity, hand in (("ab", AB), ("ca", CA)):
        values, reasons = _checked(ctx, entity)
        assert reasons == {}, entity
        assert values == {
            m: (pytest.approx(v, rel=HAND_REL, abs=HAND_REL), UNITS[m])
            for m, v in hand.items()
        }, entity
    # The weights are unequal and the objective column would give another input.
    assert AB["link_input_energy"] != 20 + 24 + 9
    assert (
        ctx.network.links.at["ab", "p_nom"] != ctx.network.links.at["ab", "p_nom_opt"]
    )


def test_the_lossless_reverse_link_has_an_exact_zero_loss(link_case):
    values, reasons = _checked(link_case(), "ca")
    assert "link_conversion_loss" not in reasons
    assert values["link_conversion_loss"] == (0.0, "MWh")


def test_a_window_is_its_own_arithmetic(link_case):
    ctx = link_case()
    window = (STAMPS[1].isoformat(), STAMPS[2].isoformat())
    values, reasons = _checked(ctx, "ab", window)
    assert reasons == {}
    assert values["link_input_energy"][0] == pytest.approx(76.5, rel=HAND_REL)
    assert values["link_output_energy"][0] == pytest.approx(56.25, rel=HAND_REL)
    assert values["link_conversion_loss"][0] == pytest.approx(20.25, rel=HAND_REL)
    last = (STAMPS[2].isoformat(), STAMPS[2].isoformat())
    values, _ = _checked(ctx, "ca", last)
    # The peak is over the window only: |p0| at t2 is 6, not the horizon's 24.
    assert values["peak_link_flow"][0] == pytest.approx(6.0, rel=HAND_REL)
    assert values["peak_link_loading"][0] == pytest.approx(12.0, rel=HAND_REL)


def test_the_series_efficiency_is_read_not_the_static_one(link_case):
    raw = _raw(link_case())
    assert raw["efficiency_source"] == "series"
    assert raw["efficiency"] == pytest.approx([0.9, 0.75, 0.5], rel=HAND_REL)
    ca = _raw(link_case(), "ca")
    assert (ca["efficiency_source"], ca["efficiency"]) == ("static", [1.0, 1.0, 1.0])


def test_a_nan_series_efficiency_is_missing_data_not_the_static_value(
    link_case, monkeypatch
):
    ctx = link_case()
    _live(monkeypatch)
    ctx.network.links_t.efficiency.loc[STAMPS[1], "ab"] = float("nan")
    raw = _raw(ctx)
    assert raw["efficiency"][1] is None
    assert set(_slots(raw)[1].values()) == {"invalid_or_missing_raw_data"}


def test_loading_above_one_hundred_percent_is_not_clamped(link_case):
    def tweak(n):
        n.links.loc["ab", "p_max_pu"] = 1.2
        n.links.loc["ab", "p_nom_min"] = 22.0

    values, reasons = _checked(link_case(tweak))
    assert reasons == {}
    assert values["peak_link_loading"][0] == pytest.approx(100 * 24 / 22, rel=HAND_REL)
    assert values["peak_link_loading"][0] > 100


# ── reverse flow ──────────────────────────────────────────────────────────


def test_reverse_flow_through_a_lossy_link_withholds_the_loss_alone(link_case):
    """Link ca at efficiency 0.95: p0 = 10/0.95, 24/0.95, -6 and p1 = -10, -24, 5.7 (a booked gain)."""
    values, reasons = _checked(link_case(ca_efficiency=0.95), "ca")
    assert reasons == {"link_conversion_loss": "reverse_flow_with_losses"}
    assert values["link_input_energy"][0] == pytest.approx(82 / 0.95 - 3, rel=HAND_REL)
    assert values["link_output_energy"][0] == pytest.approx(79.15, rel=HAND_REL)
    assert values["peak_link_flow"][0] == pytest.approx(24 / 0.95, rel=HAND_REL)


@pytest.mark.parametrize(
    "reverse,withheld",
    [(-NEAR_ZERO, False), (-2 * NEAR_ZERO, True)],
    ids=["at", "past"],
)
def test_the_reverse_flow_boundary(reverse, withheld):
    values, reasons = _slots(_hand_raw([10.0, reverse, 0.0], [0.5, 0.5, 0.5]))
    assert reasons == (
        {"link_conversion_loss": "reverse_flow_with_losses"} if withheld else {}
    )
    if not withheld:
        # The signed loss: sum w (1 - eta) p0, where |p0| would give 5 + 5e-10.
        assert values["link_conversion_loss"] == pytest.approx(
            0.5 * (10 + reverse), rel=HAND_REL
        )


def test_a_lossless_reverse_snapshot_is_answered_with_zero_loss():
    values, reasons = _slots(_hand_raw([0.0, -4.0, 0.0], [1.0, 1.0, 1.0]))
    assert reasons == {}
    assert values["link_conversion_loss"] == 0.0
    assert values["link_input_energy"] == -4.0 and values["link_output_energy"] == -4.0


# ── peaks ─────────────────────────────────────────────────────────────────


def test_a_zero_weight_snapshot_still_counts_for_the_peak():
    """A peak is per snapshot: a zero energy weight does not hide it."""
    values, reasons = _slots(
        _hand_raw([1.0, 7.0, 2.0], [1.0, 1.0, 1.0], weights=[1.0, 0.0, 1.0])
    )
    assert reasons == {}
    assert values["peak_link_flow"] == 7.0
    assert values["peak_link_loading"] == 70.0
    assert values["link_input_energy"] == 3.0


def test_the_peak_is_the_largest_magnitude_either_way():
    values, _ = _slots(_hand_raw([3.0, -5.0, 1.0], [1.0, 1.0, 1.0]))
    assert values["peak_link_flow"] == 5.0


# ── capacity ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "capacity",
    [None, 0.0, -1.0, NEAR_ZERO],
    ids=["missing", "zero", "negative", "near_zero"],
)
def test_no_capacity_withholds_the_loading_alone(capacity):
    values, reasons = _slots(_hand_raw([1.0, 2.0], [1.0, 1.0], p_nom_opt=capacity))
    assert reasons == {"peak_link_loading": "no_link_capacity"}
    assert values["peak_link_flow"] == 2.0


def test_just_above_near_zero_capacity_is_answered():
    values, reasons = _slots(_hand_raw([1.0, 2.0], [1.0, 1.0], p_nom_opt=2 * NEAR_ZERO))
    assert reasons == {}
    assert values["peak_link_loading"] == 100.0 * 2.0 / (2 * NEAR_ZERO)


@pytest.mark.parametrize("capacity", [float("nan"), float("inf")], ids=["nan", "inf"])
def test_a_nan_or_infinite_capacity_is_read_as_missing(
    link_case, monkeypatch, capacity
):
    ctx = link_case()
    _live(monkeypatch)
    ctx.network.links.loc["ab", "p_nom_opt"] = capacity
    raw = _raw(ctx)
    assert raw["p_nom_opt"] is None
    assert _slots(raw)[1] == {"peak_link_loading": "no_link_capacity"}


def test_the_loading_reads_the_optimised_capacity(link_case):
    raw = _raw(link_case())
    assert raw["p_nom_opt"] == pytest.approx(30.0, rel=HAND_REL)


# ── guards ────────────────────────────────────────────────────────────────


def test_the_reasons_and_refusals_are_the_named_sets():
    assert link.REASONS == {
        "unverified_solve",
        "unsupported_link_ports",
        "invalid_or_missing_raw_data",
        "inconsistent_link_flow",
        "reverse_flow_with_losses",
        "efficiency_above_one",
        "no_link_capacity",
    }
    assert link.REFUSALS == {"unknown_link", "unsupported_snapshot_scope"}


def _every(raw):
    return set(_slots(raw)[1].values())


GUARD_CASES = {
    "unverified_solve": lambda r: r.update(valid_solve=False),
    "unsupported_link_ports": lambda r: r.update(extra_ports=["bus2"]),
    "invalid_or_missing_raw_data": lambda r: r["p1"].__setitem__(1, None),
    "inconsistent_link_flow": lambda r: r["p1"].__setitem__(1, r["p1"][1] + 1e-3),
}


@pytest.mark.parametrize("reason", sorted(GUARD_CASES))
def test_each_guard_withholds_every_figure(reason):
    raw = _hand_raw([1.0, 2.0, 3.0], [0.9, 0.9, 0.9])
    assert _every(raw) == set()
    GUARD_CASES[reason](raw)
    assert _every(raw) == {reason}
    assert set(_slots(raw)[1]) == set(link.METRICS)


def test_every_reason_has_a_case():
    covered = set(GUARD_CASES) | {
        "reverse_flow_with_losses",
        "efficiency_above_one",
        "no_link_capacity",
    }
    assert covered == link.REASONS


@pytest.mark.parametrize("weight", [-0.5, None], ids=["negative", "non_finite"])
def test_a_negative_or_non_finite_weight_answers_nothing(weight):
    raw = _hand_raw([1.0, 2.0, 3.0], [1.0, 1.0, 1.0], weights=[1.0, weight, 1.0])
    assert _every(raw) == {"unverified_solve"}


@pytest.mark.parametrize("field", ["p0", "p1", "efficiency"])
def test_a_missing_series_or_value_is_missing_data(field):
    raw = _hand_raw([1.0, 2.0, 3.0], [1.0, 1.0, 1.0])
    raw[field][2] = None
    assert _every(raw) == {"invalid_or_missing_raw_data"}
    raw = _hand_raw([1.0, 2.0, 3.0], [1.0, 1.0, 1.0])
    if field != "efficiency":
        raw[field] = None
        assert _every(raw) == {"invalid_or_missing_raw_data"}


def test_the_first_failing_guard_wins():
    raw = _hand_raw([1.0, 2.0], [1.0, 1.0])
    raw.update(valid_solve=False, extra_ports=["bus2"])
    raw["p0"][0] = None
    assert _every(raw) == {"unverified_solve"}
    raw["valid_solve"] = True
    assert _every(raw) == {"unsupported_link_ports"}
    raw["extra_ports"] = []
    assert _every(raw) == {"invalid_or_missing_raw_data"}
    raw["p0"][0] = 1.0
    raw["p1"][1] = 5.0
    assert _every(raw) == {"inconsistent_link_flow"}


@pytest.mark.parametrize(
    "p0,offset,consistent",
    [
        (0.0, 1e-6, True),
        (0.0, 2e-6, False),
        (1024.0, 0.5 * 1024e-6, True),
        (1024.0, 2 * 1024e-6, False),
    ],
    ids=["unit_at", "unit_past", "scaled_inside", "scaled_past"],
)
def test_the_flow_tolerance_boundary(p0, offset, consistent):
    assert link.FLOW_TOLERANCE == 1e-6
    raw = _hand_raw([p0], [1.0], p1=[-p0 + offset])
    assert _every(raw) == (set() if consistent else {"inconsistent_link_flow"})


def test_a_three_port_link_is_refused_and_its_empty_ports_elsewhere_are_not(
    link_case, monkeypatch
):
    def tweak(n):
        n.add(
            "Link",
            "abc",
            bus0="A",
            bus1="B",
            bus2="C",
            efficiency=0.5,
            efficiency2=0.1,
            p_nom=0,
        )

    ctx = link_case(tweak)
    assert ctx.network.links.at["ab", "bus2"] == ""
    assert _checked(ctx, "ab")[1] == {}
    assert set(_checked(ctx, "abc")[1].values()) == {"unsupported_link_ports"}
    _live(monkeypatch)
    ctx.network.links.loc["ab", "bus2"] = float("nan")
    assert _slots(_raw(ctx))[1] == {}
    ctx.network.links.loc["ab", "bus2"] = " B "
    assert _every(_raw(ctx)) == {"unsupported_link_ports"}


def test_an_edit_after_the_solve_withholds_every_figure(link_case):
    ctx = link_case()
    ctx.network.loads.loc["LB", "p_set"] = 1.0
    assert set(_checked(ctx)[1].values()) == {"unverified_solve"}


# ── refusals, identity and grammar ────────────────────────────────────────


def test_an_unknown_link_is_refused_with_its_kind(link_case):
    ctx = link_case()
    scope = assurance_scope.validate(_scope(ctx, "nope"))
    with pytest.raises(ValueError) as refused:
        records.capture_scoped(records.actor_id(), scope)
    assert refused.value.args[0] == "unknown_link"


def test_a_multi_period_network_is_refused(link_case):
    ctx = link_case()
    n = ctx.network
    n.set_snapshots(pd.MultiIndex.from_product([[2030], STAMPS]))
    n.set_investment_periods([2030])
    with pytest.raises(ValueError, match="unsupported_snapshot_scope"):
        _raw(ctx)


def test_a_record_of_another_link_is_rejected(link_case):
    raw = _raw(link_case())
    with pytest.raises(ValueError, match="link_flow_source_identity"):
        link.calculate(raw, {"profile": link.PROFILE, "entity_id": "ca"})


def test_a_cost_or_comparison_scope_is_refused():
    base = {
        "version": 1,
        "profile": link.PROFILE,
        "project_id": "p1",
        "entity_id": "ab",
    }
    with pytest.raises(ValueError, match="unsupported_result_cost_scope"):
        assurance_scope.validate({**base, "comparison_evidence_id": "0" * 36})


def test_grammar():
    scope = assurance_scope.from_message("Show the link flow for link ab.", "p1", {})
    assert (scope["profile"], scope["entity_id"]) == (link.PROFILE, "ab")
    windowed = assurance_scope.from_message(
        "show link flow for link H2 pipe; from 2026-01-01T01:00 to 2026-01-01T02:00",
        "p1",
        {},
    )
    assert (windowed["entity_id"], windowed["start"], windowed["end"]) == (
        "H2 pipe",
        "2026-01-01T01:00",
        "2026-01-01T02:00",
    )
    assert (
        assurance_scope.from_message("show the link flow for line ab", "p1", {}) is None
    )


def test_the_profile_is_opt_in():
    assert link.PROFILE in calculations.OPT_IN_PROFILES


# ── the route: /results/links serves p0 ───────────────────────────────────


def test_the_route_shows_the_same_peak(link_case, client):
    ctx = link_case()
    payload = client.get("/api/results/links").json()
    column = payload["columns"].index("ab")
    p0 = [row[column] for row in payload["data"]]
    values, _ = _checked(ctx)
    assert max(abs(v) for v in p0) == pytest.approx(
        values["peak_link_flow"][0], rel=HAND_REL
    )
    assert p0 == pytest.approx([10.0, 24.0, 9.0], rel=HAND_REL)


def test_notes_name_the_direction_the_efficiency_and_the_capacity(link_case):
    raw = _raw(link_case())
    lines = link.notes(raw, {}, str)
    assert lines[0].startswith(
        "Flow from A (bus0) to B (bus1), time-varying efficiency"
    )
    assert lines[1] == "Loading is against the optimised capacity p_nom_opt (30 MW)."
    assert math.isfinite(raw["p_nom_opt"])


@pytest.mark.parametrize(
    "eta,withheld",
    [(1.0, False), (1.0 + 1e-12, True), (3.5, True)],
    ids=["one", "just_above", "cop"],
)
def test_an_efficiency_above_one_withholds_the_loss_alone(eta, withheld):
    """A heat pump's COP books more out than in: never a negative loss."""
    values, reasons = _slots(_hand_raw([2.0, 4.0], [1.0, eta]))
    assert reasons == (
        {"link_conversion_loss": "efficiency_above_one"} if withheld else {}
    )
    assert values["link_input_energy"] == 6.0
    assert values["link_output_energy"] == 2.0 + 4.0 * eta


def test_an_efficiency_above_one_wins_over_reverse_flow():
    reasons = _slots(_hand_raw([-2.0, 4.0], [0.5, 3.5]))[1]
    assert reasons == {"link_conversion_loss": "efficiency_above_one"}


@pytest.mark.parametrize("port", [None, "", float("nan")], ids=["none", "blank", "nan"])
def test_an_empty_port_value_is_not_a_port(port):
    assert link._filled(port) is False
    assert link._filled("C") is True


def test_an_unknown_link_keeps_its_kind_through_the_turn(link_case, checked):
    ctx = link_case()
    frames = checked.run(
        NumericalProvider({}),
        message="Check link flow.",
        checked_scope=_scope(ctx, "nope"),
    )
    receipt = approval(frames).get("receipt") or {}
    assert receipt.get("request_complete") is False
    assert {s["reason"] for s in receipt["result_slots"]} == {"unknown_link"}


def test_unusable_capacity_reads_not_available_in_the_notes():
    for capacity in (None, 0.0, -1.0):
        raw = _hand_raw([1.0], [1.0], p_nom_opt=capacity)
        assert link.notes(raw, {}, str)[1].endswith("(not available).")
