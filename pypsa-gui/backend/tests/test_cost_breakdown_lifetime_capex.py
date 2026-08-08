"""
`/api/results/cost_breakdown` must never present an unresolvable lifetime
CAPEX as 0.00 — and must not report one as unresolvable when it can be derived.

The defect, measured on the user's live network:

    Generator    capex   271,434,259.98   capex_lifetime 1,054,393,908.71
    Link          capex   81,430,196.78   capex_lifetime   316,317,857.14
    Line       capex 8,067,640,123.99     capex_lifetime           0.00   <--
    StorageUnit  capex            0.00    capex_lifetime           0.00

Line was 95.8% of system CAPEX and its lifetime figure read 0.00, so the
top-level `capex_lifetime` excluded the dominant component with nothing on
screen saying so.

Diagnosis (empirical, not inferred): `n.c["Line"].overnight_cost` RAISES.
PyPSA back-calculates an upfront cost from `capital_cost / (annuity x nyears)`
for assets that left `overnight_cost` blank, and refuses — `ValueError:
Cannot back-calculate overnight_cost for ['L1', ...] : both 'discount_rate'
and 'lifetime' must be provided` — when `discount_rate` is NaN. The transient
fill (`fill_periodized_cost_defaults`) only populated `discount_rate` for
assets that already carry an `overnight_cost`, which lines do not. The bare
`except Exception: continue` in `get_cost_breakdown` then dropped the whole
component class, and the emission's `.get(comp_class, 0.0)` turned the missing
key into a confident zero. It was NOT the `fillna(0)` and NOT the NaN/inf
coercion — both are downstream of a `continue` that already fired.

Two things follow, and this file pins both:

  1. It IS derivable. `discount_rate` has a global value in the solver config —
     the same one `_pv_factor_series` already substitutes per asset in this very
     function — so the fill is widened to cover back-calculable assets and PyPSA
     computes the real number. A real number beats a null.
  2. When it genuinely cannot be resolved, the answer is `null` plus the
     `capex_lifetime_available` flag, never 0.0 — the same shape and vocabulary
     `get_asset_economics` adopted in d11d4ee1 (`capital_costs_available` +
     nulls). Nulls propagate into the totals: a horizon total that silently
     omits 95.8% of the system is the defect, not the fix.

Every test below names the production change that makes it fail, and each was
verified by making that change rather than by reading the code.
"""
from __future__ import annotations

import logging

import pandas as pd
import pypsa
import pytest

import routers.results as R
import routers.simulation as sim_router
from routers.results import get_cost_breakdown
from services.solver_service import SolverConfig

# Textbook capital recovery factor, written out here rather than imported so
# the expectation is an independent restatement of the arithmetic and not a
# copy of whatever the implementation happens to call.
DISCOUNT_RATE = 0.07          # SolverConfig default
LINE_LIFETIME = 40.0
GEN_LIFETIME = 25.0
CRF_40 = DISCOUNT_RATE * (1 + DISCOUNT_RATE) ** LINE_LIFETIME / (
    (1 + DISCOUNT_RATE) ** LINE_LIFETIME - 1
)

LINE_CAPITAL_COST = 1_165_000.0   # EUR/MW for the modelled horizon
LINE_S_NOM = 500.0
GEN_OVERNIGHT_COST = 1_200_000.0  # EUR/MW upfront, typed by the user
GEN_P_NOM = 500.0

# The upfront cost PyPSA derives for the line once `discount_rate` is present:
# capital_cost / (annuity x nyears), with nyears pinned to 1.0 by the snapshot
# weightings below.
LINE_UPFRONT = LINE_CAPITAL_COST / CRF_40
LINE_LIFETIME_CAPEX = LINE_UPFRONT * LINE_S_NOM
GEN_LIFETIME_CAPEX = GEN_OVERNIGHT_COST * GEN_P_NOM


def _solved_network() -> pypsa.Network:
    """
    Two buses, one costed AC line, one generator — solved by HiGHS.

    The line mirrors the live network exactly: a `capital_cost` with NO
    `overnight_cost` and NO `discount_rate`, which is the input shape that
    made `n.c["Line"].overnight_cost` raise.

    The generator is the control in the other direction: it carries a typed
    `overnight_cost`, so it always resolved and always reported a real
    lifetime CAPEX. Any fix that reports the whole network as unavailable has
    to break the generator's number to do it.

    `snapshot_weightings` are set so `n.nyears == 1.0` (4 x 2190 = 8760 h),
    which pins PyPSA's `capital_cost / (annuity x nyears)` back-calculation to
    a number that can be written down by hand.

    The generator's `discount_rate` is set for the solve and CLEARED again
    afterwards. That is not a convenience — it is what the live network looks
    like: PyPSA's consistency check refuses `overnight_cost` without a
    `discount_rate`, `run_simulation` fills the field transiently to get past
    it, and `fill_periodized_cost_defaults`' revert puts the NaN back the
    moment the solve finishes. A fixture that left the rate populated would
    never exercise the fill that `get_cost_breakdown` depends on.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 2190.0   # -> n.nyears == 1.0
    n.add("Bus", "A", v_nom=380.0)
    n.add("Bus", "B", v_nom=380.0)
    n.add("Carrier", "gas")
    n.add("Carrier", "ac")
    n.add("Load", "L", bus="B", p_set=50.0)
    # Priced through `overnight_cost` — resolves without any fill widening.
    n.add(
        "Generator", "G", bus="A", carrier="gas", p_nom=GEN_P_NOM,
        marginal_cost=10.0, overnight_cost=GEN_OVERNIGHT_COST,
        lifetime=GEN_LIFETIME, discount_rate=DISCOUNT_RATE,
    )
    # Priced through `capital_cost` only — the shape that broke.
    n.add(
        "Line", "L1", bus0="A", bus1="B", carrier="ac", x=0.1, r=0.01,
        s_nom=LINE_S_NOM, capital_cost=LINE_CAPITAL_COST,
        lifetime=LINE_LIFETIME,
    )
    n.optimize(solver_name="highs")
    # Post-solve state, as the app leaves it: the transient discount-rate fill
    # has been reverted, so nothing on the network carries a rate any more.
    n.generators["discount_rate"] = float("nan")
    return n


def _by_component(payload: dict) -> dict[str, dict]:
    return {row["component"]: row for row in payload["by_component"]}


@pytest.fixture()
def payload(install_network) -> dict:
    n = _solved_network()
    install_network(n)
    sim_router._state["solver_config"] = SolverConfig()
    out = get_cost_breakdown()
    assert isinstance(out, dict), (
        "cost_breakdown returned a 204 — the fixture is not dispatch-ready, "
        "so nothing below is testing what it claims to test"
    )
    return out


def _boom_for_lines(n, comp_class: str):
    """Resolver that fails for Line only, exactly as PyPSA's ValueError did."""
    if comp_class == "Line":
        raise ValueError("Cannot back-calculate overnight_cost for ['L1']")
    return n.c[comp_class].overnight_cost


@pytest.fixture()
def line_unresolvable(install_network, monkeypatch) -> dict:
    """The response when Line's upfront cost cannot be resolved at all."""
    n = _solved_network()
    install_network(n)
    sim_router._state["solver_config"] = SolverConfig()
    monkeypatch.setattr(R, "_upfront_cost_series", _boom_for_lines)
    out = get_cost_breakdown()
    assert isinstance(out, dict)
    return out


# ── 1. The dominant component reports a real number ───────────────────────

def test_a_line_priced_by_capital_cost_reports_a_real_lifetime_capex(payload):
    """
    Fails if: the `except Exception: continue` around the upfront-cost resolve
    is restored, or the widened `for_back_calculation` fill is dropped from
    `with_periodized_cost_defaults`.

    This is the whole defect in one assertion. Under the old code `Line` never
    entered `capex_lifetime_by_class` at all, so the emission's
    `.get(comp_class, 0.0)` published 0.00 — indistinguishable from a line
    that genuinely costs nothing. Asserting the DERIVED value rather than just
    "not zero" also catches a fix that reports the class as unavailable when
    the number was there to be computed.
    """
    line = _by_component(payload)["Line"]
    assert line["capex_lifetime"] is not None, (
        "Line's lifetime CAPEX came back unavailable, but capital_cost, "
        "lifetime and the config discount rate are all present — it is derivable"
    )
    assert line["capex_lifetime"] == pytest.approx(LINE_LIFETIME_CAPEX, rel=1e-9)
    assert line["capex_lifetime"] > 0.0


def test_the_annualised_capex_is_unchanged_by_the_widened_fill(payload):
    """
    Fails if: the widened fill leaks into the annualised numbers — e.g. by
    filling `overnight_cost` itself instead of only `discount_rate` /
    `lifetime`, which would make PyPSA annuitise a cost it should pass through.

    `periodized_cost` uses `discount_rate` only to annuitise an
    `overnight_cost`; an asset without one keeps its raw `capital_cost`. That
    is what makes the wider fill safe, and this pins it: Line's ANNUALISED
    CAPEX must still be capital_cost x s_nom_opt to the cent.
    """
    line = _by_component(payload)["Line"]
    assert line["capex"] == pytest.approx(LINE_CAPITAL_COST * LINE_S_NOM, rel=1e-9)


def test_the_widened_fill_does_not_retire_an_asset_on_a_multi_period_network(
    install_network,
):
    """
    Fails if: the back-calculation fill also substitutes `lifetime` (the
    obvious symmetric-looking version of `fill_periodized_cost_defaults`'s
    widening).

    `lifetime` is not only a cost input. On a multi-period network PyPSA
    derives asset ACTIVITY from `build_year + lifetime`, so filling a blank
    lifetime with the config default (25 y) retires an asset built at the
    default `build_year=0` before the first period even starts — and its
    ANNUALISED Capital Expenditure silently drops to 0.00, which is the exact
    defect this change exists to remove, reintroduced one layer down. Measured
    on the golden fixture at EUR 500 M per period before the guard was added.

    The line here carries no lifetime at all (PyPSA's default is `inf`), which
    is the shape that triggers it.
    """
    n = pypsa.Network()
    hours = pd.date_range("2030-01-01", periods=2, freq="h")
    mi = pd.MultiIndex.from_product(
        [[2030, 2035], hours], names=["period", "timestep"])
    mi.name = "snapshot"
    n.set_snapshots(mi)
    n.investment_periods = [2030, 2035]
    n.add("Bus", "A", v_nom=380.0)
    n.add("Bus", "B", v_nom=380.0)
    n.add("Load", "L", bus="B", p_set=50.0)
    n.add("Generator", "G", bus="A", p_nom=500.0, marginal_cost=10.0)
    # No `lifetime`, no `build_year` — both left at PyPSA's defaults (inf, 0).
    n.add("Line", "L1", bus0="A", bus1="B", x=0.1, r=0.01,
          s_nom=LINE_S_NOM, capital_cost=LINE_CAPITAL_COST)
    n.optimize(solver_name="highs", multi_investment_periods=True)
    install_network(n)
    sim_router._state["solver_config"] = SolverConfig()

    out = get_cost_breakdown()
    assert isinstance(out, dict)
    line = _by_component(out)["Line"]
    assert line["capex"] > 0.0, (
        "the line's ANNUALISED CAPEX is 0 — it was retired out of every "
        "investment period by a substituted lifetime, not priced at zero"
    )
    # …and the lifetime figure it came here for is still derived, because
    # `annuity(r, inf)` is just `r` and the rate alone is enough.
    assert line["capex_lifetime"] is not None
    assert line["capex_lifetime"] > 0.0


def test_the_horizon_total_includes_the_dominant_component(payload):
    """
    Fails if: the total is summed from a dict that the failing class never
    entered (the old `sum(capex_lifetime_by_class.values())`).

    The reported symptom was a top-level `capex_lifetime` that excluded 95.8%
    of the system. Asserting the total equals the SUM OF THE PARTS — not just
    "greater than the generator" — is what makes a silently-dropped class fail
    here rather than pass quietly.
    """
    rows = _by_component(payload)
    assert payload["capex_lifetime"] is not None
    assert payload["capex_lifetime"] == pytest.approx(
        LINE_LIFETIME_CAPEX + GEN_LIFETIME_CAPEX, rel=1e-9
    )
    assert payload["capex_lifetime"] == pytest.approx(
        sum(r["capex_lifetime"] for r in rows.values()), rel=1e-9
    )


def test_the_component_that_always_worked_still_reports_the_same_number(payload):
    """
    Fails if: the fix nulls or perturbs classes that resolved fine before —
    e.g. by routing every class through the unavailable path, or by applying
    the back-calculation to an asset that already has a typed `overnight_cost`.

    The generator's lifetime CAPEX is `overnight_cost x p_nom_opt` with a PV
    factor of 1, and was correct before this change. It must still be.
    """
    gen = _by_component(payload)["Generator"]
    assert gen["capex_lifetime"] == pytest.approx(GEN_LIFETIME_CAPEX, rel=1e-9)


def test_the_flag_is_true_when_every_class_resolved(payload):
    """
    Fails if: `capex_lifetime_available` is dropped from the response or
    hardcoded False.

    Guards the direction the failure-path tests cannot: a fix that reports
    every network as unavailable would satisfy every null assertion below.
    """
    assert payload["capex_lifetime_available"] is True


# ── 2. When it truly cannot be resolved: null, never 0.0 ──────────────────

def test_an_unresolvable_class_is_null_not_zero(line_unresolvable):
    """
    Fails if: the unresolvable class falls through to `.get(comp_class, 0.0)`,
    or its sum is coerced to 0.0 by the NaN/inf guard.

    `0.0 is None` is False, so restoring either zero-default fails here rather
    than passing quietly. This is the invariant the user chose: show
    "unavailable", never a confident number.
    """
    line = _by_component(line_unresolvable)["Line"]
    assert line["capex_lifetime"] is None, (
        f"Line.capex_lifetime came back as {line['capex_lifetime']!r} — an "
        f"unresolvable lifetime CAPEX must be null on the wire, never a number"
    )
    assert line["capex_expansion_lifetime"] is None
    assert line_unresolvable["capex_lifetime_available"] is False


def test_a_null_class_makes_the_horizon_total_null(line_unresolvable):
    """
    Fails if: the totals sum over the resolvable classes and skip the null one
    (`sum(v for v in values if v is not None)`).

    That is precisely the reported bug — €1.37 bn published as the system's
    lifetime CAPEX while the 95.8% component was missing from it. A partial
    sum under a whole-system label is worse than no number, because it looks
    like an answer.
    """
    assert line_unresolvable["capex_lifetime"] is None
    assert line_unresolvable["capex_expansion_lifetime"] is None


def test_a_nan_upfront_cost_is_null_not_a_partial_total(install_network, monkeypatch):
    """
    Fails if: `_lifetime_total` returns 0.0 instead of None for an unresolved
    cost — i.e. if the `.fillna(0)` is allowed to swallow a NaN upfront cost.

    The second way the resolve can fail, and the quieter one: PyPSA returns a
    Series rather than raising, but some entries are NaN because the
    back-calculation had nothing to work from. `.fillna(0).sum()` turns those
    assets into free ones and publishes the remainder as if it were the whole
    class. Distinct code path from the raising case above, so it gets its own
    test — the raise never reaches `_lifetime_total` at all.
    """
    n = _solved_network()
    install_network(n)
    sim_router._state["solver_config"] = SolverConfig()

    def _nan_for_lines(net, comp_class: str):
        series = net.c[comp_class].overnight_cost
        if comp_class == "Line":
            return series * float("nan")
        return series

    monkeypatch.setattr(R, "_upfront_cost_series", _nan_for_lines)
    out = get_cost_breakdown()
    assert isinstance(out, dict)

    line = _by_component(out)["Line"]
    assert line["capex_lifetime"] is None, (
        f"a NaN upfront cost was filled with 0 and published as "
        f"{line['capex_lifetime']!r}"
    )
    assert out["capex_lifetime"] is None
    assert out["capex_lifetime_available"] is False
    # The class that resolved cleanly is untouched by its neighbour's NaN.
    assert _by_component(out)["Generator"]["capex_lifetime"] == pytest.approx(
        GEN_LIFETIME_CAPEX, rel=1e-9)


def test_a_non_finite_total_is_null_not_zero(install_network, monkeypatch):
    """
    Fails if: the non-finite guard coerces to 0.0 (the original
    `if isnan or isinf: capex_lifetime_sum = 0.0`).

    An infinite upfront cost is not NaN, so it passes the unresolved-cost
    check and only the finiteness guard stands between it and the response.
    Publishing 0.00 for a cost that overflowed is the same lie in the other
    direction.
    """
    n = _solved_network()
    install_network(n)
    sim_router._state["solver_config"] = SolverConfig()

    def _inf_for_lines(net, comp_class: str):
        series = net.c[comp_class].overnight_cost
        if comp_class == "Line":
            return series * float("inf")
        return series

    monkeypatch.setattr(R, "_upfront_cost_series", _inf_for_lines)
    out = get_cost_breakdown()
    assert isinstance(out, dict)

    assert _by_component(out)["Line"]["capex_lifetime"] is None
    assert out["capex_lifetime"] is None
    assert out["capex_lifetime_available"] is False


def test_classes_that_did_resolve_keep_their_real_values(line_unresolvable):
    """
    Fails if: one class failing blanks the whole response — an early return,
    or nulling every class rather than the one that failed.

    The generator's upfront cost was resolved successfully; the line's failure
    says nothing about it. Throwing it away would trade one wrong impression
    for another, exactly as d11d4ee1 refused to blank revenue and VOM.
    """
    gen = _by_component(line_unresolvable)["Generator"]
    assert gen["capex_lifetime"] == pytest.approx(GEN_LIFETIME_CAPEX, rel=1e-9)
    # …and everything that owes nothing to the upfront-cost resolve survives.
    line = _by_component(line_unresolvable)["Line"]
    assert line["capex"] == pytest.approx(LINE_CAPITAL_COST * LINE_S_NOM, rel=1e-9)
    assert line_unresolvable["capex"] > 0.0
    assert line_unresolvable["opex"] > 0.0


# ── 3. The log ────────────────────────────────────────────────────────────

def test_the_failed_resolve_is_logged_with_a_traceback(install_network, monkeypatch, caplog):
    """
    Fails if: the `except` branch drops `logger.exception(...)` (including a
    downgrade to `logger.debug`, which would not reach `pypsa-gui.log`).

    The original bare `except Exception: continue` produced no record at all,
    which is why a component worth 8 bn EUR could vanish from a KPI without
    anybody being able to say why. Asserts the logger, the level and the
    presence of exception info — not merely "something was logged".
    """
    n = _solved_network()
    install_network(n)
    sim_router._state["solver_config"] = SolverConfig()
    monkeypatch.setattr(R, "_upfront_cost_series", _boom_for_lines)

    with caplog.at_level(logging.ERROR, logger="pypsa_gui.results"):
        out = get_cost_breakdown()

    records = [
        r for r in caplog.records
        if r.name == "pypsa_gui.results" and r.levelno >= logging.ERROR
    ]
    assert records, "the upfront-cost resolve failed and nothing was logged"
    assert any(r.exc_info is not None for r in records), (
        "logged without exception info — use logger.exception(), not "
        "logger.error(), so the traceback reaches pypsa-gui.log"
    )
    assert "Line" in caplog.text
    # …and the endpoint still degrades rather than 500-ing or 204-ing.
    assert isinstance(out, dict)
    assert out["capex_lifetime_available"] is False


def test_compare_logs_when_the_capital_cost_resolver_fails(install_network, caplog):
    """
    Fails if: `routers/compare.py::_periodized_lookup` goes back to a bare
    `except Exception: return {}`.

    Same defect family, different endpoint. The return contract is
    deliberately unchanged — every caller reads through `_safe_capital_cost`
    and reshaping it without auditing them is out of scope — but an empty map
    silently reports EUR 0.00 CAPEX for every asset in the comparison, and the
    reason has to be recoverable from pypsa-gui.log.
    """
    import services.solver_service as _svc
    from routers.compare import _periodized_lookup

    n = _solved_network()
    install_network(n)
    sim_router._state["solver_config"] = SolverConfig()

    def _boom(*_a, **_kw):
        raise RuntimeError("annuity lookup exploded")

    # `_periodized_lookup` imports the resolver inside its own body, so the
    # patch has to land on the defining module rather than on `compare`.
    original = _svc.periodized_capital_costs
    _svc.periodized_capital_costs = _boom
    try:
        with caplog.at_level(logging.ERROR, logger="pypsa_gui.compare"):
            assert _periodized_lookup(n) == {}
    finally:
        _svc.periodized_capital_costs = original

    records = [
        r for r in caplog.records
        if r.name == "pypsa_gui.compare" and r.levelno >= logging.ERROR
    ]
    assert records, "compare swallowed the resolver failure without a word"
    assert records[0].exc_info is not None, "use logger.exception(), not logger.error()"
    assert "annuity lookup exploded" in caplog.text


def test_asset_costs_logs_when_the_capital_cost_resolver_fails(install_network, caplog):
    """
    Fails if: `routers/simulation.py::asset_costs` goes back to a bare
    `except Exception: return {}`.

    The third instance of the same swallow. An empty map renders the whole
    "Investments by asset" table as EUR 0 with nothing anywhere saying why.
    """
    n = _solved_network()
    install_network(n)
    sim_router._state["solver_config"] = SolverConfig()

    def _boom(*_a, **_kw):
        raise RuntimeError("annuity lookup exploded")

    monkey = sim_router.periodized_capital_costs
    sim_router.periodized_capital_costs = _boom
    try:
        with caplog.at_level(logging.ERROR, logger="pypsa_gui.results"):
            assert sim_router.asset_costs() == {}
    finally:
        sim_router.periodized_capital_costs = monkey

    records = [
        r for r in caplog.records
        if r.name == "pypsa_gui.results" and r.levelno >= logging.ERROR
    ]
    assert records, "asset_costs swallowed the resolver failure without a word"
    assert records[0].exc_info is not None, "use logger.exception(), not logger.error()"
    assert "annuity lookup exploded" in caplog.text


def test_nothing_is_logged_when_every_class_resolves(install_network, caplog):
    """
    Fails if: the log line is emitted unconditionally rather than from the
    `except` branch.

    A warning that fires on every solved network is one users learn to ignore,
    which erases its value for the run that actually failed.
    """
    n = _solved_network()
    install_network(n)
    sim_router._state["solver_config"] = SolverConfig()

    with caplog.at_level(logging.ERROR, logger="pypsa_gui.results"):
        get_cost_breakdown()

    assert not [
        r for r in caplog.records
        if r.name == "pypsa_gui.results" and r.levelno >= logging.ERROR
    ]
