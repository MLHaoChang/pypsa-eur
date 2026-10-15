"""The investment case's baseline: the site as it stands today (owner decision
2026-10-15; gatekeeper design of #294's follow-up).

The counterfactual is a second solve of the same network with the plan's new
builds removed. The baseline removes exactly what the case charges capex for,
plus the LP's new capacity:
- an extendable owned asset is fixed at its starting size (its increment goes);
- a candidate of a later period (build_year after the first) is removed;
- a non-extendable owned row stays only when the case charges it no capex (a
  typed overnight cost of 0); a priced or untyped one is a new investment.
Incremental = actual − baseline, line by line; a case with no new builds is 0.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.results import baseline as B


def _row(n, comp, name, **kw):
    n.add(comp, name, bus="b", **kw)


def _fixture():
    """One asset of each kind the rule distinguishes."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "b")
    # extendable, starting at 2 MW, built up to 5 MW: its 3 MW increment goes
    _row(n, "Generator", "grown", p_nom=2.0, p_nom_extendable=True, capital_cost=10.0)
    n.generators.loc["grown", "p_nom_opt"] = 5.0
    # extendable, built nothing: nothing to remove, fixed at its 0
    _row(n, "Generator", "idle", p_nom=0.0, p_nom_extendable=True, capital_cost=10.0)
    n.generators.loc["idle", "p_nom_opt"] = 0.0
    # existing, typed overnight 0: stays
    _row(n, "Generator", "existing", p_nom=10.0)
    n.generators.loc["existing", "overnight_cost"] = 0.0
    # a fixed-size priced proposal: removed
    _row(n, "Generator", "proposal", p_nom=5.0)
    n.generators.loc["proposal", "overnight_cost"] = 2.5e6
    # untyped, with a capital cost: treated as new, removed and flagged
    _row(n, "Generator", "untyped", p_nom=3.0, capital_cost=50.0)
    # untyped at no capital cost: the case charges it no capex, so it stays
    # (gatekeeper review of #302, item 2), unflagged
    _row(n, "Generator", "untyped_free", p_nom=1.0, capital_cost=0.0)
    # an extendable increment typed overnight 0: capacity added for free, flagged
    _row(n, "StorageUnit", "free", p_nom=0.0, p_nom_extendable=True, capital_cost=5.0)
    n.storage_units.loc["free", ["p_nom_opt", "overnight_cost"]] = [1.5, 0.0]
    return n


OWNED = [("Generator", g) for g in ("grown", "idle", "existing", "proposal", "untyped",
                                     "untyped_free")] \
    + [("StorageUnit", "free")]


def test_the_baseline_removes_exactly_the_charged_capex_and_the_lp_increments():
    n = _fixture()
    plan = B.plan_baseline(n, OWNED)
    removed = {name: keep for _c, name, keep in plan.removed}
    # the invariant: removed == {capex > 0 rows} ∪ {LP increments}
    assert set(removed) == {"grown", "proposal", "untyped", "free"}
    assert removed == {"grown": 2.0, "proposal": 0.0, "untyped": 0.0, "free": 0.0}
    assert plan.kept == (("Generator", "existing"), ("Generator", "untyped_free"))
    assert plan.needed                                    # a kept existing asset
    assert "baseline_typing_mismatch:untyped" in plan.flags
    assert "baseline_free_capacity:free" in plan.flags
    assert not [f for f in plan.flags
                if f.endswith((":existing", ":proposal", ":grown", ":untyped_free"))]


def test_every_owned_extendable_asset_is_fixed_in_the_baseline_network():
    """Fixed at its starting size, never re-sized by the baseline's LP."""
    n = _fixture()
    base = B.baseline_network(n, B.plan_baseline(n, OWNED))
    for comp, name in OWNED:
        df = base.generators if comp == "Generator" else base.storage_units
        fixed = not bool(df.at[name, "p_nom_extendable"]) or \
            df.at[name, "p_nom_max"] <= B._REMOVED_MAX
        assert fixed, name
    assert base.generators.at["grown", "p_nom"] == 2.0
    assert base.generators.at["existing", "p_nom"] == 10.0
    assert base.generators.at["proposal", "p_nom"] == 0.0
    assert n.generators.at["proposal", "p_nom"] == 5.0          # the original untouched


def test_a_later_period_candidate_is_removed():
    n = pypsa.Network()
    n.set_snapshots(pd.MultiIndex.from_product(
        [[2030, 2035], pd.date_range("2030-01-01", periods=2, freq="h")]))
    n.investment_periods = [2030, 2035]
    n.add("Bus", "b")
    n.add("Generator", "existing", bus="b", p_nom=4.0, overnight_cost=0.0)
    n.add("Generator", "later", bus="b", p_nom=0.0, p_nom_extendable=True, build_year=2035,
          capital_cost=1.0)
    n.generators.loc["later", "p_nom_opt"] = 3.0
    plan = B.plan_baseline(n, [("Generator", "existing"), ("Generator", "later")])
    assert {name: keep for _c, name, keep in plan.removed} == {"later": 0.0}


def test_no_kept_existing_asset_needs_no_baseline():
    """Nothing kept: the baseline is the site without the owner's assets, which
    is the analytic counterfactual's own definition (kept byte-identical)."""
    n = _fixture()
    plan = B.plan_baseline(n, [("Generator", "grown"), ("Generator", "proposal")])
    assert not plan.needed


def test_the_owners_meter_links_are_kept_and_never_flagged():
    n = _fixture()
    n.add("Bus", "grid")
    n.add("Link", "poc", bus0="grid", bus1="b", p_nom=40.0)
    plan = B.plan_baseline(n, OWNED + [("Link", "poc")], meter_links=("poc",))
    assert "poc" not in {name for _c, name, _k in plan.removed}
    assert not [f for f in plan.flags if f.endswith(":poc")]


def test_the_flag_text_is_plain():
    assert B.flag_text("baseline_typing_mismatch:pv_roof") == (
        "pv_roof has no typed cost; treated as a new investment. Type overnight 0 if it "
        "already exists.")
    assert B.flag_text("baseline_free_capacity:bess") == (
        "bess adds capacity typed at no cost (overnight 0); the baseline removes the "
        "addition, so the case gets its value for free. Type its cost.")


# ── live: the anchor, a priced proposal, isolation, the cache, a refusal ─────

from tests.test_finance_case_adapter import OWNED as VF_OWNED, _case, _edge7, _fin  # noqa: E402
from tests.test_value_flow_reconciliation import _commercial, _solve  # noqa: E402


def _existing(n, name, comp="Generator", cost=0.0):
    df = n.generators if comp == "Generator" else n.storage_units
    df.loc[name, "p_nom_extendable"] = False
    df.loc[name, ["overnight_cost", "discount_rate"]] = [cost, 0.07]   # PyPSA needs a rate


def _site(pv_cost=0.0, pv_mw=5.0):
    n = _edge7()
    n.generators.loc["pv", "p_nom"] = pv_mw
    _existing(n, "pv", cost=pv_cost)
    _existing(n, "bess", comp="StorageUnit")                 # an existing battery, kept
    com = _commercial(VF_OWNED, contracts=[])
    com.pop("connection")
    return n, com


def _baseline_case(n, cfg, fin=None, **kw):
    from services.results.finance_case import baseline_plan

    base = B.solve_baseline(n, cfg, baseline_plan(n, cfg))
    return _case(n, cfg, fin or _fin(annualise=True), baseline=base, **kw), base


@pytest.fixture(autouse=True)
def _fresh_cache():
    B.clear_cache()
    yield
    B.clear_cache()


@pytest.mark.live_solve
def test_a_case_with_no_new_builds_is_zero(reset_backend):
    """The anchor: nothing new is built, so the baseline is the actual site and
    the incremental cash is 0 in every year (LP degeneracy: the two solves are
    the same LP with the same options, so the tolerance is a cent per year)."""
    from services.finance.engine import run_case

    n, com = _site()
    n, cfg = _solve(n, com)
    case, base = _baseline_case(n, cfg)
    assert base.plan.removed == () and base.plan.needed
    assert "counterfactual_basis:site_today" in case.flags
    r = run_case(case)
    inc = r.op_incremental["net"]
    assert inc is not None and np.allclose(inc, 0.0, atol=0.01), inc
    assert abs(r.metrics["project_pre_tax_npv"]) < 1.0
    assert r.metrics.get("project_pre_tax_irr") is None


@pytest.mark.live_solve
def test_a_fixed_size_priced_proposal_gets_its_own_value(reset_backend):
    """A non-extendable 5 MW PV typed at 500 k/MW (2.5 M) is the investment:
    the baseline removes it, so its savings count, not only its capex."""
    from services.finance.engine import run_case

    n, com = _site(pv_cost=500_000.0)
    n, cfg = _solve(n, com)
    case, base = _baseline_case(n, cfg)
    assert {name for _c, name, _k in base.plan.removed} == {"pv"}
    r = run_case(case)
    capex = 2.5e6
    inc = r.op_incremental["net"]
    first_op = r.op.tl.index(2030)                            # COD; earlier years are close
    assert inc is not None and inc[first_op] > 0              # it saves money each year
    npv = r.metrics["project_pre_tax_npv"]
    assert npv is not None and npv > -0.8 * capex             # never ≈ −capex
    assert npv < 10 * capex                                   # and plausible


@pytest.mark.live_solve
def test_the_baseline_solve_leaves_the_live_project_untouched(reset_backend):
    from services.pypsa_service import PyPSAService
    from services.results.finance_case import baseline_plan

    n, com = _site(pv_cost=500_000.0)
    n, cfg = _solve(n, com)
    before = (float(n.objective), n.generators_t.p.copy(), n.storage_units_t.p.copy(),
              n.generators.p_nom.copy())
    ctx = PyPSAService.get_active_context()
    rows = {k: set(v) for k, v in (getattr(ctx, "transient_rows", {}) or {}).items()}
    base = B.solve_baseline(n, cfg, baseline_plan(n, cfg))
    assert base.network is not n
    assert float(n.objective) == before[0]
    pd.testing.assert_frame_equal(n.generators_t.p, before[1])
    pd.testing.assert_frame_equal(n.storage_units_t.p, before[2])
    pd.testing.assert_series_equal(n.generators.p_nom, before[3])
    assert {k: set(v) for k, v in (getattr(ctx, "transient_rows", {}) or {}).items()} == rows


@pytest.mark.live_solve
def test_the_baseline_is_cached_by_its_inputs(reset_backend):
    from services.results.finance_case import baseline_plan
    from services.solver_service import run_simulation

    n, com = _site(pv_cost=500_000.0)
    n, cfg = _solve(n, com)
    calls = []

    def counted(*a, **kw):
        calls.append(1)
        return run_simulation(*a, **kw)

    plan = baseline_plan(n, cfg)
    first = B.solve_baseline(n, cfg, plan, solve=counted)
    again = B.solve_baseline(n, cfg, plan, solve=counted)
    assert again is first and len(calls) == 1


def test_a_baseline_that_does_not_solve_is_refused():
    n = _fixture()
    plan = B.plan_baseline(n, OWNED)

    class Cfg:
        finance = None

    with pytest.raises(B.BaselineRefused) as exc:
        B.solve_baseline(n, Cfg(), plan, solve=lambda *a, **kw: ("warning", "infeasible"))
    assert exc.value.code == "baseline_not_established:infeasible"


def test_the_guided_study_says_when_its_analytic_basis_keeps_existing_assets():
    """U2/Guided keeps the analytic counterfactual; with kept existing owner
    assets it adds the basis note, else nothing (byte-identical)."""
    from types import SimpleNamespace

    from services.study.engine_adapter import analytic_basis_note

    n, com = _site()                                      # pv and bess existing, typed 0
    assert analytic_basis_note(n, SimpleNamespace(commercial=com)) == \
        "counterfactual_analytic_existing_not_redispatched"
    n = _edge7()                                          # pv and bess extendable: nothing kept
    assert analytic_basis_note(n, SimpleNamespace(commercial=com)) is None


def test_an_island_infeasible_baseline_is_solved_without_the_island_rows():
    """The island rows name the plan's new builds; the site as it stands may
    not meet them. Its comparison is solved without them, disclosed; a baseline
    that does not solve without them either is refused."""
    n = _fixture()
    plan = B.plan_baseline(n, OWNED)
    seen = []

    class Cfg:
        finance = None
        island_constraints = True

    def solve(conf, *a, **kw):
        seen.append(conf.island_constraints)
        return ("ok", "optimal") if not conf.island_constraints else ("warning", "infeasible")

    got = B.solve_baseline(n, Cfg(), plan, solve=solve)
    assert seen == [True, False]
    assert "baseline_island_rows_relaxed" in got.plan.flags
    assert B.flag_text("baseline_island_rows_relaxed").startswith("The site as it stands")
    B.clear_cache()
    with pytest.raises(B.BaselineRefused):
        B.solve_baseline(n, Cfg(), plan, solve=lambda *a, **kw: ("warning", "infeasible"))


@pytest.mark.live_solve
def test_the_h2e_inputs_read_not_applied_on_the_site_today_basis(reset_backend):
    """Merge of #302 into #305: a reference purchase and a CO2 price act on the
    analytic counterfactual only. On the site-today basis they are flagged not
    applied, and the avoided CO2 cost reads not established, never "none"."""
    from services.finance.engine import run_case
    from services.finance.report import _counterfactual_block

    n, com = _site(pv_cost=500_000.0)
    com["reference_purchases"] = [{"load": "h2_offtake", "price_eur_per_kg": 2.0,
                                   "co2_t_per_kg": 0.01, "source": "test"}]
    com["co2_price"] = {"eur_per_t_by_year": {2030: 80.0}, "money_year": 2030,
                        "source": "test", "grid_t_per_mwh": 0.3, "grid_source": "test"}
    n, cfg = _solve(n, com)
    case, _base = _baseline_case(n, cfg)
    assert "counterfactual_basis:site_today" in case.flags
    assert {"reference_purchases_not_applied:site_today_basis",
            "co2_price_not_applied:site_today_basis"} <= set(case.flags)
    assert not any(ln.key.startswith("co2:") for t in (*case.templates, *case.counterfactual)
                   for ln in t.lines)
    block = _counterfactual_block(run_case(case), case)
    assert block["co2_priced"] is True and block["avoided_co2_cost"] is None
    assert any("CO2 price is not applied" in t for t in block["notes"])
