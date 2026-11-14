# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness issue 44: the gates table shows words, never raw rule ids."""

from harness.gate_words import RULE_WORDS, rule_words
from services import run_profiles
from services.reports import harness_chapter


def _profile_rule_ids() -> set[str]:
    return {
        r["id"]
        for p in run_profiles.PROFILES.values()
        for s in p["stages"].values()
        for r in ((s.get("gate") or s).get("rules") or [])
    }


def test_every_profile_rule_has_words():
    assert _profile_rule_ids() - set(RULE_WORDS) == set()


def test_no_word_is_a_raw_id():
    for rid, word in RULE_WORDS.items():
        assert word and rid not in word and "." not in word and "_" not in word


def test_unknown_id_falls_back_visibly():
    assert rule_words("zz.new.rule") == "unnamed check (zz.new.rule)"


def test_gates_table_cell_uses_words():
    run = {"stages": {"result_gate": {"status": "passed", "gate": {"rules": [
        {"rule": "mg.fuel.autonomy", "verdict": "met", "facts": [{"fact": "f", "value": 1}]},
        {"rule": "zz.new.rule", "verdict": "met", "facts": [{"fact": "f", "value": 1}]},
    ]}}}}
    cells = [row[2] for row in harness_chapter.gates_rows(run)]
    assert cells == ["Fuel autonomy", "unnamed check (zz.new.rule)"]
