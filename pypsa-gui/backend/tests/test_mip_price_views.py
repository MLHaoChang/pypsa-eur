# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Assistant-assurance 17, slice 2b: the Results price views on an integer solve.

On a MIP, HiGHS returns no duals and PyPSA writes 0.0 to `buses_t.marginal_price`.
The prices and price-driver views, the generator-cost fallback (GK ruling b) and the
checked bus-price answers (S7) withhold them with the reason; an LP is unchanged (S4).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pypsa
import pytest

from harness.solve_validity import DUALS_NOT_MARGINAL
from services.solver_service import SolverConfig
from tests.test_mip_economics_readers import _rdf, _solved

CFG = SolverConfig()
UNAVAILABLE = {"status": "unavailable", "reason": DUALS_NOT_MARGINAL}


@pytest.fixture(scope="module")
def mip():
    return _solved(committable=True)


@pytest.fixture(scope="module")
def lp():
    return _solved(committable=False)


# ── prices and price drivers (the service) ───────────────────────────────


def test_prices_of_a_mip_are_withheld_with_the_reason(mip):
    from services.results.prices import compute_prices

    assert (
        compute_prices(
            mip, "lopf", None, None, result_df=_rdf, price_limit=DUALS_NOT_MARGINAL
        )
        == UNAVAILABLE
    )


def test_the_generator_cost_fallback_is_withheld_on_a_mip(mip):
    """GK ruling (b): a MIP's all-zero duals would trigger the fallback, which shows
    a generator's marginal cost as a price. On a MIP it is not shown."""
    from services.results.prices import compute_prices

    ungated = compute_prices(mip, "lopf", None, None, result_df=_rdf)
    assert ungated["source"] == "fallback" and ungated["fallback_per_snapshot"]
    gated = compute_prices(
        mip, "lopf", None, None, result_df=_rdf, price_limit=DUALS_NOT_MARGINAL
    )
    assert "fallback_per_snapshot" not in gated and "data" not in gated


def test_prices_of_an_lp_are_unchanged(lp):
    from services.results.prices import compute_prices

    out = compute_prices(lp, "lopf", None, None, result_df=_rdf, price_limit=None)
    assert json.dumps(out, sort_keys=True, default=str) == json.dumps(
        compute_prices(lp, "lopf", None, None, result_df=_rdf),
        sort_keys=True,
        default=str,
    )
    assert out["source"] == "lp" and out.get("status") != "unavailable"


def test_price_drivers_of_a_mip_are_withheld_and_an_lp_s_kept(mip, lp):
    from services.results.prices import compute_price_drivers

    assert (
        compute_price_drivers(mip, 0.0, 200, price_limit=DUALS_NOT_MARGINAL)
        == UNAVAILABLE
    )
    out = compute_price_drivers(lp, 0.0, 200, price_limit=None)
    assert out == compute_price_drivers(lp, 0.0, 200)
    assert out.get("status") != "unavailable"


# ── the live routes and the chat tool read the solve's own record (S3, S6) ──


@pytest.mark.parametrize(
    "which, record, expect",
    [
        ("mip", {"price_limit": DUALS_NOT_MARGINAL}, True),
        # The record wins over today's judgement of the network.
        ("lp", {"price_limit": DUALS_NOT_MARGINAL}, True),
        ("mip", {}, True),  # no record: judged with the current config
        ("lp", {}, False),
    ],
)
def test_the_price_routes_gate_by_the_solve_s_record(
    which, record, expect, request, monkeypatch
):
    import routers.results as results
    from services import chat_tools
    from services.pypsa_service import PyPSAService

    n = request.getfixturevalue(which)
    monkeypatch.setattr(results, "_dispatch_ready", lambda n: True)
    monkeypatch.setattr(PyPSAService, "get_network", classmethod(lambda cls: n))
    monkeypatch.setitem(results._state, "last_solve_settings", record)
    monkeypatch.setitem(results._state, "solver_config", CFG)
    for got in (
        results.get_prices(source="lopf", from_=None, to_=None),
        results.get_price_drivers(threshold=0.0, limit=200),
        chat_tools.get_results("prices"),
        chat_tools.get_results("price_drivers"),
    ):
        assert (got.get("status") == "unavailable") is expect
        if expect:
            assert got["reason"] == DUALS_NOT_MARGINAL


# ── S7: the checked bus-price answers judge the solve, not today's config ──


def _process_network() -> pypsa.Network:
    """A committable process unit: a MIP under binary commitment, an LP once
    process commitment is linearised (`solve_validity._integer_commitment`)."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=3, freq="h"))
    n.add("Bus", ["a", "b"])
    n.add("Load", "d", bus="a", p_set=[5.0, 8.0, 3.0])
    n.add("Generator", "g1", bus="a", p_nom=10, marginal_cost=10)
    n.add("Link", "proc", bus0="a", bus1="b", p_nom=5, committable=True)
    n.links["eh_process"] = True
    n.buses_t.marginal_price = pd.DataFrame(
        0.0, index=n.snapshots, columns=n.buses.index
    )
    return n


def test_checked_bus_prices_keep_a_mip_solve_s_limit_after_a_switch_to_linearised(
    monkeypatch,
):
    """A MIP solve, then a config edit to linearised UC with no re-solve: the prices
    are still the MIP's zeros, so the checked answer still withholds them."""
    from harness import price_calculations, profile_common
    from harness import solve_validity as sv

    monkeypatch.setattr(profile_common, "lp_outputs_live", lambda ctx: True)
    n = _process_network()
    binary = {"solver_name": "highs", "process_uc": "binary"}
    linearised = {"solver_name": "highs", "process_uc": "linearised"}
    assert sv.is_mip(n, binary) and not sv.is_mip(n, linearised)
    edited = SimpleNamespace(
        network=n,
        solver_state={
            "solver_config": linearised,
            "last_solve_settings": {"price_limit": DUALS_NOT_MARGINAL},
        },
    )
    raw = price_calculations.source(edited, "a")
    assert raw["solve_limit"] == DUALS_NOT_MARGINAL
    facts, slots = price_calculations.calculate(
        raw, {"profile": price_calculations.PROFILE, "entity_id": "a"}
    )
    assert facts == []
    assert {s["metric"]: s["reason"] for s in slots} == {
        m: DUALS_NOT_MARGINAL for m in price_calculations.METRICS
    }
    # An LP solve under linearised UC (no record) keeps its prices.
    lp_solve = SimpleNamespace(
        network=n, solver_state={"solver_config": linearised, "last_solve_settings": {}}
    )
    assert price_calculations.source(lp_solve, "a")["solve_limit"] is None


def test_checked_bus_prices_still_refuse_a_stochastic_run(monkeypatch):
    from harness import price_calculations, profile_common
    from harness import solve_validity as sv

    monkeypatch.setattr(profile_common, "lp_outputs_live", lambda ctx: True)
    monkeypatch.setattr(sv, "is_stochastic", lambda n: True)
    n = _process_network()
    ctx = SimpleNamespace(
        network=n,
        solver_state={"solver_config": {"process_uc": "linearised"}},
    )
    assert price_calculations.source(ctx, "a")["solve_limit"] == sv.STOCHASTIC


# ── the asset-results registry: every price-derived metric (REQ_DUALS) ────


@pytest.mark.parametrize(
    "which, record, withheld",
    [
        ("mip", {"price_limit": DUALS_NOT_MARGINAL}, True),
        ("lp", {"price_limit": DUALS_NOT_MARGINAL}, True),  # the record wins
        ("mip", {}, True),
        ("lp", {}, False),
    ],
)
def test_asset_results_block_every_price_metric_on_a_mip(
    which, record, withheld, request, monkeypatch
):
    """`price_at_bus` feeds revenue, capture price, spreads, congestion rent and
    charge cost; all of them require REQ_DUALS, which a MIP's solve leaves unmet
    with the one sentence and no re-run remedy (a re-run would not help)."""
    import routers.results as results
    from harness.solve_validity import MIP_PRICES_TEXT
    from services.asset_results import compute as C
    from services.asset_results import registry as reg
    from services.asset_results.applicability import resolve_metric

    n = request.getfixturevalue(which)
    monkeypatch.setitem(results._state, "last_solve_settings", record)
    monkeypatch.setitem(results._state, "solver_config", CFG)
    st = C.preconditions(n, "Generator", "cheap")[reg.REQ_DUALS]
    if withheld:
        assert st.status == "blocked" and st.reason == MIP_PRICES_TEXT
        assert st.remedy is None
    else:
        assert st.status == "ok"
    pre = {
        req: C.preconditions(n, "Generator", "cheap")[req] for req in [reg.REQ_DUALS]
    }
    for mid in ("revenue_eur", "capture_price", "bus_marginal_price"):
        got = resolve_metric(reg.metric_for("Generator", mid), "Generator", pre)
        assert (got.status == "blocked") is withheld, mid


def test_mip_cause_sentence_matches_the_frontend_words():
    """One cause sentence on every surface (UX 29 on #1994): the backend's MIP text and the
    frontend's `NOT_KEPT_WORDS.mip_duals_not_marginal.why` say the same thing."""
    import pathlib
    import re

    from harness.solve_validity import MIP_PRICES_CAUSE, MIP_PRICES_TEXT

    ts = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"
          / "results" / "sectionWords.ts").read_text(encoding="utf-8")
    m = re.search(r"mip_duals_not_marginal: \{ why: '([^']+)'", ts)
    assert m, "NOT_KEPT_WORDS.mip_duals_not_marginal not found in sectionWords.ts"
    why = m.group(1)
    assert MIP_PRICES_CAUSE == f"{why[0].upper()}{why[1:]}."
    assert MIP_PRICES_TEXT == f"Not available. {MIP_PRICES_CAUSE}"
