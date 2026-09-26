"""
P21 — the in-app guide catalogue (plan 2026-09-26).

One catalogue feeds the GUI tours, the hover tips and (P22) the assistant.
These tests pin it to the product: every tour step anchors to a test id a
component really renders, every EH tag / pack override has help text, and a
frozen build ships the file at the path the loader reads.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from services import guides as G

BACKEND = pathlib.Path(__file__).resolve().parents[1]
FE_SRC = BACKEND.parent / "frontend" / "src"


def _fe_test_ids() -> set[str]:
    ids: set[str] = set()
    pat = re.compile(r"""(?:data-testid|testId)=["'{`]+([a-z0-9-]+)["'`}]""")
    for f in FE_SRC.rglob("*.tsx"):
        if ".test." in f.name:
            continue
        ids |= set(pat.findall(f.read_text(encoding="utf-8")))
    return ids


def test_route_serves_the_catalogue(client):
    assert client.get("/api/guides").json() == {"topics": ["eh_fmea"]}
    body = client.get("/api/guides/eh_fmea").json()
    assert {"eh_study", "fmea", "eh_tagging"} <= set(body["tours"])
    assert client.get("/api/guides/nope").status_code == 404


def test_every_tour_target_is_a_rendered_test_id():
    ids = _fe_test_ids()
    guide = G.load_guide("eh_fmea")
    missing = [(t, s["target"]) for t, tour in guide["tours"].items()
               for s in tour["steps"] if s["target"] not in ids]
    reveals = [(t, s["reveal"]) for t, tour in guide["tours"].items()
               for s in tour["steps"] if s.get("reveal") and s["reveal"] not in ids]
    assert not missing and not reveals, (missing, reveals)


def test_every_eh_tag_and_override_has_help_text():
    from models.energy_hub import EH_CUSTOM_COLUMNS
    from services.adequacy.eh_study_runner import PackOverrides
    fields = G.load_guide("eh_fmea")["fields"]
    for cols in EH_CUSTOM_COLUMNS.values():
        for col in cols:
            assert fields.get(col), col
    for key in ("ens_cap_permyriad", "target_lole_h", "import_p_nom_mw",
                "import_energy_mwh_per_year", "levers"):
        assert key in PackOverrides.model_fields and fields.get(key), key


def test_a_malformed_catalogue_is_refused(tmp_path, monkeypatch):
    (tmp_path / "eh_fmea_guide.json").write_text(
        '{"tours": {"t": {"steps": [{"target": "x", "title": ""}]}}, "fields": {}}')
    monkeypatch.setattr(G, "GUIDE_DIR", tmp_path)
    G.load_guide.cache_clear()
    try:
        with pytest.raises(G.GuideError, match="lacks 'title'"):
            G.load_guide("eh_fmea")
    finally:
        G.load_guide.cache_clear()


def test_the_build_ships_the_guide_where_the_loader_reads_it():
    import sys
    spec = (BACKEND.parent / "pypsa-gui.spec").read_text(encoding="utf-8")
    assert '(str(BACKEND / "data" / "guides"), "data/guides")' in spec
    rel = G.GUIDE_DIR.relative_to(pathlib.Path(G.__file__).resolve().parents[1])
    assert rel.as_posix() == "data/guides"
    sys.path.insert(0, str(BACKEND / "smoke"))
    try:
        import check_bundle
    finally:
        sys.path.pop(0)
    assert "data/guides/eh_fmea_guide.json" in check_bundle.ROOTED
