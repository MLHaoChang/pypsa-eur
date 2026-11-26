import json
import pathlib

import pytest

from services.adequacy.verdict_line import verdict_line

CASES = json.loads(
    (pathlib.Path(__file__).parent / "data" / "verdict_line_cases.json").read_text()
)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["lead"][:40])
def test_lead_matches_the_shared_case(case):
    assert verdict_line(case["summary"]) == case["lead"]


def test_no_summary_has_no_line():
    assert verdict_line(None) is None
