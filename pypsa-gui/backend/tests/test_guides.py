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


def test_every_panel_hover_key_is_in_the_catalogue():
    """P19–P22 gate: the EH panel's hover tips read catalogue keys — a key
    missing here renders a control with no help."""
    src = (FE_SRC / "pages" / "results" / "EhReferenceDesignPanel.tsx").read_text()
    keys = set(re.findall(r"fieldTip\('([a-z_]+)'\)", src))
    keys |= set(re.findall(r"'eh-pack-[a-z-]+', [^\]]*?, '([a-z_]+)'\]", src))
    assert {"ens_cap_permyriad", "target_lole_h", "mc_seed", "dsr_buses",
            "dtc_attribution", "levers", "stages", "archetype"} <= keys
    fields = G.load_guide("eh_fmea")["fields"]
    assert keys <= set(fields), keys - set(fields)


# ── P24: plain-language terms for the hub-design step cards ────────────────

HUB_FIELDS = (
    "hub_start", "hub_site", "hub_goal", "hub_results", "hub_improve",
    "site_type", "grid_connection", "critical_load", "grid_strength",
    "outage_data", "shortfall_hours", "energy_strictness", "verdict",
    "cost_at_target", "top_risks", "not_established", "stress_scenario",
    "fmea_check", "template_provenance", "voll_plain",
)


def test_hub_fields_present():
    fields = G.load_guide("eh_fmea")["fields"]
    assert len(set(HUB_FIELDS)) == 20
    missing = [k for k in HUB_FIELDS if not (fields.get(k) or "").strip()]
    assert not missing, missing


# Stage ids and engine names the Guided cards must not show (spec §4.3, §5.8).
_JARGON = ("P19", "decision 6", "Class-B", "‱", "lp_proxy", "copt")


@pytest.mark.parametrize("key", HUB_FIELDS)
def test_field_text_is_plain(key):
    text = G.load_guide("eh_fmea")["fields"].get(key) or ""
    assert text, key
    bad = [w for w in _JARGON if w.lower() in text.lower()]
    assert not bad, (key, bad)
    assert len(text.split()) <= 30, (key, len(text.split()))


# P24-BE gate N5: a first-time user reads these as hovers — no unit or
# equipment jargon, and the step intros are statements (the card footer's
# "Ask" button carries the question).
_HUB_JARGON = ("MVA", "MWh", "inverter")


@pytest.mark.parametrize("key", HUB_FIELDS)
def test_field_text_has_no_unit_jargon(key):
    text = G.load_guide("eh_fmea")["fields"][key]
    bad = [w for w in _HUB_JARGON if w.lower() in text.lower()]
    assert not bad, (key, bad)


@pytest.mark.parametrize("key", [k for k in HUB_FIELDS if k.startswith("hub_")])
def test_hub_step_text_is_a_statement(key):
    text = G.load_guide("eh_fmea")["fields"][key]
    assert "?" not in text, key


def test_verdict_text_covers_the_no_target_case():
    text = G.load_guide("eh_fmea")["fields"]["verdict"].lower()
    for outcome in ("not certified", "certified", "not decided", "no goal"):
        assert outcome in text, outcome


# P24-FE: the hub-design tour (spec §4.3) — targets in order, each revealed by
# the rail step that shows its card. Its ids are literal test ids in the step
# cards, which `test_every_tour_target_is_a_rendered_test_id` pins.
_HUB_TOUR = [
    ("hub-rail", None, False),
    ("hub-start-templates", "hub-rail-step-start", False),
    ("hub-site-readiness", "hub-rail-step-site", False),
    ("hub-site-type", "hub-rail-step-site", False),
    ("hub-goal-lole", "hub-rail-step-goal", False),
    ("hub-goal-run", "hub-rail-step-goal", False),
    ("hub-results-verdict", "hub-rail-step-results", True),
    ("hub-improve-list", "hub-rail-step-improve", True),
    ("hub-improve-fmea", "hub-rail-step-improve", False),
]


def test_hub_design_tour_matches_the_spec():
    tour = G.load_guide("eh_fmea")["tours"]["hub_design"]
    assert tour["title"] == "Design a hub in five steps"
    got = [(s["target"], s.get("reveal"), bool(s.get("optional")))
           for s in tour["steps"]]
    assert got == _HUB_TOUR
    for s in tour["steps"]:
        text = f"{s['title']} {s['body']} {s.get('enter', '')}"
        bad = [w for w in _JARGON + _HUB_JARGON if w.lower() in text.lower()]
        assert not bad, (s["target"], bad)
