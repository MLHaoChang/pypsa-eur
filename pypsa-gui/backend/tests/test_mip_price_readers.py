# ruff: noqa: F811  (pytest fixtures imported by name)
"""The backend readers of a MIP's bus prices (assistant-assurance 17).

On master's solve path HiGHS returns no duals for an integer (MIP) solve and
PyPSA writes 0.0 to every bus price. Each reader outside checked answers reads
the solve kind through `harness/solve_validity.mip_price_limit` and withholds
the figure, so no 0.0 is presented as a price. An LP of the same network is the
control: its prices are still read.
"""

from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from harness.solve_validity import DUALS_NOT_MARGINAL, mip_price_limit

from services.solver_service import SolverConfig

CFG = SolverConfig()


def _solved(committable: bool) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=3, freq="h"))
    n.add("Bus", "el", carrier="AC")
    n.add("Bus", "h2", carrier="H2")
    n.add("Load", "d", bus="el", p_set=[5.0, 8.0, 3.0])
    n.add("Load", "h2_demand", bus="h2", p_set=1.0)
    n.add("Generator", "g1", bus="el", p_nom=20, marginal_cost=10, committable=committable,
          p_min_pu=0.1 if committable else 0.0)
    n.add("Generator", "g2", bus="el", p_nom=20, marginal_cost=30)
    n.add("Link", "electrolysis", bus0="el", bus1="h2", carrier="H2 electrolysis",
          p_nom=5, efficiency=0.7, capital_cost=100.0)
    status, _ = n.optimize(solver_name="highs")
    assert status == "ok"
    return n


@pytest.fixture(scope="module")
def mip():
    n = _solved(committable=True)
    assert mip_price_limit(n, CFG) == DUALS_NOT_MARGINAL
    # What the readers must never show: the solve's 0.0 "prices".
    assert (n.buses_t.marginal_price == 0.0).all().all()
    return n


@pytest.fixture(scope="module")
def lp():
    n = _solved(committable=False)
    assert mip_price_limit(n, CFG) is None
    assert (n.buses_t.marginal_price["el"] > 0).all()
    return n


def test_the_prices_digest_of_a_mip_is_unavailable(mip, lp):
    from services.not_kept import WHY
    from services.results.prices_digest import prices_digest

    got = prices_digest(mip, solver_config=CFG)
    assert got == {"status": "unavailable", "reason": DUALS_NOT_MARGINAL}
    assert "integer (MIP) solve" in WHY[DUALS_NOT_MARGINAL]
    assert prices_digest(lp, solver_config=CFG)["status"] == "ok"


def test_compare_prices_of_a_mip_are_unavailable_with_the_reason(mip, lp):
    from services.compare.prices import _compute_prices_summary

    got = _compute_prices_summary(mip, [], False, True, solve_config=CFG)
    assert not got.available and got.unavailable_reason == DUALS_NOT_MARGINAL
    assert got.duration_curve == [] and got.mean_price.total == 0.0  # defaults, flagged
    ok = _compute_prices_summary(lp, [], False, True, solve_config=CFG)
    assert ok.available and ok.unavailable_reason is None and ok.max_price > 0


def test_compare_economics_of_a_mip_flags_its_prices_and_drops_lcoh(mip, lp):
    from services.compare.economics import _compute_economics_summary

    got = _compute_economics_summary(mip, [], False, True, False, cfg=CFG)
    assert got.available and got.price_limit == DUALS_NOT_MARGINAL
    assert got.per_asset_lcoh == []
    ok = _compute_economics_summary(lp, [], False, True, False, cfg=CFG)
    assert ok.price_limit is None and ok.per_asset_lcoh


def test_lcoh_of_a_mip_is_not_established(mip, lp):
    from services.results.lcoh import compute_lcoh

    got = compute_lcoh(mip, CFG, result_df=None)
    assert got["rows"] == [] and got["total"] is None
    assert got["unavailable_reason"] == DUALS_NOT_MARGINAL
    assert "integer (MIP) solve" in got["message"]
    assert compute_lcoh(lp, CFG, result_df=None)["rows"]


def test_the_energy_hub_report_names_the_mip_not_missing_hydrogen(mip):
    from services.adequacy.eh_report import compute_tea

    block = compute_tea(cost_eur=100.0, served_energy_mwh=10.0, network=mip, cfg=CFG)
    assert block.lcoh_status == "not_established" and block.lcoh_eur_per_kg is None
    assert "integer (MIP) solve" in block.lcoh_note


# ── the decision-study report ────────────────────────────────────────────

from tests.test_study_report_routes import _econ_text, _full, _post, studies_on  # noqa: E402,F401
from tests.test_study_runner import FakeSolver  # noqa: E402


class _CommittableGrid(FakeSolver):
    """The study's fork with a committable grid Generator: an integer solve."""

    def __call__(self, config, n, *args, **kwargs):
        n.generators["committable"] = n.generators.index == "grid"
        return super().__call__(config, n, *args, **kwargs)


def test_the_study_report_withholds_figures_priced_at_a_mip_s_duals(
        client, api_project, studies_on, monkeypatch):
    from services import solver_service

    monkeypatch.setattr(solver_service, "run_simulation", _CommittableGrid())
    body = _post(client, "rep-mip", _full(client, api_project, "rep-mip"))
    econ = body["sections"]["economics"]
    assert econ["status"] == "ok"
    for key in ("lcos_incl_charging", "market_revenue_at_duals"):
        assert econ["facts"][key]["value"] is None
        assert econ["facts"][key]["unavailable"] == DUALS_NOT_MARGINAL
    text = _econ_text(body)
    assert "integer (MIP) solve has no marginal prices" in text
    assert "zero profit" not in text
    codes = {d["code"] for d in body["required_disclosures"]}
    assert not any(c.startswith("market_revenue_at_duals_") and c != "market_revenue_at_duals_excluded_from_cash_flow"
                   for c in codes)
