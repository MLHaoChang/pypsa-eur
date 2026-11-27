"""AA issue 28: the checked metrics' /2 profiles carry the ruled figure names beside the frozen /1 ones.

AA-V1-CHANGE-1: a changed metric definition gets a new profile version beside the old one; the /1
definitions never move. These tests pin the v1 labels as literals, the /2 labels to `figure_words`
through an explicit metric -> figure map, and everything but the label to be the same.
"""

import hashlib
import json
import pathlib

import pytest

from harness import calculations, copt_calculations, investment_calculations
from harness import rollout
from services import figure_words as FW

SNAPSHOT = pathlib.Path(calculations.__file__).with_name("assurance_contracts.v1.json")
# v2 profile -> its v1 predecessor
PAIRS = {
    "investment-case/2": "investment-case/1",
    "investment-lcoe/2": "investment-lcoe/1",
    "copt-screening/2": "copt-screening/1",
}
# The frozen /1 labels the chat printed before the ruling.
V1_LABELS = {
    ("investment-case/1", "project_npv"): "Project NPV (post tax, at the WACC)",
    ("investment-lcoe/1", "lcoe_real"): "LCOE, real (engine definition)",
    ("investment-lcoe/1", "lcoe_nominal"): "LCOE, nominal (engine definition)",
    (
        "copt-screening/1",
        "screening_lole",
    ): "Screening LOLE over the modelled horizon (COPT)",
}
# The one explicit map: (v2 profile, metric) -> the ruled figure whose name it carries.
V2_FIGURES = {
    ("investment-case/2", "project_npv"): "project_npv",
    ("investment-lcoe/2", "lcoe_real"): "lcoe_real",
    ("investment-lcoe/2", "lcoe_nominal"): "lcoe_nominal",
    ("copt-screening/2", "screening_lole"): "screening_shortfall",
}
# sha256 of each frozen /1 profile entry of the snapshot, taken on master before AA 28.
V1_PROFILE_SHA256 = {
    "investment-case/1": "e2f5936167fd684bf7f28253b53c518ef3184674bb106ce5bdcf439c6511fb5f",
    "investment-lcoe/1": "1106269dc3e6ee33aa9860a5840113b66f8e71d7ee8c82351a609efe509383cb",
    "copt-screening/1": "95817bc0caf61adfff5dc76c84a48f50659fe34978911e2960a5aa00e8a4f86c",
}


def _digest(entry):
    return hashlib.sha256(
        json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_the_v1_labels_did_not_move():
    for (profile, metric), label in V1_LABELS.items():
        assert calculations.definition(metric, profile)["label"] == label, (
            profile,
            metric,
        )


def test_the_v1_snapshot_entries_are_byte_identical():
    profiles = json.loads(SNAPSHOT.read_text())["profiles"]
    for profile, digest in V1_PROFILE_SHA256.items():
        assert _digest(profiles[profile]) == digest, profile


def test_the_v2_labels_are_the_ruled_figure_names():
    for (profile, metric), figure in V2_FIGURES.items():
        assert calculations.definition(metric, profile)["label"] == FW.name(figure), (
            profile,
            metric,
        )
    # a swap of the two LCOE figures must fail: the real and nominal names differ
    assert FW.name("lcoe_real") != FW.name("lcoe_nominal")
    assert (
        calculations.definition("lcoe_real", "investment-lcoe/2")["label"]
        == "LCOE (real)"
    )
    assert (
        calculations.definition("lcoe_nominal", "investment-lcoe/2")["label"]
        == "LCOE (finance-consistent, nominal)"
    )


@pytest.mark.parametrize("v2,v1", PAIRS.items())
def test_a_v2_profile_differs_from_its_v1_only_in_labels(v2, v1):
    assert (
        calculations.PROFILES[v2] == calculations.PROFILES[v1]
    )  # same metric ids, same order
    assert calculations.PREDECESSORS[v2] == v1
    for metric in calculations.PROFILES[v2]:
        old, new = (
            calculations.definition(metric, v1),
            calculations.definition(metric, v2),
        )
        assert {k: v for k, v in old.items() if k != "label"} == {
            k: v for k, v in new.items() if k != "label"
        }
        if (v2, metric) not in V2_FIGURES:
            assert old["label"] == new["label"], (
                v2,
                metric,
            )  # unruled metrics keep their label


def test_the_snapshot_holds_both_versions_with_their_own_labels():
    profiles = json.loads(SNAPSHOT.read_text())["profiles"]
    for (profile, metric), figure in V2_FIGURES.items():
        assert profiles[profile]["definitions"][metric]["label"] == FW.name(figure)
    for (profile, metric), label in V1_LABELS.items():
        assert profiles[profile]["definitions"][metric]["label"] == label


def test_the_definition_hash_differs_between_versions():
    # the definition hash covers the labels, so a stored /1 answer keeps its own hash and replays as /1
    for v2, v1 in PAIRS.items():
        assert calculations.definition_hash(v1) != calculations.definition_hash(v2)


def test_a_v2_profile_runs_when_its_v1_is_listed(monkeypatch):
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", "investment-case/1")
    assert rollout.profile_enabled("investment-case/2") and rollout.profile_enabled(
        "investment-case/1"
    )
    assert not rollout.profile_enabled("investment-lcoe/2")
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", "")
    assert not rollout.profile_enabled("investment-case/2")


def test_an_unknown_version_is_refused():
    from pydantic import ValidationError

    from harness.assurance_scope import RequestScope

    base = {"version": 1, "project_id": "p", "entity_id": "network", "retain": False}
    for good in (
        "investment-case/2",
        "investment-lcoe/2",
        "copt-screening/2",
        "investment-case/1",
    ):
        RequestScope(**base, profile=good)
    for bad in ("investment-case/3", "copt-screening/0", "investment-lcoe/22"):
        with pytest.raises(ValidationError):
            RequestScope(**base, profile=bad)
    raw = {"version": 1, "profile": "investment-case/3", "entity_id": "network"}
    with pytest.raises(ValueError, match="investment_source_identity"):
        investment_calculations.calculate(
            raw, {"profile": "investment-case/3", "entity_id": "network"}
        )
    with pytest.raises(ValueError, match="copt_source_identity"):
        copt_calculations.calculate(
            {"version": 1, "profile": "copt-screening/3", "entity_id": "network"},
            {"profile": "copt-screening/3", "entity_id": "network"},
        )


# ── GK B (74) conditions on #2077 ───────────────────────────────────────────


def test_the_strip_gate_follows_the_version_chat_emits(monkeypatch):
    from services.results import headline_kpis as hk

    profile = hk.CHECKED["lcoe"][0]
    assert (
        profile == "investment-lcoe/2"
    )  # chat now answers /2, so the card asks for /2 (STRIP-GATE-1)
    for listed, shown in (
        (
            "investment-lcoe/2",
            True,
        ),  # "/2 only": chat answers checked, so the strip shows
        ("investment-lcoe/1", True),  # an operator who listed /1 keeps the card
        ("investment-case/1", False),
        ("", False),
    ):
        monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", listed)
        assert rollout.profile_enabled(profile) is shown, listed


def test_rollout_with_only_the_v2_listed(monkeypatch):
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", "investment-case/2")
    assert rollout.profile_enabled("investment-case/2")
    assert not rollout.profile_enabled(
        "investment-case/1"
    )  # a /2 never enables its predecessor
    assert not rollout.profile_enabled("investment-lcoe/2")


def test_rollout_unset_leaves_every_opt_in_profile_off(monkeypatch):
    monkeypatch.delenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", raising=False)
    for profile in (*PAIRS, *PAIRS.values()):
        assert profile in calculations.OPT_IN_PROFILES
        assert not rollout.profile_enabled(profile), profile
    assert rollout.profile_enabled(
        "generator-dispatch/1"
    )  # a profile that is not opt-in stays on


def test_a_record_of_the_other_version_is_refused():
    inv = {"version": 1, "entity_id": "network"}
    for raw_profile, scope_profile in (
        ("investment-case/1", "investment-case/2"),
        ("investment-case/2", "investment-case/1"),
        ("investment-lcoe/1", "investment-lcoe/2"),
    ):
        with pytest.raises(ValueError, match="investment_source_identity"):
            investment_calculations.calculate(
                {**inv, "profile": raw_profile},
                {"profile": scope_profile, "entity_id": "network"},
            )
    for raw_profile, scope_profile in (
        ("copt-screening/1", "copt-screening/2"),
        ("copt-screening/2", "copt-screening/1"),
    ):
        with pytest.raises(ValueError, match="copt_source_identity"):
            copt_calculations.calculate(
                {**inv, "profile": raw_profile},
                {"profile": scope_profile, "entity_id": "network"},
            )
