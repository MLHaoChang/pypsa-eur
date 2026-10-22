"""Applying the recommended GFM settings through the edit card (W10 R3, LC-04): one card for
the Control › Grid-forming tab and the copilot's ``campus_apply_gfm_settings``.

The card's digest binds the recommendation (its time, and each unit's current → recommended
values), the apply is refused when it moved, and the refusals are the card's existing kinds.
"""
import json
import os
import time

import pytest
from fastapi import HTTPException

from harness import edit_card
from services import campus_electrical_service as ce
from services import campus_island_tuning as cit
from services import chat_tools, project_registry
from tests.test_campus_island_tuning_apply import (  # noqa: F401
    _recommend, _sidecar_of, _uid, drafted, user_and_db,
)

TOOL = "campus_apply_gfm_settings"
_CARD = "/api/campus-electrical/Tune Hub/island/gfm-settings/card"
_APPLY = "/api/campus-electrical/Tune Hub/island/gfm-settings/apply"


def _card(project):
    with edit_card.for_project(project):
        return edit_card.preview(TOOL, {}, with_state=False)


def test_the_card_shows_each_changed_setting_old_to_new_and_what_follows(drafted):  # noqa: F811
    _recommend(drafted)
    before = _sidecar_of(drafted)
    card = _card(drafted)
    typed = [r for r in card["rows"] if r["role"] == "typed"]
    assert {(r["name"], r["field"], r["old"], r["new"], r["unit"]) for r in typed} == {
        (_uid(drafted), "droop_pct", 4.0, 2.5, "%"), (_uid(drafted), "tau_f_s", 0.1, 0.6, "s")}
    text = " ".join(r["note"] for r in card["rows"] if r["role"] == "note")
    assert "heuristic screening" in text and "assumed" in text
    assert "island study" in text and "discarded" in text and "re-solved" in text
    assert card["section"]["id"] == "assets" and card["complete"] is True and card["kind"] == "settings_edit"
    assert _sidecar_of(drafted) == before              # a card writes nothing


def test_with_the_island_rows_off_the_card_says_the_hub_solve_does_not_read_them(drafted):  # noqa: F811
    (project_registry.project_dir(drafted) / "solver_config.json").write_text(json.dumps({}))
    _recommend(drafted)
    text = " ".join(r["note"] for r in _card(drafted)["rows"] if r["role"] == "note")
    assert "off" in text and "re-solved" not in text


@pytest.mark.parametrize("status, kind, code", [
    ("not_achievable", "nothing_to_change", 422), ("unchanged", "nothing_to_change", 422), ("incomplete", "nothing_to_change", 422)])
def test_a_recommendation_with_nothing_to_apply_is_the_cards_nothing_to_change(drafted, status, kind, code):  # noqa: F811
    _recommend(drafted, status=status)
    with pytest.raises(HTTPException) as exc:
        _card(drafted)
    assert exc.value.status_code == code and exc.value.detail["error_kind"] == kind


def test_no_or_a_stale_recommendation_is_edit_preview_stale(drafted):  # noqa: F811
    with pytest.raises(HTTPException) as exc:
        _card(drafted)
    assert exc.value.status_code == 409 and exc.value.detail["error_kind"] == "edit_preview_stale"


def test_an_invalid_recommended_value_is_invalid_edit_value(drafted):  # noqa: F811
    _recommend(drafted, recommended={"droop_pct": -1.0, "tau_f_s": 0.6, "i_max_pu": 1.2, "H_equivalent_s": 1.2})
    with pytest.raises(HTTPException) as exc:
        _card(drafted)
    assert exc.value.status_code == 422 and exc.value.detail["error_kind"] == "invalid_edit_value"


def test_a_fresh_recommendation_is_a_different_card(drafted):  # noqa: F811
    _recommend(drafted)
    first = _card(drafted)["digest"]
    _recommend(drafted, recommended={"droop_pct": 3.0, "tau_f_s": 0.6, "i_max_pu": 1.2, "H_equivalent_s": 1.2})
    assert _card(drafted)["digest"] != first
    # the same values recommended again, later: the time is bound too
    again = _card(drafted)["digest"]
    path = ce.campus_dir(drafted) / "run" / ce.cs.GFM_SETTINGS_JSON
    later = time.time() + 60
    os.utime(path, (later, later))
    ev = ce.campus_dir(drafted) / "run" / ce.cs.FREQ_EVENTS_CSV
    os.utime(ev, (later - 5, later - 5))
    assert _card(drafted)["digest"] != again


def test_the_receipt_reads_both_files_back(drafted):  # noqa: F811
    _recommend(drafted)
    card = _card(drafted)
    cit.apply_settings(drafted)
    with edit_card.for_project(drafted):
        receipt = edit_card.receipt(card, {})
    assert receipt["outcome"] == "verified"
    assert {(r["field"], r["readback"]) for r in receipt["rows"] if r["role"] == "typed"} == {
        ("droop_pct", 2.5), ("tau_f_s", 0.6)}


def test_the_settings_say_when_the_recommendation_was_made(drafted):  # noqa: F811
    assert cit.get_settings(drafted)["recommended_at"] is None
    _recommend(drafted)
    at = cit.get_settings(drafted)["recommended_at"]
    assert at.endswith("+00:00") and len(at) == 25


def test_a_value_the_files_disagree_on_is_not_verified(drafted):  # noqa: F811
    _recommend(drafted)
    card = _card(drafted)
    cit.apply_settings(drafted)
    import yaml
    path = ce.campus_dir(drafted) / ce.CAMPUS_FILE
    spec = yaml.safe_load(path.read_text())
    spec["campus"]["units"][_uid(drafted)]["island"]["droop_pct"]["value"] = 9.0
    path.write_text(yaml.safe_dump(spec, sort_keys=False))
    with edit_card.for_project(drafted):
        assert edit_card.receipt(card, {})["outcome"] == "uncertain"


# ── the route (the real signature, no monkeypatched service) ────────────────


def test_the_card_route_returns_the_card_and_writes_nothing(client, drafted):  # noqa: F811
    _recommend(drafted)
    before = _sidecar_of(drafted)
    resp = client.get(_CARD)
    assert resp.status_code == 200, resp.text
    assert resp.json()["digest"] == _card(drafted)["digest"]
    assert _sidecar_of(drafted) == before


def test_the_apply_route_with_the_approved_digest_writes_and_returns_the_receipt(client, drafted):  # noqa: F811
    _recommend(drafted)
    digest = client.get(_CARD).json()["digest"]
    resp = client.post(_APPLY, json={"digest": digest})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["hub_resolve_needed"] is True and body["receipt"]["outcome"] == "verified"
    assert _sidecar_of(drafted)["units"]["bess"]["droop_pct"]["value"] == 2.5


def test_the_apply_route_refuses_a_stale_digest_and_writes_nothing(client, drafted):  # noqa: F811
    _recommend(drafted)
    digest = client.get(_CARD).json()["digest"]
    _recommend(drafted, recommended={"droop_pct": 3.0, "tau_f_s": 0.6, "i_max_pu": 1.2, "H_equivalent_s": 1.2})
    before = _sidecar_of(drafted)
    resp = client.post(_APPLY, json={"digest": digest})
    assert resp.status_code == 409
    assert resp.json()["detail"]["error_kind"] == "edit_preview_stale"
    assert _sidecar_of(drafted) == before


def test_the_bare_apply_route_still_works(client, drafted):  # noqa: F811
    _recommend(drafted)
    resp = client.post(_APPLY)
    assert resp.status_code == 200 and "receipt" not in resp.json()


# ── the copilot's path ──────────────────────────────────────────────────────


def test_the_copilot_tool_has_this_card_and_rechecks_it_before_the_write(drafted, monkeypatch):  # noqa: F811
    """The loop previews, asks and rechecks through the adapter; the handler rechecks the approved
    digest once more right before the write, so a moved recommendation never applies."""
    assert TOOL in edit_card.ADAPTERS and edit_card.is_edit(TOOL)
    _recommend(drafted)
    card = _card(drafted)
    monkeypatch.setattr(edit_card, "preview", lambda tool, args, **kw: {"digest": "moved"})
    monkeypatch.setattr(chat_tools, "_acting", lambda: pytest.fail("the write was reached"))
    with edit_card.approved(card["digest"]):
        with pytest.raises(HTTPException) as exc:
            chat_tools.campus_apply_gfm_settings(str(drafted.id))
    assert exc.value.detail["error_kind"] == "edit_preview_stale"
