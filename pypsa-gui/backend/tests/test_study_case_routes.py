"""
The investment-case routes (plan S5): ``GET .../{study_id}/options/{option_id}
/case`` and ``.../case.xlsx``.

The run is the S4 runner with the fake solver of `tests/test_study_runner.py`
(the queue, the forks and the result reads are real; the dispatch is
written), so the case is built from a real fork's saved network and the
run record's bills and asset economics.
"""
from __future__ import annotations

import inspect
import io

import openpyxl
import pytest

from routers import studies as studies_router
from services.http_filenames import content_disposition
from tests.study_s4_support import create_pack_study, enable_studies, wait_run
from tests.test_study_runner import FakeSolver
from tests.xlsx_formula_eval import FormulaEvaluator


@pytest.fixture
def studies_on(monkeypatch):
    yield from enable_studies(monkeypatch)


@pytest.fixture
def fake(monkeypatch):
    from services import solver_service

    solver = FakeSolver()
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    return solver


def _setup(client, api_project, name):
    api_project(f"{name}-src")
    r = create_pack_study(client, f"{name}-src", name)
    assert r.status_code == 201, r.text
    return r.json()["study_id"]


def _run(client, name, sid):
    r = client.post(f"/api/projects/{name}/studies/{sid}/run", json={})
    assert r.status_code == 202, r.text
    rec = wait_run(client, name, sid)
    assert rec["status"] == "done", rec
    return rec


def test_a_study_never_run_has_no_case(client, api_project, studies_on):
    sid = _setup(client, api_project, "case-unrun")
    for suffix in ("case", "case.xlsx"):
        r = client.get(f"/api/projects/case-unrun/studies/{sid}/options/bess_2h/{suffix}")
        assert r.status_code == 404, r.text
        assert r.json()["detail"]["error_kind"] == "study_never_run"


def test_the_case_and_its_workbook_after_a_run(client, api_project, studies_on, fake):
    sid = _setup(client, api_project, "case-run")
    _run(client, "case-run", sid)
    r = client.get(f"/api/projects/case-run/studies/{sid}/options/bess_2h/case")
    assert r.status_code == 200, r.text
    case = r.json()
    assert case["available"] is True and case["status"] == "ok"
    assert case["option_id"] == "bess_2h" and case["study_id"] == sid
    assert case["currency_year"] == 2020 and case["engine"] == "cash_flow_expander"
    assert case["horizon_years"] == 25 and len(case["years"]) == 26
    assert case["sources"]["asset_economics_ref"] == "run_record"
    assert case["provenance"]["model_hash"]
    # The fake run sized the battery at 0.3 MW: CAPEX is the ledger upfront.
    ledger = client.get(f"/api/projects/case-run/studies/{sid}/ledger").json()["ledger"]
    v = {row["key"]: row["value"] for row in ledger["rows"]}
    upfront = (v["battery_inverter_eur_per_kw"] + 2 * v["battery_storage_eur_per_kwh"]) * 1000.0
    assert case["kpis"]["capex_total"] == pytest.approx(upfront * 0.3, rel=1e-9)

    r = client.get(f"/api/projects/case-run/studies/{sid}/options/bess_2h/case.xlsx")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    study = client.get(f"/api/projects/case-run/studies/{sid}").json()
    assert r.headers["content-disposition"] == content_disposition(
        f"{study['name']} - bess_2h - pro forma.xlsx")
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    kpis = wb["KPIs"]
    row = next(c.row for c in kpis["A"] if c.value == "npv")
    assert FormulaEvaluator(wb).value("KPIs", f"B{row}") == pytest.approx(
        case["kpis"]["npv"], abs=1.0)


def test_an_unknown_option_the_baseline_and_a_non_member_are_refused(
        client, other_org_client, api_project, studies_on, fake):
    sid = _setup(client, api_project, "case-404")
    _run(client, "case-404", sid)
    base = f"/api/projects/case-404/studies/{sid}/options"
    r = client.get(f"{base}/bess_9h/case")
    assert r.status_code == 404 and r.json()["detail"]["error_kind"] == "option_unknown"
    r = client.get(f"{base}/bess_9h/case.xlsx")
    assert r.status_code == 404
    r = client.get(f"{base}/none/case")
    assert r.status_code == 422 and r.json()["detail"]["error_kind"] == "baseline_has_no_case"
    assert client.get(f"/api/projects/case-404/studies/{'0' * 32}/options/bess_2h/case").status_code == 404
    for suffix in ("case", "case.xlsx"):
        assert other_org_client.get(f"{base}/bess_2h/{suffix}").status_code == 404


def test_a_ledger_edited_after_the_run_is_refused_not_mixed(
        client, api_project, studies_on, fake):
    sid = _setup(client, api_project, "case-stale")
    _run(client, "case-stale", sid)
    r = client.put(f"/api/projects/case-stale/studies/{sid}/ledger", json={"rows": [
        {"key": "discount_rate", "value": 0.05, "unit": "per unit"}]})
    assert r.status_code == 200, r.text
    r = client.get(f"/api/projects/case-stale/studies/{sid}/options/bess_2h/case")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error_kind"] == "ledger_changed_since_run"


def test_the_case_handlers_declare_access_and_refuse_first():
    from routers.deps import ProjectAccessDep

    for fn in (studies_router.get_option_case, studies_router.get_option_case_xlsx):
        params = inspect.signature(fn).parameters
        assert params["project"].default is ProjectAccessDep, fn.__name__
        body = inspect.getsource(fn).split('"""')[-1].strip().splitlines()
        assert body[0].strip() == "_refuse_unless_enabled()", fn.__name__


def test_a_case_handler_called_as_a_plain_function_refuses_in_auth_mode(monkeypatch):
    import uuid
    from types import SimpleNamespace

    import local_mode
    from fastapi import HTTPException

    monkeypatch.setattr(local_mode, "is_local_mode", lambda: False)
    monkeypatch.setenv("PYPSAGUI_DECISION_STUDIES", "1")
    project = SimpleNamespace(uuid=str(uuid.uuid4()), name="p", directory=None)
    for fn in (studies_router.get_option_case, studies_router.get_option_case_xlsx):
        with pytest.raises(HTTPException) as exc:
            fn("x", "bess_2h", project=project, db=None, user=None)
        assert exc.value.status_code == 404
        assert exc.value.detail["code"] == "decision_studies_unavailable"


# ── gate S5 BC-S5-1: the case is built from the fork the run solved ──────

def _fork_nc(project_storage_dir, name, option):
    return project_storage_dir(f"{name}-opt-{option}") / "network.nc"


def test_a_fork_edited_after_the_run_is_refused_not_mixed(
        client, api_project, studies_on, fake, project_storage_dir):
    """
    The run record's bills and hashes belong to the network the run solved.
    A fork edited afterwards (the Expert view opens it) must not be priced
    with them, nor attested by the run's model hash.
    """
    import pypsa

    sid = _setup(client, api_project, "case-edit")
    _run(client, "case-edit", sid)
    path = _fork_nc(project_storage_dir, "case-edit", "bess_2h")
    n = pypsa.Network(str(path))
    n.storage_units.loc["battery", "p_nom_opt"] *= 5
    n.export_to_netcdf(str(path))
    for suffix in ("case", "case.xlsx"):
        r = client.get(f"/api/projects/case-edit/studies/{sid}/options/bess_2h/{suffix}")
        assert r.status_code == 409, (suffix, r.status_code, r.text[:300])
        assert r.json()["detail"]["error_kind"] == "fork_changed_since_run"


def test_building_a_case_never_reads_or_touches_a_resident_fork(
        client, api_project, studies_on, fake, project_storage_dir, registry_key_for):
    """A resident fork context is neither read nor mutated (no fill, no revert)."""
    import pypsa

    from services.pypsa_service import PyPSAService

    sid = _setup(client, api_project, "case-res")
    _run(client, "case-res", sid)
    resident = pypsa.Network(str(_fork_nc(project_storage_dir, "case-res", "bess_2h")))
    resident.storage_units.loc["battery", "p_nom_opt"] = 99.0
    before = {attr: getattr(resident, attr).copy(deep=True)
              for attr in ("storage_units", "generators", "links")}
    key = registry_key_for("case-res-opt-bess_2h")
    ctx = PyPSAService.build_context()
    ctx.network = resident
    PyPSAService.mark_study_owned(key)
    PyPSAService.register(key, ctx)
    try:
        r = client.get(f"/api/projects/case-res/studies/{sid}/options/bess_2h/case")
        assert r.status_code == 200, r.text
        ledger = client.get(f"/api/projects/case-res/studies/{sid}/ledger").json()["ledger"]
        v = {row["key"]: row["value"] for row in ledger["rows"]}
        upfront = (v["battery_inverter_eur_per_kw"] + 2 * v["battery_storage_eur_per_kwh"]) * 1000.0
        # The disk fork's 0.3 MW, not the resident's 99 MW.
        assert r.json()["kpis"]["capex_total"] == pytest.approx(upfront * 0.3, rel=1e-9)
        assert PyPSAService.get_context(key) is ctx and ctx.network is resident
        for attr, frame in before.items():
            assert getattr(resident, attr).equals(frame), attr
    finally:
        PyPSAService.drop(key)
        PyPSAService.unmark_study_owned(key)


def test_the_run_records_the_bills_fidelity(client, api_project, studies_on, fake):
    sid = _setup(client, api_project, "case-fid")
    rec = _run(client, "case-fid", sid)
    assert rec["details"]["bess_2h"]["bill"]["fidelity"] == "quick_screen"
    assert rec["details"]["none"]["bill"]["fidelity"] == "quick_screen"


def test_a_run_record_without_the_forks_hash_is_refused_not_trusted(
        client, api_project, studies_on, fake, project_storage_dir):
    """
    Gate S5 carry G11: `run_hashes.fork_matches(None, current)` is False, so
    a run record that never recorded the option fork's hash (an older record,
    a hand-edited one, a write that failed) answers 409, exactly as an edited
    fork does — a missing hash is not a match. Mutation G11 (accept a
    missing hash) left the suite green before this test. The recorded
    hashes of the OTHER options stay, so only this option is refused.
    """
    from services.study import store

    sid = _setup(client, api_project, "case-nohash")
    _run(client, "case-nohash", sid)
    base = project_storage_dir("case-nohash")
    findings = store.load_aux(base, sid, "findings")
    ref = next(o["project_ref"] for o in findings["options"] if o["option_id"] == "bess_2h")
    hashes = findings["hashes"]["option_network_hashes"]
    assert ref in hashes and len(hashes) == 5
    del hashes[ref]
    store.save_aux(base, sid, "findings", findings)
    for suffix in ("case", "case.xlsx"):
        r = client.get(f"/api/projects/case-nohash/studies/{sid}/options/bess_2h/{suffix}")
        assert r.status_code == 409, (suffix, r.status_code, r.text[:300])
        assert r.json()["detail"]["error_kind"] == "fork_changed_since_run"
    other = client.get(f"/api/projects/case-nohash/studies/{sid}/options/bess_1h/case")
    assert other.status_code == 200, other.text
