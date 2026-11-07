# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""Study-harness issue 27: the Library tariffs a run's grant holds.

Only the organisation's own tariffs are held (GK B, Q1 A): a shipped common
tariff is left out by exact ref equality, so an own tariff sharing a shipped
id still counts. A held tariff is named by its ref's `id`, never "None"; a
ref without one grants no tariff (fail closed).
"""

import pytest
from services import chat_tools, run_envelope

H1, H2, H3 = "a" * 64, "b" * 64, "c" * 64


def shipped():
    return run_envelope.shipped_ref_dumps("tariff")


@pytest.fixture
def library(monkeypatch):
    """The Library listing `_library_tariffs` reads: the given own refs plus
    every shipped one, as `items.list_items` returns them."""

    def use(*own):
        monkeypatch.setattr(
            chat_tools, "_library_call", lambda fn, *a, **k: [*own, *shipped()]
        )

    return use


def own(name, digest, version=1):
    return {"kind": "tariff", "id": name, "version": version, "hash": digest}


def entry(tariffs):
    return {
        "tool": "attach_tariff",
        "source": {"kind": "library_tariff"},
        "target": {
            "component": "Settings",
            "name": "Commercial",
            "attr": "import_tariff_ref",
        },
        "at_grant": {"Commercial": {"tariffs": tariffs}},
    }


def card():
    return {
        "complete": True,
        "rows": [
            {
                "component_class": "Settings",
                "name": "Commercial",
                "field": "import_tariff_ref",
                "old": None,
                "new": "nl v1",
                "role": "typed",
            }
        ],
    }


def match(tariffs, digest, monkeypatch):
    monkeypatch.setattr(run_envelope, "_tariff_hash", lambda args: digest)
    envelope = {"edits": [], "runs": [], "gaps": [entry(tariffs)]}
    return run_envelope._match_gap(
        envelope, "attach_tariff", {"name": "nl"}, card(), None, {}
    )


def test_shipped_tariffs_are_never_held(library):
    assert len(shipped()) >= 2  # the case that made the fill unreachable
    library()
    assert run_envelope._library_tariffs() == {}
    assert (
        run_envelope._gap_row(entry({}))
        == "- No tariff is filled: none is held in the Library when you approve."
    )


def test_one_own_tariff_beside_the_shipped_ones_is_admitted_and_named(
    library, monkeypatch
):
    library(own("nl", H1))
    held = run_envelope._library_tariffs()
    assert held == {H1: ["nl", 1]}
    assert match(held, H1, monkeypatch) is None
    assert "from the Library tariff nl v1" in run_envelope._gap_row(entry(held))


def test_two_own_tariffs_are_the_users_choice_named_by_id(library, monkeypatch):
    library(own("nl", H1), own("other", H2))
    held = run_envelope._library_tariffs()
    assert held == {H1: ["nl", 1], H2: ["other", 1]}
    assert match(held, H1, monkeypatch) == "not_granted_source"
    row = run_envelope._gap_row(entry(held))
    assert (
        row
        == "- No tariff is filled: nl v1 and other v1 are both held, so you choose one yourself."
    )
    assert "None" not in row


@pytest.mark.parametrize("change", [{"hash": H3}, {"version": 2}])
def test_an_own_tariff_sharing_a_shipped_id_still_counts(library, change):
    """R1: shipped is told apart by the whole ref, not the id alone."""
    twin = {**shipped()[0], **change}
    library(twin)
    assert run_envelope._library_tariffs() == {
        twin["hash"]: [twin["id"], twin["version"]]
    }


@pytest.mark.parametrize("missing", [{"id": None}, {"id": ""}])
def test_a_held_ref_without_an_id_grants_no_tariff(
    library, monkeypatch, caplog, missing
):
    """R2: fail closed; the grant records no tariff, the fill is refused, and no row reads "None"."""
    library({**own("nl", H1), **missing})
    with pytest.raises(run_envelope.HeldTariffWithoutId):
        run_envelope._library_tariffs()
    gap = {k: v for k, v in entry({}).items() if k != "at_grant"}
    with caplog.at_level("WARNING", logger=run_envelope.logger.name):
        assert run_envelope._gap_instances(None, gap) == {}
    # the typed warning names the bad ref by hash and version, never "None"
    (logged,) = [r.getMessage() for r in caplog.records if "no id" in r.getMessage()]
    assert H1 in logged and "version 1" in logged and "None" not in logged
    assert "None" not in run_envelope._gap_row({**gap, "at_grant": {}})
    monkeypatch.setattr(run_envelope, "_tariff_hash", lambda args: H1)
    envelope = {"edits": [], "runs": [], "gaps": [{**gap, "at_grant": {}}]}
    refused = run_envelope._match_gap(
        envelope, "attach_tariff", {"name": "nl"}, card(), None, {}
    )
    assert refused == "new_instance", refused  # nothing granted: refused


# SH-26: the plan card names the held tariffs (`describe` -> gaps[].held).
def plan(**extra):
    gap = {k: v for k, v in entry({}).items() if k != "at_grant"}
    return {"gaps": [{**gap, "at_grant": {}, **extra}], "edits": [], "runs": [], "audit": [],
            "id": "e", "state": "proposed", "expires_at": None, "notify": {},
            "budget": {"solves": 1, "rounds": 1, "wall_time_h": 1.0}, "research": {"allowed": False}}


def test_the_plan_names_the_held_tariffs(library):
    library(own("nl", H1), own("other", H2, 3))
    gap = run_envelope.describe(plan())["gaps"][0]
    assert gap["held_state"] == "held"
    assert gap["held"] == [{"name": "nl", "version": 1, "hash": H1}, {"name": "other", "version": 3, "hash": H2}]
    assert "at_grant" not in gap


def test_the_plan_says_none_held(library):
    library()
    gap = run_envelope.describe(plan())["gaps"][0]
    assert (gap["held"], gap["held_state"]) == ([], "none")


def test_an_unreadable_library_is_not_none_held(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no acting context")

    monkeypatch.setattr(chat_tools, "_library_call", boom)
    gap = run_envelope.describe(plan())["gaps"][0]
    assert (gap["held"], gap["held_state"]) == (None, "unreadable")


def test_a_held_tariff_without_an_id_is_unnamed(library):
    library({**own("nl", H1), "id": None})
    gap = run_envelope.describe(plan())["gaps"][0]
    assert (gap["held"], gap["held_state"]) == (None, "unnamed")


def test_a_granted_envelope_carries_no_held(library):
    library(own("nl", H1))
    gap = run_envelope.describe(plan(at_grant={"Commercial": {"tariffs": {H1: ["nl", 1]}}}))["gaps"][0]
    assert "held" not in gap and "held_state" not in gap


def test_another_gap_source_carries_no_held(library):
    library(own("nl", H1))
    env = plan()
    env["gaps"][0]["source"] = {"kind": "weather_choices", "choices": []}
    assert "held" not in run_envelope.describe(env)["gaps"][0]
