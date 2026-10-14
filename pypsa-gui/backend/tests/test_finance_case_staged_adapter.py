"""
P5c: the finance-case ADAPTER for staged builds (E2E demo plan 2026-10-14
§7.1 items 2, 3, 7 and the gatekeeper's §7.4 P5 changes).

The two-period edge site of the P3 fixtures (periods 2030 and 2040): an owned
PV built in 2030 and an owned battery built in 2040 that the LP dispatches
from 2040. By rule:

- each invested asset's COD is its typed `cod_by_asset`, else 1 January of
  its `build_year`; the case COD is the earliest; `asset_cod` names every
  invested asset only when the CODs differ (an unstaged case keeps its hash);
- rule P5-a: a later stage's COD year is the asset's FIRST ACTIVE investment
  period, else `staged_cod_not_active_period:<asset>` (both years given);
- an owned asset built after the first period with no built capacity is not
  an investment (`not_built:<asset>`), never a refusal;
- an invested asset active in one modelled period and inactive in a later
  one retires inside the horizon: `asset_retires_in_horizon:<asset>`;
- a campus extra with a later `build_year` is a stage under the same rules.
"""
from __future__ import annotations

import copy
import dataclasses
import json
from datetime import date

import pytest

from services.finance.case import FinanceRefused
from tests.test_finance_case_adapter import OWNED, _case, _fin, _refused
from tests.test_value_flow_reconciliation import _commercial, _network, _solve

C2030, C2040 = date(2030, 1, 1), date(2040, 1, 1)
PV_OVERNIGHT, BESS_OVERNIGHT = 700_000.0, 1_000_000.0

# The hash of `test_multi_period_templates_and_a_staged_build`'s unstaged case
# (OWNED, `_fin(annualise=True)`) computed on origin/master 0a0a35e (before
# P5c): the adapter must leave an unstaged case's hash exactly as it was.

GEN2 = {"asset_id": "gen2", "component": "Generator", "owner": "site"}


def _staged(*, gen2: bool = False):
    """The two-period site with the battery staged to 2040 (and, with `gen2`,
    an owned 2040 generator candidate too dear for the LP to build)."""
    n = _network(multi=True)
    n.generators.loc["pv", ["build_year", "lifetime"]] = [2030, 25.0]
    n.storage_units.loc["bess", ["build_year", "lifetime"]] = [2040, 15.0]
    vf = copy.deepcopy(OWNED)
    if gen2:
        n.add("Generator", "gen2", bus="site", carrier="solar", p_nom_extendable=True,
              p_nom_max=5.0, capital_cost=1e9, marginal_cost=1_000.0, build_year=2040,
              lifetime=20.0)
        vf["asset_owners"].append(GEN2)
    n, cfg = _solve(n, _commercial(vf), multi=True)
    n.generators.loc["pv", "overnight_cost"] = PV_OVERNIGHT
    n.storage_units.loc["bess", "overnight_cost"] = BESS_OVERNIGHT
    return n, cfg


def _sfin(**over):
    return _fin(**{"annualise": True, "cod_by_asset": {}, **over})


@pytest.mark.live_solve
def test_a_later_build_is_a_stage_with_its_own_cod(reset_backend):
    from services.finance.engine import run_case

    n, cfg = _staged()
    case = _case(n, cfg, _sfin())
    assert case.cod == C2030
    assert case.asset_cod == {"pv": C2030, "bess": C2040}
    assert {a.name for a in case.assets} == {"pv", "bess"}
    assert not any(f.startswith("generation_not_established") for f in case.flags)
    assert {"cod_from_build_year:pv", "cod_from_build_year:bess"} <= set(case.flags)
    # A typed COD that agrees with the first active period is the same case.
    typed = _case(n, cfg, _sfin(cod_by_asset={"pv": C2030, "bess": C2040}))
    assert typed.asset_cod == case.asset_cod

    r = run_case(case)
    tl = r.op.tl
    bess = next(a for a in case.assets if a.name == "bess")
    assert bess.overnight_cost == pytest.approx(BESS_OVERNIGHT * 10)
    # The stage's capex: one lead year before its COD, escalated by `capex`
    # (2 %) from the base year 2030; stage 1 (pv) alone at y0.
    assert r.op.stage_capex[tl.index(2039)] == pytest.approx(
        BESS_OVERNIGHT * 10 * 1.02 ** (2039 - 2030))
    assert sum(r.op.stage_capex) == pytest.approx(r.op.stage_capex[tl.index(2039)])
    pv = next(a for a in case.assets if a.name == "pv")
    assert sum(r.op.capex) == pytest.approx(pv.overnight_cost)
    assert "staged_capex_equity_funded" in r.flags
    assert r.op.status["capex"] == "ok"


@pytest.mark.live_solve
def test_a_candidate_built_zero_is_not_built_never_a_refusal(reset_backend):
    n, cfg = _staged(gen2=True)
    assert float(n.generators.at["gen2", "p_nom_opt"]) <= 1e-6
    case = _case(n, cfg, _sfin())
    assert "not_built:gen2" in case.flags
    assert "gen2" not in {a.name for a in case.assets}
    assert "gen2" not in case.asset_cod
    # It dispatched nothing: its generation is an established 0, not unknown.
    assert not any(f.startswith("generation_not_established") for f in case.flags)
    assert case.asset_cod == {"pv": C2030, "bess": C2040}


@pytest.mark.live_solve
def test_a_stage_cod_off_its_first_active_period_is_refused(reset_backend):
    n, cfg = _staged()
    got = _refused(n, cfg, _sfin(cod_by_asset={"pv": C2030, "bess": date(2041, 1, 1)}))
    assert got.code == "staged_cod_not_active_period:bess"
    assert "2041" in str(got) and "2040" in str(got)
    # A later asset typed at the case COD is not dispatched until 2040 either.
    got = _refused(n, cfg, _sfin(cod_by_asset={"pv": C2030, "bess": C2030}))
    assert got.code == "staged_cod_not_active_period:bess"


@pytest.mark.live_solve
def test_an_asset_retiring_inside_the_modelled_periods_is_refused(reset_backend):
    n, cfg = _staged()
    n.generators.loc["pv", "lifetime"] = 5.0             # active in 2030, gone by 2040
    got = _refused(n, cfg, _sfin())
    assert got.code == "asset_retires_in_horizon:pv"
    # Active in every modelled period: no refusal (the analysis may run on).
    n.generators.loc["pv", "lifetime"] = 11.0
    _case(n, cfg, _sfin())


@pytest.mark.live_solve
def test_a_later_campus_extra_is_a_stage(reset_backend):
    from tests.test_finance_case_extra_assets import _ext

    n, cfg = _solve(_network(multi=True), _commercial(OWNED), multi=True)
    fin = _fin(annualise=True)                           # pv and bess typed at 2030
    case = _case(n, cfg, fin, extra_assets=(_ext(build_year=2040),))
    assert case.cod == C2030
    assert case.asset_cod == {"pv": C2030, "bess": C2030, "trf": C2040}
    assert "cod_from_build_year:trf" in case.flags
    # Its fixed O&M starts in its own period.
    fom = [[ln for ln in t.lines if ln.key == "extra_asset_fom:trf"] for t in case.templates]
    assert [len(x) for x in fom] == [0, 1]
    # Not an investment period year: refused by rule P5-a.
    got = _refused(n, cfg, fin, extra_assets=(_ext(build_year=2035),))
    assert got.code == "staged_cod_not_active_period:trf"


@pytest.mark.live_solve
def test_an_unstaged_case_keeps_its_hash(reset_backend):
    from services.results.finance_case import finance_case_hash

    n, cfg = _solve(_network(multi=True), _commercial(OWNED), multi=True)
    case = _case(n, cfg, _fin(annualise=True))
    assert case.asset_cod == {}
    # The hash input carries no asset_cod for an unstaged case, so its hash is
    # the pre-P5 one (a literal pin would depend on the solver's numbers).
    from services.results.finance_case import _canon

    assert "asset_cod" not in json.dumps(_canon(case), default=str)
    assert finance_case_hash(case) == finance_case_hash(dataclasses.replace(case, asset_cod={}))

