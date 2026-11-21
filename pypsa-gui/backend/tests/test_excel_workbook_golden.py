# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""GK B T5 (ticket 04): a valid workbook read back has the golden's input fingerprint."""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

GRIDSPINE_TESTS = pathlib.Path(__file__).resolve().parents[3] / "tests" / "gridspine"
sys.path.insert(0, str(GRIDSPINE_TESTS))

import excel_golden  # noqa: E402


@pytest.mark.parametrize("case", list(excel_golden.cases()))
def test_a_valid_workbook_keeps_the_golden_s_input_fingerprint(tmp_path, case):
    golden = json.loads(excel_golden.GOLDEN.read_text())[case]
    n, sidecars = excel_golden.cases()[case]
    res = excel_golden.read_back(n, sidecars, tmp_path)
    assert golden["fingerprint"] is not None
    assert excel_golden.fingerprint(res.network) == golden["fingerprint"]
