"""Old → new edit cards for the study and campus settings tools (Q3-CARDS-1, group `campus`).

gridspine_update_config, gridspine_set_dispatch_source, gridspine_edit_template_param,
campus_set_island_config, campus_set_library and campus_draft_campus.
"""
from __future__ import annotations

import copy
import json
import uuid

import pytest
import yaml
from fastapi import HTTPException

import harness.edit_card_campus  # noqa: F401  (registers the adapters; the integrator imports it from edit_card)
from db.models import Project
from harness import edit_card
from services import campus_electrical_service as ce
from services import chat_tools, project_registry
from services import gridspine_service as gs
from tests.study_s4_support import dir_hash
from tests.test_campus_electrical_service import hub, user_and_db  # noqa: F401
from tests.test_campus_island_service import _config, project as island_hub  # noqa: F401
from tests.test_edit_card import _dispatch, grid  # noqa: F401

CONFIG = {"hours": 24, "k": 1, "window": 24, "overlap": 0, "screen": False}


@pytest.fixture
def study(user_and_db):  # noqa: F811
    db, user = user_and_db
    created = gs.create_study(db, user, "Card Study", config=CONFIG)
    return db.get(Project, uuid.UUID(created["id"]))


def _rows(card):
    return {r["field"]: r for r in card["rows"] if r["role"] != "note"}


def _find_receipt(value):
    if isinstance(value, dict):
        if isinstance(value.get("_edit_receipt"), dict):
            return value["_edit_receipt"]
        value = list(value.values())
    if isinstance(value, (list, tuple)):
        return next((r for r in map(_find_receipt, value) if r), None)
    return None


def _verified(result, session=None):
    """The receipt on the tool's result. A result large enough to spill reaches the model as a
    preview and a locator; the receipt is then read from the spilled whole."""
    receipt = result.get("_edit_receipt")
    if receipt is None and "spill" in result:
        from harness import spill

        receipt = _find_receipt(json.loads(spill.path_for(session, result["spill"]["locator"]).read_text()))
    assert receipt and receipt["outcome"] == "verified", receipt


def _refused(tool, args, kind=None, status=None):
    with pytest.raises(HTTPException) as exc:
        edit_card.preview(tool, args)
    if kind:
        assert exc.value.detail["error_kind"] == kind, exc.value.detail
    if status:
        assert exc.value.status_code == status
    return exc.value


def _storage(row):
    return project_registry.project_dir(row)


def _outsider(second_identity):
    chat_tools.set_acting_user(second_identity["user_id"])  # after the owner's setup


# ── gridspine_update_config ─────────────────────────────────────────────────

def test_update_config_shows_old_and_new_and_the_receipt_verifies(grid, study):
    args = {"project_id": "Card Study", "k": 2, "screen": True}
    frames, cards, result = _dispatch(grid, "gridspine_update_config", args)
    rows = _rows(cards[0]["edit_card"])
    assert cards[0]["edit_card"]["complete"] and cards[0]["edit_card"]["kind"] == "settings_edit"
    assert (rows["k"]["label"], rows["k"]["old"], rows["k"]["new"]) == ("k (hours per criterion)", 1, 2)
    assert (rows["screen"]["label"], rows["screen"]["old"], rows["screen"]["new"]) == (
        "Screen N-1 / N-2 at the selected hours", False, True)
    assert rows["screen"]["words"] == {"old": "Off", "new": "On"}
    _verified(result)
    assert gs.stored_config(study).k == 2


def test_update_config_a_named_value_that_is_unchanged_still_shows(study):
    card = edit_card.preview("gridspine_update_config", {"project_id": "Card Study", "k": 1, "screen": True})
    rows = _rows(card)
    assert (rows["k"]["old"], rows["k"]["new"]) == (1, 1)
    assert (rows["screen"]["old"], rows["screen"]["new"]) == (False, True)


def test_update_config_no_op_is_refused_and_so_is_what_the_service_refuses(study):
    _refused("gridspine_update_config", {"project_id": "Card Study", "k": 1}, "empty_edit")
    _refused("gridspine_update_config", {"project_id": "Card Study"}, "empty_edit")
    unknown = _refused("gridspine_update_config", {"project_id": "Card Study", "outdir": "/x"}, "invalid_edit_value", 422)
    assert "not editable" in unknown.detail["message"]
    _refused("gridspine_update_config", {"project_id": "Card Study", "k": 0}, "invalid_edit_value", 422)


def test_update_config_dispatch_directory_is_a_dispatch_source_row(study, hub):
    run = _storage(study) / "gridspine" / "run"
    run.mkdir(parents=True)
    (run / "dispatch.csv").write_text("x\n")
    (run / "loads.csv").write_text("x\n")
    card = edit_card.preview("gridspine_update_config", {"project_id": "Card Study", "from_dispatch": str(run)})
    (row,) = card["rows"]
    assert (row["label"], row["old"]) == ("Dispatch source", "Generate here (unit commitment)")
    assert row["new"] == "A finished study's directory: run"  # the folder's name, never the server path
    assert str(run.parent) not in json.dumps(card, default=str)


def test_two_dispatch_folders_of_one_name_read_back_by_fingerprint(study, user_and_db):
    db, user = user_and_db
    other = db.get(Project, uuid.UUID(gs.create_study(db, user, "Other Study", config=CONFIG)["id"]))
    first, second = _storage(study) / "gridspine" / "run", _storage(other) / "gridspine" / "run"
    for run in (first, second):
        run.mkdir(parents=True)
        (run / "dispatch.csv").write_text("x\n")
        (run / "loads.csv").write_text("x\n")
    chat_tools.gridspine_update_config("Card Study", from_dispatch=str(first))
    args = {"project_id": "Card Study", "from_dispatch": str(second)}
    card = edit_card.preview("gridspine_update_config", args)
    (row,) = card["rows"]
    assert row["new"] == "A finished study's directory: run (changed)" and row.get("fingerprint")
    assert edit_card.receipt(card, args)["outcome"] == "uncertain"  # the store still holds the first folder
    chat_tools.gridspine_update_config("Card Study", from_dispatch=str(second))
    assert edit_card.receipt(card, args)["outcome"] == "verified"


# ── gridspine_set_dispatch_source ───────────────────────────────────────────

def test_set_dispatch_source_old_to_new_and_back(grid, study, hub):
    frames, cards, result = _dispatch(grid, "gridspine_set_dispatch_source",
                                      {"project_id": "Card Study", "from_project": "Campus Hub"})
    (row,) = cards[0]["edit_card"]["rows"]
    assert (row["label"], row["old"], row["new"]) == ("Dispatch source", "Generate here (unit commitment)",
                                                      "A solved project's network: Campus Hub")
    _verified(result)
    frames, cards, result = _dispatch(grid, "gridspine_set_dispatch_source", {"project_id": "Card Study"})
    (row,) = cards[0]["edit_card"]["rows"]
    assert (row["old"], row["new"]) == ("A solved project's network: Campus Hub", "Generate here (unit commitment)")
    _verified(result)


def test_set_dispatch_source_no_op_and_handler_refusals(study, hub):
    _refused("gridspine_set_dispatch_source", {"project_id": "Card Study"}, "empty_edit")
    _refused("gridspine_set_dispatch_source", {"project_id": "Card Study", "from_project": "Nowhere"}, "edit_refused", 404)
    own = _refused("gridspine_set_dispatch_source", {"project_id": "Card Study", "from_project": "Card Study"},
                   "invalid_edit_value", 422)
    assert "own dispatch source" in own.detail["message"]
    _refused("gridspine_set_dispatch_source", {"project_id": "Card Study", "from_dispatch": "/etc"},
             "invalid_edit_value", 422)


def test_a_running_job_refuses_both_study_cards_like_the_services(study, monkeypatch):
    monkeypatch.setattr(gs, "_active_job_for", lambda project: {"id": "j"})
    _refused("gridspine_update_config", {"project_id": "Card Study", "k": 2}, "edit_refused", 409)
    _refused("gridspine_set_dispatch_source", {"project_id": "Card Study", "from_project": "x"}, "edit_refused", 409)


# ── gridspine_edit_template_param ───────────────────────────────────────────

def _editable_param(study):
    """A (unit, param, value, source) whose one-percent change the service accepts."""
    templates = gs.load_unit_templates()
    for row in templates.params.itertuples():
        try:
            gs.planned_template_edit(study, row.unit_id, row.param, row.value * 1.01, "datasheet", "chat")
        except HTTPException:
            continue
        return row.unit_id, row.param, float(row.value), str(row.source)
    pytest.fail("no template parameter accepts an edit")


def test_template_param_shows_value_and_source_old_to_new(grid, study):
    unit, param, value, source = _editable_param(study)
    args = {"project_id": "Card Study", "unit_id": unit, "param": param, "value": value * 1.01, "source": "datasheet"}
    frames, cards, result = _dispatch(grid, "gridspine_edit_template_param", args)
    rows = _rows(cards[0]["edit_card"])
    assert rows[f"{unit}.{param}"]["label"] == f"{unit} · {param}"
    assert (rows[f"{unit}.{param}"]["old"], rows[f"{unit}.{param}"]["new"]) == (value, pytest.approx(value * 1.01))
    assert (rows[f"{unit}.{param}.source"]["old"], rows[f"{unit}.{param}.source"]["new"]) == (source, "datasheet")
    _verified(result)
    # The second edit starts from the first: the old value is the project's own.
    again = edit_card.preview("gridspine_edit_template_param", {**args, "value": value * 1.02})
    assert _rows(again)[f"{unit}.{param}"]["old"] == pytest.approx(value * 1.01)


def test_template_param_a_named_unchanged_value_shows_and_a_no_op_refuses(study):
    unit, param, value, source = _editable_param(study)
    other = "datasheet" if source != "datasheet" else "measured"
    card = edit_card.preview("gridspine_edit_template_param", {
        "project_id": "Card Study", "unit_id": unit, "param": param, "value": value, "source": other})
    rows = _rows(card)
    assert rows[f"{unit}.{param}"]["old"] == rows[f"{unit}.{param}"]["new"] == value
    assert rows[f"{unit}.{param}.source"]["new"] == other
    _refused("gridspine_edit_template_param", {"project_id": "Card Study", "unit_id": unit, "param": param,
                                               "value": value, "source": source}, "empty_edit")
    _refused("gridspine_edit_template_param", {"project_id": "Card Study", "unit_id": "NO_SUCH_UNIT", "param": param,
                                               "value": 1.0, "source": "measured"}, "edit_refused", 404)
    _refused("gridspine_edit_template_param", {"project_id": "Card Study", "unit_id": unit, "param": param,
                                               "value": value, "source": "hearsay"}, "invalid_edit_value", 422)


# ── campus_set_island_config ────────────────────────────────────────────────

def test_island_config_first_save_then_changes_show_old_to_new(grid, island_hub):
    config = _config()
    frames, cards, result = _dispatch(grid, "campus_set_island_config", {"project_id": "Island Hub", "config": config})
    rows = {r["field"]: r for r in cards[0]["edit_card"]["rows"] if r["role"] != "note"}
    assert rows["units.bess"]["old"] is None and rows["units.bess"]["new"] == "bess"
    assert rows["units.g"]["words"]["new"] == "added"
    assert rows["requirements.sustained_h.value"]["label"] == "Sustained ride-through"
    assert rows["requirements.sustained_h.value"]["unit"] == "h"
    assert all(r["old"] is None for r in rows.values() if r.get("member") or r["field"].startswith("requirements"))
    _verified(result, grid)

    changed = copy.deepcopy(config)
    changed["requirements"]["sustained_h"] = {"value": 4.0, "source": "datasheet"}
    changed["requirements"]["scenarios"] = ["seamless"]
    changed["units"]["bess"]["droop_pct"] = {"value": 5.0, "source": "assumed"}
    frames, cards, result = _dispatch(grid, "campus_set_island_config", {"project_id": "Island Hub", "config": changed})
    card = cards[0]["edit_card"]
    rows = _rows(card)
    assert set(rows) == {"requirements.sustained_h.value", "requirements.sustained_h.source",
                         "requirements.scenarios", "units.bess.droop_pct.value"}
    assert (rows["requirements.sustained_h.value"]["old"], rows["requirements.sustained_h.value"]["new"]) == (1.0, 4.0)
    assert (rows["requirements.sustained_h.source"]["label"], rows["requirements.sustained_h.source"]["old"],
            rows["requirements.sustained_h.source"]["new"]) == ("Sustained ride-through source", "assumed", "datasheet")
    assert (rows["requirements.scenarios"]["old"], rows["requirements.scenarios"]["new"]) == (
        "UPS-bridged, Seamless", "Seamless")
    assert (rows["units.bess.droop_pct.value"]["label"], rows["units.bess.droop_pct.value"]["old"],
            rows["units.bess.droop_pct.value"]["new"]) == ("bess \u00b7 Droop pct", 4.0, 5.0)
    assert harness.edit_card_campus._island_label(["units", "bess", "control"]) == ("Control mode of bess", "")
    _verified(result, grid)


def test_island_config_removed_unit_no_op_and_handler_refusals(island_hub):
    chat_tools.campus_set_island_config("Island Hub", _config())
    _refused("campus_set_island_config", {"project_id": "Island Hub", "config": _config()}, "empty_edit")
    smaller = _config()
    del smaller["units"]["crit"]
    card = edit_card.preview("campus_set_island_config", {"project_id": "Island Hub", "config": smaller})
    (row,) = card["rows"]
    assert (row["field"], row["old"], row["new"], row["words"]["new"]) == ("units.crit", "load", None, "removed")
    bad = _config()
    bad["requirements"]["scenarios"] = ["nonsense"]
    _refused("campus_set_island_config", {"project_id": "Island Hub", "config": bad}, "invalid_edit_value", 422)
    _refused("campus_set_island_config", {"project_id": "Island Hub", "config": "not a mapping"},
             "invalid_edit_value", 422)


def test_island_config_rows_are_capped_and_a_discarded_study_is_noted(island_hub):
    chat_tools.campus_set_island_config("Island Hub", _config())
    wide = _config()
    for field, value in (("droop_pct", 5.0), ("q_droop_pct", 5.0), ("H_s", 2.0), ("T_g_s", 0.6), ("ramp_pu_per_s", 0.3),
                         ("start_s", 11.0), ("sync_s", 6.0), ("load_step_max_pct", 40.0), ("xd_sat", 3.0),
                         ("cos_phi_r", 0.85)):
        wide["units"]["g"][field] = {"value": value, "source": "datasheet"}
    card = edit_card.preview("campus_set_island_config", {"project_id": "Island Hub", "config": wide})
    typed = [r for r in card["rows"] if r["role"] == "typed"]
    assert len(typed) == edit_card.STUDY_LEAF_CAP
    assert any("more changes" in r["note"] for r in card["rows"] if r["role"] == "note")
    assert not any("island study" in r["note"] for r in card["rows"] if r["role"] == "note")
    ce._island_paths(island_hub)[1].write_text("{}")
    card = edit_card.preview("campus_set_island_config", {"project_id": "Island Hub", "config": wide})
    assert any("island study" in r["note"] for r in card["rows"] if r["role"] == "note")


# ── campus_set_library ──────────────────────────────────────────────────────

def _shipped_library():
    return yaml.safe_load(open(ce.cs.ASSET_LIBRARY_PATH).read())


def test_library_edit_added_and_removed_entries_show_old_to_new(grid, island_hub):
    library = _shipped_library()
    first = library["transformers"][0]
    old_capex = first["capex_eur"]["value"]
    first["capex_eur"]["value"] = old_capex + 1000
    clone = copy.deepcopy(library["cables"][0])
    clone["id"] = "CB_NEW"
    library["cables"].append(clone)
    removed = library["statcoms"].pop()
    frames, cards, result = _dispatch(grid, "campus_set_library",
                                      {"project_id": "Island Hub", "yaml": yaml.safe_dump(library, sort_keys=False)})
    card = cards[0]["edit_card"]
    rows = _rows(card)
    capex = rows[f"transformers.{first['id']}.capex_eur.value"]
    assert (capex["label"], capex["old"], capex["new"]) == (f"Transformers · {first['id']} · capex_eur",
                                                           old_capex, old_capex + 1000)
    added = rows["cables.CB_NEW"]
    assert (added["label"], added["old"], added["new"], added["words"]["new"]) == (
        "Cables · CB_NEW", None, "entry", "added")
    gone = rows[f"statcoms.{removed['id']}"]
    assert (gone["old"], gone["new"], gone["words"]["new"]) == ("entry", None, "removed")
    assert len(rows) == 3
    assert any("own copy" in r["note"] for r in card["rows"] if r["role"] == "note")
    _verified(result, grid)


def test_library_no_op_and_refusals(island_hub):
    shipped = yaml.safe_dump(_shipped_library(), sort_keys=False)
    _refused("campus_set_library", {"project_id": "Island Hub", "yaml": shipped}, "empty_edit")
    _refused("campus_set_library", {"project_id": "Island Hub", "yaml": "transformers: ["}, "invalid_edit_value", 422)
    _refused("campus_set_library", {"project_id": "Island Hub", "yaml": "x: 1"}, "invalid_edit_value", 422)
    _refused("campus_set_library", {"project_id": "Island Hub", "yaml": "x" * (ce.MAX_LIBRARY_BYTES + 1)},
             "invalid_edit_value", 413)
    # With the project's own copy saved, the old values are the copy's, not the shipped library's.
    library = _shipped_library()
    library["discount_rate"]["value"] = 0.09
    chat_tools.campus_set_library("Island Hub", yaml.safe_dump(library, sort_keys=False))
    library["discount_rate"]["value"] = 0.1
    card = edit_card.preview("campus_set_library", {"project_id": "Island Hub", "yaml": yaml.safe_dump(library)})
    (row,) = [r for r in card["rows"] if r["role"] == "typed"]
    assert (row["field"], row["old"], row["new"]) == ("discount_rate.value", 0.09, 0.1)
    assert not [r for r in card["rows"] if r["role"] == "note"]


# ── campus_draft_campus ─────────────────────────────────────────────────────

def test_draft_first_time_says_not_set_and_what_is_created(grid, hub):
    frames, cards, result = _dispatch(grid, "campus_draft_campus", {"project_id": "Campus Hub"})
    card = cards[0]["edit_card"]
    rows = _rows(card)
    assert rows["pcc"]["old"] is None and rows["pcc"]["new"] == "grid"
    assert rows["units"]["old"] is None and isinstance(rows["units"]["new"], int) and rows["units"]["new"] > 0
    assert rows["units"]["label"] == "Units"
    _verified(result)


def test_draft_again_needs_overwrite_and_shows_the_counts_it_replaces(hub):
    chat_tools.campus_draft_campus("Campus Hub")
    _refused("campus_draft_campus", {"project_id": "Campus Hub"}, "edit_refused", 409)
    path = _storage(hub) / ce.SUBDIR / ce.CAMPUS_FILE
    spec = yaml.safe_load(path.read_text())
    spec["campus"]["units"]["extra"] = {"kind": "load", "bus": next(iter(spec["campus"]["buses"])), "pypsa_name": "dc_load"}
    path.write_text(yaml.safe_dump(spec, sort_keys=False))
    card = edit_card.preview("campus_draft_campus", {"project_id": "Campus Hub", "overwrite": True})
    rows = _rows(card)
    assert set(rows) == {"units"} and rows["units"]["old"] == rows["units"]["new"] + 1
    assert any("replaced" in r["note"] for r in card["rows"] if r["role"] == "note")
    path.write_text(yaml.safe_dump(yaml.safe_load(chat_tools.campus_draft_campus("Campus Hub", True)["campus_yaml"])))
    _refused("campus_draft_campus", {"project_id": "Campus Hub", "overwrite": True}, "empty_edit")


def test_draft_refuses_what_the_service_refuses(user_and_db):  # noqa: F811
    db, user = user_and_db
    project_registry.create_root(db, user, "Never Saved")
    err = _refused("campus_draft_campus", {"project_id": "Never Saved"}, "invalid_edit_value", 422)
    assert "save" in err.detail["message"]


# ── conditions common to the six ────────────────────────────────────────────

def _calls(study, island_hub, hub):
    shipped = _shipped_library()
    shipped["discount_rate"]["value"] = 0.123
    unit, param, value, _ = _editable_param(study)
    return [
        ("gridspine_update_config", {"project_id": "Card Study", "k": 2}),
        ("gridspine_set_dispatch_source", {"project_id": "Card Study", "from_project": "Campus Hub"}),
        ("gridspine_edit_template_param", {"project_id": "Card Study", "unit_id": unit, "param": param,
                                           "value": value * 1.01, "source": "datasheet"}),
        ("campus_set_island_config", {"project_id": "Island Hub", "config": _config()}),
        ("campus_set_library", {"project_id": "Island Hub", "yaml": yaml.safe_dump(shipped, sort_keys=False)}),
        ("campus_draft_campus", {"project_id": "Campus Hub"}),
    ]


def test_every_preview_writes_nothing(study, island_hub, hub):
    calls = _calls(study, island_hub, hub)
    dirs = [_storage(p) for p in (study, island_hub, hub)]
    before = [dir_hash(d) for d in dirs]
    for tool, args in calls:
        edit_card.preview(tool, args)
    assert [dir_hash(d) for d in dirs] == before


def test_an_outsider_is_refused_before_any_old_value(study, island_hub, hub, second_identity):
    calls = _calls(study, island_hub, hub)
    _outsider(second_identity)
    for tool, args in calls:
        with pytest.raises(HTTPException) as exc:
            edit_card.preview(tool, args)
        assert exc.value.status_code in (403, 404), tool
        assert "old" not in json.dumps(exc.value.detail, default=str), tool


def test_a_foreign_edit_lock_refuses_the_card_where_it_refuses_the_tool(island_hub, hub, monkeypatch):
    from routers import projects

    def locked(db, project, user):
        raise HTTPException(409, {"error_kind": "project_locked", "message": "being edited"})

    monkeypatch.setattr(projects, "_check_project_lock", locked)
    for tool, args in (("campus_set_island_config", {"project_id": "Island Hub", "config": _config()}),
                       ("campus_set_library", {"project_id": "Island Hub", "yaml": "x: 1"}),
                       ("campus_draft_campus", {"project_id": "Campus Hub"})):
        _refused(tool, args, "project_locked", 409)
        with pytest.raises(HTTPException) as handler:
            getattr(chat_tools, tool)(**args)
        assert handler.value.status_code == 409


def test_readback_after_the_real_call_equals_new(study, island_hub, hub):
    for tool, args in _calls(study, island_hub, hub):
        card = edit_card.preview(tool, args)
        getattr(chat_tools, tool)(**args)
        stored = edit_card.ADAPTERS[tool].readback(args, card["rows"])
        expected = [None if r["role"] == "note" else r["new"] for r in card["rows"]]
        assert stored == pytest.approx(expected) if tool == "gridspine_edit_template_param" else stored == expected, tool


def test_island_words_mirror_the_island_form():
    """`_ISLAND_WORDS` and `_SCENARIO_WORDS` are CampusIslandSection's FIELD_WORDS and the Cuts
    section's scenarioWords, so a card names a field the way the form does."""
    import pathlib
    import re

    from harness import edit_card_campus as ec

    pages = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"
    form = (pages / "CampusIslandSection.tsx").read_text(encoding="utf-8")
    block = form[form.index("const FIELD_WORDS"):]
    block = block[:block.index("\n}\n")]
    words = {}
    for key, text in re.findall(r"(\w+): '([^']*)'", block):
        label, unit = re.fullmatch(r"(.*?)(?: \(([^)]*)\))?", text).groups()
        words[key] = (label, unit or "")
    # "Genset redundancy (N+k)" keeps its parenthesis: it is a name, not a unit.
    words["genset_redundancy_n"] = ("Genset redundancy (N+k)", "")
    assert ec._ISLAND_WORDS == words
    cuts = (pages / "CampusIslandCutsSection.tsx").read_text(encoding="utf-8")
    scen = cuts[cuts.index("const SCENARIO_WORDS"):cuts.index("\n", cuts.index("const SCENARIO_WORDS"))]
    assert ec._SCENARIO_WORDS == dict(re.findall(r"(\w+): '([^']*)'", scen))


# ── a summary that hides a difference: "(changed)" + fingerprint, and a receipt that can fail ──

def _blocks(last):
    config = _config()
    config["requirements"]["pickup_blocks"]["value"] = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, last]
    return config


def test_island_list_with_the_same_summary_is_changed_then_verified(grid, island_hub):
    chat_tools.campus_set_island_config("Island Hub", _blocks(7.0))
    args = {"project_id": "Island Hub", "config": _blocks(8.0)}
    card = edit_card.preview("campus_set_island_config", args)
    (row,) = [r for r in card["rows"] if r["role"] == "typed"]
    assert (row["old"], row["new"]) == ("7 items", "7 items (changed)") and row["fingerprint"]
    assert edit_card.receipt(card, args)["outcome"] == "uncertain"  # nothing written yet
    frames, cards, result = _dispatch(grid, "campus_set_island_config", args)
    _verified(result, grid)


def test_library_text_changed_past_the_cap_is_changed_then_verified(island_hub):
    def library(last):
        lib = _shipped_library()
        lib["transformers"][0]["note"] = "n" * 130 + last
        return yaml.safe_dump(lib, sort_keys=False)

    chat_tools.campus_set_library("Island Hub", library("a"))
    args = {"project_id": "Island Hub", "yaml": library("b")}
    card = edit_card.preview("campus_set_library", args)
    (row,) = [r for r in card["rows"] if r["role"] == "typed"]
    assert row["new"] == "n" * 120 + "\u2026 (changed)" and row["fingerprint"]
    assert edit_card.receipt(card, args)["outcome"] == "uncertain"
    chat_tools.campus_set_library(**args)
    assert edit_card.receipt(card, args)["outcome"] == "verified"


def test_draft_with_the_same_counts_but_a_changed_member_is_fingerprinted(hub):
    chat_tools.campus_draft_campus("Campus Hub")
    path = _storage(hub) / ce.SUBDIR / ce.CAMPUS_FILE
    spec = yaml.safe_load(path.read_text())
    units = spec["campus"]["units"]
    first = next(iter(units))
    units["renamed"] = units.pop(first)  # one unit swapped for another: the count is the same
    path.write_text(yaml.safe_dump(spec, sort_keys=False))
    args = {"project_id": "Campus Hub", "overwrite": True}
    card = edit_card.preview("campus_draft_campus", args)
    row = _rows(card)["units"]
    assert str(row["new"]).endswith(" (changed)") and row["fingerprint"]
    assert edit_card.receipt(card, args)["outcome"] == "uncertain"
    chat_tools.campus_draft_campus("Campus Hub", True)
    assert edit_card.receipt(card, args)["outcome"] == "verified"
