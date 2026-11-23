# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Template-e2e issue 22, slice S2 (GK B3 F1): the investment case's "needs"
checklist is keyed on the codes a run really refuses with. The frontend table
(`investmentCaseNeeds.json`) names each code family once, either with the input
it needs or as `generic`; this reads every code the start route, the runner and
the case builders raise and checks the two sets are equal, so a new code fails
here until it is given its words."""

from __future__ import annotations

import json
import re
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
NEEDS = (
    _BACKEND.parent / "frontend/src/pages/results/investment/investmentCaseNeeds.json"
)

#: Where a run of the investment case refuses, and the call each refusal is.
_SOURCES = {
    "services/finance/investment_case_runner.py": r'(?:_422\(|error_code=)\s*"([a-z_0-9]+)',
    "services/finance/case.py": r'FinanceRefused\(\s*f?"([a-z_0-9]+)',
    "services/finance/engine.py": r'FinanceRefused\(\s*f?"([a-z_0-9]+)',
    "services/finance/timeline.py": r'FinanceRefused\(\s*f?"([a-z_0-9]+)',
    "services/finance/replacements.py": r'FinanceRefused\(\s*f?"([a-z_0-9]+)',
    "services/results/finance_case.py": r'FinanceRefused\(\s*f?"([a-z_0-9]+)',
    "services/results/baseline.py": r'BaselineRefused\(\s*f?"([a-z_0-9]+)',
    "routers/results.py": r'(?:FinanceRefused\(|CampusSource\.refused\()\s*"([a-z_0-9]+)',
}


def _backend_codes() -> set[str]:
    codes: set[str] = set()
    for rel, pattern in _SOURCES.items():
        found = set(re.findall(pattern, (_BACKEND / rel).read_text()))
        assert found, f"{rel}: no refusal code found; has the pattern moved?"
        codes |= found
    return codes | set(re.findall(r'409, \{"code": "([a-z_0-9]+)', _start_route()))


def _start_route() -> str:
    """The investment-case start route's source: its 409s (the mesh's busy
    refusal is a plain sentence, so only coded ones count)."""
    src = (_BACKEND / "routers/results.py").read_text()
    start = src.index("\ndef post_investment_case(")
    return src[start : src.index("\n@", start)]


def _table() -> dict[str, list[str]]:
    raw = json.loads(NEEDS.read_text())
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def test_every_code_is_named_once():
    table = _table()
    assert set(table) == {"solve", "connection", "cod", "finance", "generic"}
    flat = [c for codes in table.values() for c in codes]
    assert len(flat) == len(set(flat)), "a code is listed under two needs"


def test_the_table_matches_the_backend():
    named = {c for codes in _table().values() for c in codes}
    backend = _backend_codes()
    assert backend - named == set(), "codes with no words in investmentCaseNeeds.json"
    assert named - backend == set(), "codes the backend no longer raises"


def test_the_start_route_refuses_unsolved_as_not_solved():
    assert re.findall(r'409, \{"code": "([a-z_0-9]+)', _start_route()) == ["not_solved"]


def _fn_source(rel: str, name: str) -> str:
    src = (_BACKEND / rel).read_text()
    start = src.index(f"\ndef {name}(")
    end = src.find("\ndef ", start + 1)
    return src[start : end if end != -1 else len(src)]


def _needs_codes() -> set[str]:
    """Every code the start can send in `detail.needs` (issue 40): the
    refusals' own `needs=["<code>", *needs]`, the 409's `"needs": ["<code>"`,
    and what `finance_needs` and `connection_need` return."""
    runner = (_BACKEND / "services/finance/investment_case_runner.py").read_text()
    codes = set(re.findall(r'"needs": \["([a-z_0-9]+)", \*needs\]', runner))
    codes |= set(re.findall(r'needs=\["([a-z_0-9]+)", \*needs\]', runner))
    codes |= set(re.findall(r'"needs": \["([a-z_0-9]+)"', _start_route()))
    codes |= set(
        re.findall(
            r'return \["([a-z_0-9]+)"\]',
            _fn_source("services/finance/investment_case_runner.py", "finance_needs"),
        )
    )
    codes |= set(
        re.findall(
            r'return "([a-z_0-9]+)"',
            _fn_source("services/results/finance_case.py", "connection_need"),
        )
    )
    return codes


def test_every_listed_need_has_a_plain_fix():
    """`detail.needs` names only codes the table gives a need (never `generic`):
    the run panel words each one as a checklist line."""
    table = _table()
    with_fix = {c for k, codes in table.items() if k != "generic" for c in codes}
    codes = _needs_codes()
    assert {
        "not_solved",
        "value_flows_not_configured",
        "value_flows_invalid",
        "finance_inputs_missing",
        "finance_inputs_invalid",
        "tax_pack_not_found",
    } <= codes
    assert codes - with_fix == set(), "a need the run panel has no words for"
