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
import re

import pytest

from services import flow_scope as S
from tests.test_project_flow import edit  # noqa: F401, F811 — the shared fixture

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
    assert eff["options"]["grid"] == ["grid_code"]           # load flow: Solver settings
    assert eff["options"]["control"] == ["presets"]            # island checks: not yet
    assert eff["options"]["commercial"] == ["tariffs"]         # investment case: not yet


# ── availability follows the separability matrix (W0) ───────────────────────

def _matrix_row(kind, *, runnable):
    from services.run_separability import Row
    return Row(kind, kind, "supported" if runnable else "unsupported", runnable,
               entry="-", inputs="-", outputs="-", locks_and_state="-",
               implicit_steps="-", freshness="-", evidence=())


def test_every_not_yet_row_is_decided_by_the_matrix():
    # A static not-yet row with no matrix kind could only be flipped by editing
    # this table; the matrix is the one place that says a kind can run.
    for key, (status, _reason) in S.AVAILABILITY.items():
        if status == S.NOT_YET:
            assert key in S.MATRIX_KIND, key


# Why each row the matrix does not decide is not its to decide.
_NOT_THE_MATRIXS = {
    ("capacity", "dispatch"): "always part of the run",
    ("grid", "load_flow"): "the run_ac_pf_after_lopf solver setting decides",
    ("grid", "grid_code"): "an advisory scorecard, not a run kind",
    ("grid", "n1_sclopf"): "the sclopf solver setting decides",
    ("control", "presets"): "inputs only; nothing runs",
    ("reliability", "goal_study"): "the study's own start gate decides",
    ("reliability", "stress"): "inputs to the study; nothing runs on its own",
    ("reliability", "outage_screening"): "inputs to the study; nothing runs on its own",
    ("commercial", "tariffs"): "terms the optimisation reads",
    ("commercial", "contracts"): "terms the optimisation reads",
    ("handoff", "reports"): "a read of results, not a run kind",
    ("handoff", "exports"): "a read of results, not a run kind",
    ("handoff", "specialist_packet"): "a read of results, not a run kind",
}


def test_every_option_is_matrix_decided_or_says_why_not():
    assert not set(_NOT_THE_MATRIXS) & set(S.MATRIX_KIND)
    assert set(_NOT_THE_MATRIXS) | set(S.MATRIX_KIND) == set(S.AVAILABILITY)


def test_today_the_matrix_clears_only_sizing_off():
    from services import run_separability as RS
    for (card, option), kind in S.MATRIX_KIND.items():
        if (card, option) == ("capacity", "sizing"):
            assert RS.runnable(kind)
            assert S.option_status(card, option, has_template=True) == (S.AVAILABLE, None)
            continue
        assert not RS.runnable(kind), kind
        assert S.option_status(card, option, has_template=True)[0] == S.NOT_YET


# ── Asset sizing is scope-owned (W4b): sizing in scope ⇔ fixed_sizes == "off" ─

def _cap(options):
    return {"version": 1, "groups": ["capacity", "handoff"],
            "options": {"capacity": options, "handoff": ["reports"]}}


@pytest.mark.parametrize("fixed, sizing", [("off", True), ("capacity_run", False),
                                           ("nameplate", False)])
def test_the_scope_reads_sizing_from_the_fixed_sizes_setting(tmp_path, fixed, sizing):
    scope = S.read_scope(tmp_path, {"id": "eh_datacenter"}, {"fixed_sizes": fixed})
    assert ("sizing" in scope["options"]["capacity"]) is sizing
    assert "dispatch" in scope["options"]["capacity"]


def test_the_transition_leaves_a_chosen_source_and_flips_off():
    assert S.fixed_sizes_transition(False, "off") == "capacity_run"
    assert S.fixed_sizes_transition(False, "nameplate") is None
    assert S.fixed_sizes_transition(False, "capacity_run") is None
    assert S.fixed_sizes_transition(True, "nameplate") == "off"
    assert S.fixed_sizes_transition(True, "off") is None


def test_write_scope_hands_the_store_the_transition_and_reads_its_answer(tmp_path):
    calls = []

    def store(sizing):
        calls.append(sizing)
        return "capacity_run" if not sizing else "off"

    out = S.write_scope(tmp_path, _cap(["dispatch"]), {"id": "eh_datacenter"},
                        {"fixed_sizes": "off"}, store_fixed_sizes=store)
    assert "sizing" not in out["options"]["capacity"]
    out = S.write_scope(tmp_path, _cap(["sizing", "dispatch"]), {"id": "eh_datacenter"},
                        {"fixed_sizes": "capacity_run"}, store_fixed_sizes=store)
    assert calls == [False, True]
    assert "sizing" in out["options"]["capacity"]


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


def test_load_flow_and_n1_follow_the_solver_settings(client, tmp_projects_dir, bound_ctx):
    """"What the Run computes" is true: load flow and N-1 security are on
    exactly when their solver settings are, read-only in the scope (UX gate)."""
    from dataclasses import replace

    from services.pypsa_service import PyPSAService

    name = _create(client, "eh_h2_hub", "sclopf_mirror")
    reg = client.get(f"/api/projects/{name}/flow/scope").json()
    assert not S.selected(reg["scope"], "grid")
    grid = next(c for c in reg["cards"] if c["id"] == "grid")
    assert {o["id"]: o["status"] for o in grid["options"]} == {
        "load_flow": "from_settings", "grid_code": "available", "n1_sclopf": "from_settings"}
    with bound_ctx(client):
        state = PyPSAService.get_solver_state()
        state["solver_config"] = replace(state["solver_config"], sclopf=True)
    reg = client.get(f"/api/projects/{name}/flow/scope").json()
    assert S.selected(reg["scope"], "grid", "n1_sclopf")
    assert not S.selected(reg["scope"], "grid", "load_flow")
    assert next(c for c in reg["cards"] if c["id"] == "grid")["locked_reason"]
    assert S.selected(_flow(client, name)["scope"], "grid", "n1_sclopf")
    # A write cannot switch it off (the settings decide), nor store it.
    off = _put(client, name, {**reg["scope"],
                              "groups": [g for g in reg["scope"]["groups"] if g != "grid"]})
    assert S.selected(off, "grid", "n1_sclopf")
    assert "n1_sclopf" not in S.normalise(off, has_template=True)["options"]["grid"]


_INTERNAL = re.compile(r"§|\bW\d|\bQ\d|\(owner")


def test_every_reason_and_label_is_in_the_users_words():
    texts = [c.label for c in S.CARDS] + [c.locked_reason or "" for c in S.CARDS]
    texts += [label for c in S.CARDS for _o, label in c.options]
    texts += [r or "" for _s, r in S.AVAILABILITY.values()]
    texts += [S.option_status("reliability", "goal_study", has_template=False)[1] or ""]
    reg = S.registry({"id": "eh_datacenter"}, {"sclopf": True})
    texts += [c["locked_reason"] or "" for c in reg["cards"]]
    for t in texts:
        assert not _INTERNAL.search(t), t


# ── gatekeeper's conditions (W4a plan review) ───────────────────────────────

_ADDED_KEYS = ("card", "in_scope")


def _strip_scope(body: dict) -> dict:
    """The payload as master built it: without `scope` and the item keys W4a adds."""
    out = {k: v for k, v in body.items() if k != "scope"}
    out["sections"] = [{**row, "items": [{k: v for k, v in it.items() if k not in _ADDED_KEYS}
                                         for it in row["items"]]}
                       for row in body["sections"]]
    return out


def _all_on(template: dict | None) -> dict:
    """Every card on with every sub-option it may take: master's behaviour
    (study requested for every EH project, every item counted)."""
    has = template is not None
    return S.normalise({"version": 1, "groups": [c.id for c in S.CARDS],
                        "options": {c.id: [o for o, _ in c.options] for c in S.CARDS}},
                       has_template=has)


def _golden_cases() -> list[tuple[str, dict]]:
    tids = [t for t in _eh_template_ids() if t != "eh_site"]
    return ([(t, {}) for t in tids]
            + [("eh_site", {"json": {"site_type": st}}) for st in S.SITE_TYPE_PRESETS])


@pytest.mark.parametrize("tid, kw", _golden_cases(),
                         ids=[f"{t}-{kw.get('json', {}).get('site_type', '')}"
                              for t, kw in _golden_cases()])
def test_no_scope_file_reads_exactly_as_before(client, tmp_projects_dir, project_storage_dir,
                                               tid, kw):
    """No `flow_scope.json`: the payload equals the one with every card on,
    which is master's rule (study requested for every EH template, every item
    counted), apart from the added keys."""
    name = _create(client, tid, f"g_{tid}", **kw)
    preset = _flow(client, name)
    assert preset["scope"]["source"] == "preset"
    template = json.loads((project_storage_dir(name) / "eh_template.json").read_text())
    # Master's rule: every EH template requests the study, except a load-less
    # generation-only plant (#485's IPP hybrid).
    generation_only = bool(template.get("generation_only"))
    assert preset["run_request"] == {"optimisation": True, "study": not generation_only}
    items = [i for s in preset["sections"] for i in s["items"]]
    assert all(i["in_scope"] for i in items)
    (project_storage_dir(name) / S.SCOPE_FILE).write_text(json.dumps(_all_on(template)))
    everything = _flow(client, name)
    assert _strip_scope(preset) == _strip_scope(everything)
    # Only the islanded connection reads not_needed.
    nn = {s["section"] for s in preset["sections"] if s["state"] == "not_needed"}
    assert nn == (({"connection"} if template.get("recommended_archetype") == "off_grid"
                   else set()) | ({"loads"} if generation_only else set()))


def test_a_project_without_a_template_keeps_its_payload(client, install_network,
                                                        tmp_projects_dir):
    from tests.test_project_flow import _site_network

    install_network(_site_network(), name="plain")
    assert client.post("/api/projects/plain", params={"force": True,
                                                      "rebind": True}).status_code == 200
    body = _flow(client, "plain")
    assert body["scope"]["preset_id"] == "blank"
    assert body["run_request"] == {"optimisation": True, "study": False}
    assert all(i["in_scope"] for s in body["sections"] for i in s["items"])
    assert not any(s["state"] == "not_needed" for s in body["sections"])


def _no_out_of_scope_blocker(body: dict) -> None:
    for s in body["sections"]:
        for i in s["items"]:
            assert not (i["in_scope"] is False and i["blocking"]), i


def test_a_study_refusal_with_reliability_off_blocks_nothing(client, tmp_projects_dir,
                                                             project_storage_dir):
    """The invariant (gatekeeper #3, #7): no `in_scope: false` item blocks, and
    run_ready is the verdict of the Run run_request describes. The study's
    refusal is listed, out of scope; the study-start route itself is unchanged."""
    name = _create(client, "eh_datacenter", "refused_off")
    path = project_storage_dir(name) / "eh_template.json"
    meta = json.loads(path.read_text())
    meta["pack_overrides"] = {**meta["pack_overrides"], "import_energy_mwh_per_year": 1000.0}
    path.write_text(json.dumps(meta))
    links = client.get("/api/network/links").json()
    grid = next(r for r in links if r["name"] == "grid_import")
    assert client.put("/api/network/links/grid_import",
                      json={**grid, "p_min_pu": -1.0}).status_code == 200
    on = _flow(client, name)
    assert on["run_ready"] is False and on["run_blocker"]["section"] == "scenarios"

    scope = client.get(f"/api/projects/{name}/flow/scope").json()["scope"]
    _put(client, name, {**scope, "groups": [g for g in scope["groups"] if g != "reliability"]})
    off = _flow(client, name)
    refused = next(i for s in off["sections"] for i in s["items"]
                   if i["code"] == "eh_study_refused")
    assert refused["in_scope"] is False and refused["blocking"] is False
    _no_out_of_scope_blocker(off)
    pre = client.post("/api/simulation/preflight").json()
    assert off["run_request"] == {"optimisation": True, "study": False}
    assert off["run_ready"] is pre["ok"] is True
    assert off["run_blocker"] is None


@pytest.mark.parametrize("card, in_scope", [("grid", True), ("commercial", False)])
def test_an_assumed_row_leaves_the_scope_only_when_it_feeds_nothing_the_run_reads(
        client, tmp_projects_dir, project_storage_dir, card, in_scope):
    """A row tagged with a card the network reads (grid) stays in scope with
    that card off; a finance row (the investment case, not in the Run) leaves."""
    name = _create(client, "eh_h2_hub", f"row_{card}")
    path = project_storage_dir(name) / "eh_template.json"
    meta = json.loads(path.read_text())
    meta["ledger"].append({"item": f"probe {card}", "value": 1, "unit": "x",
                           "source": "assumed", "card": card})
    path.write_text(json.dumps(meta))
    scope = client.get(f"/api/projects/{name}/flow/scope").json()["scope"]
    assert not S.selected(scope, "grid") and not S.selected(scope, "commercial",
                                                            "investment_case")
    body = _flow(client, name)
    row = next(i for s in body["sections"] for i in s["items"]
               if i["target"]["entity"] == f"probe {card}")
    assert row["card"] == card and row["in_scope"] is in_scope


def test_deselecting_changes_no_input_and_no_result_state(client, tmp_projects_dir,
                                                         project_storage_dir, bound_ctx):
    """A scope write touches only flow_scope.json: no network revision, no
    other sidecar, no result state, no freshness change."""
    from services import dirty_state
    from services.project_context import RESULT_STATE_KEYS
    from services.pypsa_service import PyPSAService

    name = _create(client, "eh_microgrid", "no_input")
    before_flow = _flow(client, name)
    pdir = project_storage_dir(name)
    files = {p.name: p.read_bytes() for p in pdir.iterdir() if p.is_file()}
    with bound_ctx(client):
        rev = dirty_state.revision()
        state = PyPSAService.get_solver_state()
        results = {k: id(state.get(k)) for k in RESULT_STATE_KEYS}
    scope = client.get(f"/api/projects/{name}/flow/scope").json()["scope"]
    _put(client, name, {**scope, "groups": ["capacity", "handoff"]})
    with bound_ctx(client):
        assert dirty_state.revision() == rev
        state = PyPSAService.get_solver_state()
        assert {k: id(state.get(k)) for k in RESULT_STATE_KEYS} == results
    after = {p.name: p.read_bytes() for p in pdir.iterdir() if p.is_file()}
    assert set(after) - set(files) == {S.SCOPE_FILE}
    assert {k: v for k, v in after.items() if k != S.SCOPE_FILE} == files
    after_flow = _flow(client, name)
    results_row = lambda b: next(s for s in b["sections"] if s["section"] == "results")  # noqa: E731
    assert results_row(after_flow)["freshness"] == results_row(before_flow)["freshness"]


def test_a_busy_study_lock_defers_only_a_run_that_requests_the_study(
        client, tmp_projects_dir, monkeypatch):
    """Gatekeeper #1: with Reliability off, a held study lock does not defer
    the flow; run_ready is the optimisation's verdict."""
    from services.adequacy import eh_study_runner

    name = _create(client, "eh_datacenter", "lock_busy")

    def busy(*_a, **_k):
        raise eh_study_runner.EhStudyLockBusy()

    monkeypatch.setattr(eh_study_runner, "check_eh_study_start", busy)
    assert _flow(client, name)["deferred"] is True
    scope = client.get(f"/api/projects/{name}/flow/scope").json()["scope"]
    _put(client, name, {**scope, "groups": [g for g in scope["groups"] if g != "reliability"]})
    body = _flow(client, name)
    assert body["deferred"] is False
    pre = client.post("/api/simulation/preflight").json()
    assert body["run_ready"] is pre["ok"]
    assert body["verdicts"]["study"] is None


def test_a_ledger_row_with_a_network_target_stays_in_scope(client, tmp_projects_dir,
                                                           project_storage_dir):
    """Gatekeeper #5: a row that names a component field (as DC-02's rows do)
    stays in scope whatever its card."""
    name = _create(client, "eh_h2_hub", "row_target")
    path = project_storage_dir(name) / "eh_template.json"
    meta = json.loads(path.read_text())
    meta["ledger"].append({"item": "probe_target", "value": 1, "source": "assumed",
                           "card": "commercial", "section": "connection",
                           "component": "Link", "entity": "grid_import", "field": "p_nom"})
    path.write_text(json.dumps(meta))
    body = _flow(client, name)
    row = next(i for s in body["sections"] for i in s["items"]
               if i["code"] == "assumed_probe_target")
    assert row["card"] == "commercial" and row["in_scope"] is True


def test_a_scope_write_keeps_a_solved_projects_results_fresh(client, install_network,
                                                             tmp_projects_dir, session_state):
    """Gatekeeper #5: on a solved project a scope change neither stales nor
    clears the results."""
    from tests.test_project_flow import _site_network

    install_network(_site_network(), name="solved")
    assert client.post("/api/projects/solved", params={"force": True,
                                                       "rebind": True}).status_code == 200
    r = client.post("/api/simulation/run")
    assert r.status_code == 200, r.text
    t = session_state(client).get("thread")
    if t is not None:
        t.join(timeout=120)
    before = _flow(client, "solved")
    fresh = next(s for s in before["sections"] if s["section"] == "results")["freshness"]
    assert fresh == "fresh"
    objective = session_state(client).get("objective")
    scope = client.get("/api/projects/solved/flow/scope").json()["scope"]
    _put(client, "solved", {**scope, "groups": ["capacity", "handoff"]})
    after = _flow(client, "solved")
    assert next(s for s in after["sections"] if s["section"] == "results")["freshness"] == fresh
    assert session_state(client).get("objective") == objective is not None


def test_a_load_less_generation_only_plant_is_not_offered_reliability(
        client, tmp_projects_dir, project_storage_dir, edit):  # noqa: F811
    """UX gate on the #485 merge: on the IPP plant (generation only, no Load)
    the study never runs, so Reliability reads not available with its reason,
    a write selecting it is refused, a stored one is dropped, and run_request
    never holds the study. A Load added brings it back."""
    name = _create(client, "eh_ipp_hybrid", "ipp_scope")
    reg = client.get(f"/api/projects/{name}/flow/scope").json()
    rel = next(c for c in reg["cards"] if c["id"] == "reliability")
    assert {o["status"] for o in rel["options"]} == {S.NOT_YET}
    assert rel["options"][0]["reason"] == S._NO_LOAD
    scope = reg["scope"]
    r = client.put(f"/api/projects/{name}/flow/scope", json={
        "version": 1, "groups": [*scope["groups"], "reliability"],
        "options": {**scope["options"], "reliability": ["goal_study"]}})
    assert r.status_code == 422
    assert r.json()["detail"]["error_kind"] == "flow_scope_unavailable"
    # A stored scope that selected it (an older file) is normalised away.
    (project_storage_dir(name) / S.SCOPE_FILE).write_text(json.dumps({
        "version": 1, "groups": ["capacity", "reliability", "handoff"],
        "options": {"capacity": ["sizing", "dispatch"], "reliability": ["goal_study"],
                    "handoff": ["reports"]}}))
    body = _flow(client, name)
    assert body["run_request"]["study"] is False
    assert not S.selected(body["scope"], "reliability")
    with edit() as n:
        n.add("Load", "aux", bus="poi", p_set=1.0)
    rel = next(c for c in client.get(f"/api/projects/{name}/flow/scope").json()["cards"]
               if c["id"] == "reliability")
    assert next(o for o in rel["options"] if o["id"] == "goal_study")["status"] == S.AVAILABLE


def test_the_registry_names_each_cards_sections_and_chip():
    """W3a's section rows read which card holds which section from here."""
    reg = S.registry({"id": "eh_datacenter"})
    by_id = {c["id"]: c for c in reg["cards"]}
    for c in reg["cards"]:
        assert set(c["sections"]) <= set(INPUTS), c["id"]
    assert by_id["capacity"]["chip"] is None and by_id["handoff"]["chip"] is None
    assert {k: c["chip"] for k, c in by_id.items() if c["chip"]} == {
        "grid": "Grid", "control": "Control", "reliability": "Reliability",
        "commercial": "Commercial"}
    assert by_id["commercial"]["sections"] == ["commercial"]


def test_a_scope_put_changes_only_fixed_sizes_and_the_checkbox_mirrors_it(client, tmp_projects_dir):
    """W4b: sizing out of scope sets fixed_sizes and no other solver_config
    field; a source named in Solver settings is not clobbered; sizing back in
    sets it off; the scope reads what the config says."""
    name = _create(client, "eh_datacenter", "dc_sizing")
    cfg = lambda: client.get("/api/simulation/solver_config").json()   # noqa: E731
    before = cfg()
    assert before["fixed_sizes"] == "off"
    scope = client.get(f"/api/projects/{name}/flow/scope").json()["scope"]
    assert "sizing" in scope["options"]["capacity"]
    opts = {**scope["options"], "capacity": ["dispatch"]}
    off = _put(client, name, {**scope, "options": opts})
    assert "sizing" not in off["options"]["capacity"]
    after = cfg()
    assert after["fixed_sizes"] == "capacity_run"
    assert {k: v for k, v in after.items() if k != "fixed_sizes"} == \
        {k: v for k, v in before.items() if k != "fixed_sizes"}
    # A source chosen in Solver settings stays when sizing stays out of scope.
    assert client.put("/api/simulation/solver_config", json={"fixed_sizes": "nameplate"}).status_code == 200
    _put(client, name, {**scope, "options": opts})
    assert cfg()["fixed_sizes"] == "nameplate"
    assert "sizing" not in client.get(f"/api/projects/{name}/flow/scope").json()["scope"]["options"]["capacity"]
    # Sizing back in sets off.
    on = _put(client, name, {**scope, "options": {**opts, "capacity": ["sizing", "dispatch"]}})
    assert "sizing" in on["options"]["capacity"]
    assert cfg()["fixed_sizes"] == "off"


def test_a_stale_snapshot_never_overwrites_a_source_chosen_meanwhile(client, tmp_projects_dir, monkeypatch):
    """The live value decides: the snapshot says "off", a Solver-settings save
    has set nameplate since, and a scope PUT without sizing leaves nameplate."""
    from routers import flow as flow_router

    name = _create(client, "eh_datacenter", "dc_race")
    scope = client.get(f"/api/projects/{name}/flow/scope").json()["scope"]
    real = flow_router._solver_settings

    def stale(project):
        snap = dict(real(project) or {})
        client.put("/api/simulation/solver_config", json={"fixed_sizes": "nameplate"})
        return {**snap, "fixed_sizes": "off"}

    monkeypatch.setattr(flow_router, "_solver_settings", stale)
    _put(client, name, {**scope, "options": {**scope["options"], "capacity": ["dispatch"]}})
    assert client.get("/api/simulation/solver_config").json()["fixed_sizes"] == "nameplate"


def test_a_closed_project_without_a_saved_config_refuses_by_name(tmp_path):
    from routers import flow as flow_router

    project = type("P", (), {"directory": tmp_path})()
    import services.network_lengths as nl
    orig = nl.active_project_dir
    nl.active_project_dir = lambda: None
    try:
        with pytest.raises(S.ScopeError) as exc:
            flow_router._store_fixed_sizes(project, False, None, None)
        assert exc.value.error_kind == "solver_config_unavailable"
        assert flow_router._store_fixed_sizes(project, True, None, None) == "off"
    finally:
        nl.active_project_dir = orig
