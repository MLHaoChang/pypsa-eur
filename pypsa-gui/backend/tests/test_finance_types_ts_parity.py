"""
`frontend/src/api/types.ts` mirrors the finance contracts (IC P4 WP4.0,
review round 1 #17 — there was no such parity test): every field of the
pydantic model is in the TS interface with the same optionality (a field with
a default is optional in TS; a nullable field admits `null`), and TS carries
no field the model lacks.
"""
from __future__ import annotations

import pathlib
import re
import typing

import pytest

from models.finance import (
    CashflowLine, DebtTranche, EligibilityRule, FinanceInputs, Incentive, Provenance, SolvePpa,
    TerminalValueRule,
)

TYPES_TS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "types.ts"

PAIRS = [(FinanceInputs, "FinanceInputs"), (DebtTranche, "DebtTranche"),
         (EligibilityRule, "EligibilityRule"), (Incentive, "Incentive"), (SolvePpa, "SolvePpa"),
         (TerminalValueRule, "TerminalValueRule"), (CashflowLine, "CashflowLine"),
         (Provenance, "CashflowProvenance")]


def _ts_fields(name: str) -> dict[str, tuple[bool, str]]:
    text = TYPES_TS.read_text()
    m = re.search(r"export interface " + name + r" \{\n(.*?)\n\}", text, re.S)
    assert m, f"interface {name} missing from types.ts"
    out = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        fm = re.match(r"(\w+)(\??):\s*(.+)$", line)
        if fm:
            out[fm.group(1)] = (fm.group(2) == "?", fm.group(3))
    return out


def _nullable(ann) -> bool:
    return type(None) in typing.get_args(ann)


@pytest.mark.parametrize("model,ts", PAIRS, ids=[t for _, t in PAIRS])
def test_the_ts_interface_mirrors_the_model(model, ts):
    fields = _ts_fields(ts)
    assert set(fields) == set(model.model_fields), (
        f"{ts}: TS only {sorted(set(fields) - set(model.model_fields))}, "
        f"model only {sorted(set(model.model_fields) - set(fields))}")
    for name, f in model.model_fields.items():
        optional, ts_type = fields[name]
        assert optional == (not f.is_required()), f"{ts}.{name}: optionality"
        if _nullable(f.annotation):
            assert "null" in ts_type, f"{ts}.{name}: nullable in the model, not in TS"


@pytest.mark.parametrize("model,ts", PAIRS, ids=[t for _, t in PAIRS])
def test_a_ts_nullable_field_is_nullable_in_the_model(model, ts):
    """The reverse direction (WP4.0 review R1): TS never admits `null` where
    the model refuses it."""
    for name, (_, ts_type) in _ts_fields(ts).items():
        if re.search(r"\bnull\b", ts_type):
            assert _nullable(model.model_fields[name].annotation), f"{ts}.{name}: null in TS only"


def _ts_union(name: str) -> set[str]:
    text = TYPES_TS.read_text()
    m = re.search(r"export type " + name + r" =(.*?)(?=\nexport |\n\n)", text, re.S)
    assert m, f"type {name} missing from types.ts"
    body = m.group(1)
    members = set(re.findall(r"'(\w+)'", body))
    for ref in re.findall(r"(?<!')\b([A-Z]\w+)\b(?!')", body):
        members |= _ts_union(ref)
    return members


def test_the_literal_unions_match():
    """`ValueStreamKind`, `CashflowStream` and `EscalationClass` carry the
    model's literal sets exactly (WP4.0 review R1)."""
    from models.commercial import ValueStreamKind
    from models.finance import ESCALATION_CLASSES, CashflowStream

    def lits(t) -> set[str]:
        out: set[str] = set()
        for a in typing.get_args(t):
            out |= lits(a) if typing.get_args(a) else {a}
        return out

    assert _ts_union("ValueStreamKind") == lits(ValueStreamKind)
    assert _ts_union("CashflowStream") == lits(CashflowStream)
    assert _ts_union("EscalationClass") == set(ESCALATION_CLASSES)
