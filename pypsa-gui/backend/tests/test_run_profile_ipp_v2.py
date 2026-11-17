# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 14 S-B2c (GK B (60) Q2 = A, P4, P6): eh_ipp_hybrid@2 explores
with the template's shipped cases and no proposal turn; its budget is the solve,
two L2 rounds and two fork solves. eh_ipp_hybrid@1 stays byte-equal (L8)."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from harness import run_explore, run_shipped_engine as E
from services import run_actions, run_profiles as R

V1, V2 = "eh_ipp_hybrid@1", "eh_ipp_hybrid@2"
# sha256 of eh_ipp_hybrid@1 as master 755cbe3b builds it.
V1_SHA = "849693e2af1aaef3475546c099f627b584957e05a5d058b5cecd54d6701fcc62"


def _sha(profile):
    return hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()


def test_v1_is_byte_equal_and_still_startable():
    assert _sha(R.PROFILES[V1]) == V1_SHA
    assert "skip" in R.resolve(V1)["stages"]["explore"]
    assert R.resolve(V1)["envelope"]["budget"]["solves"] == 3


def test_v2_differs_from_v1_only_in_the_explore_stage_the_forks_and_the_budget():
    one, two = copy.deepcopy(R.PROFILES[V1]), copy.deepcopy(R.PROFILES[V2])
    assert two.pop("version") == 2 and one.pop("version") == 1
    assert two.pop("shipped") == {"forks": 2}
    explore = two["stages"].pop("explore")
    one["stages"].pop("explore")
    assert explore["actions"] == [
        {
            "tool": run_actions.SHIPPED,
            "args": run_actions.MARKERS[run_actions.SHIPPED_KIND],
            "native": run_actions.KIND,
        }
    ]
    assert [r["id"] for r in explore["rules"]] == ["ex.cases.settled"]
    assert two["envelope"]["budget"].pop("solves") == 5
    one["envelope"]["budget"].pop("solves")
    assert one == two


def test_the_budget_is_the_solve_two_rounds_and_two_forks():
    profile = R.resolve(V2)
    budget = profile["envelope"]["budget"]
    assert budget["rounds"] == 2 and profile["shipped"]["forks"] == 2
    assert budget["solves"] == 1 + budget["rounds"] + profile["shipped"]["forks"] == 5
    assert E.forks(profile) == 2


def test_v2_explores_with_no_proposal_turn():
    profile = R.resolve(V2)
    assert "explore" not in profile and "agent" not in profile["stages"]["explore"]
    task = {
        "run": {
            "profile": V2,
            "stage": "explore",
            "status": "running",
            "stages": {"explore": {"status": "active"}},
        }
    }
    assert run_explore.due(task) is False  # no agent turn is due
    steps = R.materialise(profile, "explore", "p1")
    assert [s["tool"] for s in steps] == [run_actions.SHIPPED]
    assert run_actions.needs_card(steps[0])


def _v2(**stage):
    profile = copy.deepcopy(R.PROFILES[V2])
    profile["stages"]["explore"].update(stage)
    return profile


def test_an_explore_stage_with_neither_a_turn_nor_shipped_cases_refuses():
    with pytest.raises(R.ProfileError, match="explore block"):
        R.load(_v2(actions=[]))


def test_shipped_cases_without_the_settled_rule_refuse():
    with pytest.raises(R.ProfileError, match="ex.cases.settled"):
        R.load(_v2(rules=[]))


@pytest.mark.parametrize("forks", [None, 0, R.EXPLORE_CASES + 1, "2", True, 2.0])
def test_shipped_cases_need_a_whole_fork_allowance(forks):
    profile = copy.deepcopy(R.PROFILES[V2])
    if forks is None:
        profile.pop("shipped")
    else:
        profile["shipped"] = {"forks": forks}
    with pytest.raises(R.ProfileError, match="forks"):
        R.load(profile)


def test_the_largest_fork_allowance_loads():
    profile = copy.deepcopy(R.PROFILES[V2])
    profile["shipped"] = {"forks": R.EXPLORE_CASES}
    assert R.load(profile)["shipped"]["forks"] == R.EXPLORE_CASES


def test_shipped_cases_with_a_proposal_turn_need_the_explore_block():
    # P6: only the no-turn shape skips the levers block; each part has its own case.
    agent = {"tools": list(R.EXPLORE_TOOLS), "rounds": 1, "changes": 2, "cases": 4}
    with pytest.raises(R.ProfileError, match="explore block"):
        R.load(_v2(agent=agent))
