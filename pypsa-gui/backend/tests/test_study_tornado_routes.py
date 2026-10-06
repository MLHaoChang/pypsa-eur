"""
S6: the tornado worker, its throw-away forks and the findings routes (plan
S6 "Acceptance"; S4 M2; gates S1, S4 and S5 carries), with the fake solver
of `tests/test_study_runner.py` (the queue, the forks, the campaign, the
mesh and the reads are real; only `run_simulation` is replaced).
"""
from __future__ import annotations

import json
import threading

import pytest

from services.pypsa_service import PyPSAService
from services.study import runner as R
from tests.study_s4_support import INTAKE, create_pack_study, enable_studies, wait_run
from tests.test_study_runner import FakeSolver

# Two energy bands AND a demand charge, so both dispatch-sensitive drivers
# (the energy-price level and the demand-charge price) have a bar.
TOU_DC = {
    "tariff_id": "tou_dc_test", "name": "Two bands and a demand charge", "source": "test",
    "source_year": 2026, "currency": "EUR", "currency_year": 2020, "billing_period": "month",
    "energy_bands": [
        {"label": "peak", "price_per_mwh": 160.0,
         "applies": {"weekdays": [0, 1, 2, 3, 4], "hours": list(range(8, 20))}},
        {"label": "off-peak", "price_per_mwh": 90.0, "applies": {}}],
    "demand_charge": {"price_per_mw_per_period": 9000.0},
    "fixed_charge_per_period": 100.0,
    "export": {"price_per_mwh": 30.0},
    "honesty_notes": ["tariff_illustrative"],
}
NO_PV_TOU = {**INTAKE, "tariff": {"custom": TOU_DC}, "pv": {"enabled": False}}
BATTERY_ONLY = ["none", "bess_1h", "bess_2h", "bess_4h"]


@pytest.fixture
def studies_on(monkeypatch):
    yield from enable_studies(monkeypatch)


@pytest.fixture
def fake(monkeypatch):
    from services import solver_service

    solver = FakeSolver()
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    return solver


def _run(client, api_project, name, intake=NO_PV_TOU):
    api_project(f"{name}-src")
    r = create_pack_study(client, f"{name}-src", name, intake=intake)
    assert r.status_code == 201, r.text
    sid = r.json()["study_id"]
    assert client.post(f"/api/projects/{name}/studies/{sid}/run", json={}).status_code == 202
    assert wait_run(client, name, sid)["status"] == "done"
    return sid


def wait_tornado(client, base, sid, timeout=90.0) -> dict:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/projects/{base}/studies/{sid}/findings/tornado")
        if r.status_code == 200 and r.json().get("status") != "running":
            return r.json()
        time.sleep(0.1)
    raise AssertionError("the tornado did not finish")


def _variant_rows(session_local, base):
    from sqlalchemy import select

    from db.models import Project

    with session_local() as db:
        return [p.name for p in db.scalars(select(Project)).all()
                if p.name.startswith(f"{base}-var-")]


def _option_hashes(project_storage_dir, base, options):
    from tests.study_s4_support import dir_hash

    return {o: dir_hash(project_storage_dir(f"{base}-opt-{o}")) for o in options}


# ── four solves on throw-away forks; option forks untouched ──────────────

def test_two_dispatch_drivers_charge_four_solves_on_forks_that_are_gone_afterwards(
        client, api_project, studies_on, fake, _auth_db, project_storage_dir):
    _e, session_local = _auth_db
    sid = _run(client, api_project, "tor-four")
    before = _option_hashes(project_storage_dir, "tor-four", BATTERY_ONLY)
    run_solves = len(fake.calls)
    r = client.post(f"/api/projects/tor-four/studies/{sid}/findings/tornado", json={})
    assert r.status_code == 202, r.text
    assert r.json()["solves_estimated"] == 4
    rec = wait_tornado(client, "tor-four", sid)
    assert rec["status"] == "done", rec
    # Four re-dispatches, each on its own throw-away fork of the base, each
    # with the sizes fixed and its own explicit config.
    tornado_calls = fake.calls[run_solves:]
    assert len(tornado_calls) == 4 and rec["solves_charged"] == 4
    assert all(name.startswith("tor-four-var-") for name, _c, _s in tornado_calls)
    # U2 WP6: each variant config carries the engine's commercial block (C6),
    # never GS's demand charge.
    assert all(cfg.solve_strategy == "full" and cfg.demand_charge is None
               and cfg.commercial["poc_link"] == "grid_import"
               for _n, cfg, _s in tornado_calls)
    assert rec["campaign"]["spent_solves"] == 4 and rec["campaign"]["active"] is False
    # Gone afterwards: no row, no directory; nothing left exempt from the cap.
    assert _variant_rows(session_local, "tor-four") == []
    assert rec["forks_left"] == []
    assert not any(p.name.startswith("tor-four-var-")
                   for p in project_storage_dir("tor-four").parent.iterdir())
    assert PyPSAService._study_owned == set()
    # Option forks are never re-solved or written.
    assert _option_hashes(project_storage_dir, "tor-four", BATTERY_ONLY) == before

    rob = rec["robustness"]
    assert rob["status"] == "ok" and rob["method"] == "redispatch_fixed_sizes"
    keys = [row["key"] for row in rob["tornado"]]
    assert sorted(keys) == sorted(["battery_storage_eur_per_kwh", "battery_inverter_eur_per_kw",
                                   "demand_charge_price", "energy_price_level",
                                   "discount_rate"])
    swings = [row["swing"] for row in rob["tornado"]]
    assert swings == sorted(swings, reverse=True)

    f = client.get(f"/api/projects/tor-four/studies/{sid}/findings")
    assert f.status_code == 200, f.text
    body = f.json()
    assert body["available"] is True
    assert body["robustness"]["tornado"] == rob["tornado"]
    assert body["verdict"]["status"] == "ok"
    assert body["verdict"]["class"] in ("recommended", "marginal")
    assert body["value_streams_status"] == "ok"
    # S8 (gate S6 carry): the waterfall's label is typed, not read from a note.
    assert body["value_streams_basis"] == "baseline"
    assert {s["key"] for s in body["value_streams"]} == {
        "demand_charge_reduction", "energy_shift", "export_credit", "fixed", "taxes_levies"}
    assert body["hashes"]["ledger_hash"] and body["hashes"]["base_network_hash"]
    assert len(body["hashes"]["option_network_hashes"]) == 4
    assert {e["name"] for e in body["explain"]} == {"battery"}
    for note in body["honesty_notes"]:
        assert not any(ch.isdigit() for ch in note), note


def test_the_findings_before_a_tornado_are_not_established_and_name_why(
        client, api_project, studies_on, fake):
    sid = _run(client, api_project, "tor-pre")
    body = client.get(f"/api/projects/tor-pre/studies/{sid}/findings").json()
    assert body["available"] is True
    assert body["robustness"]["status"] == "not_established"
    assert body["robustness"]["note"] == "tornado_not_run"
    assert body["verdict"]["status"] == "not_established"
    assert "tornado_not_run" in body["verdict"]["reasons"]
    atts = {a["option_id"]: a for a in body["battery_attribution"]}
    assert set(atts) == {"bess_1h", "bess_2h", "bess_4h"}
    assert all(a["method"] == "battery_only" and a["status"] == "ok" for a in atts.values())


class PvBestSolver(FakeSolver):
    """The fake, sizing only the battery that sits beside PV: bess_pv is the best."""

    def __call__(self, config, n, *a, **k):
        out = super().__call__(config, n, *a, **k)
        if len(n.storage_units) and "pv" not in n.generators.index:
            n.storage_units["p_nom_opt"] = 0.0
        return out


def test_a_bess_pv_option_is_attributed_only_after_its_reference_solves(
        client, api_project, studies_on, monkeypatch):
    from services import solver_service

    fake = PvBestSolver()
    monkeypatch.setattr(solver_service, "run_simulation", fake)
    intake = {**NO_PV_TOU, "pv": {"enabled": True, "kind": "rooftop"}}
    sid = _run(client, api_project, "tor-pv", intake=intake)
    pre = client.get(f"/api/projects/tor-pv/studies/{sid}/findings").json()
    pv = next(a for a in pre["battery_attribution"] if a["option_id"] == "bess_pv_2h")
    assert pv["status"] == "not_established"
    assert pv["unavailable"]["battery_npv"] == "bess_pv_value_not_attributable_to_battery"
    assert (pre["value_streams_basis"] is None) == (pre["value_streams_status"] != "ok")
    run_solves = len(fake.calls)
    r = client.post(f"/api/projects/tor-pv/studies/{sid}/findings/tornado", json={})
    assert r.status_code == 202, r.text
    # One reference, and two drivers x two bounds x (option + reference).
    assert r.json()["solves_estimated"] == 1 + 2 * 2 * 2
    rec = wait_tornado(client, "tor-pv", sid)
    assert rec["status"] == "done", rec
    ref_call = fake.calls[run_solves]
    assert ref_call[2] == 0, "the reference omits the battery"
    # BC-S6-3: every PV-only reference re-dispatch (the centre `ref-...` and
    # each price bound's `...r`) omits the battery; every option one keeps it.
    storage = {name: s for name, _cfg, s in fake.calls[run_solves:]}
    variants = {v["fork"]: v["variant"] for v in rec["variants"]}
    assert len(variants) == 9
    for fork, variant in variants.items():
        is_ref = variant.startswith("ref-") or variant.endswith("r")
        assert storage[fork] == (0 if is_ref else 1), (variant, storage[fork])
    post = client.get(f"/api/projects/tor-pv/studies/{sid}/findings").json()
    pv = next(a for a in post["battery_attribution"] if a["option_id"] == "bess_pv_2h")
    assert pv["status"] == "ok" and pv["method"] == "battery_removed_same_pv"
    assert pv["battery_npv"] == pytest.approx(pv["option_npv"] - pv["reference_npv"])
    assert rec["solves_charged"] == len(fake.calls) - run_solves == 9
    assert post["verdict"]["option_id"] == "bess_pv_2h"
    # S8: the streams of a bess_pv verdict are the battery's increment.
    assert post["value_streams_status"] == "ok"
    assert post["value_streams_basis"] == "pv_only_reference"


# ── abort mid-tornado ─────────────────────────────────────────────────────

def test_abort_mid_tornado_keeps_the_bars_and_names_the_rest(
        client, api_project, studies_on, monkeypatch, _auth_db, registry_key_for):
    from services import solver_service

    _e, session_local = _auth_db
    holder: dict = {}

    def after(k):
        if holder.get("armed") and k == holder["armed"]:
            ctx = PyPSAService.get_context(registry_key_for("tor-abort"))
            ctx.solver_state["decision_study"]["stop_event"].set()

    solver = FakeSolver(on_call=after)
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    sid = _run(client, api_project, "tor-abort")
    holder.update(sid=sid, armed=len(solver.calls) + 1)
    assert client.post(f"/api/projects/tor-abort/studies/{sid}/findings/tornado",
                       json={}).status_code == 202
    rec = wait_tornado(client, "tor-abort", sid)
    assert rec["status"] == "aborted", rec
    rob = rec["robustness"]
    assert rob["status"] == "not_established" and rob["note"] == "tornado_aborted"
    kept = [row["key"] for row in rob["tornado"]]
    # The cost rows solve nothing and come first; the first re-dispatch was
    # aborted, so its row and every later one are named, not dropped.
    assert sorted(kept) == ["battery_inverter_eur_per_kw", "battery_storage_eur_per_kwh"]
    assert rob["pending"] == ["demand_charge_price", "energy_price_level", "discount_rate"]
    assert _variant_rows(session_local, "tor-abort") == []
    assert PyPSAService._study_owned == set()
    body = client.get(f"/api/projects/tor-abort/studies/{sid}/findings").json()
    assert body["verdict"]["status"] == "not_established"
    assert body["robustness"]["pending"] == rob["pending"]
    r = client.post(f"/api/projects/tor-abort/studies/{sid}/findings/tornado/abort")
    assert r.status_code == 200 and r.json()["aborting"] is False


# ── budget, mesh, in-flight ──────────────────────────────────────────────

def test_a_budget_below_the_worst_case_refuses_before_the_first_solve(
        client, api_project, studies_on, fake, _auth_db):
    _e, session_local = _auth_db
    sid = _run(client, api_project, "tor-budget")
    n = len(fake.calls)
    r = client.post(f"/api/projects/tor-budget/studies/{sid}/findings/tornado",
                    json={"budget_solves": 3})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error_kind"] == "campaign_budget_exhausted"
    assert "4 solve(s)" in detail["message"] and "3 of 3 left" in detail["message"]
    assert len(fake.calls) == n
    assert _variant_rows(session_local, "tor-budget") == []


def test_the_tornado_route_takes_its_own_in_flight_check_on_the_base_context(
        client, api_project, studies_on, fake, registry_key_for):
    sid = _run(client, api_project, "tor-mesh")
    from services import project_registry
    from tests.test_study_runner import _db_user

    db, user = _db_user(client)
    ctx = R.base_context(project_registry.find_project(db, user, "tor-mesh"))
    release = threading.Event()
    t = threading.Thread(target=release.wait, daemon=True)
    t.start()
    ctx.solver_state["mc"] = {"status": "running", "thread": t}
    try:
        r = client.post(f"/api/projects/tor-mesh/studies/{sid}/findings/tornado", json={})
        assert r.status_code == 409, r.text
        assert "a sequential-MC study" in r.json()["detail"]["message"]
    finally:
        release.set()
        t.join(5)
        ctx.solver_state["mc"] = None
    # A solve in flight on the base context refuses too.
    import routers.simulation as sim

    orig = sim._solver_in_flight_ctx
    sim._solver_in_flight_ctx = lambda c: c is ctx
    try:
        r = client.post(f"/api/projects/tor-mesh/studies/{sid}/findings/tornado", json={})
        assert r.status_code == 409 and r.json()["detail"]["error_kind"] == "solver_in_flight"
    finally:
        sim._solver_in_flight_ctx = orig


def test_findings_refuse_while_a_run_replaces_the_forks(
        client, api_project, studies_on, monkeypatch):
    from services import solver_service

    release = threading.Event()
    sid = _run(client, api_project, "tor-live")
    monkeypatch.setattr(solver_service, "run_simulation",
                        FakeSolver(on_call=lambda k: release.wait(30)))
    try:
        assert client.post(f"/api/projects/tor-live/studies/{sid}/run", json={}).status_code == 202
        r = client.get(f"/api/projects/tor-live/studies/{sid}/findings")
        assert r.status_code == 409 and r.json()["detail"]["error_kind"] == "study_running"
        r = client.post(f"/api/projects/tor-live/studies/{sid}/findings/tornado", json={})
        assert r.status_code == 409, r.text
    finally:
        client.post(f"/api/projects/tor-live/studies/{sid}/run/abort")
        release.set()
    wait_run(client, "tor-live", sid)


def test_a_fork_changed_since_the_run_refuses_the_tornado(
        client, api_project, studies_on, fake, project_storage_dir):
    import pypsa

    sid = _run(client, api_project, "tor-edit")
    path = project_storage_dir("tor-edit-opt-bess_2h") / "network.nc"
    n = pypsa.Network(str(path))
    n.storage_units.loc["battery", "p_nom_opt"] = 5.0
    n.export_to_netcdf(str(path))
    r = client.post(f"/api/projects/tor-edit/studies/{sid}/findings/tornado", json={})
    assert r.status_code == 409 and r.json()["detail"]["error_kind"] == "fork_changed_since_run"
    body = client.get(f"/api/projects/tor-edit/studies/{sid}/findings").json()
    assert "fork_changed_since_run" in body["honesty_notes"]
    assert "bess_2h" not in {a["option_id"] for a in body["battery_attribution"]}


def test_a_ledger_edited_after_the_run_refuses_the_findings(
        client, api_project, studies_on, fake):
    sid = _run(client, api_project, "tor-led")
    r = client.put(f"/api/projects/tor-led/studies/{sid}/ledger", json={"rows": [
        {"key": "battery_storage_eur_per_kwh", "value": 150.0, "unit": "EUR/kWh"}]})
    assert r.status_code == 200, r.text
    for method, url in (("get", "findings"), ("post", "findings/tornado")):
        r = getattr(client, method)(f"/api/projects/tor-led/studies/{sid}/{url}",
                                    **({"json": {}} if method == "post" else {}))
        assert r.status_code == 409, (url, r.text)
        assert r.json()["detail"]["error_kind"] == "ledger_changed_since_run"


# ── the resident cap (gate S4 carry) ─────────────────────────────────────

def test_a_tornado_at_the_resident_cap_changes_no_user_project(
        client, api_project, studies_on, fake, _auth_db, registry_key_for,
        project_storage_dir):
    import pypsa

    from tests.study_s4_support import all_project_dirs, dir_hash

    _e, session_local = _auth_db
    sid = _run(client, api_project, "tcap-base")
    for i in range(5):
        api_project(f"tcap-user{i}")
    edited = PyPSAService.get_context(registry_key_for("tcap-user1"))
    assert edited is not None, "the fixture needs tcap-user1 resident"
    edited.network.add("Bus", "unsaved_edit_bus")
    from sqlalchemy import select

    from db.models import Project

    with session_local() as db:
        names = {str(p.id): p.name for p in db.scalars(select(Project)).all()}
    before = {k: dir_hash(d) for k, d in all_project_dirs(session_local).items()
              if names[k].startswith("tcap-user")}
    resident_before = {k for k in PyPSAService._contexts}
    user_ctx = sum(1 for k in PyPSAService._contexts if k not in PyPSAService._study_owned)
    assert user_ctx >= PyPSAService.RESIDENT_CAP

    assert client.post(f"/api/projects/tcap-base/studies/{sid}/findings/tornado",
                       json={}).status_code == 202
    assert wait_tornado(client, "tcap-base", sid)["status"] == "done"

    after = all_project_dirs(session_local)
    changed = [names[k] for k, d in before.items() if dir_hash(after[k]) != d]
    assert changed == [], f"user projects rewritten by a tornado: {changed}"
    assert {k for k in resident_before if names.get(k.split(":")[-1], "").startswith(
        "tcap-user")} <= set(PyPSAService._contexts), "a user context was evicted"
    still = PyPSAService.get_context(registry_key_for("tcap-user1"))
    assert still is edited and "unsaved_edit_bus" in still.network.buses.index
    on_disk = pypsa.Network(str(project_storage_dir("tcap-user1") / "network.nc"))
    assert "unsaved_edit_bus" not in on_disk.buses.index
    assert PyPSAService._study_owned == set(), sorted(PyPSAService._study_owned)
    assert _variant_rows(session_local, "tcap-base") == []
    assert sum(1 for k in PyPSAService._contexts
               if k not in PyPSAService._study_owned) <= PyPSAService.RESIDENT_CAP


# ── routes: 404s, the refusal, the inventory ─────────────────────────────

def test_findings_routes_404(client, other_org_client, api_project, studies_on, fake):
    api_project("tor-404-src")
    sid = create_pack_study(client, "tor-404-src", "tor-404", intake=NO_PV_TOU).json()["study_id"]
    r = client.get(f"/api/projects/tor-404/studies/{sid}/findings")
    assert r.status_code == 404 and r.json()["detail"]["error_kind"] == "study_never_run"
    r = client.get(f"/api/projects/tor-404/studies/{sid}/findings/tornado")
    assert r.status_code == 404 and r.json()["detail"]["error_kind"] == "tornado_never_run"
    assert client.post(f"/api/projects/tor-404/studies/{sid}/findings/tornado/abort").status_code == 404
    for method, url in (("get", "findings"), ("post", "findings/tornado"),
                        ("get", "findings/tornado"), ("post", "findings/tornado/abort")):
        r = getattr(other_org_client, method)(f"/api/projects/tor-404/studies/{sid}/{url}",
                                              **({"json": {}} if url == "findings/tornado"
                                                 and method == "post" else {}))
        assert r.status_code == 404, (url, r.status_code)
    assert fake.calls == []


def test_every_findings_handler_declares_access_and_refuses_when_disabled():
    import inspect

    from routers import studies as S
    from routers.deps import ProjectAccessDep

    handlers = (S.get_findings, S.start_findings_tornado, S.get_findings_tornado,
                S.abort_findings_tornado)
    for fn in handlers:
        params = inspect.signature(fn).parameters
        assert params["project"].default is ProjectAccessDep, fn.__name__
        assert "_refuse_unless_enabled()" in inspect.getsource(fn), fn.__name__
    for fn in (S.start_findings_tornado, S.abort_findings_tornado):
        assert "_enforce_project_lock(" in inspect.getsource(fn), fn.__name__


def test_the_tornado_record_does_not_answer_for_the_run(
        client, api_project, studies_on, monkeypatch):
    """The run's status and abort routes ignore a running tornado (its own kind)."""
    from services import solver_service

    release = threading.Event()
    solver = FakeSolver()
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    sid = _run(client, api_project, "tor-kind")
    solver.on_call = lambda k: release.wait(30)
    try:
        assert client.post(f"/api/projects/tor-kind/studies/{sid}/findings/tornado",
                           json={}).status_code == 202
        run = client.get(f"/api/projects/tor-kind/studies/{sid}/run").json()
        assert run["status"] == "done" and run.get("kind", "run") == "run"
        r = client.post(f"/api/projects/tor-kind/studies/{sid}/run/abort")
        assert r.status_code == 200 and r.json()["aborting"] is False
        tor = client.get(f"/api/projects/tor-kind/studies/{sid}/findings/tornado").json()
        assert tor["status"] == "running" and tor["kind"] == "tornado"
        # A second run is refused while the tornado holds the base project.
        r = client.post(f"/api/projects/tor-kind/studies/{sid}/run", json={})
        assert r.status_code == 409 and "a decision study" in r.json()["detail"]["message"]
    finally:
        client.post(f"/api/projects/tor-kind/studies/{sid}/findings/tornado/abort")
        release.set()
    assert wait_tornado(client, "tor-kind", sid)["status"] == "aborted"
    json.dumps(tor)


# ── gate S6 binding conditions ───────────────────────────────────────────

class ZeroSolver(FakeSolver):
    """The fake, sizing every battery to zero."""

    def __call__(self, config, n, *a, **k):
        out = super().__call__(config, n, *a, **k)
        if len(n.storage_units):
            n.storage_units["p_nom_opt"] = 0.0
        return out


def test_an_incomplete_run_is_never_not_recommended(
        client, api_project, studies_on, monkeypatch, registry_key_for):
    """
    BC-S6-1: the run is aborted after `none` and a zero-size `bess_1h`;
    `bess_2h` and `bess_4h` were never judged, so nothing may say "not
    recommended" (either could be the one worth building).
    """
    from services import solver_service

    def after(k):
        if k == 2:
            ctx = PyPSAService.get_context(registry_key_for("inc-run"))
            ctx.solver_state["decision_study"]["stop_event"].set()

    monkeypatch.setattr(solver_service, "run_simulation", ZeroSolver(on_call=after))
    api_project("inc-run-src")
    sid = create_pack_study(client, "inc-run-src", "inc-run", intake=NO_PV_TOU).json()["study_id"]
    assert client.post(f"/api/projects/inc-run/studies/{sid}/run", json={}).status_code == 202
    assert wait_run(client, "inc-run", sid)["status"] == "aborted"
    body = client.get(f"/api/projects/inc-run/studies/{sid}/findings").json()
    assert body["available"] is False
    assert body["pending_options"] == ["bess_2h", "bess_4h"]
    v = body["verdict"]
    assert v["status"] == "not_established" and v["class"] is None, v
    assert "options_not_all_judged" in v["reasons"]
    assert body["completeness"]["battery_attribution"] == "not_established"


def test_a_tariff_without_a_currency_year_takes_the_cases_year(
        client, api_project, studies_on, fake):
    """BC-S6-2: the money figures carry the year the cases were built on (200, never 500)."""
    tou = {k: v for k, v in TOU_DC.items() if k != "currency_year"}
    sid = _run(client, api_project, "cy-none", intake={**NO_PV_TOU, "tariff": {"custom": tou}})
    assert client.post(f"/api/projects/cy-none/studies/{sid}/findings/tornado",
                       json={}).status_code == 202
    assert wait_tornado(client, "cy-none", sid)["status"] == "done"
    r = client.get(f"/api/projects/cy-none/studies/{sid}/findings")
    assert r.status_code == 200, r.text[:300]
    v = r.json()["verdict"]
    assert v["status"] == "ok"
    npv = next(f for f in v["headline_kpis"] if f["key"] == "battery_npv")
    assert npv["currency_year"] == 2020 and npv["basis"] is not None


def test_a_tornado_on_an_earlier_run_is_stale(client, api_project, studies_on, fake):
    """BC-S6-4: a re-run makes new forks; the old tornado no longer describes them."""
    sid = _run(client, api_project, "tor-stale")
    assert client.post(f"/api/projects/tor-stale/studies/{sid}/findings/tornado",
                       json={}).status_code == 202
    assert wait_tornado(client, "tor-stale", sid)["status"] == "done"
    assert client.get(f"/api/projects/tor-stale/studies/{sid}/findings").json()[
        "robustness"]["status"] == "ok"
    r = client.patch(f"/api/projects/tor-stale/studies/{sid}", json={
        "intake": {"site": {**NO_PV_TOU["site"], "connection_mw": 3.0}}})
    assert r.status_code == 200, r.text
    assert client.post(f"/api/projects/tor-stale/studies/{sid}/run", json={}).status_code == 202
    assert wait_run(client, "tor-stale", sid)["status"] == "done"
    body = client.get(f"/api/projects/tor-stale/studies/{sid}/findings").json()
    assert body["robustness"]["status"] == "not_established"
    assert body["robustness"]["note"] == "tornado_stale"
    assert "tornado_stale" in body["honesty_notes"]
    assert body["verdict"]["status"] == "not_established"
    assert "tornado_stale" in body["verdict"]["reasons"]


def test_a_case_can_be_read_while_a_tornado_runs(client, api_project, studies_on, monkeypatch):
    """BC-S6-5: the tornado never touches the option forks; a delete still waits."""
    from services import solver_service

    release = threading.Event()
    solver = FakeSolver()
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    sid = _run(client, api_project, "tor-case")
    solver.on_call = lambda k: release.wait(30)
    try:
        assert client.post(f"/api/projects/tor-case/studies/{sid}/findings/tornado",
                           json={}).status_code == 202
        for url in ("case", "case.xlsx"):
            r = client.get(f"/api/projects/tor-case/studies/{sid}/options/bess_2h/{url}")
            assert r.status_code == 200, (url, r.text[:200])
        assert client.delete(f"/api/projects/tor-case/studies/{sid}").status_code == 409
    finally:
        client.post(f"/api/projects/tor-case/studies/{sid}/findings/tornado/abort")
        release.set()
    assert wait_tornado(client, "tor-case", sid)["status"] == "aborted"


def test_a_tornado_reports_done_only_after_it_has_released_everything(
        client, api_project, studies_on, fake, monkeypatch, registry_key_for):
    """The tornado's terminal status is published after its release (see the runner's twin)."""
    import time

    sid = _run(client, api_project, "tor-late")
    slow = PyPSAService._session_active_keys.__func__

    def probe(cls):
        time.sleep(1.0)
        return slow(cls)

    monkeypatch.setattr(PyPSAService, "_session_active_keys", classmethod(probe))
    assert client.post(f"/api/projects/tor-late/studies/{sid}/findings/tornado",
                       json={}).status_code == 202
    assert wait_tornado(client, "tor-late", sid)["status"] == "done"
    assert PyPSAService._study_owned == set(), sorted(PyPSAService._study_owned)
    assert registry_key_for("tor-late") not in PyPSAService._contexts
