"""
The oracle must be arithmetically right AND structurally independent.

Independence is not a style preference. This session produced a test that
passed against a never-reset deque and asserted nothing; an oracle that calls
the helper it is checking is the same failure with better manners. The AST test
below makes the independence mechanical rather than aspirational.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from tests.golden import oracle


def test_crf_matches_the_textbook_capital_recovery_factor():
    # 0.07 / (1 - 1.07^-25), worked by hand to 10 places.
    assert oracle.crf(0.07, 25.0) == pytest.approx(0.0858105172, abs=1e-10)


def test_crf_of_a_zero_rate_is_straight_line_depreciation():
    # r -> 0 is a removable singularity: the limit is 1/n. A naive formula
    # divides by zero here.
    assert oracle.crf(0.0, 25.0) == pytest.approx(1.0 / 25.0)


def test_annualised_capital_cost_scales_by_one_periods_snapshots():
    # MEASURED against PyPSA 1.1.2: the components accessor returns
    # overnight x CRF x (snapshots_in_ONE_period / 8760). Multi-period
    # normalises by one period, NOT the total across periods.
    got = oracle.annualised_capital_cost(
        overnight_cost=900_000.0, rate=0.07, lifetime=25.0, snapshots_per_period=24
    )
    assert got == pytest.approx(211.5876, abs=1e-3)


def test_annualised_capital_cost_at_a_full_year_is_the_plain_annuity():
    got = oracle.annualised_capital_cost(
        overnight_cost=900_000.0, rate=0.07, lifetime=25.0, snapshots_per_period=8760
    )
    assert got == pytest.approx(77_229.4655, abs=1e-3)


def test_horizon_capex_weights_by_investment_period_years():
    # years are applied by the reporting layer, NOT baked into capital_cost.
    assert oracle.horizon_capex(100.0, 2.0, (5, 10)) == pytest.approx(100.0 * 2.0 * 15)


def test_the_oracle_imports_nothing_from_services():
    """
    STRUCTURAL GUARANTEE, not a convention. An oracle that shares an
    implementation with its subject is a tautology; this makes that verifiable
    by reading the import block.
    """
    src = pathlib.Path(oracle.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    banned = [m for m in imported if m.split(".")[0] in {"services", "routers"}]
    assert not banned, (
        f"oracle.py must not import from services/ or routers/: {banned}. "
        "It exists to check them from the outside."
    )
