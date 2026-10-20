"""
IC G3 (plan `docs/superpowers/plans/2026-10-12-ic-g3-campus-route-and-followups.md` §4 WP-R; R-1 … R-5):
the investment-case route takes the extra owner assets from the latest solved campus study of the same
project (`campus_electrical_service.owner_assets_response`), pinned by its `source_hash`.

- R-1. The request-time seam `routers.results._ic_campus_source()` decides, in order: no project, no row
  or no `campus_electrical/run` directory → none (and nothing created); a project of another kind →
  none; the engine absent → none, flagged `campus_study_unavailable`; the producer raising → refused
  `campus_assets_invalid`; the money year → refused `campus_assets_money_year_mismatch`; a stale study →
  no extras, flagged `campus_study_stale`; otherwise the assets as `ExtraOwnerAsset`s. An unsaved
  network beside an applying study is flagged `campus_study_network_unsaved` (the run only).
- R-2. Read once per request; the same source goes to the factory (`campus=`) and the staleness key,
  and to `solve_ppa_price`'s case.
- R-4. The `extra_assets` part: None without a study, `{"error": code}` for one that cannot be read.

`owner_assets_response`'s shape is a contract the IC route consumes (R-6): the end-to-end test below
runs the real producer on a hand-made investment run (`fake_investment`, #97's own fixture).
"""
from __future__ import annotations

import dataclasses
import shutil
import time
import uuid
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from tests.conftest import build_network
from tests.fixtures.investment_case.sam import sam_case as S
from tests.test_campus_electrical_service import fake_investment, inv_row

FIN_URL = "/api/simulation/finance"
STUDY_URL = "/api/results/investment_case"
REPORT_URL = "/api/results/investment_case/report"


# ── helpers ──────────────────────────────────────────────────────────────────


def _poll(client, until=lambda b: b.get("status") != "running", timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    body: dict = {}
    while time.time() < deadline:
        r = client.get(STUDY_URL)
        if r.status_code == 200:
            body = r.json()
            if until(body):
                return body
        time.sleep(0.05)
    raise AssertionError(f"investment case did not settle: {body}")


def _put_fin(client, fin: dict):
    r = client.put(FIN_URL, json={"finance": fin})
    assert r.status_code == 200, r.text


def _s2_inputs(**over) -> dict:
    return {**S.to_finance_case("s2").inputs.model_dump(mode="json"), **over}


def _save(client, install_network, name: str, n=None):
    """Save the in-memory network as a (hub) project through the route: the context then
    carries the project's uuid (`project_registry.bind_context`)."""
    install_network(n if n is not None else build_network(solve=True), name=name)
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


@contextmanager
def _row(_auth_db, session_ctx, client):
    from db.models import Project

    _engine, session_local = _auth_db
    with session_local() as db:
        yield db, db.get(Project, uuid.UUID(session_ctx(client).project_uuid))


def _campus_dir(row):
    from services import project_registry

    return project_registry.project_dir(row) / "campus_electrical"


def _purchase():
    return [inv_row("transformer T", "TR_132_33_40", "transformer", 1),
            inv_row("cable CBL_1", "CB_33_AL95", "cable", 1, length_km=2.5),
            inv_row("switchgear G", "SG_132_31p5", "switchgear", 3)]


def _fake_adapter(monkeypatch):
    """The real `_ic_build_case` with the adapter (`build_finance_case`) replaced by the SAM S2
    case: what it was given is recorded."""
    import routers.results as R
    from services.results import finance_case as FC

    calls: list[dict] = []

    def fake(n, cfg, fin, *, result_df, lost_load=None, owner=None, extra_assets=(),
             baseline=None):
        calls.append({"extra_assets": tuple(extra_assets), "owner": owner})
        return S.to_finance_case("s2")

    monkeypatch.setattr(FC, "build_finance_case", fake)
    monkeypatch.setattr(R, "_ic_case_hash", lambda case: "case-hash-for-test")
    return calls


def _capture_factory(monkeypatch):
    """Replace the factory: record the `campus` source each request hands it."""
    import routers.results as R

    seen: list = []

    def factory(n, cfg, *, owner, lost_load, campus=None):
        seen.append(campus)
        return lambda: S.to_finance_case("s2")

    monkeypatch.setattr(R, "_ic_build_case", factory)
    monkeypatch.setattr(R, "_ic_case_hash", lambda case: "case-hash-for-test")
    return seen


def _run(client) -> dict:
    r = client.post(STUDY_URL, json={})
    assert r.status_code == 200, r.text
    return _poll(client)


def _response(assets, *, source_hash="0123456789abcdef", stale=False):
    return {"assets": assets, "source_hash": source_hash, "stale": stale}


def _asset(name="campus:TR_132_33_40#1", *, currency="EUR", price_year=2026, meta=True):
    a = {"name": name, "kind": "transformer", "basis": "lump", "quantity": 1.0,
         "parts": [{"name": "investment", "upfront_per_unit": 1_800_000.0, "lifetime": 40.0,
                    "fom_share": 0.015}],
         "build_year": 2030, "source": "campus_study", "source_hash": "0123456789abcdef"}
    if meta:
        a["meta"] = {"need": "transformer T", "library_id": "TR_132_33_40", "units": 1,
                     "length_km": None, "currency": currency, "price_year": price_year,
                     "provenance": "assumed", "illustrative": True}
    return a


# ── end to end: the real producer through the route ──────────────────────────


@pytest.mark.live_solve
def test_the_campus_study_assets_are_the_reports_extra_assets_and_its_capex(
        client, install_network, session_ctx, _auth_db, reset_backend):
    from services import campus_electrical_service as ce
    from tests.test_finance_case_storage_and_meters import _commercial, _fin, _single_owner, _site
    from tests.test_value_flow_reconciliation import _solve

    n, cfg = _solve(_site(), _commercial(_single_owner(_site())))
    _save(client, install_network, "campus_ic_e2e", n)
    # `install_network` (inside `_save`) resets the solver config: put the solve's back.
    session_ctx(client).solver_state["solver_config"] = cfg
    with _row(_auth_db, session_ctx, client) as (_db, row):
        fake_investment(row, _purchase())
        want_hash = ce.campus_study_hash(row)
        names = [a["name"] for a in ce.extra_owner_assets(row)]
        want_capex = sum(a["quantity"] * a["parts"][0]["upfront_per_unit"]
                         for a in ce.extra_owner_assets(row))
        campus_dir = _campus_dir(row)
    _put_fin(client, _fin(currency_year=2026).model_dump(mode="json"))

    body = _run(client)
    assert body["status"] == "done", (body["error_code"], body["error"])
    assert "campus_study_network_unsaved" not in body["flags"]      # saved, not edited since
    full = client.get(REPORT_URL, params={"detail": "full"}).json()
    block = full["sections"]["project"]["payload"]["extra_assets"]
    assert [b["name"] for b in block] == names
    assert {b["source_hash"] for b in block} == {want_hash}
    capex = -sum(ln["amount"] for ln in full["cashflow_lines"]
                 if ln["provenance"]["source"] == "capex")
    assert body["report"]["stale"] is False

    # The study removed: the report names `extra_assets`; the re-run drops exactly its capex.
    shutil.rmtree(campus_dir / "run")
    rep = client.get(STUDY_URL).json()["report"]
    assert rep["stale"] is True and rep["changed"] == ["extra_assets"]
    assert _run(client)["status"] == "done"
    full2 = client.get(REPORT_URL, params={"detail": "full"}).json()
    assert full2["sections"]["project"]["payload"]["extra_assets"] == []
    capex2 = -sum(ln["amount"] for ln in full2["cashflow_lines"]
                  if ln["provenance"]["source"] == "capex")
    assert capex - capex2 == pytest.approx(want_capex)
    assert not (campus_dir / "run").exists()


# ── R-1, the branches ────────────────────────────────────────────────────────


def test_a_scratch_context_has_no_campus_source(client, install_network, monkeypatch):
    seen = _capture_factory(monkeypatch)
    install_network(build_network(solve=True))
    _put_fin(client, _s2_inputs())
    assert _run(client)["status"] == "done"
    (src,) = seen
    assert src.extras == () and src.run_flags == () and src.refusal is None and src.digest is None


def test_no_run_directory_is_none_and_creates_nothing(client, install_network, session_ctx,
                                                      _auth_db, monkeypatch):
    seen = _capture_factory(monkeypatch)
    _save(client, install_network, "campus_ic_norun")
    _put_fin(client, _s2_inputs())
    body = _run(client)
    assert body["status"] == "done"
    assert seen[0].digest is None and seen[0].extras == () and seen[0].run_flags == ()
    client.get(STUDY_URL)                                       # a GET poll reads it too
    with _row(_auth_db, session_ctx, client) as (_db, row):
        assert not _campus_dir(row).exists()


def test_the_engine_absent_with_a_run_directory_is_flagged_unavailable(
        client, install_network, session_ctx, _auth_db, monkeypatch):
    from services import campus_electrical_service as ce

    seen = _capture_factory(monkeypatch)
    _save(client, install_network, "campus_ic_noengine")
    with _row(_auth_db, session_ctx, client) as (_db, row):
        fake_investment(row, _purchase())
    monkeypatch.setattr(ce, "CAMPUS_AVAILABLE", False)
    monkeypatch.setattr(ce, "owner_assets_response",
                        lambda p: pytest.fail("the producer is not called without the engine"))
    _put_fin(client, _s2_inputs())
    assert _run(client)["status"] == "done"
    (src,) = seen
    assert src.extras == () and src.refusal is None
    assert src.run_flags == ("campus_study_unavailable",) and src.digest is not None


def test_a_project_of_another_kind_is_none_even_with_a_run_directory(
        client, install_network, session_ctx, _auth_db, monkeypatch):
    from services import campus_electrical_service as ce
    from services.gridspine_service import PLANNING_DYNAMICS

    seen = _capture_factory(monkeypatch)
    _save(client, install_network, "campus_ic_nonhub")
    with _row(_auth_db, session_ctx, client) as (db, row):
        fake_investment(row, _purchase())
        row.project_kind = PLANNING_DYNAMICS
        db.commit()
    monkeypatch.setattr(ce, "CAMPUS_AVAILABLE", False)        # not even the engine is asked
    _put_fin(client, _s2_inputs())
    assert _run(client)["status"] == "done"
    assert seen[0].digest is None and seen[0].run_flags == ()


@pytest.mark.parametrize("raise_", ["http422", "contract", "other"])
def test_a_producer_error_is_a_refused_report_campus_assets_invalid(
        raise_, client, install_network, session_ctx, _auth_db, monkeypatch):
    from services import campus_electrical_service as ce

    calls = _fake_adapter(monkeypatch)
    _save(client, install_network, f"campus_ic_err_{raise_}")
    with _row(_auth_db, session_ctx, client) as (_db, row):
        fake_investment(row, _purchase())

    def boom(project):
        if raise_ == "http422":
            raise HTTPException(422, "/secret/path: X is not in the library the run used")
        if raise_ == "contract":
            raise ce.ContractError("/secret/path: bad library")
        raise RuntimeError("/secret/path: disk")
    monkeypatch.setattr(ce, "owner_assets_response", boom)
    _put_fin(client, _s2_inputs())
    body = _run(client)
    assert body["status"] == "refused" and body["error_code"] == "campus_assets_invalid", body
    assert "/secret/path" not in (body["error"] or "")
    assert calls == []                                          # refused before the adapter
    refusal = client.get(REPORT_URL, params={"detail": "full"}).json()[
        "sections"]["project"]["payload"]["refusal"]
    assert refusal["code"] == "campus_assets_invalid" and "/secret/path" not in refusal["detail"]
    # The GET key reads the same source: the refused report is current, never a 500.
    rep = client.get(STUDY_URL)
    assert rep.status_code == 200 and rep.json()["report"]["stale"] is False


SECRET = "SECRET_VALUE"
NOT_A_LIST = "the campus study's owner assets are not a list of entries"


def _no_secret(client, body):
    """The value text reaches neither the run record, the stored report nor the GET."""
    assert SECRET not in str(body)
    assert SECRET not in client.get(REPORT_URL, params={"detail": "full"}).text
    assert SECRET not in client.get(STUDY_URL).text


def _missing_build_year():
    a = _asset()
    del a["build_year"]
    return a


@pytest.mark.parametrize("resp,detail", [
    (_response(SECRET), NOT_A_LIST),                                # `assets` not a list
    (_response([_asset(), SECRET]), NOT_A_LIST),                    # a non-dict entry
    (_response([{**_asset(), "quantity": SECRET}]),
     "'campus:TR_132_33_40#1' is not a valid extra owner asset"),      # ExtraOwnerAsset refuses
    (_response([_missing_build_year()]),
     "'campus:TR_132_33_40#1' is not a valid extra owner asset"),      # a missing field
    (_response([{**_asset(), "name": 7, "quantity": SECRET}]),
     "asset 0 is not a valid extra owner asset"),                       # no str name: the index
    (_response([{**_asset(), "name": "campus:" + "x" * 200, "quantity": SECRET}]),
     repr("campus:" + "x" * 73) + " is not a valid extra owner asset"),  # a long name: cut at 80
], ids=["assets_not_a_list", "non_dict_entry", "rejected_value", "missing_field", "no_name",
        "long_name"])
def test_a_malformed_or_rejected_campus_response_is_refused_without_its_values(
        resp, detail, client, install_network, session_ctx, _auth_db, monkeypatch):
    from services import campus_electrical_service as ce

    calls = _fake_adapter(monkeypatch)
    _save(client, install_network, "campus_ic_malformed")
    with _row(_auth_db, session_ctx, client) as (_db, row):
        fake_investment(row, _purchase())
    monkeypatch.setattr(ce, "owner_assets_response", lambda p: resp)
    _put_fin(client, _s2_inputs(currency="EUR", currency_year=2026))
    body = _run(client)
    assert body["status"] == "refused" and body["error_code"] == "campus_assets_invalid", body
    assert calls == []
    refusal = client.get(REPORT_URL, params={"detail": "full"}).json()[
        "sections"]["project"]["payload"]["refusal"]
    assert refusal == {"code": "campus_assets_invalid", "detail": detail}
    _no_secret(client, body)


def test_a_money_year_refusal_names_data_not_exception_text(client, install_network, session_ctx,
                                                            _auth_db, monkeypatch):
    from services import campus_electrical_service as ce

    _fake_adapter(monkeypatch)
    _save(client, install_network, "campus_ic_money_detail")
    with _row(_auth_db, session_ctx, client) as (_db, row):
        fake_investment(row, _purchase())
    monkeypatch.setattr(ce, "owner_assets_response",
                        lambda p: _response([{**_asset(), "meta": SECRET}]))
    _put_fin(client, _s2_inputs(currency="EUR", currency_year=2026))
    body = _run(client)
    assert body["error_code"] == "campus_assets_money_year_mismatch", body
    _no_secret(client, body)


def test_the_ppa_solve_refuses_through_the_real_build_without_the_values(install_network,
                                                                         monkeypatch):
    """`solve_ppa_price` with the real factory and `build()`: the campus refusal is the
    tool's 422 `investment_case_refused`, its code `campus_assets_invalid`."""
    import routers.results as R
    import routers.simulation as SIM
    from services import chat_tools
    from services.results import finance_case as FC

    monkeypatch.setattr(
        R, "_ic_campus_source",
        lambda finance: R._ic_campus_from_response(
            _response([{**_asset(), "quantity": SECRET}]), None, unsaved=False))
    monkeypatch.setattr(FC, "build_finance_case",
                        lambda *a, **k: pytest.fail("refused before the adapter"))
    install_network(build_network(solve=True))
    SIM.put_finance(SIM.FinanceIn(finance=_s2_inputs()), if_match=None)
    try:
        with pytest.raises(HTTPException) as exc:
            chat_tools.DISPATCHERS["solve_ppa_price"](target_irr=0.1, target_year=10)
    finally:
        SIM.put_finance(SIM.FinanceIn(finance=None), if_match=None)
    assert exc.value.status_code == 422
    assert exc.value.detail["error_kind"] == "investment_case_refused"
    assert exc.value.detail["code"] == "campus_assets_invalid"
    assert SECRET not in str(exc.value.detail)


def test_a_campus_refusal_never_precedes_the_runners_finance_inputs_422(
        client, install_network, monkeypatch):
    import routers.results as R

    _capture_factory(monkeypatch)
    monkeypatch.setattr(R, "_ic_campus_source",
                        lambda finance: R.CampusSource.refused("campus_assets_invalid", "test"))
    install_network(build_network(solve=True))
    r = client.post(STUDY_URL, json={})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "finance_inputs_missing"


@pytest.mark.parametrize("assets,fin_over", [
    ([_asset(currency="USD")], {}),                                       # ≠ the case currency
    ([_asset(price_year=2024)], {"currency_year": 2026}),                 # ≠ the stated year
    ([_asset(), _asset("campus:X#1", price_year=2025)], {}),              # assets disagree
    ([_asset(), _asset("campus:X#1", currency="USD")], {}),
    ([_asset(meta=False)], {}),                                           # no meta
    ([{**_asset(), "meta": {"currency": "EUR"}}], {}),                    # no price year
], ids=["currency", "year", "years_disagree", "currencies_disagree", "no_meta", "no_year"])
def test_a_money_year_problem_is_refused(assets, fin_over, client, install_network, session_ctx,
                                         _auth_db, monkeypatch):
    from services import campus_electrical_service as ce

    calls = _fake_adapter(monkeypatch)
    _save(client, install_network, "campus_ic_money")
    with _row(_auth_db, session_ctx, client) as (_db, row):
        fake_investment(row, _purchase())
    monkeypatch.setattr(ce, "owner_assets_response", lambda p: _response(assets))
    _put_fin(client, _s2_inputs(currency="EUR", **fin_over))
    body = _run(client)
    assert body["status"] == "refused", body
    assert body["error_code"] == "campus_assets_money_year_mismatch" and calls == []


def test_a_money_year_refusal_names_both_years_and_what_to_change(
        client, install_network, session_ctx, _auth_db, monkeypatch):
    """Guided smoke 2026-10-16, issue 03 (owner: keep the refusal, say what to do):
    the library's 2026 money against the case's 2020 money, both named, and the way out."""
    from services import campus_electrical_service as ce

    calls = _fake_adapter(monkeypatch)
    _save(client, install_network, "campus_ic_money_text")
    with _row(_auth_db, session_ctx, client) as (_db, row):
        fake_investment(row, _purchase())
    monkeypatch.setattr(ce, "owner_assets_response", lambda p: _response([_asset(price_year=2026)]))
    _put_fin(client, _s2_inputs(currency="EUR", currency_year=2020))
    body = _run(client)
    assert body["error_code"] == "campus_assets_money_year_mismatch" and calls == []
    text = body["error"]
    assert "2026 money (the campus asset library's price_year)" in text
    assert "the case's price data are 2020 (the finance inputs' currency_year)" in text
    assert "no price index converts" in text and "reprice the campus asset library in 2020" in text


def test_an_unstated_currency_year_takes_any_agreeing_price_year(
        client, install_network, session_ctx, _auth_db, monkeypatch):
    from services import campus_electrical_service as ce

    calls = _fake_adapter(monkeypatch)
    _save(client, install_network, "campus_ic_unstated")
    with _row(_auth_db, session_ctx, client) as (_db, row):
        fake_investment(row, _purchase())
    monkeypatch.setattr(ce, "owner_assets_response", lambda p: _response([_asset(price_year=2019)]))
    _put_fin(client, _s2_inputs(currency="EUR", currency_year=None))
    assert _run(client)["status"] == "done"
    assert [e.name for e in calls[0]["extra_assets"]] == ["campus:TR_132_33_40#1"]


def test_a_stale_study_gives_no_extras_and_is_flagged(client, install_network, session_ctx,
                                                       _auth_db, monkeypatch):
    from services import campus_electrical_service as ce

    calls = _fake_adapter(monkeypatch)
    _save(client, install_network, "campus_ic_stale")
    with _row(_auth_db, session_ctx, client) as (_db, row):
        fake_investment(row, _purchase())
    real = ce.owner_assets_response
    monkeypatch.setattr(ce, "owner_assets_response", lambda p: {**real(p), "stale": True})
    _put_fin(client, _s2_inputs(currency="EUR", currency_year=2026))
    body = _run(client)
    assert body["status"] == "done", body
    assert calls[0]["extra_assets"] == ()
    assert "campus_study_stale" in body["flags"]
    full = client.get(REPORT_URL, params={"detail": "full"}).json()
    assert "campus_study_stale" in full["sections"]["project"]["payload"]["flags"]
    # The study re-run (no longer stale): `extra_assets` changed.
    monkeypatch.setattr(ce, "owner_assets_response", real)
    rep = client.get(STUDY_URL).json()["report"]
    assert rep["stale"] is True and rep["changed"] == ["extra_assets"]


def test_a_study_with_no_purchase_is_valid_and_its_hash_is_digested(
        client, install_network, session_ctx, _auth_db, monkeypatch):
    from services import campus_electrical_service as ce

    calls = _fake_adapter(monkeypatch)
    _save(client, install_network, "campus_ic_empty")
    with _row(_auth_db, session_ctx, client) as (_db, row):
        fake_investment(row, [inv_row("transformer OLD", "TR_132_33_40", "transformer", 1,
                                      existing=True)])
        assert ce.owner_assets_response(row)["assets"] == []
        assert ce.owner_assets_response(row)["source_hash"]
    _put_fin(client, _s2_inputs())
    body = _run(client)
    assert body["status"] == "done" and calls[0]["extra_assets"] == ()
    stored = client.get(REPORT_URL, params={"detail": "full"}).json()
    assert stored["sections"]["project"]["payload"]["provenance"]["inputs"]["extra_assets"]
    # Another (still empty) study is another source_hash: stale, `extra_assets` named.
    real = ce.owner_assets_response
    monkeypatch.setattr(ce, "owner_assets_response",
                        lambda p: {**real(p), "source_hash": "fedcba9876543210"})
    rep = client.get(STUDY_URL).json()["report"]
    assert rep["stale"] is True and rep["changed"] == ["extra_assets"]


def test_an_unsaved_network_is_flagged_on_the_run_and_never_in_the_key(
        client, install_network, session_ctx, _auth_db, monkeypatch):
    calls = _fake_adapter(monkeypatch)
    _save(client, install_network, "campus_ic_unsaved")
    with _row(_auth_db, session_ctx, client) as (_db, row):
        fake_investment(row, _purchase())
    _put_fin(client, _s2_inputs(currency="EUR", currency_year=2026))
    ctx = session_ctx(client)
    ctx.results_unsaved = True
    body = _run(client)
    assert body["status"] == "done", body
    assert "campus_study_network_unsaved" in body["flags"]
    assert len(calls[0]["extra_assets"]) == 3
    # A save later (memory = disk) is not "the campus study changed".
    ctx.results_unsaved = False
    assert client.get(STUDY_URL).json()["report"]["stale"] is False
    # An undo step counts as unsaved too.
    from services import undo_service

    monkeypatch.setattr(undo_service, "depth", lambda: 1)
    assert "campus_study_network_unsaved" in _run(client)["flags"]


# ── R-4: staleness ───────────────────────────────────────────────────────────


def test_a_new_source_hash_marks_the_report_stale(client, install_network, session_ctx,
                                                  _auth_db, monkeypatch):
    from services import campus_electrical_service as ce

    _fake_adapter(monkeypatch)
    _save(client, install_network, "campus_ic_rehash")
    with _row(_auth_db, session_ctx, client) as (_db, row):
        fake_investment(row, _purchase())
    _put_fin(client, _s2_inputs(currency="EUR", currency_year=2026))
    assert _run(client)["report"]["stale"] is False
    real = ce.owner_assets_response
    monkeypatch.setattr(ce, "owner_assets_response",
                        lambda p: {**real(p), "source_hash": "fedcba9876543210"})
    rep = client.get(STUDY_URL).json()["report"]
    assert rep["stale"] is True and rep["changed"] == ["extra_assets"]
    monkeypatch.setattr(ce, "owner_assets_response", real)
    assert client.get(STUDY_URL).json()["report"]["stale"] is False


def test_a_source_that_cannot_be_read_on_get_digests_as_an_error_never_a_500(
        client, install_network, monkeypatch):
    import routers.results as R
    from services.finance.investment_case_runner import json_digest

    _capture_factory(monkeypatch)
    install_network(build_network(solve=True))
    _put_fin(client, _s2_inputs())
    assert _run(client)["report"]["stale"] is False

    def boom(finance):
        raise RuntimeError("the database is gone")
    monkeypatch.setattr(R, "_ic_campus_source", boom)
    r = client.get(STUDY_URL)
    assert r.status_code == 200, r.text
    rep = r.json()["report"]
    assert rep["stale"] is True and rep["changed"] == ["extra_assets"]
    got = R._ic_read_campus(None)
    assert got.refusal == "campus_assets_invalid"
    assert got.digest == json_digest({"error": "campus_assets_invalid"})


# ── R-2: the seam ────────────────────────────────────────────────────────────


def test_the_factory_without_campus_behaves_as_before(monkeypatch):
    """`_ic_build_case(n, cfg, owner=, lost_load=)` — no `campus` — passes no extras and adds
    no flag (the chat tools and older callers)."""
    import routers.results as R
    from services.results import finance_case as FC

    seen = {}

    def fake(n, cfg, fin, *, result_df, lost_load=None, owner=None, extra_assets=(),
             baseline=None):
        seen["extra_assets"] = extra_assets
        return S.to_finance_case("s2")
    monkeypatch.setattr(FC, "build_finance_case", fake)
    cfg = type("Cfg", (), {"finance": _s2_inputs()})()
    case = R._ic_build_case(None, cfg, owner="o", lost_load=None)()
    assert tuple(seen["extra_assets"]) == ()
    assert case.flags == S.to_finance_case("s2").flags


def test_the_factory_applies_a_source_extras_flags_and_refusal(monkeypatch):
    import routers.results as R
    from services.finance.case import ExtraOwnerAsset, FinanceRefused
    from services.results import finance_case as FC

    seen = {}

    def fake(n, cfg, fin, *, result_df, lost_load=None, owner=None, extra_assets=(),
             baseline=None):
        seen["extra_assets"] = extra_assets
        return S.to_finance_case("s2")
    monkeypatch.setattr(FC, "build_finance_case", fake)
    cfg = type("Cfg", (), {"finance": _s2_inputs()})()
    names = [f.name for f in dataclasses.fields(ExtraOwnerAsset)]
    extra = ExtraOwnerAsset(**{k: _asset()[k] for k in names})
    src = R.CampusSource(extras=(extra,), flags=("campus_study_stale",), unsaved=True, digest="d")
    case = R._ic_build_case(None, cfg, owner="o", lost_load=None, campus=src)()
    assert seen["extra_assets"] == (extra,)
    assert case.flags[-2:] == ("campus_study_stale", "campus_study_network_unsaved")
    with pytest.raises(FinanceRefused) as exc:
        R._ic_build_case(None, cfg, owner="o", lost_load=None,
                         campus=R.CampusSource.refused("campus_assets_invalid", "x"))()
    assert exc.value.code == "campus_assets_invalid"


def test_the_route_and_solve_ppa_price_hand_the_seam_the_same_source(install_network,
                                                                     monkeypatch):
    """Both called the way the chat calls them (in process, on the active context)."""
    import routers.results as R
    import routers.simulation as SIM
    from services import chat_tools
    from services.finance import engine as E
    from services.finance.case import ExtraOwnerAsset
    from services.results import finance_case as FC

    names = [f.name for f in dataclasses.fields(ExtraOwnerAsset)]
    extra = ExtraOwnerAsset(**{k: _asset()[k] for k in names})
    source = R.CampusSource(extras=(extra,), digest="campus-digest-for-test")
    finances: list = []

    def seam(finance):
        finances.append(finance)
        return source
    monkeypatch.setattr(R, "_ic_campus_source", seam)
    seen: list = []
    real_factory = R._ic_build_case

    def factory(n, cfg, *, owner, lost_load, campus=None):
        seen.append(campus)
        return real_factory(n, cfg, owner=owner, lost_load=lost_load, campus=campus)
    monkeypatch.setattr(R, "_ic_build_case", factory)
    monkeypatch.setattr(R, "_ic_case_hash", lambda case: "case-hash-for-test")
    monkeypatch.setattr(FC, "build_finance_case",
                        lambda n, cfg, fin, *, result_df, lost_load=None, owner=None,
                        extra_assets=(), baseline=None: dataclasses.replace(S.to_finance_case("s1b", solve=True),
                                                             extra_assets=tuple(extra_assets)))

    install_network(build_network(solve=True))
    SIM.put_finance(SIM.FinanceIn(finance=_s2_inputs()), if_match=None)
    try:
        assert chat_tools.DISPATCHERS["run_investment_case"]()["status"] == "running"
        deadline = time.time() + 60
        while R.get_investment_case()["status"] == "running":
            assert time.time() < deadline
            time.sleep(0.05)
        assert R.get_investment_case()["status"] == "done"
        prov = SIM._state["investment_case_report"]["sections"]["project"]["payload"]["provenance"]
        assert prov["inputs"]["extra_assets"] == "campus-digest-for-test"

        # The PPA solve values the same case: the same source, its extras in the solved case.
        solved_cases: list = []

        def spy(case, pack=None, layers=None):
            solved_cases.append(case)
            return {"solved_ppa_price": None, "solve_ppa_status": "solve_ppa_no_root:test"}
        monkeypatch.setattr(E, "solve_ppa", spy)
        with pytest.raises(HTTPException) as exc:
            chat_tools.DISPATCHERS["solve_ppa_price"](target_irr=0.1, target_year=10)
        assert exc.value.detail["error_kind"] == "solve_ppa_no_root", exc.value.detail
        assert seen == [source, source]
        assert [e.name for e in solved_cases[0].extra_assets] == ["campus:TR_132_33_40#1"]
        # The seam is handed the finance inputs the caller already holds (POST, GETs, PPA).
        assert finances and all(f == _s2_inputs() for f in finances)
    finally:
        SIM.put_finance(SIM.FinanceIn(finance=None), if_match=None)


def test_solve_ppa_price_carries_the_cases_campus_flags(install_network, monkeypatch):
    """Gate assessor C1: a PPA price solved without the campus extras (a stale study, the
    engine absent, an unsaved network) says so in its own result."""
    import routers.results as R
    import routers.simulation as SIM
    from services import chat_tools
    from services.finance import engine as E
    from services.results import finance_case as FC

    source = R.CampusSource(flags=("campus_study_stale",), unsaved=True, digest="d")
    monkeypatch.setattr(R, "_ic_campus_source", lambda finance: source)
    monkeypatch.setattr(FC, "build_finance_case",
                        lambda n, cfg, fin, *, result_df, lost_load=None, owner=None,
                        extra_assets=(), baseline=None: S.to_finance_case("s1b", solve=True))
    monkeypatch.setattr(E, "solve_ppa", lambda case, pack=None, layers=None: {
        "solved_ppa_price": 55.0, "solve_ppa_status": "ok", "solved_ppa_price_money_year": 2026,
        "solve_ppa_flags": ["solve_ppa_flag_for_test"]})
    install_network(build_network(solve=True))
    SIM.put_finance(SIM.FinanceIn(finance=_s2_inputs()), if_match=None)
    try:
        out = chat_tools.DISPATCHERS["solve_ppa_price"](target_irr=0.1, target_year=10)
    finally:
        SIM.put_finance(SIM.FinanceIn(finance=None), if_match=None)
    assert out["status"] == "ok"
    assert out["flags"] == ["solve_ppa_flag_for_test", "campus_study_stale",
                            "campus_study_network_unsaved"]
