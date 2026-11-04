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
from services.library import common

H1, H2, H3 = "a" * 64, "b" * 64, "c" * 64


def shipped():
    return [
        common.ref("tariff", name).model_dump(mode="json")
        for name in common.names("tariff")
    ]


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
def test_a_held_ref_without_an_id_grants_no_tariff(library, monkeypatch, missing):
    """R2: fail closed; the grant records no tariff, the fill is refused, and no row reads "None"."""
    library({**own("nl", H1), **missing})
    with pytest.raises(ValueError):
        run_envelope._library_tariffs()
    gap = {k: v for k, v in entry({}).items() if k != "at_grant"}
    assert run_envelope._gap_instances(None, gap) == {}
    assert "None" not in run_envelope._gap_row({**gap, "at_grant": {}})
    monkeypatch.setattr(run_envelope, "_tariff_hash", lambda args: H1)
    envelope = {"edits": [], "runs": [], "gaps": [{**gap, "at_grant": {}}]}
    refused = run_envelope._match_gap(
        envelope, "attach_tariff", {"name": "nl"}, card(), None, {}
    )
    assert refused is not None, refused  # nothing granted: refused
