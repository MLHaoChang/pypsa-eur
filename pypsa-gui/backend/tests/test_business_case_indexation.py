# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""Business-case indexation (bc-23): a fraction per year, refused outside -20..+30 %/yr,
warned beyond +-10 %/yr on chat cards. 2 typed for 2 % must not become 200 %/yr."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from models import business_case as B
from services.business_case import chat_edit
from tests.test_business_case_inputs import _case, _refusals, _rev


def _kinds_for(rate):
    return [
        k
        for k in _refusals(_case(_rev(indexation=rate)))
        if k[0] == "indexation_out_of_range"
    ]


@pytest.mark.parametrize("rate", [-0.2, -0.05, 0.0, 0.02, 0.1, 0.3])
def test_a_rate_inside_the_hard_band_is_not_refused(rate):
    assert _kinds_for(rate) == []


@pytest.mark.parametrize("rate", [-0.2001, 0.3001, 2.0, -5.0])
def test_a_rate_outside_the_hard_band_is_refused_on_its_cell(rate):
    assert _kinds_for(rate) == [("indexation_out_of_range", "lines[0].indexation")]


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_a_non_finite_rate_never_parses(bad):
    with pytest.raises(ValidationError):
        B.BusinessCaseInputs(lines=[_rev(indexation=bad)])


def test_a_stated_zero_stays_zero_and_empty_stays_empty():
    assert _case(_rev(indexation=0.0)).lines[0].indexation == 0.0
    assert _case(_rev(indexation=None)).lines[0].indexation is None


def test_a_saved_line_outside_the_band_loads_and_refuses_on_run_only():
    case = _case(
        _rev(indexation=2.0)
    )  # stored before the band existed: loads, no crash
    assert case.lines[0].indexation == 2.0
    assert ("indexation_out_of_range", "lines[0].indexation") in _refusals(case)
    with pytest.raises(B.IndexationOutOfRange):
        case.require_runnable(horizon=(2027, 2046), base_year=2026)


def test_a_stored_fraction_is_unchanged():
    """Only the display moved: 0.02 round-trips through the model as 0.02."""
    dumped = _case(_rev(indexation=0.02)).model_dump(mode="json")
    assert dumped["lines"][0]["indexation"] == 0.02


@pytest.mark.parametrize(
    "v, unusual",
    [
        (0.1, False),
        (-0.1, False),
        (0.1001, True),
        (-0.1001, True),
        (0.02, False),
        (None, False),
    ],
)
def test_the_soft_band_is_beyond_ten_percent(v, unusual):
    assert B.indexation_unusual(v) is unusual


def _card_rows(old_rate, new_rate):
    old = {"inputs": {"lines": [_rev(indexation=old_rate)]}}
    new = {"inputs": {"lines": [_rev(indexation=new_rate)]}}
    return [
        r
        for r in chat_edit.rows(old, new, [], None)
        if r["path"][-1:] == ["indexation"]
    ]


def test_a_chat_card_row_beyond_the_soft_band_warns_and_names_its_unit():
    (row,) = _card_rows(0.02, 0.15)
    assert row["warn"] is True and "unusually high" in row["reason"]
    assert row["unit"] == "fraction per year"


def test_a_chat_card_row_inside_the_soft_band_does_not_warn():
    (row,) = _card_rows(0.02, 0.05)
    assert "warn" not in row and row["unit"] == "fraction per year"


def test_an_empty_indexation_is_not_range_checked():
    assert _kinds_for(None) == []


def test_only_an_indexation_row_gets_the_soft_band_warning():
    row = {"path": ["lines", 0, "capex"], "old": 1.0, "new": 5.0}
    chat_edit.indexation_warning(row)
    assert "warn" not in row
