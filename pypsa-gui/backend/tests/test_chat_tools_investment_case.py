"""
Chat for the investment case (Edge Investment Case P4 WP4.6c):
`run_investment_case`, `get_investment_case`, `solve_ppa_price` and
`explain_cashflow`.

Plan: docs/superpowers/plans/2026-09-30-edge-investment-case-p4.md WP4.6c (C9,
C12). The adapter is replaced through the router seam `_ic_build_case` (as in
`test_investment_case_routes.py`) by a SAM oracle case; `solve_ppa_price` gets
SAM's tax layers through the chat layer's test-only `_ic_solve_layers` hook
(the runner's `layers` hook). Stored reports are assembled from the engine and
put in the solver state directly.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import time

import pytest
from fastapi import HTTPException

from services import chat_tools
from services.adequacy import campaign as C
from services.chat_tools_schema import TOOL_ROUTES, safety_tier_for
from tests.conftest import build_network
from tests.fixtures.investment_case.sam import sam_case as S

_CAP = 4000
_NE = "not established"


def _tool(name, **kw):
    return chat_tools.DISPATCHERS[name](**kw)


def _sim_state():
    import routers.simulation as SIM

    return SIM._state


def _put_finance(fin: dict | None):
    import routers.simulation as SIM

    return SIM.put_finance(SIM.FinanceIn(finance=fin), if_match=None)


def _fake_build(monkeypatch, case_fn):
    """Replace the router's adapter closure factory (plan C1 seam)."""
    import routers.results as R

    calls: list[dict] = []

    def factory(n, cfg, *, owner, lost_load):
        calls.append({"owner": owner})
        return lambda: case_fn()

    monkeypatch.setattr(R, "_ic_build_case", factory)
    monkeypatch.setattr(R, "_ic_case_hash", lambda case: "case-hash-for-test")
    return calls


def _report(name="s2", *, layers=True, finance_digest=None, mutate=None) -> dict:
    """A stored report (JSON) assembled from the engine on a SAM case."""
    from services.finance.engine import run_case
    from services.finance.report import assemble_finance_sections

    case = S.to_finance_case(name)
    result = run_case(case, layers=S.sam_tax_layers(name) if layers else None)
    prov = {"inputs": {"finance": finance_digest}, "finance_case_hash": "h", "owner": case.owner}
    rep = assemble_finance_sections(result, case, assumptions_hash="a" * 16, packs={},
                                    provenance=prov).model_dump(mode="json")
    if mutate:
        mutate(rep)
    return rep


@pytest.fixture
def solved(install_network):
    return install_network(build_network(solve=True), name="chat_ic")


@pytest.fixture
def store():
    """Put a report in the solver state; clear it (and the run) afterwards."""
    st = _sim_state()

    def put(rep):
        st["investment_case_report"] = rep
    yield put
    st["investment_case_report"] = None
    st["investment_case"] = None


@pytest.fixture(autouse=True)
def _clean_campaign():
    C.reset()
    yield
    C.reset()


def _size(obj) -> int:
    return len(json.dumps(obj, default=str))


def _numbers(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _numbers(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _numbers(v)
    else:
        yield obj


# ── registration ─────────────────────────────────────────────────────────────


def test_the_four_tools_are_registered_with_their_tiers_and_routes():
    tiers = {"run_investment_case": "execution_long_running", "get_investment_case": "read",
             "solve_ppa_price": "read", "explain_cashflow": "read"}
    for name, tier in tiers.items():
        assert name in chat_tools.DISPATCHERS
        assert safety_tier_for(name) == tier
    assert TOOL_ROUTES["run_investment_case"] == [("POST", "/api/results/investment_case")]
    assert ("GET", "/api/results/investment_case/report") in TOOL_ROUTES["get_investment_case"]
    # No collision with the asset-sizing explainer.
    assert chat_tools.DISPATCHERS["explain_investment"] is not chat_tools.DISPATCHERS[
        "explain_cashflow"]


def test_every_new_error_kind_is_in_the_manifest():
    from tests.test_tool_error_kind_manifest import MANIFEST

    kinds = json.loads(MANIFEST.read_text(encoding="utf-8"))["kinds"]
    for d in chat_tools._IC_ERROR_KINDS + chat_tools._SOLVE_PPA_ERROR_KINDS:
        assert d["error_kind"] in kinds, d


# ── get_investment_case: summary ────────────────────────────────────────────


def test_before_any_run_it_is_no_data_not_a_zero(solved, store):
    out = _tool("get_investment_case")
    assert out["status"] == "no_data" and "zero" in out["message"]
    assert _tool("explain_cashflow")["status"] == "no_data"


def test_summary_headlines_gate_completeness_and_staleness(solved, store):
    store(_report())
    out = _tool("get_investment_case")
    assert out["status"] == "ok" and out["report"]["present"] is True
    # The stored hash is not the current one: stale, never silently current.
    assert out["report"]["stale"] is True
    h = out["headlines"]
    assert isinstance(h["equity_post_tax_irr"], float)
    assert isinstance(h["min_dscr"], float)
    assert set(out["completeness"]) == {"project", "debt", "tax", "participants", "gates"}
    assert "wacc_vs_discount_rate_consistent" in out["wacc_gate"]
    assert out["cashflow_lines"] > 0
    assert _size(out) < _CAP


def test_none_reads_not_established_never_zero(solved, store):
    # Without tax layers (and no pack) the post-tax headlines are None (C12).
    store(_report(layers=False))
    out = _tool("get_investment_case")
    h = out["headlines"]
    assert h["equity_post_tax_irr"] == _NE
    assert h["project_post_tax_irr"] == _NE
    assert out["completeness"]["tax"] == "not_established"
    assert out["reasons"]["tax"]
    # The gate's unknown legs (no LP basis in the SAM case) are named, not null.
    assert out["wacc_gate"]["wacc_vs_discount_rate_consistent"] == _NE
    assert all(v is not None for v in h.values())


def test_the_summary_stays_under_the_cap_with_long_user_text(solved, store):
    def bloat(rep):
        p = rep["sections"]["project"]["payload"]
        p["owner"] = "O" * 5000
        p["flags"] = [f"flag_{i}:" + "x" * 900 for i in range(300)]
        rep["case_id"] = "C" * 5000
        for name in ("project", "debt", "tax", "participants", "gates"):
            rep["sections"][name]["status"] = "not_established"
            rep["sections"][name]["note"] = "n" * 5000
            rep["completeness"][name] = "not_established"
        g = rep["sections"]["gates"]["payload"]
        g["assets_with_other_rates"] = ["A" * 3000] * 50
    store(_report(mutate=bloat))
    out = _tool("get_investment_case")
    assert _size(out) < _CAP
    assert len(out["owner"]) <= 80 and len(out["case_id"]) <= 80
    assert out["flags_total"] == 300
    assert all(len(f) <= 160 for f in out["flags"])
    assert "omitted" in out


# ── get_investment_case: cashflows ──────────────────────────────────────────


def test_cashflow_pages_cover_every_line_once_and_cut_ids(solved, store):
    def long_ids(rep):
        for ln in rep["cashflow_lines"]:
            ln["counterparty"] = "P" * 400
            ln["provenance"]["contract_id"] = "K" * 400
            ln["provenance"]["source_id"] = "S" * 400
            ln["asset"] = "A" * 400
    rep = _report(mutate=long_ids)
    store(rep)
    first = _tool("get_investment_case", detail="cashflows")
    assert first["kind"] == "investment_case_cashflows" and first["page"] == 1
    # The lines carry the counterfactual negated (WP4.6b B2): the basis says so,
    # so the model never subtracts it twice (WP4.6b review round 2).
    assert "NEGATED" in first["basis"] and "not a line" not in first["basis"]
    total, pages = first["total_count"], first["pages"]
    assert total == len(rep["cashflow_lines"]) and pages > 1
    seen = []
    for page in range(1, pages + 1):
        out = _tool("get_investment_case", detail="cashflows", page=page)
        assert _size(out) < _CAP
        assert out["has_more"] is (page < pages)
        for row in out["items"]:
            assert len(row["counterparty"]) == 80 and len(row["contract"]) == 80
            assert len(row["asset"]) == 80 and len(row["source"]) <= 120
            assert set(row) >= {"year", "stream", "counterparty", "amount", "source"}
        seen += out["items"]
    assert len(seen) == total
    years = [r["year"] for r in seen]
    assert years == sorted(years)
    # Deterministic: the same page twice is the same page.
    assert _tool("get_investment_case", detail="cashflows", page=2) == \
        _tool("get_investment_case", detail="cashflows", page=2)
    past = _tool("get_investment_case", detail="cashflows", page=pages + 1)
    assert past["items"] == [] and past["has_more"] is False


def test_cashflow_pages_never_read_as_summing_to_an_unknown_cash(solved, store):
    """WP4.6c review F1 (C12): a not-established line is left out of the lines,
    so the page says the equity cash is not established and which lines are,
    and does not claim the lines sum to it; a NaN amount reads "not
    established" (F4), never NaN or 0."""
    def unknown(rep):
        p = rep["sections"]["project"]
        p["status"], p["note"] = "not_established", "line_not_established:fuel"
        rep["completeness"]["project"] = "not_established"
        p["payload"]["cash"]["equity_post_tax"] = None
        p["payload"]["operating_status"] = {"operating": "not_established", "capex": "ok"}
        first = min(rep["cashflow_lines"], key=lambda ln: ln["year"])   # on page 1
        first["amount"] = float("nan")
    store(_report(mutate=unknown))
    out = _tool("get_investment_case", detail="cashflows")
    assert out["equity_post_tax_cash"] == "not established"
    assert "line_not_established:fuel" in out["lines_not_established"]
    assert "operating:operating" in out["lines_not_established"]
    assert "NOT established" in out["basis"] and "sum to the post-tax" not in out["basis"]
    assert [r["amount"] for r in out["items"]].count("not established") == 1
    json.dumps(out, allow_nan=False)                    # strict JSON: no NaN reaches the model
    assert _tool("explain_cashflow")["equity_irr"]["status"] == "not_established"
    # An established case says so, and the lines sum to it.
    store(_report())
    ok = _tool("get_investment_case", detail="cashflows")
    assert ok["equity_post_tax_cash"] == "established" and "lines_not_established" not in ok
    assert "sum to the post-tax equity cash" in ok["basis"]


def test_bad_arguments_are_request_invalid(solved, store):
    store(_report())
    for kw in ({"detail": "lines"}, {"detail": "cashflows", "page": 0}):
        with pytest.raises(HTTPException) as exc:
            _tool("get_investment_case", **kw)
        assert exc.value.detail["error_kind"] == "investment_case_request_invalid"


# ── solve_ppa_price ─────────────────────────────────────────────────────────


@pytest.fixture
def s1b(solved, monkeypatch):
    """The SAM S1b case (solve-for-PPA oracle) as the adapter's output, its
    inputs stored, SAM's tax layers for the solve."""
    case = S.to_finance_case("s1b", solve=True)
    holder = {"case": case}
    holder["calls"] = _fake_build(monkeypatch, lambda: holder["case"])
    monkeypatch.setattr(chat_tools, "_ic_solve_layers", lambda c: S.sam_tax_layers("s1b"))
    _put_finance(S.to_finance_case("s2").inputs.model_dump(mode="json"))
    yield holder
    _put_finance(None)


def test_solve_ppa_price_matches_the_engine(s1b, store):
    from services.finance.engine import solve_ppa

    case = s1b["case"]
    sp = case.inputs.solve_ppa
    ref = solve_ppa(case, layers=S.sam_tax_layers("s1b"))
    assert ref["solve_ppa_status"] == "ok"
    out = _tool("solve_ppa_price", target_irr=sp.target_irr, target_year=sp.target_year)
    assert out["status"] == "ok"
    assert out["solved_ppa_price_per_mwh"] == pytest.approx(ref["solved_ppa_price"], abs=1e-4)
    assert out["equity_post_tax_irr_at_target_year"] == pytest.approx(sp.target_irr, abs=1e-6)
    assert out["contract_id"] == "ppa" and out["stored_inputs_changed"] is False
    assert out["money_year"] == ref["solved_ppa_price_money_year"]
    # Another target: the engine on the same case with that target.
    other = dataclasses.replace(case, inputs=case.inputs.model_copy(update={
        "solve_ppa": sp.model_copy(update={"target_irr": sp.target_irr + 0.02})}))
    ref2 = solve_ppa(other, layers=S.sam_tax_layers("s1b"))
    out2 = _tool("solve_ppa_price", target_irr=sp.target_irr + 0.02,
                 target_year=sp.target_year, contract_id="ppa")
    assert out2["solved_ppa_price_per_mwh"] == pytest.approx(ref2["solved_ppa_price"], abs=1e-4)
    assert out2["solved_ppa_price_per_mwh"] > out["solved_ppa_price_per_mwh"]


def test_solve_ppa_price_solves_the_last_runs_owner(s1b, store):
    """WP4.6c review F2: the solve builds the SAME case as the report — the
    owner of the last run (its record, else the report's provenance), or the
    one named."""
    st = _sim_state()
    rep = _report()
    rep["sections"]["project"]["payload"]["provenance"]["owner"] = "site_owner_B"
    store(rep)
    sp = s1b["case"].inputs.solve_ppa
    _tool("solve_ppa_price", target_irr=sp.target_irr, target_year=sp.target_year)
    assert s1b["calls"][-1]["owner"] == "site_owner_B"
    st["investment_case"] = {"status": "done", "owner": "dev_A"}
    _tool("solve_ppa_price", target_irr=sp.target_irr, target_year=sp.target_year)
    assert s1b["calls"][-1]["owner"] == "dev_A"
    _tool("solve_ppa_price", target_irr=sp.target_irr, target_year=sp.target_year, owner="dev_C")
    assert s1b["calls"][-1]["owner"] == "dev_C"
    with pytest.raises(HTTPException) as exc:
        _tool("solve_ppa_price", target_irr=sp.target_irr, target_year=sp.target_year, owner=" ")
    assert exc.value.detail["error_kind"] == "investment_case_request_invalid"


def test_an_unknown_solve_status_is_not_reported_as_no_root(s1b, store, monkeypatch):
    import services.finance.engine as E

    monkeypatch.setattr(E, "solve_ppa", lambda *a, **k: {"solve_ppa_status": "some_future_status"})
    sp = s1b["case"].inputs.solve_ppa
    with pytest.raises(HTTPException) as exc:
        _tool("solve_ppa_price", target_irr=sp.target_irr, target_year=sp.target_year)
    assert exc.value.detail["error_kind"] == "investment_case_refused"
    assert exc.value.detail["code"] == "some_future_status"


def test_solve_ppa_price_never_mutates_the_stored_inputs(s1b, store):
    rep = _report()
    store(rep)
    st = _sim_state()
    fin_before = copy.deepcopy(st["solver_config"].finance)
    rep_before = copy.deepcopy(st["investment_case_report"])
    case = s1b["case"]
    sp_before = case.inputs.solve_ppa.model_dump()
    _tool("solve_ppa_price", target_irr=0.2, target_year=5)
    assert st["solver_config"].finance == fin_before
    assert st["investment_case_report"] == rep_before
    assert case.inputs.solve_ppa.model_dump() == sp_before     # the built case untouched


def _with_ppa(case, **changes):
    temps = tuple(dataclasses.replace(t, lines=tuple(
        dataclasses.replace(ln, **changes) if ln.key == "ppa" else ln for ln in t.lines))
        for t in case.templates)
    return dataclasses.replace(case, templates=temps)


@pytest.mark.parametrize("changes,kind", [
    ({"amount": -1000.0}, "solve_ppa_not_owner_sold"),
    ({"esc_class": "ppa"}, "solve_ppa_not_linear"),
    ({"changes_dispatch": True}, "solve_ppa_needs_redispatch"),
    ({"price": None}, "solve_ppa_price_unknown"),
])
def test_the_c9_refusals_come_back_as_error_kinds(s1b, changes, kind):
    s1b["case"] = _with_ppa(s1b["case"], **changes)
    with pytest.raises(HTTPException) as exc:
        _tool("solve_ppa_price", target_irr=0.1, target_year=10)
    assert exc.value.status_code == 422
    assert exc.value.detail["error_kind"] == kind and exc.value.detail["code"] == kind


def test_an_unknown_contract_is_not_found(s1b):
    with pytest.raises(HTTPException) as exc:
        _tool("solve_ppa_price", target_irr=0.1, target_year=10, contract_id="nope")
    assert exc.value.detail["error_kind"] == "solve_ppa_contract_not_found"


def test_no_root_keeps_its_reason_in_code(s1b, monkeypatch):
    import services.finance.engine as E

    monkeypatch.setattr(E, "solve_ppa", lambda case, pack=None, layers=None: {
        "solved_ppa_price": None,
        "solve_ppa_status": "solve_ppa_no_root:target_met_at_zero_price"})
    with pytest.raises(HTTPException) as exc:
        _tool("solve_ppa_price", target_irr=0.1, target_year=10)
    assert exc.value.detail["error_kind"] == "solve_ppa_no_root"
    assert exc.value.detail["code"] == "solve_ppa_no_root:target_met_at_zero_price"


def test_an_adapter_refusal_is_investment_case_refused(s1b, monkeypatch):
    import routers.results as R
    from services.finance.case import FinanceRefused

    def refuse():
        raise FinanceRefused("template_not_annual", "stated by the test")
    monkeypatch.setattr(R, "_ic_build_case", lambda n, cfg, *, owner, lost_load: refuse)
    with pytest.raises(HTTPException) as exc:
        _tool("solve_ppa_price", target_irr=0.1, target_year=10)
    assert exc.value.detail["error_kind"] == "investment_case_refused"
    assert exc.value.detail["code"] == "template_not_annual"


def test_out_of_range_targets_are_request_invalid(s1b):
    with pytest.raises(HTTPException) as exc:
        _tool("solve_ppa_price", target_irr=0.1, target_year=0)
    assert exc.value.detail["error_kind"] == "investment_case_request_invalid"


def test_solve_ppa_needs_a_solve_and_finance_inputs(install_network, monkeypatch):
    _fake_build(monkeypatch, lambda: S.to_finance_case("s1b", solve=True))
    install_network(build_network(), name="chat_ic_unsolved")
    with pytest.raises(HTTPException) as exc:
        _tool("solve_ppa_price", target_irr=0.1, target_year=10)
    assert exc.value.detail["error_kind"] == "investment_case_not_solved"
    install_network(build_network(solve=True), name="chat_ic_nofin")
    with pytest.raises(HTTPException) as exc:
        _tool("solve_ppa_price", target_irr=0.1, target_year=10)
    assert exc.value.detail["error_kind"] == "finance_inputs_missing"


# ── explain_cashflow ────────────────────────────────────────────────────────


def test_explain_cashflow_attributes_the_equity_npv_and_the_min_dscr_year(solved, store):
    from services.finance.investment_case_runner import finance_digest

    fin = S.to_finance_case("s2").inputs.model_dump(mode="json")
    _put_finance(fin)
    try:
        # The report's provenance names the stored inputs: the cost of equity is known.
        rep = _report(finance_digest=finance_digest(_sim_state()["solver_config"].finance))
        store(rep)
        out = _tool("explain_cashflow")
    finally:
        _put_finance(None)
    assert out["status"] == "ok" and "method" in out and "CFADS" in out["method"]
    assert out["rate"]["basis"] == "cost_of_equity"
    assert out["rate"]["value"] == pytest.approx(fin["cost_of_equity"])
    eq = out["equity_irr"]
    p = rep["sections"]["project"]["payload"]
    assert eq["status"] == "ok" and eq["reconciles"] is True
    # The stream PVs sum to the post-tax equity NPV at the cost of equity.
    assert eq["sum_of_stream_pvs"] == pytest.approx(p["equity_post_tax_npv"], abs=0.05)
    assert eq["equity_post_tax_npv_at_rate"] == pytest.approx(p["equity_post_tax_npv"], abs=0.01)
    pvs = [abs(r["pv"]) for r in eq["by_stream"]]
    assert pvs == sorted(pvs, reverse=True)
    assert {"ppa_settlement", "capex"} <= {r["stream"] for r in eq["by_stream"]}
    assert all(r["effect_on_irr"] in ("raises", "lowers", "none") for r in eq["by_stream"])
    assert not any("tax:tax" in r["stream"] for r in eq["by_stream"])
    # The min-DSCR year: argmin of the debt payload's DSCR; CFADS reconciles.
    d = rep["sections"]["debt"]["payload"]
    dscr = [(v, i) for i, v in enumerate(d["dscr"]) if v is not None]
    vmin = min(w for w, _ in dscr)                       # a tie: the earliest year (review F3)
    v, i = next((w, j) for w, j in dscr if abs(w - vmin) <= 1e-9 * max(1.0, vmin))
    m = out["min_dscr_year"]
    assert m["status"] == "ok" and m["year"] == p["years"][i]
    assert m["dscr"] == pytest.approx(v, abs=1e-4)
    assert m["years_at_min"] == sum(1 for w, _ in dscr if abs(w - v) <= 1e-9 * max(1.0, v))
    assert m["cfads_reconciles"] is True
    assert m["service"]["total"] == pytest.approx(d["service"][i], abs=0.01)
    assert m["by_tranche"]
    assert _size(out) < _CAP


def test_explain_cashflow_falls_back_to_the_irr_when_the_inputs_moved_on(solved, store):
    def older_report(rep):          # before the payload recorded the cost of equity
        rep["sections"]["project"]["payload"].pop("cost_of_equity", None)
    store(_report(finance_digest="no-longer-stored", mutate=older_report))
    out = _tool("explain_cashflow", detail="full")
    assert out["rate"]["basis"].startswith("equity_post_tax_irr")
    # At the IRR the stream PVs sum to zero.
    assert out["equity_irr"]["sum_of_stream_pvs"] == pytest.approx(0.0, abs=0.05)
    assert _size(out) < _CAP


def test_explain_cashflow_reads_the_reports_own_cost_of_equity(solved, store):
    store(_report(finance_digest="no-longer-stored"))
    out = _tool("explain_cashflow")
    assert out["rate"]["basis"] == "cost_of_equity"


def test_explain_cashflow_takes_the_counterfactual_from_its_lines_once():
    """The report's lines carry the counterfactual negated (WP4.6b review B2):
    the attribution must not add `counterfactual_net` a second time."""
    from services.chat_tools import _IC_AVOIDED, _ic_equity_attribution
    from services.finance.case import Template, TemplateLine
    from services.finance.engine import run_case
    from services.finance.report import assemble_finance_sections
    from tests.test_finance_engine import LAYER, _case

    cf = (Template(first_year=2031, lines=(TemplateLine("bill", "energy_import", -100.0, "tariff",
                                                        source="counterfactual",
                                                        source_id="bill"),)),)
    case = _case(cf=cf)
    rep = assemble_finance_sections(run_case(case, layers=LAYER), case, assumptions_hash="a" * 16,
                                    packs={}).model_dump(mode="json")
    out = _ic_equity_attribution(rep, 0.09, 50)
    assert out["reconciles"] is True, out["max_yearly_residual"]
    avoided = [x for x in out["by_stream"] if x["stream"] == _IC_AVOIDED]
    assert len(avoided) == 1 and avoided[0]["undiscounted"] > 0


def test_explain_cashflow_without_equity_cash_says_not_established(solved, store):
    store(_report(layers=False))
    out = _tool("explain_cashflow")
    assert out["equity_irr"]["status"] == "not_established"
    assert out["equity_irr"]["reason"]
    assert 0 not in [x for x in _numbers(out["equity_irr"]) if not isinstance(x, bool)]


# ── run_investment_case ─────────────────────────────────────────────────────


def _wait(timeout=30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = _tool("get_investment_case")
        if out.get("run", {}).get("status") not in (None, "running"):
            return out
        time.sleep(0.05)
    raise AssertionError("investment case did not settle")


def test_run_investment_case_is_campaign_gated_and_charges_nothing(solved, store,
                                                                    monkeypatch):
    calls = _fake_build(monkeypatch, lambda: S.to_finance_case("s2"))
    _put_finance(S.to_finance_case("s2").inputs.model_dump(mode="json"))
    try:
        C.start("value the BESS")
        out = _tool("run_investment_case", owner="owner")
        assert out["status"] == "running" and out["study"] == "investment_case"
        assert out["campaign"]["solves_charged"] == 0
        assert calls == [{"owner": "owner"}]
        done = _wait()
        assert done["run"]["status"] == "done" and done["report"]["present"] is True
        log = C.status()
        assert [e["study"] for e in log["entries"]] == ["investment_case"]
        assert log["spent_solves"] == 0
    finally:
        _put_finance(None)


def test_a_refused_start_burns_nothing_and_maps_its_code(install_network, store,
                                                         monkeypatch):
    _fake_build(monkeypatch, lambda: S.to_finance_case("s2"))
    install_network(build_network(), name="chat_ic_unsolved")
    C.start("value the BESS")
    with pytest.raises(HTTPException) as exc:
        _tool("run_investment_case")
    assert exc.value.status_code == 409
    assert exc.value.detail["error_kind"] == "investment_case_not_solved"
    assert C.status()["entries"] == []
    install_network(build_network(solve=True), name="chat_ic_nofin")
    with pytest.raises(HTTPException) as exc:
        _tool("run_investment_case")
    assert exc.value.status_code == 422
    assert exc.value.detail["error_kind"] == "finance_inputs_missing"
    assert C.status()["entries"] == []


def test_run_investment_case_refuses_an_empty_owner(solved):
    with pytest.raises(HTTPException) as exc:
        _tool("run_investment_case", owner="")
    assert exc.value.detail["error_kind"] == "investment_case_request_invalid"


def test_a_busy_mesh_is_investment_case_busy(solved, store, monkeypatch):
    import routers.results as R

    monkeypatch.setattr(R, "_study_mesh_blocker", lambda key: "a solve is running")
    _fake_build(monkeypatch, lambda: S.to_finance_case("s2"))
    _put_finance(S.to_finance_case("s2").inputs.model_dump(mode="json"))
    try:
        for name, kw in (("run_investment_case", {}),
                         ("solve_ppa_price", {"target_irr": 0.1, "target_year": 5})):
            with pytest.raises(HTTPException) as exc:
                _tool(name, **kw)
            assert exc.value.status_code == 409
            assert exc.value.detail["error_kind"] == "investment_case_busy"
    finally:
        _put_finance(None)
