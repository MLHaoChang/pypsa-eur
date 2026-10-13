"""
The U2 WP8 part B gate's binding conditions Z2 and Z3 and its nits
(`docs/superpowers/notes/2026-10-08-u2-wp8b-gate.md`), taken in WP9. Z1 (the
`ast` scan of the tariff engine's note writes) is in
`test_decision_vocabulary_parity.py`.

* Z2 — the report picks its LCOS fact and label by the case's ENGINE, so an
  engine case whose LCOS is not established is keyed and labelled the
  investment case's, with the engine's flag (never "(pro forma)").
* Z3 — a centre refusal the run did not record answers the tornado POST with
  the same 422 (code and message) as the findings and the report, before
  anything is queued; never a 202 and a worker failing with a raw string.
"""
from __future__ import annotations

import json
import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "pre_u2_study"


def _stored_case():
    from models.study import InvestmentCase

    raw = json.loads((FIXTURES / "case.json").read_text(encoding="utf-8"))
    raw.pop("available", None)
    return InvestmentCase.model_validate(raw)


def _engine_case_without_lcos():
    """The S8 case as the engine would build it with its LCOS not established."""
    case = _stored_case()
    kpis = case.kpis.model_copy(update={
        "levelised_cost": None, "levelised_cost_basis": None,
        "unavailable": {**case.kpis.unavailable, "levelised_cost": "lcos_not_established"}})
    notes = tuple(n for n in case.honesty_notes if not n.startswith("lcos_"))
    return case.model_copy(update={"engine": "finance_engine", "kpis": kpis,
                                   "honesty_notes": notes})


def _report_over(case):
    from services.study import report as R
    from tests.test_study_decision_report import (
        LIB, Q, _att, _findings, _ledger, _rob, _study)

    oid = case.option_id
    atts = [_att(o, 3e5 if o == oid else 1e5) for o in ("bess_1h", "bess_2h", "bess_4h")]
    return R.build_decision_report(R.ReportInputs(
        study=_study(), question=Q.BESS_AT_SITE, findings=_findings(atts, _rob(oid, (4e5, 2e5))),
        cases={oid: case}, ledger=_ledger(), tariff=LIB.tariffs["de_industrial_illustrative"]))


def test_a_null_engine_lcos_is_the_investment_cases_with_the_engines_flag():
    from services.study import report as R

    case = _engine_case_without_lcos()
    report = _report_over(case)
    econ = report.sections["economics"]
    assert econ.status == "ok", econ.note
    assert "lcos_excl_charging" not in econ.facts
    fact = econ.facts["lcos_finance_engine"]
    assert fact.value is None and fact.engine == "finance_engine"
    assert fact.unavailable == "lcos_not_established"
    assert "pro forma" not in fact.label and "investment case" in fact.label
    codes = {d.code for d in report.required_disclosures}
    # no LCOS shown: neither sentence about how it was built
    assert not {"lcos_two_definitions", "lcos_includes_charging_energy_cost"} & codes
    assert R.validate_prose(report)["economics"]


def test_a_pro_forma_case_is_never_assembled_as_the_engines():
    """
    B1 after U2 WP10: the pro forma is deleted, so a NEW report is assembled
    on the engine's cases only (`load_inputs` refuses a pre-U2 run first). A
    stored pre-U2 case handed to the assembler anyway is not shown as
    current — not under the engine's LCOS label, and not under the pro
    forma's charging-free one either: the economics section is not
    established, `case_by_an_earlier_version`, a re-run offered. (A stored
    pre-U2 REPORT keeps its own facts and sentences:
    `test_study_engine_literal_compat.py`.)
    """
    from services.study import report as R

    case = _stored_case()
    assert case.engine == "cash_flow_expander"
    report = _report_over(case)
    econ = report.sections["economics"]
    assert (econ.status, econ.note) == ("not_established", "case_by_an_earlier_version")
    assert not {"lcos_finance_engine", "lcos_excl_charging", "case_npv"} & set(econ.facts)
    assert R.help_for("case_by_an_earlier_version")[1] == "study"
    assert "run the study again" in R.help_for("case_by_an_earlier_version")[0].lower()


# ── Z3: the tornado POST and a centre refusal the run did not record ──────

@pytest.fixture
def studies_on(monkeypatch):
    from tests.study_s4_support import enable_studies

    yield from enable_studies(monkeypatch)


@pytest.fixture
def fake(monkeypatch):
    from services import solver_service
    from tests.test_study_runner import FakeSolver

    solver = FakeSolver()
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    return solver


def test_a_centre_refusal_the_run_did_not_record_is_a_422_on_the_tornado_post(
        client, api_project, studies_on, fake, monkeypatch):
    """
    Gate P3: the engine refuses the centre case only when the case is valued
    (a `FinanceRefused` from `run_case`, which the runner never calls). The
    tornado POST checks the centre before it answers, so it refuses like the
    findings, with the code and the message, and starts nothing.
    """
    from services.study import engine_adapter as A
    from tests.test_study_tornado_routes import _run

    name = "z3-centre"
    sid = _run(client, api_project, name)
    base = f"/api/projects/{name}/studies/{sid}"

    def refuse(*_a, **_kw):
        raise A.EngineRefused("engine_refused", "the finance engine refused the case: boom",
                              engine_code="asset_lifetime_short")

    monkeypatch.setattr(A, "option_case", refuse)
    for method, path in (("get", "/findings"), ("post", "/findings/tornado")):
        r = getattr(client, method)(f"{base}{path}", **({"json": {}} if method == "post" else {}))
        assert r.status_code == 422, (path, r.status_code, r.text)
        detail = r.json()["detail"]
        assert detail["error_kind"] == "engine_refused", (path, detail)
        assert detail["message"] == "the finance engine refused the case: boom", (path, detail)
    never = client.get(f"{base}/findings/tornado")
    assert never.status_code == 404, never.text
    assert never.json()["detail"]["error_kind"] == "tornado_never_run"


# ── nit (N10): the Y1 message names options and rows by their labels ─────

def test_the_unavailable_finance_message_names_option_labels_without_the_baseline():
    from services.study import findings as F
    from services.study import questions as Q

    run = {"details": {oid: {"finance_unavailable": "currency_year_mixed",
                             "finance_unavailable_message": "the case would mix years"}
                       for oid in ("none", "bess_1h", "bess_2h")}}
    with pytest.raises(F.FindingsRefused) as err:
        F._refuse_unavailable_finance(run, Q.BESS_AT_SITE)
    msg = err.value.message
    assert err.value.code == "currency_year_mixed" and err.value.status == 422
    assert msg.startswith("the investment case of Battery, 1 hour and Battery, 2 hours "
                          "cannot be valued: the case would mix years"), msg
    assert "none" not in msg.split(":")[0] and "grid only" not in msg


def test_the_currency_year_message_names_ledger_rows_by_label():
    from services.study import compile as C
    from tests.test_study_decision_report import _ledger

    ledger = _ledger()
    row = next(r for r in ledger.rows if r.key == "battery_inverter_eur_per_kw")
    with pytest.raises(C.CompileError) as err:
        C.currency_year_of(ledger, {"currency": "EUR", "currency_year": 2024,
                                    "tariff_id": "tou_dc_test", "name": "My TOU tariff"})
    msg = err.value.message
    assert row.label in msg and "battery_inverter_eur_per_kw" not in msg, msg
    assert "My TOU tariff" in msg and "tou_dc_test" not in msg, msg
