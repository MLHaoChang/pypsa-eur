"""
`build_operating` is pinned byte for byte (IPP ticket 05, gatekeeper): the revenue-by-stream
reader needs each difference line's price and reference legs per year, so the two-leg branch
became a helper the cash calls too. That refactor must change no cash: every line array of
these cases, and the case hash, equals the digest recorded from master before it.

Only fixed cases are pinned: a case from a solve differs in its last bits by machine (and a
degenerate plan by more), so the solved IPP and ammonia cases check their legs instead.

Regenerate (only after an intended cash change, with the gatekeeper):
`OPERATING_PIN_WRITE=1 pytest tests/test_operating_pin.py`.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib

import numpy as np
import pytest

import tests.test_finance_two_leg as TL
from services.finance.case import CONTRACT_CLASS, TemplateLine
from services.finance.cashflow import build_operating
from services.finance.timeline import build_timeline
from services.results.finance_case import finance_case_hash

GOLDEN = pathlib.Path(__file__).parent / "fixtures" / "operating_lines_pin.json"


def lines_digest(case) -> dict:
    """{line key: sha256 of its float64 bytes, or None} and the case hash."""
    op = build_operating(case, build_timeline(case))
    return {"case_hash": finance_case_hash(case),
            "lines": {k: None if v is None else
                      hashlib.sha256(np.ascontiguousarray(v, dtype=np.float64).tobytes())
                      .hexdigest() for k, v in sorted(op.lines.items())}}


def check(name: str, case) -> None:
    got = lines_digest(case)
    pins = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
    if os.environ.get("OPERATING_PIN_WRITE"):
        pins[name] = got
        GOLDEN.write_text(json.dumps(pins, indent=1, sort_keys=True) + "\n")
    assert got == pins[name], name


def _cfd():
    q = TL.SHARE * TL.GEN
    return (TemplateLine("contract:c:cfd_difference", "cfd_settlement", (70.0 - TL.REF) * q,
                         CONTRACT_CLASS, indexation=TL.INDEX, contract_id="c", price=70.0,
                         price_qty_mwh=q),
            TemplateLine("export_price:export_price", "energy_export", TL.REF * TL.EXPORT,
                         "export"))


def _escalating(case):
    """The same case with a moving market (export 3 %/yr) and degradation, so both legs and
    the degradation factor are exercised."""
    fin = case.inputs.model_copy(update={
        "escalation": {**case.inputs.escalation, "export": 0.03, "opex": 0.02},
        "degradation_by_asset": {"pv": 0.005}})
    return case.__class__(**{**case.__dict__, "inputs": fin})


CASES = {
    "physical_ppa": lambda: TL._case(TL._physical()),
    "financial_ppa": lambda: TL._case(TL._financial()),
    "cfd": lambda: TL._case(_cfd()),
    "financial_ppa_escalating": lambda: _escalating(TL._case(TL._financial())),
    "cfd_escalating": lambda: _escalating(TL._case(_cfd())),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_operating_lines_are_unchanged(name):
    check(name, CASES[name]())
