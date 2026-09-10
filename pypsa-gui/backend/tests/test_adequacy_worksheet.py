"""
The FMEA worksheet sidecar (Phase 3 Task 1).

Design: spec §§4.2, 8.3; plan 2026-08-28-fmea-phase3-worksheet.md. Manual
worksheet state — class-D expert rows and mitigability overlays keyed by
mode_id — persists as a schema-versioned per-project JSON file
(adequacy_worksheet.json, atomic_io, no pickle). Computed rows are NEVER
in the sidecar: they regenerate from /results/copt, so overlays surviving
"a re-solve" is simply overlays surviving unchanged on disk while the
computed side changes underneath them.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from routers.deps import AuthorizedProject
from services.adequacy import worksheet as W


def _proj(tmp_path: pathlib.Path) -> AuthorizedProject:
    return AuthorizedProject(name="Demo", directory=tmp_path,
                             uuid="u-1", org_id="o-1", registry_key="o-1/u-1")


def _expert_row(name="ops_error", occ=0.5, sev=2.0e5) -> dict:
    return {
        "mode_id": f"manual:{name}",
        "component_class": "Network",
        "name": name,
        "failure_class": "D",
        "occurrence_per_year": occ,
        "occurrence_basis": "expert",
        "severity_eur": sev,
        "criticality_eur_per_year": occ * sev,
        "in_metric_scope": False,
        "mitigability": "operator drill + checklist",
        "engine": "expert",
        "fidelity": "expert_judgement",
    }


def test_missing_sidecar_is_empty_state_not_an_error(tmp_path):
    state = W.load_worksheet(tmp_path)
    assert state == {"__schema__": 1, "version": 0,
                     "manual_rows": [], "overlays": {}}


def test_round_trip_and_version_monotonicity(tmp_path):
    s1 = W.save_worksheet(tmp_path, manual_rows=[_expert_row()],
                          overlays={"generator:g1:forced_outage":
                                    {"mitigability": "N-1 reserve covers it"}})
    assert s1["version"] == 1
    s2 = W.save_worksheet(tmp_path, manual_rows=[], overlays=s1["overlays"])
    assert s2["version"] == 2
    loaded = W.load_worksheet(tmp_path)
    assert loaded["manual_rows"] == []
    assert loaded["overlays"]["generator:g1:forced_outage"]["mitigability"] \
        == "N-1 reserve covers it"


def test_overlays_survive_regeneration_by_construction(tmp_path):
    """The sidecar stores no computed rows, so a re-solve cannot wipe an
    overlay — the file is untouched by solving. Pin the file contents."""
    W.save_worksheet(tmp_path, manual_rows=[],
                     overlays={"m1": {"mitigability": "spares on site"}})
    raw = json.loads((tmp_path / "adequacy_worksheet.json").read_text())
    assert "computed" not in raw and "per_mode" not in raw
    assert raw["overlays"]["m1"]["mitigability"] == "spares on site"


def test_manual_rows_validate_against_the_contract(tmp_path):
    bad = _expert_row()
    bad["engine"] = "copt"          # expert rows must be labelled expert
    with pytest.raises(W.WorksheetValidationError):
        W.save_worksheet(tmp_path, manual_rows=[bad], overlays={})
    bad2 = _expert_row()
    bad2["severity_eur"] = -5.0     # contract: never negative
    with pytest.raises(W.WorksheetValidationError):
        W.save_worksheet(tmp_path, manual_rows=[bad2], overlays={})


def test_size_caps_reject_rather_than_truncate(tmp_path):
    with pytest.raises(W.WorksheetValidationError):
        W.save_worksheet(tmp_path, manual_rows=[],
                         overlays={"m1": {"mitigability": "x" * 2001}})
    with pytest.raises(W.WorksheetValidationError):
        W.save_worksheet(tmp_path,
                         manual_rows=[_expert_row(name=f"r{i}")
                                      for i in range(201)],
                         overlays={})


def test_atomic_write_leaves_no_partial_file(tmp_path):
    W.save_worksheet(tmp_path, manual_rows=[_expert_row()], overlays={})
    before = (tmp_path / "adequacy_worksheet.json").read_text()
    with pytest.raises(W.WorksheetValidationError):
        W.save_worksheet(tmp_path, manual_rows=[{"garbage": True}], overlays={})
    assert (tmp_path / "adequacy_worksheet.json").read_text() == before
    assert not list(tmp_path.glob("*.tmp*"))


# ── the routes ────────────────────────────────────────────────────────────

def test_routes_round_trip(tmp_path):
    import routers.adequacy_worksheet as R
    proj = _proj(tmp_path)
    out = R.get_worksheet(project=proj)
    assert out["manual_rows"] == [] and out["version"] == 0
    body = R.WorksheetPut(manual_rows=[_expert_row()],
                          overlays={"m1": {"mitigability": "spares"}})
    saved = R.put_worksheet(body=body, project=proj)
    assert saved["version"] == 1
    again = R.get_worksheet(project=proj)
    assert again["manual_rows"][0]["name"] == "ops_error"
    assert again["overlays"]["m1"]["mitigability"] == "spares"


def test_routes_422_on_invalid_rows(tmp_path):
    import routers.adequacy_worksheet as R
    from fastapi import HTTPException
    proj = _proj(tmp_path)
    bad = _expert_row(); bad["failure_class"] = "Z"
    with pytest.raises(HTTPException) as e:
        R.put_worksheet(body=R.WorksheetPut(manual_rows=[bad], overlays={}),
                        project=proj)
    assert e.value.status_code == 422
