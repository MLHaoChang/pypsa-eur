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
    # U2 WP8 (gate C6): the engine's bill of the unsolved baseline pack's
    # meter (import = the load, export 0), established.
    assert bill["status"] == "ok" and bill["bill"]["engine"] == "tariff_engine"
    assert bill["bill"]["annual_bill"] > 0 and bill["bill"]["unavailable"] == {}
    assert bill["bill"]["by_component"]["export_credit"] == 0.0
    assert bill["tariff"]["tariff_id"] == "de_industrial_illustrative"
    assert bill["tariff"]["honesty_help"]


def test_the_preview_bill_of_the_site_golden_load_equals_wp0(client, api_project, studies_on):
    """
    U2 WP8 (gate C6, WP1's `rate_meter` fact): the preview's engine bill of
    the grid-only baseline equals the WP0 record of GS's bill on the same load
    and tariff (the golden `none`: import = the load), component by component.
    """
    from tests.golden import site_fixture as SF
    from tests.u2_targets import DE, WP0, close

    name = api_project("s8-prev-wp0")
    body = client.post(f"/api/projects/{name}/studies/preview",
                       json={"intake": SF.site_intake()}).json()
    assert body["bill"]["status"] == "ok", body["bill"]
    bill, want = body["bill"]["bill"], WP0["seed_bills"][DE]["none"]
    assert close(bill["annual_bill"], want["annual_bill"])
    for key, value in want["by_component"].items():
        if key != "unavailable":
            assert close(bill["by_component"][key], value), key


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


# ── gate S8 BC-S8-5: a draft's upload touches no user project ────────────

def test_a_draft_load_touches_no_existing_project_and_lands_in_the_new_base(
        client, api_project, studies_on, _auth_db, project_storage_dir):
    from tests.study_s4_support import all_project_dirs, dir_hash

    _engine, session_local = _auth_db
    src = api_project("s8-draft-src")
    api_project("s8-draft-other")
    before = {k: dir_hash(d) for k, d in all_project_dirs(session_local).items()}
    text = _csv(_kw()).decode()
    intake = {**INTAKE, "load": {"source": "upload", "csv_text": text, "filename": "meter.csv"}}
    r = client.post(f"/api/projects/{src}/studies/preview", json={"intake": intake})
    assert r.status_code == 200 and r.json()["load"]["status"] == "ok", r.text
    assert r.json()["load"]["peak_mw"] == pytest.approx(1.5)
    r = create_pack_study(client, src, "s8-draft-base", intake=intake)
    assert r.status_code == 201, r.text
    after = all_project_dirs(session_local)
    for key, digest in before.items():
        assert dir_hash(after[key]) == digest, f"project {key} changed on disk"
    stored = r.json()["intake"]["load"]
    assert "csv_text" not in stored and stored["upload_id"] and stored["filename"] == "meter.csv"
    base_dir = project_storage_dir("s8-draft-base")
    assert (base_dir / "uploads" / stored["upload_id"]).is_dir()
    n = pypsa.Network(str(base_dir / "network.nc"))
    assert n.loads_t.p_set[P.LOAD_NAME].max() == pytest.approx(1.5)
    # The stored intake runs: the run reads the upload from the base project.
    sid = r.json()["study_id"]
    got = client.get(f"/api/projects/s8-draft-base/studies/{sid}").json()
    assert got["intake"]["load"]["upload_id"] == stored["upload_id"]


def test_a_draft_load_larger_than_an_upload_is_refused(client, api_project, studies_on):
    from services import upload_service

    src = api_project("s8-draft-big")
    intake = {**INTAKE, "load": {"source": "upload", "csv_text": "x" * (upload_service.MAX_FILE_BYTES + 1)}}
    r = client.post(f"/api/projects/{src}/studies/preview", json={"intake": intake})
    assert r.json()["load"]["error_kind"] == "load_upload_invalid"


# ── gate S8 re-verification BC-S8-v2-2: csv_text is a draft-only field ────

def test_a_patch_cannot_store_draft_load_text(client, api_project, studies_on):
    src = api_project("s8-patch-csv")
    r = create_pack_study(client, src, "s8-patch-csv-base", intake=INTAKE)
    assert r.status_code == 201, r.text
    sid = r.json()["study_id"]
    text = _csv(_kw()).decode()
    for body in ({"intake": {"load": {"source": "upload", "csv_text": text}}},
                 {"step": "load", "intake": {"load": {"source": "upload", "csv_text": text}}}):
        r = client.patch(f"/api/projects/s8-patch-csv-base/studies/{sid}", json=body)
        assert r.status_code == 422, r.text
        assert "csv_text" in r.text
    got = client.get(f"/api/projects/s8-patch-csv-base/studies/{sid}").json()
    assert "csv_text" not in got["intake"].get("load", {})
