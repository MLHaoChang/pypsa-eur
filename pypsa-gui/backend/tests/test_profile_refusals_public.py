"""What a profile refusal may tell its caller (CodeQL follow-up to #466).

* A typed refusal says the sentence it was raised with, byte for byte, on the
  route and in the chat.
* Anything else, and a typed refusal raised with no sentence, says only its
  type: never its text, which goes to the server log with the route and the
  job id.
* The ERA5 downloader's own text (it can carry the request URL) stays in the
  log.
"""
from __future__ import annotations

import logging

import pytest
from fastapi import HTTPException

from services import chat_service, chat_tools
from services.profile_gen import cutout_cache, engine, presets
from services.profile_gen.refusals import public_message
from tests.test_profiles_load_routes import _generate, load_project  # noqa: F401 — fixture

_FOREIGN = "CDSAPI_KEY=abc123 at /srv/secret/path"


def _subclasses(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from _subclasses(sub)


def test_every_typed_refusal_says_its_own_sentence():
    import services.profile_gen.apply  # noqa: F401 — register the subclasses
    import services.profile_gen.load_upload  # noqa: F401
    import services.profile_gen.shape  # noqa: F401

    for cls in {engine.ProfileGenError, *_subclasses(engine.ProfileGenError)}:
        kw = {"reachable_max": 1.0} if cls.__name__ == "UnreachableTargetError" else {}
        exc = cls("the sentence we wrote", **kw)
        assert public_message(exc, route="t") == str(exc) == "the sentence we wrote", cls


def test_a_typed_refusal_on_a_route_is_byte_identical(client):
    with pytest.raises(engine.ProfileGenError) as raised:
        presets.resolve_params("no-such-preset", "wind")
    resp = client.post("/api/profiles/estimate", json={
        "points": [{"id": "p", "lon": 8.0, "lat": 50.0}], "technology": "wind",
        "preset": "no-such-preset", "year": 2023})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["message"] == str(raised.value)


def test_a_typed_refusal_with_no_sentence_says_only_its_type(client, monkeypatch):
    def refuse(technology=None):
        raise presets.UnknownPresetError()

    monkeypatch.setattr(presets, "list_presets", refuse)
    resp = client.get("/api/profiles/presets")
    assert resp.json()["detail"] == "unexpected error (UnknownPresetError); see the server log"


def test_a_foreign_exception_reaches_the_log_not_the_reply(client, monkeypatch, caplog):
    def boom(technology=None):
        raise engine.ProfileGenError() from ValueError(_FOREIGN)

    monkeypatch.setattr(presets, "list_presets", boom)
    with caplog.at_level(logging.ERROR, logger="services.profile_gen.refusals"):
        resp = client.get("/api/profiles/presets", params={"technology": "wind"})
    assert _FOREIGN not in resp.text
    (record,) = [r for r in caplog.records if r.name == "services.profile_gen.refusals"]
    assert record.getMessage() == "profile route presets (job None) failed: ProfileGenError"
    assert record.exc_info is not None


def test_a_corrupt_pending_artifact_says_only_the_readers_type(client, load_project,  # noqa: F811
                                                             monkeypatch):
    import routers.profiles as profiles_router

    job = _generate(client, load_project).json()

    def unreadable(*a, **k):
        raise OSError(_FOREIGN)

    monkeypatch.setattr(profiles_router.xr, "open_dataset", unreadable)
    resp = client.post(f"/api/projects/{load_project}/profiles/apply",
                       json={"job_id": job["job_id"], "component": "plant"})
    assert resp.status_code == 422
    assert resp.json()["detail"] == (
        f"pending profile for job {job['job_id']!r} is unreadable: "
        "unexpected error (OSError); see the server log")


def test_the_chat_tool_says_the_type_and_the_model_sees_no_more(client, monkeypatch, caplog):
    def boom(technology=None):
        raise ValueError(_FOREIGN)

    monkeypatch.setattr(presets, "list_presets", boom)
    with caplog.at_level(logging.ERROR, logger="services.profile_gen.refusals"):
        with pytest.raises(HTTPException) as err:
            chat_tools.DISPATCHERS["list_profile_presets"](technology="wind")
    assert err.value.detail == "unexpected error (ValueError); see the server log"
    content = chat_service._error_result_content(err.value.detail, err.value, "tool_error")
    assert _FOREIGN not in content and "ValueError" in content
    assert any("list_profile_presets" in r.getMessage() for r in caplog.records)


def test_the_chat_tool_keeps_a_typed_sentence(client):
    with pytest.raises(engine.ProfileGenError) as raised:
        presets.get_preset("no-such-preset", "wind")
    with pytest.raises(HTTPException) as err:
        chat_tools.DISPATCHERS["get_profile_parameters"](preset="no-such-preset", technology="wind")
    assert err.value.detail == str(raised.value)


def test_the_downloaders_own_text_stays_in_the_log(caplog):
    def fail():
        raise RuntimeError(_FOREIGN)

    with caplog.at_level(logging.ERROR, logger="services.profile_gen.cutout_cache"):
        with pytest.raises(cutout_cache.CutoutDownloadError) as err:
            cutout_cache._call_with_timeout(fail, 5.0, month=4)
    assert str(err.value) == "ERA5 download failed at month 4 (RuntimeError); see the server log"
    assert any(r.exc_info for r in caplog.records)


def test_get_profile_parameters_says_only_the_type_of_a_foreign_error(client, monkeypatch):
    def broken(name, technology=None):
        raise ValueError(_FOREIGN)

    monkeypatch.setattr(presets, "get_preset", broken)
    with pytest.raises(HTTPException) as err:
        chat_tools.DISPATCHERS["get_profile_parameters"](preset="x", technology="wind")
    assert err.value.detail == "unexpected error (ValueError); see the server log"
    assert _FOREIGN not in str(err.value.detail)


def test_an_unknown_zone_is_named_without_the_librarys_text():
    import pandas as pd

    from services.profile_gen import load_archetypes, load_upload

    idx = pd.date_range("2025-01-01", periods=48, freq="h")
    with pytest.raises(load_archetypes.InvalidArchetypeParamsError) as err:
        load_archetypes._Clock(idx, "Europe", [], 2025)
    assert str(err.value) == "timezone 'Europe' is not an IANA zone"
    with pytest.raises(load_upload.UploadFormatError) as err:
        load_upload._utc_index(pd.Index(idx.strftime("%Y-%m-%d %H:%M")), "Europe", None)
    assert str(err.value) == "timezone 'Europe' is not an IANA zone"
