"""
Coverage rule, component columns (spec expert-catalogue §3.5, issue 01a-2).

Every PyPSA input column of the catalogue's classes and every custom column the GUI may
write is a catalogue entry or a `not_offered.py` row with a reason, never both.
"""

import pypsa
import pytest

from models.energy_hub import EH_CUSTOM_COLUMNS
from models.process_unit import PROCESS_CUSTOM_COLUMNS
from services.asset_schema import catalogue as C
from services.asset_schema.fields import known_components
from services.asset_schema.not_offered import NOT_OFFERED, REASON_KINDS
from services.attribute_catalog import input_attributes

ENTRY_COLUMNS = {(e.scope, e.model_name) for e in C.entries()}


def _writable_columns() -> set[tuple[str, str]]:
    n = pypsa.Network()
    out = {(c, a) for c in known_components() for a in input_attributes(n, c)}
    for registry in (EH_CUSTOM_COLUMNS, PROCESS_CUSTOM_COLUMNS):
        out |= {(c, col) for c, cols in registry.items() for col in cols}
    return out


def _not_offered() -> set[tuple[str, str]]:
    return {(c, col) for c, cols in NOT_OFFERED.items() for col in cols}


def test_every_writable_column_is_an_entry_or_not_offered():
    missing = sorted(_writable_columns() - ENTRY_COLUMNS - _not_offered())
    assert not missing, (
        f"columns in neither the catalogue nor not_offered.py: {missing}. Add an entry, "
        "or a not_offered.py row with a one-line reason."
    )


def test_no_column_is_both_an_entry_and_not_offered():
    assert not sorted(ENTRY_COLUMNS & _not_offered())


def test_not_offered_rows_name_real_columns():
    """A stale row (a column PyPSA or the GUI no longer has) is removed, not kept."""
    assert not sorted(_not_offered() - _writable_columns())


@pytest.mark.parametrize("cls,col", sorted(_not_offered()))
def test_every_not_offered_row_has_a_one_line_reason(cls, col):
    reason = NOT_OFFERED[cls][col]
    kind, _, rest = reason.partition(": ")
    assert kind in REASON_KINDS, (cls, col, reason)
    assert rest and "\n" not in reason and len(reason) <= 140, (cls, col, reason)
