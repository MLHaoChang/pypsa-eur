"""
A colleague's email must not reach the model on the SUCCESS path either.

`docs/superpowers/findings/2026-08-27-lock-holder-email-reaches-the-model.md`
was closed on the `is_error` path: `_error_result_content` builds the
model-facing body from a typed kind plus a dict detail's `message`, dropping
`lock`, `holder_email` and anything else.

The SUCCESS path does no filtering at all — `_result_to_anthropic_content` is
a `json.dumps` plus an untrusted-data fence. And two routes return the same
member on success: `routers/projects.py::activate_project` returns
`{"activated", "evicted", "lock": lock_info}` and `load_project` returns
`{**summary, "lock": lock_info}`, where `project_locks.serialize_lock` emits
`{"holder_email": <the other user's address>, "yours": False}`. The chat
wrappers return those payloads unchanged.

So the ordinary path — a non-holder asking chat to open a project someone
else is editing — sent that person's address to the third-party provider,
kept it in `session.messages` to be replayed on every later turn, and let the
model paraphrase it into a reply persisted to `chat.jsonl`.

The model has no use for it: `activate_project`'s own schema describes the
result as `{activated, evicted}`. `yours` is kept, because whether the
project is locked against the caller IS something the model should know.

This is a PII scrub, deliberately not part of `_redact_secrets_in_str`: that
redactor is secrets-only by design and its docstring says bare addresses are
intentionally left alone, because that pattern over-redacts project and
component names. The repair belongs where the payload is built.
"""
from __future__ import annotations

import json

import pytest

from services import chat_service

EMAIL = "alice@example.com"


def _model_facing(result):
    return chat_service._result_to_anthropic_content(result)


def test_holder_email_does_not_reach_the_model(*_):
    body = _model_facing({
        "activated": "P", "evicted": None,
        "lock": {"holder_email": EMAIL, "yours": False},
    })
    assert EMAIL not in body
    assert "holder_email" not in body


def test_the_lock_signal_itself_survives(*_):
    # Scrubbing the identity must not scrub the FACT. The model still needs
    # to know the project is held against this caller.
    body = _model_facing({
        "activated": "P", "lock": {"holder_email": EMAIL, "yours": False},
    })
    assert '"yours": false' in body.lower().replace(" ", " ")
    assert "activated" in body


def test_a_nested_holder_email_is_also_scrubbed():
    # The member travels inside `detail` on some payloads, so the scrub has
    # to be recursive rather than a top-level key check.
    body = _model_facing({
        "ok": False,
        "detail": {"error_kind": "project_locked",
                   "lock": {"holder_email": EMAIL, "yours": False}},
    })
    assert EMAIL not in body


def test_a_holder_email_in_a_list_is_scrubbed():
    body = _model_facing({"locks": [{"holder_email": EMAIL, "yours": False}]})
    assert EMAIL not in body


def test_ordinary_results_are_untouched():
    # The control: the scrub must not disturb any other payload.
    payload = {"buses": 3, "generators": ["gas", "solar"], "name": "study-1"}
    body = _model_facing(payload)
    for fragment in ("buses", "generators", "gas", "solar", "study-1"):
        assert fragment in body


def test_a_plain_string_result_still_works():
    assert "hello" in _model_facing("hello")
