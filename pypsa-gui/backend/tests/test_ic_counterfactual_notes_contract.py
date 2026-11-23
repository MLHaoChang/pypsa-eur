# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Writer → reader contract for the counterfactual notes (IBA-02, GK F1).

The Expert investment view (`financeModel.statedFlags`) leaves
`baseline_island_rows_relaxed` out of its flag list whenever
`counterfactual.notes` are served, because report.py words every case flag
through `baseline_text.flag_text` into those notes. If the report stops
serving that sentence, this test fails instead of the fact silently leaving
the screen."""

from __future__ import annotations

from types import SimpleNamespace

from services.finance import report as R
from services.finance.baseline_text import flag_text

FLAG = "baseline_island_rows_relaxed"


def _block(monkeypatch, flags):
    # The money helpers read a full engine result; the notes do not depend on them.
    for name in ("_avoided_co2", "_incremental_split", "_lost_product"):
        monkeypatch.setattr(R, name, lambda *_: None)
    result = SimpleNamespace(flags=flags, reasons={})
    template = SimpleNamespace(lines=[], first_year=2030)
    case = SimpleNamespace(counterfactual=[template], templates=[template], counterfactual_hash=None)
    return R._counterfactual_block(result, case)


def test_a_report_with_the_island_relaxed_flag_serves_its_sentence_in_the_notes(monkeypatch):
    text = flag_text(FLAG)
    assert text, "the island-relaxed flag must always have words"
    block = _block(monkeypatch, ["counterfactual_basis:site_today", FLAG])
    assert text in block["notes"]
    # The basis sentence is the site-today one without island capability.
    assert block["counterfactual_basis"] == "site_today"


def test_without_the_flag_its_sentence_is_not_in_the_notes(monkeypatch):
    block = _block(monkeypatch, ["counterfactual_basis:site_today"])
    assert flag_text(FLAG) not in block["notes"]
