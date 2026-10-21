"""The project's scope: six work cards with at most three sub-options (W4a;
workspace-reconciliation §5.2, modular-cards.md), and each example's preset
(GS-6, modular-cards §3).

Covered here:
  * the registry: six cards, ≤ 3 sub-options each, an availability row for
    every sub-option, a preset for every EH template on disk (and the own
    site's site types and the IPP hybrid), never a not-yet-available
    sub-option pre-selected;
  * the store: a project with no file reads its preset; PUT stores, refuses
    unknown, unavailable and locked choices with typed kinds; the file is
    bundled;
  * the off-card test (§6): a deselected card's items don't count, aren't "to
    check" and don't block; its kind leaves `run_request`; its values survive
    an off/on round-trip.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from services import flow_scope as S

TEMPLATES_DIR = pathlib.Path(__file__).resolve().parent.parent / "project_templates"
INPUTS = ("project", "carriers", "connection", "loads", "assets", "profiles",
          "commercial", "scenarios")


def _eh_template_ids() -> list[str]:
    return sorted(json.loads(p.read_text())["id"]
                  for p in TEMPLATES_DIR.glob("*/eh_template.json"))


# ── the registry and the presets ────────────────────────────────────────────

def test_six_cards_with_at_most_three_sub_options():
    assert [c.id for c in S.CARDS] == ["capacity", "grid", "control", "reliability",
                                       "commercial", "handoff"]
    for card in S.CARDS:
        assert 1 <= len(card.options) <= 3, card.id
        for o, _label in card.options:
            assert (card.id, o) in S.AVAILABILITY
    assert len(S.AVAILABILITY) == sum(len(c.options) for c in S.CARDS)


def test_every_template_has_a_preset():
    ids = _eh_template_ids()
    assert ids, "no EH template found"
    for tid in ids:
        assert tid in S.TEMPLATE_PRESETS or tid == "eh_site", tid
    # The IPP hybrid (#485) is in flight; its preset is ready for it.
    assert "eh_ipp_hybrid" in S.TEMPLATE_PRESETS


def _all_presets() -> dict[str, S.Preset]:
    return {**S.TEMPLATE_PRESETS, **{f"eh_site:{k}": v for k, v in S.SITE_TYPE_PRESETS.items()},
            "blank": S.BLANK_PRESET}


@pytest.mark.parametrize("pid", sorted(_all_presets()))
def test_a_preset_names_real_sub_options_and_never_preselects_an_unavailable_one(pid):
    preset = _all_presets()[pid]
    for card, opts in preset.items():
        known = dict(S.CARD_BY_ID[card].options)
        assert opts and set(opts) <= set(known), (pid, card)
    eff = S.effective_preset(preset, has_template=pid != "blank")
    for card, opts in eff["options"].items():
        for o in opts:
            assert S.option_status(card, o, has_template=pid != "blank")[0] != S.NOT_YET
    # Capacity and Hand-off are always on; capacity with sizing and dispatch.
    assert {"capacity", "handoff"} <= set(eff["groups"])
    assert eff["options"]["capacity"][:2] == ["sizing", "dispatch"]
    # An EH example keeps today's reliability study, except the IPP hybrid.
    if pid not in ("blank", "eh_ipp_hybrid"):
        assert S.selected(eff, "reliability", "goal_study"), pid
    # The stored form passes the strict check the PUT route applies.
    S.validate(eff, has_template=pid != "blank")


def test_site_type_presets_cover_every_eh_site_type():
    from project_templates import eh_site

    assert set(S.SITE_TYPE_PRESETS) == set(eh_site.SITE_TYPES)


def test_data_centre_preset_exemplifies_grid_control_and_reliability():
    eff = S.effective_preset(S.TEMPLATE_PRESETS["eh_datacenter"], has_template=True)
    assert eff["groups"] == ["capacity", "grid", "control", "reliability", "commercial",
                             "handoff"]
    assert eff["options"]["grid"] == ["load_flow", "grid_code"]
    assert eff["options"]["control"] == ["presets"]            # island checks: not yet
    assert eff["options"]["commercial"] == ["tariffs"]         # investment case: not yet


def test_reliability_needs_a_template():
    status, reason = S.option_status("reliability", "goal_study", has_template=False)
    assert status == S.NOT_YET and "Energy Hub" in reason


# ── the strict check ────────────────────────────────────────────────────────

def _scope(**options) -> dict:
    base = S.effective_preset(S.BLANK_PRESET, has_template=True)
    for card, opts in options.items():
        base["options"][card] = list(opts)
        if opts and card not in base["groups"]:
            base["groups"].append(card)
    return base


@pytest.mark.parametrize("raw, kind", [
    ({"version": 2, "groups": [], "options": {}}, "flow_scope_invalid"),
    ({"version": 1, "groups": "capacity", "options": {}}, "flow_scope_invalid"),
    ({**_scope(), "groups": ["capacity", "handoff", "nope"]}, "flow_scope_unknown"),
    (_scope(grid=["load_flow", "magic"]), "flow_scope_unknown"),
    (_scope(control=["dynamics"]), "flow_scope_unavailable"),
    (_scope(capacity=["sizing"]), "flow_scope_required"),
    ({**_scope(), "groups": ["capacity"]}, "flow_scope_required"),
])
def test_the_strict_check_refuses_with_a_typed_kind(raw, kind):
    with pytest.raises(S.ScopeError) as exc:
        S.validate(raw, has_template=True)
    assert exc.value.error_kind == kind


def test_a_stored_scope_is_read_leniently(tmp_path):
    (tmp_path / S.SCOPE_FILE).write_text(json.dumps({
        "version": 1, "groups": ["grid", "nope"],
        "options": {"grid": ["n1_sclopf", "magic"], "control": ["dynamics"]}}))
    scope = S.read_scope(tmp_path, {"id": "eh_datacenter"})
    assert scope["source"] == "project"
    assert scope["groups"] == ["capacity", "grid", "handoff"]
    assert scope["options"]["grid"] == ["n1_sclopf"]
    assert scope["options"]["control"] == []
    assert scope["options"]["capacity"] == ["sizing", "dispatch"]


def test_a_card_switched_off_keeps_its_sub_options(tmp_path):
    tpl = {"id": "eh_datacenter"}
    scope = S.read_scope(tmp_path, tpl)
    off = {**scope, "groups": [g for g in scope["groups"] if g != "grid"]}
    stored = S.write_scope(tmp_path, off, tpl)
    assert not S.selected(stored, "grid")
    assert stored["options"]["grid"] == ["load_flow", "grid_code"]
    on = S.write_scope(tmp_path, {**stored, "groups": [*stored["groups"], "grid"]}, tpl)
    assert S.selected(on, "grid", "grid_code")


def test_the_scope_sidecar_travels_with_the_project():
    from routers.projects import _BUNDLE_FILES

    assert S.SCOPE_FILE in _BUNDLE_FILES


# ── the routes and the flow ─────────────────────────────────────────────────

def _create(client, tid, name, **kw) -> str:
    r = client.post(f"/api/projects/from_template/{tid}", params={"name": name}, **kw)
    assert r.status_code == 200, r.text
    return r.json().get("name", name)


def _flow(client, name) -> dict:
    r = client.get(f"/api/projects/{name}/flow")
    assert r.status_code == 200, r.text
    return r.json()


def _put(client, name, scope) -> dict:
    r = client.put(f"/api/projects/{name}/flow/scope", json={
        k: scope[k] for k in ("version", "groups", "options")})
    assert r.status_code == 200, r.text
    return r.json()["scope"]


@pytest.mark.parametrize("tid, preset_id", [
    ("eh_datacenter", "eh_datacenter"),
    ("eh_microgrid_staged", "eh_microgrid_staged"),
    ("eh_ammonia_staged", "eh_ammonia_staged"),
])
def test_a_template_opens_with_its_preset(client, tmp_projects_dir, tid, preset_id):
    name = _create(client, tid, f"p_{tid}")
    r = client.get(f"/api/projects/{name}/flow/scope")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"]["source"] == "preset" and body["preset_id"] == preset_id
    assert {k: body["scope"][k] for k in ("groups", "options")} == \
        {k: body["preset"][k] for k in ("groups", "options")}
    assert [c["id"] for c in body["cards"]] == [c.id for c in S.CARDS]
    flow = _flow(client, name)
    assert flow["scope"]["preset_id"] == preset_id
    assert flow["run_request"] == {"optimisation": True, "study": True}


def test_the_own_site_preset_follows_its_site_type(client, tmp_projects_dir):
    name = _create(client, "eh_site", "hosp", json={"site_type": "hospital"})
    body = client.get(f"/api/projects/{name}/flow/scope").json()
    assert body["preset_id"] == "eh_site:hospital"
    assert body["scope"]["options"]["reliability"] == ["goal_study", "stress"]


def test_put_refuses_an_unavailable_sub_option(client, tmp_projects_dir):
    name = _create(client, "eh_datacenter", "dc_refuse")
    scope = client.get(f"/api/projects/{name}/flow/scope").json()["scope"]
    scope["options"]["control"] = ["presets", "dynamics"]
    r = client.put(f"/api/projects/{name}/flow/scope", json=scope)
    assert r.status_code == 422
    assert r.json()["detail"]["error_kind"] == "flow_scope_unavailable"


def test_reliability_off_takes_the_study_out_of_the_run(client, tmp_projects_dir):
    """The off-card test (modular-cards §6): its items don't count, aren't to
    check and don't block; its kind leaves run_request; its values survive."""
    name = _create(client, "eh_microgrid", "off_card")
    before = _flow(client, name)
    rel_before = [i for s in before["sections"] for i in s["items"]
                  if i["card"] == "reliability"]
    assert rel_before and all(i["in_scope"] for i in rel_before)

    scope = client.get(f"/api/projects/{name}/flow/scope").json()["scope"]
    off = _put(client, name, {**scope, "groups": [g for g in scope["groups"]
                                                  if g != "reliability"]})
    assert off["source"] == "project"
    body = _flow(client, name)
    assert body["run_request"] == {"optimisation": True, "study": False}
    assert body["verdicts"]["study"] is None
    rel = [i for s in body["sections"] for i in s["items"] if i["card"] == "reliability"]
    assert rel, "out-of-scope items stay in the payload"
    assert all(i["in_scope"] is False and i["blocking"] is False for i in rel)
    assert not any(n["message"].endswith(i["message"]) for n in body["notes"] for i in rel)
    # Only in-scope items count toward a section's state.
    for row in body["sections"]:
        if row["state"] in ("complete", "not_needed"):
            counted = [i for i in row["items"] if i["in_scope"]]
            assert not any(i["severity"] == "error" for i in counted), row["section"]

    # Back on: the stored sub-options come back, and the study with them.
    on = _put(client, name, {**off, "groups": [*off["groups"], "reliability"]})
    assert on["options"]["reliability"] == scope["options"]["reliability"]
    again = _flow(client, name)
    assert again["run_request"]["study"] is True
    assert {(i["code"], i["in_scope"]) for s in again["sections"] for i in s["items"]
            if i["card"] == "reliability"} == \
        {(i["code"], True) for i in rel_before}


def test_a_solver_option_outside_the_scope_is_said(client, tmp_projects_dir, bound_ctx):
    from dataclasses import replace

    from services.pypsa_service import PyPSAService

    name = _create(client, "eh_h2_hub", "sclopf_note")
    with bound_ctx(client):
        state = PyPSAService.get_solver_state()
        state["solver_config"] = replace(state["solver_config"], sclopf=True)
    body = _flow(client, name)
    row = next(s for s in body["sections"] if s["section"] == "scenarios")
    item = next(i for i in row["items"] if i["code"] == "scope_option_configured")
    assert item["card"] == "grid" and item["in_scope"] and not item["blocking"]
    assert "N-1 security" in item["message"]
