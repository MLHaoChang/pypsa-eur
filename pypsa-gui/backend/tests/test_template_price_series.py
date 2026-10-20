"""
Price series a project template ships, minted into the org's Library at create (IPP renewables +
BESS ticket 02, spec §10 G3; gatekeeper: content-only hash, refusal before the project exists).

`project_templates/<id>/price_series/meta.json` names each CSV and the commercial fields it
fills. `POST /from_template/<id>` reads and checks them, mints them with `put_series`
(idempotent on content), then writes the refs into the new project's `solver_config.json`, which
binds as ever. A bad series refuses the create with a typed 422 and leaves no project.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from services.library import template_series as TS


def _write(tmp_path, entries, files):
    d = tmp_path / TS.DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / TS.META).write_text(json.dumps({"series": entries}))
    for name, s in files.items():
        pd.DataFrame({"timestamp": s.index.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "value": s.to_numpy()}).to_csv(d / name, index=False)
    return tmp_path


def _da(n=48):
    idx = pd.date_range("2030-01-01", periods=n, freq="h")
    return pd.Series(40.0 + 30.0 * np.sin(np.arange(n) / 24 * 2 * np.pi), index=idx)


ENTRY = {"name": "tpl_da", "file": "da.csv", "source": "illustrative (assumed)",
         "label": "Illustrative DE-LU day-ahead shape", "vintage_year": 2030,
         "notes": ["currency:EUR", "price_basis:real", "currency_year:2030", "region:DE-LU"],
         "targets": ["commercial.export_price_ref", "contract:ppa1.reference_price"]}


# ── reading and checking ───────────────────────────────────────────────────


def test_a_template_without_series_ships_none(tmp_path):
    assert TS.read(tmp_path) == []


def test_reading_labels_the_series_illustrative_and_utc(tmp_path):
    (ts,) = TS.read(_write(tmp_path, [ENTRY], {"da.csv": _da()}))
    assert ts.name == "tpl_da" and len(ts.series) == 48
    assert ts.series.index[0] == pd.Timestamp("2030-01-01 00:00")
    assert {"illustrative", "source_time_basis:UTC", "currency:EUR", "region:DE-LU"} <= \
        set(ts.meta["notes"])
    assert ts.meta["unit"] == "EUR/MWh"


@pytest.mark.parametrize("bad, code", [
    ({"targets": ["commercial.import_tariff"]}, "template_series_target_unknown"),
    ({"file": "../x.csv"}, "template_series_meta_invalid"),
    ({"file": "missing.csv"}, "template_series_file_invalid"),
])
def test_a_bad_entry_is_refused_by_code(tmp_path, bad, code):
    with pytest.raises(TS.TemplateSeriesError) as exc:
        TS.read(_write(tmp_path, [{**ENTRY, **bad}], {"da.csv": _da()}))
    assert exc.value.code == code


def test_refs_land_at_their_targets_and_a_missing_contract_is_refused(tmp_path):
    from models.commercial import PriceSeriesRef

    series = TS.read(_write(tmp_path, [ENTRY], {"da.csv": _da()}))
    ref = PriceSeriesRef(id="tpl_da", version=1, hash="a" * 64, source="illustrative")
    cfg = {"commercial": {"poc_link": "grid_import",
                          "contracts": [{"type": "ppa", "id": "ppa1"}]}}
    out = TS.apply_refs(cfg, series, {"tpl_da": ref})
    assert out["commercial"]["export_price_ref"]["id"] == "tpl_da"
    assert out["commercial"]["contracts"][0]["reference_price"]["version"] == 1
    assert "export_price_ref" not in cfg["commercial"]                 # a new dict
    with pytest.raises(TS.TemplateSeriesError) as exc:
        TS.apply_refs({"commercial": {"contracts": []}}, series, {"tpl_da": ref})
    assert exc.value.code == "template_series_target_missing"


# ── minting ────────────────────────────────────────────────────────────────


@pytest.fixture()
def db(_auth_db):
    _engine, session_local = _auth_db
    with session_local() as s:
        yield s
        s.rollback()


def test_minting_is_idempotent_on_content(db, seeded_identity, tmp_path):
    org = seeded_identity["org_id"]
    series = TS.read(_write(tmp_path / "tpl", [ENTRY], {"da.csv": _da()}))
    a = TS.mint(db, org, series, root=tmp_path / "lib")["tpl_da"]
    b = TS.mint(db, org, series, root=tmp_path / "lib")["tpl_da"]
    assert (a.version, a.hash) == (b.version, b.hash) == (1, a.hash)


# ── the route ──────────────────────────────────────────────────────────────

TID = "eh_datacenter_staged"


def _template(tmp_path, entries):
    import project_templates.eh_templates as T

    src = tmp_path / TID
    src.mkdir()
    n = T.BUILDERS[TID]()
    n.export_to_netcdf(str(src / "network.nc"))
    T.write_sidecars(src, TID)
    ts = pd.DatetimeIndex(n.snapshots.get_level_values(-1)).unique()
    cfe = pd.Series(0.4 + 0.2 * np.cos(np.arange(len(ts)) / 24 * 2 * np.pi), index=ts)
    _write(src, entries, {"cfe.csv": cfe})
    return src


CFE = {"name": "tpl_cfe", "file": "cfe.csv", "source": "illustrative (assumed)",
       "unit": "share", "targets": ["commercial.grid_cfe_share_ref"]}


def test_from_template_mints_the_series_and_binds_its_ref(
        client, tmp_path, monkeypatch, tmp_projects_dir):
    from routers import projects as projects_router

    _template(tmp_path, [CFE])
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)
    r = client.post(f"/api/projects/from_template/{TID}", params={"name": "tpl_a"})
    assert r.status_code == 200, r.text
    assert r.json().get("solver_config_issues", []) == []
    ref = client.get("/api/simulation/solver_config").json()["commercial"]["grid_cfe_share_ref"]
    assert ref["id"] == "tpl_cfe" and ref["version"] == 1
    # A second project from the same template reuses the same version.
    r2 = client.post(f"/api/projects/from_template/{TID}", params={"name": "tpl_b"})
    assert r2.status_code == 200, r2.text
    again = client.get("/api/simulation/solver_config").json()["commercial"]["grid_cfe_share_ref"]
    assert (again["version"], again["hash"]) == (ref["version"], ref["hash"])


def test_a_bad_template_series_refuses_the_create_and_leaves_no_project(
        client, tmp_path, monkeypatch, tmp_projects_dir):
    from routers import projects as projects_router

    _template(tmp_path, [{**CFE, "targets": ["contract:nope.reference_price"]}])
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)
    before = {row["name"] for row in client.get("/api/projects/").json()}
    r = client.post(f"/api/projects/from_template/{TID}", params={"name": "tpl_bad"})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == "template_series_target_missing"
    assert {row["name"] for row in client.get("/api/projects/").json()} == before
