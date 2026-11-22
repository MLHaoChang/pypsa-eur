# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""Walk 2026-11-17 issue 04 (GK B2 plan GO Q1, Q2, B1): a capacity payment linked to an
asset's capacity in service is paid on a typed de-rating of it (spec §6.6, "`capacity:bess`
× de-rating (typed)"). The de-rating is typed, 0..1, never defaulted, and taken by no other
line."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from models import business_case as B
from services.business_case import linked_outputs as L
from services.business_case import presets as P
from services.finance.business_case import _line_blob, build_business_case
from services.finance.engine import run_case
from tests.test_business_case_engine import PACK, _ic, _inputs, _line
from tests.test_business_case_inputs import BASE, H, _case, _refusals

CAP = "linked:capacity.StorageUnit.bess@1"


def _cap(**kw: Any) -> dict:
    return {
        "name": "Capacity payment",
        "kind": "revenue",
        "category": "capacity_payment",
        "unit": "kW_year",
        "quantity_source": CAP,
        "price": 40.0,
        "escalation": "contract",
        "indexation": 0.0,
        **kw,
    }


def test_q1_a_capacity_payment_linked_to_capacity_takes_a_derating_in_0_to_1():
    for d in (0.0, 0.5, 1.0):
        assert B.LineItem(**_cap(derating=d)).derating == d
    for d in (-0.01, 1.01):
        with pytest.raises(ValidationError):
            B.LineItem(**_cap(derating=d))


@pytest.mark.parametrize(
    "over",
    [
        # another revenue category, linked to capacity
        {"category": "service_fee", "unit": "kW_year"},
        # a capacity payment on a typed quantity
        {"quantity_source": "typed", "quantity": 10.0},
        # a capacity payment linked to something other than capacity in service
        {"quantity_source": "linked:it_energy@1", "unit": "MWh"},
    ],
)
def test_q1_every_other_line_refuses_a_derating_at_parse(over):
    with pytest.raises(ValidationError, match="derating"):
        B.LineItem(**_cap(derating=0.5, **over))


def test_q2_an_unset_derating_is_refused_by_name_never_read_as_1():
    case = _case(_cap())
    assert ("derating_missing", "lines[0].derating") in _refusals(case)
    with pytest.raises(B.DeratingMissing):
        case.require_runnable(horizon=H, base_year=BASE)
    assert _refusals(_case(_cap(derating=0.5))) == []
    assert "derating_missing" in B.REFUSAL_KINDS


def test_q2_a_line_that_takes_no_derating_is_never_refused_for_it():
    rev = _cap(category="service_fee")
    assert not [k for k, _ in _refusals(_case(rev)) if k == "derating_missing"]


def test_b1_the_ipp_oracle_pays_capacity_times_derating_once():
    """Walk 04 hand check: 32.24055 MW of BESS (1 289 622 at 40/kW-year, the walk's
    undivided figure) × 0.5 × 1000 × 40 = 644 811 in 2030."""
    kw = 1_289_622 / 40.0
    ln = _line(**_cap(derating=0.5, quantity=None))
    series = {ln.name: L.Series("capacity.StorageUnit.bess", "kW_year", {None: kw}, {})}
    bc = build_business_case(_ic(), _inputs(ln), series)
    r = run_case(bc.case, PACK)
    paid = [v for v in r.op.lines[ln.name] if v]
    assert paid and all(v == pytest.approx(644_811.0, abs=0.5) for v in paid)
    # Applied once: a sold share multiplies alongside it, never instead of it.
    half = _line(**_cap(derating=0.5, linked_share=0.5, quantity=None))
    bc2 = build_business_case(_ic(), _inputs(half), {half.name: series[ln.name]})
    r2 = run_case(bc2.case, PACK)
    paid2 = [v for v in r2.op.lines[half.name] if v]
    assert len(paid2) == len(paid)
    assert all(v == pytest.approx(644_811.0 / 2, abs=0.5) for v in paid2)


def test_b1_a_line_without_a_derating_hashes_as_before():
    """Byte identity: a case with no capacity payment line digests as it did before the
    field existed; a set de-rating enters the digest."""
    other = _line()
    assert "derating" not in _line_blob(other, H)
    assert _line_blob(_line(**_cap(derating=0.5, quantity=None)), H)["derating"] == 0.5


def test_the_ipp_preset_ships_the_derating_empty_and_insurance_empty():
    """Owner decisions: no invented default. The de-rating and the insurance share are
    the user's to type; a run names each until they are."""
    case = P.preset("eh_ipp_hybrid", "ipp_hybrid", currency="EUR")
    by = {ln.name: ln for ln in case.lines}
    cap = by["Capacity payment (BESS, firm capacity)"]
    assert cap.derating is None and "de-rating" in cap.note
    assert by["Insurance"].quantity is None and not by["Insurance"].assumed
    kinds = {r.kind for r in case.refusals(horizon=H, base_year=BASE)}
    assert {"derating_missing", "quantity_missing"} <= kinds


def test_capacity_in_service_names_the_asset_in_words():
    """Walk 06 (RAW-NAMES-1): the Quantity-from option reads the asset's name."""
    from tests.test_business_case_linked_outputs import _built

    n, solve = _built("eh_ipp_hybrid")
    labels = {o.id: o.label for o in L.catalogue(n, solve)}
    assert labels["capacity.Generator.pv_plant"] == "Capacity in service: PV plant"
    assert labels["capacity.StorageUnit.bess"] == "Capacity in service: BESS"


def test_the_workbook_names_every_kpi_the_engine_serves_in_words():
    """Walk 06 (GK B2 B3): the "Business case" sheet's KPI column is the Investment tab's
    names; a KPI added to `kpis()` without words fails here, not on the sheet."""
    from services.finance.business_case import kpis, pnl
    from services.results_workbook.business_case import KPI_WORDS
    from tests.test_business_case_engine import _run

    _, r = _run(_inputs(_line()))
    served = set(kpis(r, pnl(r), capacity={"a": 1.0}))
    assert set(KPI_WORDS) == served
    assert all("_" not in w for w in KPI_WORDS.values())


def test_kpi_row_names_every_kpi_and_only_rows_the_pnl_has():
    """GK B2 on #1683 (KPI_ROW): the KPI → P&L row map is served, never mirrored by
    hand: its keys are exactly `kpis()`' and each row is one `PnL.rows` holds."""
    from services.finance.business_case import KPI_ROW, kpis, pnl
    from tests.test_business_case_engine import _run

    _, r = _run(_inputs(_line()))
    p = pnl(r)
    assert set(KPI_ROW) == set(kpis(r, p, capacity={"a": 1.0}))
    assert set(KPI_ROW.values()) <= set(p.rows)


def test_b1_quantity_never_reads_a_missing_derating_or_series_as_a_figure():
    """GK B2 mutants on `_quantity`: an unset de-rating is None (never 1.0), and a
    series the solve does not have is None with or without a de-rating."""
    from services.finance.business_case import _quantity

    series = L.Series("capacity.StorageUnit.bess", "kW_year", {None: 100.0}, {})
    unset = B.LineItem(**_cap())
    assert _quantity(unset, 2030, {unset.name: series}) is None
    assert _quantity(unset, 2030, {}) is None
    set_ = B.LineItem(**_cap(derating=0.5))
    assert _quantity(set_, 2030, {set_.name: series}) == 50.0
    assert _quantity(set_, 2030, {}) is None
    # A linked line that takes no de-rating reads its series whole.
    other = B.LineItem(**_cap(category="service_fee"))
    assert _quantity(other, 2030, {other.name: series}) == 100.0


def test_b2_only_a_line_the_finance_inputs_filled_reads_from_finance():
    from services.business_case.preset_apply import _indexation_filled
    from services.business_case.presets import with_finance_indexation

    before = B.BusinessCaseInputs(
        lines=[B.LineItem(**_cap(name="stated")), B.LineItem(**_cap(name="empty"))]
    )
    before.lines[1].indexation = None
    after = with_finance_indexation(before, {"ppa": 0.02})
    assert _indexation_filled(before, after) == {"empty"}


def test_b3_the_sheet_writes_a_non_finite_figure_and_known_reasons_in_words():
    import openpyxl

    from services.results_workbook.business_case import (
        reason_words,
        write_business_case_sheet,
    )

    assert reason_words("capacity_not_stated") == "the capacity is not stated"
    assert reason_words("not_requested") == "not run"
    report = {
        "basis": {"label": "x"},
        "kpis": {"project_npv": {"status": "ok", "value": float("inf"), "unit": "EUR"}},
    }
    wb = openpyxl.Workbook()
    write_business_case_sheet(wb, report, {"stale": False})
    rows = {
        r[0].value: [c.value for c in r[1:]] for r in wb["Business case"].iter_rows()
    }
    assert rows["Project NPV"][0] == "not established"
