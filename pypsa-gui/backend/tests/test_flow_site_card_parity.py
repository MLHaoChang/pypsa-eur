"""The Site / Goal card's study request, one source (gatekeeper point 3).

`flow_service.site_card_study_body(meta)` is the Python copy of the frontend's
`buildEhStudyBody(meta.recommended_archetype, formFromTemplate(meta))`. Both
are pinned to `fixtures/flow_site_card_args.json`: this test checks the
fixture's metadata against each shipped template and the Python derivation
against the fixture; `frontend/src/pages/hubDesign/siteCardParity.test.ts`
checks the TypeScript derivation against the same rows.

Regenerate (only after an intended change on both sides):
`FLOW_PARITY_WRITE=1 pytest tests/test_flow_site_card_parity.py`.
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest

from services.flow_service import site_card_study_body

HERE = pathlib.Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "flow_site_card_args.json"
TEMPLATES = HERE.parent / "project_templates"
META_KEYS = ("recommended_archetype", "pack_overrides", "stages", "dtc_attribution")
# Rules no shipped template exercises yet, so the parity covers them too.
SYNTHETIC = {
    "synthetic:all_rules": {
        "recommended_archetype": "weak_flexible", "dtc_attribution": "per_load",
        "pack_overrides": {"ens_cap_permyriad": 2.0, "target_lole_h": 0.5,
                           "import_p_nom_mw": 40.0, "import_energy_mwh_per_year": 1000.0},
        "stages": ["mc_certify", "frontier", "not_a_stage"]},
    "synthetic:strong_grid_drops_import": {
        "recommended_archetype": "strong_grid", "dtc_attribution": None,
        "pack_overrides": {"import_p_nom_mw": 40.0, "import_energy_mwh_per_year": -1},
        "stages": []},
    "synthetic:out_of_range_is_null": {
        "recommended_archetype": "weak_flexible", "dtc_attribution": None,
        "pack_overrides": {"ens_cap_permyriad": 0}, "stages": None},
}


def _shipped() -> dict[str, dict]:
    out = {p.parent.name: json.loads(p.read_text())
           for p in sorted(TEMPLATES.glob("eh_*/eh_template.json"))}
    from project_templates import eh_site as S

    for t in S.SITE_TYPES:
        out[f"eh_site:{t}"] = S.template_meta(S.site_params({"site_type": t}))
    return {k: {m: v.get(m) for m in META_KEYS} for k, v in out.items()}


def test_the_fixture_matches_every_shipped_template():
    shipped = _shipped()
    if os.environ.get("FLOW_PARITY_WRITE"):
        rows = [{"id": k, "meta": meta, "study_body": site_card_study_body(meta)}
                for k, meta in {**shipped, **SYNTHETIC}.items()]
        FIXTURE.write_text(json.dumps(rows, indent=1, sort_keys=True) + "\n")
    rows = json.loads(FIXTURE.read_text())
    assert {r["id"]: r["meta"] for r in rows
            if not r["id"].startswith("synthetic:")} == shipped
    assert {r["id"]: r["meta"] for r in rows if r["id"].startswith("synthetic:")} == SYNTHETIC


@pytest.mark.parametrize("row", json.loads(FIXTURE.read_text()) if FIXTURE.exists() else [],
                         ids=lambda r: r["id"])
def test_the_python_body_matches_the_fixture(row):
    assert site_card_study_body(row["meta"]) == row["study_body"]


def test_the_fixture_exercises_the_filtering():
    """A synthetic row per rule, so the parity covers what no template uses yet."""
    rows = {r["id"]: r for r in json.loads(FIXTURE.read_text())}
    assert set(SYNTHETIC) <= set(rows)
    assert rows["synthetic:out_of_range_is_null"]["study_body"] is None
