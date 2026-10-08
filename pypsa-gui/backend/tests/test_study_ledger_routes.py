"""
Ledger routes (guided investment study MVP-1, phase S2).

`GET/PUT /api/projects/{name}/studies/{study_id}/ledger` and
`GET .../ledger.csv` (export only; import is MVP-2). They inherit S1's rules:
the PATH project authorises (404 for a non-member, never 403), writes check
the foreign lock, the auth-mode refusal runs as a router dependency AND inside
the handler body (SB-3). The ledger lives on the study record and the
maturity badge is recomputed from it on every PUT.
"""
from __future__ import annotations

import csv
import io
import json

import pytest
from fastapi import HTTPException

import local_mode
from routers import studies as studies_router
from services.study import library as lib
from tests import test_studies_routes as _s1
from tests.test_studies_routes import BODY, _create, _is_lock_refusal
from tests.test_study_library import USER_TARIFF

# The S1 route tests' fixtures, registered here under their own names.
studies_on = _s1.studies_on
same_org_other_user = _s1.same_org_other_user

KEY_DRIVERS = lib.BESS_KEY_DRIVERS


def _url(name, sid, tail="ledger"):
    return f"/api/projects/{name}/studies/{sid}/{tail}"


def _edits(ledger, keys=KEY_DRIVERS, factor=1.1):
    rows = {r["key"]: r for r in ledger["rows"]}
    # A row with no value is not applicable under this tariff and not
    # editable (gate S4 [S5] made `energy_price_level` one on a single-band
    # tariff), as the library test's `_customise_key_drivers` already skips.
    return [{"key": k, "value": rows[k]["value"] * factor,
             "unit": rows[k]["unit"]} for k in keys if rows[k]["value"] is not None]


# A study on a tariff the user supplied: the only kind that can reach
# feasibility (gate S2, BC-S2-1).
USER_BODY = {**BODY, "intake": {**BODY["intake"], "tariff": {"custom": USER_TARIFF}}}


def _sidecar(project_storage_dir, name, sid):
    return json.loads((project_storage_dir(name) / "studies" / f"{sid}.json")
                      .read_text(encoding="utf-8"))


def test_get_seeds_a_ledger_without_storing_it(client, api_project, studies_on,
                                              project_storage_dir):
    name = api_project("led-get")
    s = _create(client, name)
    r = client.get(_url(name, s["study_id"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stored"] is False
    # U2 WP8 (gate C4): seeded from the pinned defaults pack.
    assert body["ledger"]["ledger_version"] == lib.load_defaults().version
    rows = body["ledger"]["rows"]
    assert {x["key"] for x in rows if x["sensitivity_flag"]} == set(KEY_DRIVERS)
    assert all(x["provenance"] == "library" and x["status"] == "default" for x in rows)
    assert body["maturity"]["class"] == "screening"
    assert _sidecar(project_storage_dir, name, s["study_id"])["ledger"] is None


def test_put_stores_edits_and_recomputes_maturity(client, api_project, studies_on,
                                                  project_storage_dir):
    name = api_project("led-put")
    s = _create(client, name, USER_BODY)
    sid = s["study_id"]
    seeded = client.get(_url(name, sid)).json()["ledger"]

    r = client.put(_url(name, sid), json={"rows": _edits(seeded)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stored"] is True
    # Key rows customised but the load is not uploaded: still screening, and
    # the load is the one reason named.
    assert body["maturity"]["class"] == "screening"
    assert [x for x in body["maturity"]["reasons"] if x.startswith("load:")]
    assert not [x for x in body["maturity"]["reasons"] if not x.startswith("load:")]
    row = {x["key"]: x for x in body["ledger"]["rows"]}["discount_rate"]
    assert (row["provenance"], row["status"]) == ("user", "customised")
    assert row["changed_by"] is not None and row["changed_at"] is not None

    # Upload the load (per-step PATCH), then a PUT recomputes to feasibility.
    assert client.patch(f"/api/projects/{name}/studies/{sid}", json={
        "step": "load", "intake": {"load": {"source": "upload", "file_id": "f1"}},
    }).status_code == 200
    r = client.put(_url(name, sid), json={"rows": []})
    assert r.status_code == 200, r.text
    assert r.json()["maturity"]["class"] == "feasibility", r.json()["maturity"]

    stored = _sidecar(project_storage_dir, name, sid)
    assert stored["ledger"]["rows"] == r.json()["ledger"]["rows"]
    assert stored["ledger_version"] == lib.load_defaults().version
    assert stored["maturity"]["class"] == "feasibility"
    assert client.get(_url(name, sid)).json()["stored"] is True


def test_an_intake_patch_moves_a_stored_ledgers_maturity(client, api_project,
                                                        studies_on):
    """The badge reads the load too: uploading it moves the stored maturity."""
    name = api_project("led-patch")
    s = _create(client, name, USER_BODY)
    sid = s["study_id"]
    seeded = client.get(_url(name, sid)).json()["ledger"]
    assert client.put(_url(name, sid), json={"rows": _edits(seeded)}).status_code == 200
    item = f"/api/projects/{name}/studies/{sid}"
    assert client.get(item).json()["maturity"]["class"] == "screening"
    r = client.patch(item, json={"step": "load",
                                 "intake": {"load": {"source": "upload"}}})
    assert r.status_code == 200, r.text
    assert r.json()["maturity"]["class"] == "feasibility"
    assert client.get(item).json()["maturity"]["reasons"] == []


def test_put_refuses_a_wrong_unit_and_stores_nothing(client, api_project,
                                                    studies_on, project_storage_dir):
    name = api_project("led-unit")
    s = _create(client, name)
    before = _sidecar(project_storage_dir, name, s["study_id"])
    r = client.put(_url(name, s["study_id"]), json={"rows": [
        {"key": "battery_storage_eur_per_kwh", "value": 150.0, "unit": "EUR/MWh"}]})
    assert r.status_code == 422, r.text
    assert "battery_storage_eur_per_kwh" in r.text
    r = client.put(_url(name, s["study_id"]), json={"rows": [
        {"key": "battery_storage_eur_per_kwh", "value": 150.0}]})
    assert r.status_code == 422, r.text
    assert _sidecar(project_storage_dir, name, s["study_id"]) == before


def test_put_reseed_follows_the_intake_and_keeps_user_rows(client, api_project,
                                                          studies_on):
    name = api_project("led-reseed")
    s = _create(client, name)
    sid = s["study_id"]
    seeded = client.get(_url(name, sid)).json()["ledger"]
    client.put(_url(name, sid), json={"rows": _edits(seeded, ["discount_rate"])})
    client.patch(f"/api/projects/{name}/studies/{sid}", json={
        "step": "tariff", "intake": {"tariff": {"tariff_id": "tou_reference_illustrative"}}})
    r = client.put(_url(name, sid), json={"reseed": True})
    assert r.status_code == 200, r.text
    rows = {x["key"]: x for x in r.json()["ledger"]["rows"]}
    assert rows["demand_charge_price"]["value"] is None
    assert rows["demand_charge_price"]["unavailable"] == {"value": "not_applicable"}
    assert rows["discount_rate"]["provenance"] == "user"
    assert rows["discount_rate"]["value"] == pytest.approx(0.077)


def test_ledger_csv_is_an_attachment_that_parses(client, api_project, studies_on):
    name = api_project("led-csv")
    s = _create(client, name)
    r = client.get(_url(name, s["study_id"], "ledger.csv"))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"].startswith("attachment;")
    assert "ledger.csv" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    ledger = client.get(_url(name, s["study_id"])).json()["ledger"]
    assert [x["key"] for x in rows] == [x["key"] for x in ledger["rows"]]


def test_unknown_study_is_404(client, api_project, studies_on):
    name = api_project("led-404")
    sid = "0" * 32
    assert client.get(_url(name, sid)).status_code == 404
    assert client.put(_url(name, sid), json={"rows": []}).status_code == 404
    assert client.get(_url(name, sid, "ledger.csv")).status_code == 404


def test_non_member_gets_404_and_the_sidecar_is_unchanged(
        client, other_org_client, api_project, studies_on, project_storage_dir):
    name = api_project("led-tenant")
    s = _create(client, name)
    sidecar = project_storage_dir(name) / "studies" / f"{s['study_id']}.json"
    before = sidecar.read_bytes()
    for r in (other_org_client.get(_url(name, s["study_id"])),
              other_org_client.put(_url(name, s["study_id"]), json={"rows": []}),
              other_org_client.get(_url(name, s["study_id"], "ledger.csv"))):
        assert r.status_code == 404, (r.request.method, r.request.url, r.text)
    assert sidecar.read_bytes() == before


def test_put_is_refused_under_a_foreign_lock(client, api_project, studies_on,
                                             same_org_other_user):
    name = api_project("led-lock")
    assert client.post(f"/api/projects/{name}/lock").status_code == 200
    s = _create(client, name)
    b = same_org_other_user
    assert _is_lock_refusal(b.put(_url(name, s["study_id"]), json={"rows": []}))
    # Reading is not writing.
    assert b.get(_url(name, s["study_id"])).status_code == 200
    assert b.get(_url(name, s["study_id"], "ledger.csv")).status_code == 200
    assert client.put(_url(name, s["study_id"]), json={"rows": []}).status_code == 200


def test_the_ledger_handlers_refuse_when_called_as_plain_functions(monkeypatch):
    """SB-3, for the S2 handlers."""
    from types import SimpleNamespace

    monkeypatch.setattr(local_mode, "is_local_mode", lambda: False)
    monkeypatch.setenv("PYPSAGUI_DECISION_STUDIES", "1")
    project = SimpleNamespace(uuid="u", name="p", directory=None)
    calls = {
        "get": lambda: studies_router.get_ledger("x", project=project),
        "put": lambda: studies_router.put_ledger(
            "x", studies_router.LedgerPut(), project=project, db=None, user=None),
        "csv": lambda: studies_router.get_ledger_csv("x", project=project),
    }
    for label, call in calls.items():
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 404, label
        assert exc.value.detail["code"] == "decision_studies_unavailable", label


# ── gate S2 binding conditions, through the routes ────────────────────────

def test_an_illustrative_tariff_keeps_the_route_badge_at_screening(client, api_project,
                                                                   studies_on):
    """BC-S2-1: every key driver re-entered and the load uploaded, on the seed tariff."""
    name = api_project("led-illus")
    sid = _create(client, name)["study_id"]
    client.patch(f"/api/projects/{name}/studies/{sid}",
                 json={"step": "load", "intake": {"load": {"source": "upload"}}})
    seeded = client.get(_url(name, sid)).json()["ledger"]
    r = client.put(_url(name, sid), json={"rows": _edits(seeded, factor=1.0)})
    assert r.status_code == 200, r.text
    assert r.json()["maturity"]["class"] == "screening"
    assert r.json()["maturity"]["reasons"] == [
        "tariff: de_industrial_illustrative (illustrative, library)"]


@pytest.mark.parametrize("edit,needles", [
    # BC-S2-2: a row MVP-1 does not use.
    ({"key": "battery_storage_degradation_calendar_pct_per_year", "value": 2.0,
      "unit": "%/year"}, ["battery_storage_degradation_calendar_pct_per_year",
                         "not_used_in_mvp1"]),
    # BC-S2-3: outside the physical domain.
    ({"key": "battery_inverter_efficiency", "value": 96.0, "unit": "per unit"},
     ["battery_inverter_efficiency", "(0, 1]"]),
    # BC-S2-4: another currency year.
    ({"key": "battery_storage_eur_per_kwh", "value": 150.0, "unit": "EUR/kWh",
      "currency_year": 2026}, ["battery_storage_eur_per_kwh", "2026", "2020"]),
])
def test_put_refuses_and_names_the_row_and_the_reason(client, api_project, studies_on,
                                                      project_storage_dir, edit, needles):
    name = api_project("led-refuse")
    sid = _create(client, name)["study_id"]
    before = _sidecar(project_storage_dir, name, sid)
    r = client.put(_url(name, sid), json={"rows": [edit]})
    assert r.status_code == 422, r.text
    for needle in needles:
        assert needle in r.json()["detail"], (needle, r.text)
    assert _sidecar(project_storage_dir, name, sid) == before


def test_put_refuses_a_demand_charge_on_a_tariff_without_one_and_reset_restores(
        client, api_project, studies_on):
    """BC-S2-2 through the route, and the `reset` that clears a flagged row."""
    name = api_project("led-tou")
    sid = _create(client, name)["study_id"]
    item = f"/api/projects/{name}/studies/{sid}"
    seeded = client.get(_url(name, sid)).json()["ledger"]
    assert client.put(_url(name, sid), json={
        "rows": _edits(seeded, ["demand_charge_price"])}).status_code == 200
    client.patch(item, json={"step": "tariff", "intake": {
        "tariff": {"tariff_id": "tou_reference_illustrative"}}})
    r = client.put(_url(name, sid), json={"reseed": True})
    row = {x["key"]: x for x in r.json()["ledger"]["rows"]}["demand_charge_price"]
    assert row["status"] == "needs_attention"
    # The flagged row cannot be typed over: it must be reset first.
    r = client.put(_url(name, sid), json={"rows": [
        {"key": "demand_charge_price", "value": 5000.0, "unit": "EUR/MW/month"}]})
    assert r.status_code == 422, r.text
    assert "reset" in r.json()["detail"]
    r = client.put(_url(name, sid), json={"reset": ["demand_charge_price"]})
    assert r.status_code == 200, r.text
    row = {x["key"]: x for x in r.json()["ledger"]["rows"]}["demand_charge_price"]
    assert (row["value"], row["provenance"], row["status"]) == (None, "library", "default")
    r = client.put(_url(name, sid), json={"rows": [
        {"key": "demand_charge_price", "value": 5000.0, "unit": "EUR/MW/month"}]})
    assert r.status_code == 422, r.text
    assert "demand_charge_price" in r.json()["detail"]
    assert "no demand charge" in r.json()["detail"]


def test_a_study_currency_year_other_than_the_ledgers_is_named(client, api_project,
                                                               studies_on):
    """BC-S2-4: the study's stated year and the ledger's differ; the ledger says so."""
    name = api_project("led-cy")
    sid = _create(client, name)["study_id"]
    assert client.patch(f"/api/projects/{name}/studies/{sid}",
                        json={"settings": {"currency_year": 2026}}).status_code == 200
    notes = client.get(_url(name, sid)).json()["ledger"]["honesty_notes"]
    assert [n for n in notes if "2026" in n and "2020" in n], notes
