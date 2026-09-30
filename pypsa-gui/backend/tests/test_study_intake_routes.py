"""
S8: what the guided flow's intake needs from the backend (plan S8; gate S2
[N7]/[S8] carries, gate S4 [S6] carry).

* ``GET .../studies/library`` — the seed tariffs WITH their honesty notes and
  the sentence behind each code (`Tariff.honesty_help`; gate S2: "show it
  wherever the tariff's name appears"), the sector load profiles, and the
  library's currency year, so the intake never hard-codes them.
* ``POST .../studies/preview`` — the intake's load read exactly as the pack
  will read it (an upload through `packs.parse_load_upload`: timestamps,
  unit, time-series QA) and the baseline bill on that load (grid only: the
  import IS the load), from the bill calculator; nothing is written.
* An uploaded load reaches the study's own base project (`_copy_upload`) and
  its pack by `upload_id`.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from services.study import packs as P
from tests.study_s4_support import INTAKE, create_pack_study, enable_studies


@pytest.fixture
def studies_on(monkeypatch):
    yield from enable_studies(monkeypatch)


def _csv(values, header="timestamp,load (kW)", year=2025) -> bytes:
    idx = pd.date_range(f"{year}-01-01", periods=len(values), freq="h")
    return (header + "\n" + "\n".join(
        f"{t:%Y-%m-%d %H:%M},{v}" for t, v in zip(idx, values)) + "\n").encode()


def _kw():
    return [1000.0 + 500.0 * ((i % 24) >= 8 and (i % 24) < 18) for i in range(8760)]


def _upload(client, project, blob, filename="meter.csv") -> str:
    r = client.post(f"/api/projects/{project}/uploads",
                    files={"file": (filename, blob, "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()["file_id"]


# ── the library ──────────────────────────────────────────────────────────

def test_the_library_lists_tariffs_with_their_honesty_sentences_and_the_profiles(
        client, api_project, studies_on):
    name = api_project("s8-lib")
    r = client.get(f"/api/projects/{name}/studies/library")
    assert r.status_code == 200, r.text
    body = r.json()
    tariffs = {t["tariff_id"]: t for t in body["tariffs"]}
    assert body["default_tariff_id"] in tariffs
    de = tariffs["de_industrial_illustrative"]
    assert "tariff_demand_charge_monthly_peak_not_annual" in de["honesty_notes"]
    assert set(de["honesty_help"]) == set(de["honesty_notes"])
    assert "ANNUAL peak" in de["honesty_help"]["tariff_demand_charge_monthly_peak_not_annual"]
    profiles = {p["profile_id"]: p for p in body["load_profiles"]}
    assert {"commercial_office", "industrial_two_shift"} <= set(profiles)
    assert profiles["commercial_office"]["label"] and profiles["commercial_office"]["synthetic"] is True
    assert body["currency_year"] == 2020
    assert body["load_units"] == ["kW", "MW"]


def test_the_library_route_is_off_without_the_flag(client, api_project):
    name = api_project("s8-lib-off")
    r = client.get(f"/api/projects/{name}/studies/library")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] in ("decision_studies_unavailable",
                                          "decision_studies_disabled")


# ── the preview: the load as the pack reads it, and the bill today ───────

def test_the_preview_reads_an_upload_as_the_pack_will_and_prices_the_bill_today(
        client, api_project, studies_on):
    name = api_project("s8-prev")
    fid = _upload(client, name, _csv(_kw()))
    intake = {**INTAKE, "load": {"source": "upload", "upload_id": fid}}
    r = client.post(f"/api/projects/{name}/studies/preview", json={"intake": intake})
    assert r.status_code == 200, r.text
    body = r.json()
    load = body["load"]
    assert load["status"] == "ok" and load["unit"] == "kW" and load["hours"] == 8760
    assert load["peak_mw"] == pytest.approx(1.5)
    assert load["annual_mwh"] == pytest.approx(sum(_kw()) / 1000.0)
    assert "load_upload_converted_from_kw" in load["notes"]
    assert load["peak_exceeds_connection"] is False
    bill = body["bill"]
    assert bill["status"] == "ok" and bill["bill"]["engine"] == "bill_calculator"
    assert bill["bill"]["annual_bill"] > 0
    assert bill["tariff"]["tariff_id"] == "de_industrial_illustrative"
    assert bill["tariff"]["honesty_help"]


def test_the_preview_names_why_a_load_cannot_be_read(client, api_project, studies_on):
    name = api_project("s8-prev-bad")
    fid = _upload(client, name, _csv(_kw(), header="timestamp,load"))
    intake = {**INTAKE, "load": {"source": "upload", "upload_id": fid}}
    body = client.post(f"/api/projects/{name}/studies/preview",
                       json={"intake": intake}).json()
    assert body["load"]["status"] == "not_established"
    assert body["load"]["error_kind"] == "load_upload_unit_unknown"
    assert body["bill"]["status"] == "not_established"
    assert body["bill"]["error_kind"] == "load_not_established"


def test_the_preview_flags_a_peak_above_the_connection(client, api_project, studies_on):
    name = api_project("s8-prev-peak")
    fid = _upload(client, name, _csv(_kw(), header="timestamp,load"))
    intake = {**INTAKE, "load": {"source": "upload", "upload_id": fid, "unit": "MW"}}
    body = client.post(f"/api/projects/{name}/studies/preview",
                       json={"intake": intake}).json()
    assert body["load"]["status"] == "ok"
    assert body["load"]["peak_exceeds_connection"] is True


def test_the_preview_of_a_sector_profile_is_marked_synthetic(client, api_project, studies_on):
    name = api_project("s8-prev-prof")
    body = client.post(f"/api/projects/{name}/studies/preview",
                       json={"intake": INTAKE}).json()
    assert body["load"]["status"] == "ok"
    assert body["load"]["annual_mwh"] == pytest.approx(4000.0)
    assert any(n.startswith("synthetic_load_profile") for n in body["load"]["notes"])


# ── the upload reaches the study's own base project and its pack ────────

def test_an_uploaded_load_is_copied_into_the_base_and_read_by_upload_id(
        client, api_project, studies_on, project_storage_dir):
    src = api_project("s8-up-src")
    fid = _upload(client, src, _csv(_kw()))
    intake = {**INTAKE, "load": {"source": "upload", "upload_id": fid}}
    r = create_pack_study(client, src, "s8-up-base", intake=intake)
    assert r.status_code == 201, r.text
    base_dir = project_storage_dir("s8-up-base")
    assert (base_dir / "uploads" / fid).is_dir()          # `_copy_upload`
    n = pypsa.Network(str(base_dir / "network.nc"))
    assert n.loads_t.p_set[P.LOAD_NAME].max() == pytest.approx(1.5)
    assert "load_upload_converted_from_kw" in n.meta[P.PACK_META_KEY]["honesty_notes"]
    led = client.get(f"/api/projects/s8-up-base/studies/{r.json()['study_id']}/ledger").json()
    assert not any("synthetic" in reason for reason in led["maturity"]["reasons"])


def test_an_unreadable_upload_refuses_the_study_before_a_project_exists(
        client, api_project, studies_on, project_row):
    src = api_project("s8-up-bad")
    fid = _upload(client, src, _csv(_kw()[:100]))
    intake = {**INTAKE, "load": {"source": "upload", "upload_id": fid}}
    r = create_pack_study(client, src, "s8-up-bad-base", intake=intake)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == "load_upload_timestamps_invalid"
    assert project_row("s8-up-bad-base") is None
