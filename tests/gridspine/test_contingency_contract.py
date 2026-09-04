"""Task 2: the contingency, results and fault-level stage boundaries.

Every validator here gets at least one deliberately-broken artifact that MUST
be rejected, and the same pre-coercion discipline as `validate_dispatch`: the
guards read the values AS SUPPLIED, because `astype` would launder a
fractional hour into an integer and the string "False" into truthiness.

The rule that matters most, and the one the mutation check targets, is the
cross-field one: a non-convergent contingency is a ROW carrying
`converged=False` and the maximal-severity sentinel — never a missing row,
and never a row whose severity happens to read as benign.
"""
import numpy as np
import pandas as pd
import pytest

from gridspine.schema.contingency import (
    NON_CONVERGED_SEVERITY,
    validate_contingency_results,
    validate_contingency_set,
    validate_fault_levels,
)
from gridspine.schema.contracts import ContractError


# --------------------------------------------------------------------------
# fixtures: one good frame per contract, mutated per test
# --------------------------------------------------------------------------

def _good_set():
    return pd.DataFrame({
        "contingency_id": ["BUS_01-BUS_02-1", "G_BUS_30", "BUS_01-BUS_02-1--BUS_02-BUS_03-1"],
        "kind": ["branch", "unit", "branch"],
        "element_ids": [
            ["BUS_01-BUS_02-1"],
            ["G_BUS_30"],
            ["BUS_01-BUS_02-1", "BUS_02-BUS_03-1"],
        ],
        "order": [1, 1, 2],
    })


def _good_results():
    return pd.DataFrame({
        "contingency_id": ["BUS_01-BUS_02-1", "G_BUS_30", "BUS_02-BUS_03-1"],
        "hour": [19, 19, 19],
        "converged": [True, True, False],
        "max_branch_loading_pct": [87.5, 104.2, np.nan],
        "min_vm_pu": [0.97, 0.94, np.nan],
        "max_vm_pu": [1.05, 1.06, np.nan],
        "n_violations": [0, 1, 0],
        "severity": [0.0, 4.2, NON_CONVERGED_SEVERITY],
    })


def _good_faults():
    return pd.DataFrame({
        "bus": ["BUS_01", "BUS_02", "BUS_01"],
        "ikss_ka": [12.4, 9.8, 8.1],
        "sk_mva": [7400.0, 5850.0, 4830.0],
        "case": ["max", "max", "min"],
    })


# --------------------------------------------------------------------------
# validate_contingency_set
# --------------------------------------------------------------------------

def test_set_accepts_and_coerces():
    out = validate_contingency_set(_good_set())
    assert out["order"].dtype == "int64"
    assert out["contingency_id"].dtype == "object"
    assert list(out["element_ids"].iloc[2]) == ["BUS_01-BUS_02-1", "BUS_02-BUS_03-1"]
    assert len(out) == 3


def test_set_rejects_missing_columns():
    with pytest.raises(ContractError, match="missing columns"):
        validate_contingency_set(_good_set().drop(columns=["order"]))


def test_set_rejects_duplicate_contingency_id():
    df = _good_set()
    df.loc[len(df)] = ["G_BUS_30", "unit", ["G_BUS_30"], 1]
    with pytest.raises(ContractError, match="duplicate contingency_id"):
        validate_contingency_set(df)


def test_set_rejects_order_two_with_one_element():
    """The cross-check the mutation targets: order and len(element_ids) must agree."""
    df = _good_set()
    df.loc[2, "element_ids"] = ["BUS_01-BUS_02-1"]
    with pytest.raises(ContractError, match="order"):
        validate_contingency_set(df)


def test_set_rejects_order_one_with_two_elements():
    df = _good_set()
    df.at[0, "element_ids"] = ["BUS_01-BUS_02-1", "BUS_02-BUS_03-1"]
    with pytest.raises(ContractError, match="order"):
        validate_contingency_set(df)


@pytest.mark.parametrize("bad_order", [0, 3, 1.5, -1])
def test_set_rejects_order_outside_one_two_before_coercion(bad_order):
    df = _good_set()
    df["order"] = df["order"].astype(float)
    df.loc[0, "order"] = bad_order
    with pytest.raises(ContractError, match="order must be exactly 1 or 2"):
        validate_contingency_set(df)


def test_set_rejects_unknown_kind():
    df = _good_set()
    df.loc[0, "kind"] = "bus"
    with pytest.raises(ContractError, match="kind"):
        validate_contingency_set(df)


def test_set_rejects_empty_element_list():
    df = _good_set()
    df.at[0, "element_ids"] = []
    with pytest.raises(ContractError):
        validate_contingency_set(df)


def test_set_rejects_non_list_element_ids():
    """A bare string is iterable — it would silently become one contingency per character."""
    df = _good_set()
    df.at[0, "element_ids"] = "BUS_01-BUS_02-1"
    with pytest.raises(ContractError, match="list"):
        validate_contingency_set(df)


def test_set_rejects_repeated_element_within_a_contingency():
    df = _good_set()
    df.at[2, "element_ids"] = ["BUS_01-BUS_02-1", "BUS_01-BUS_02-1"]
    with pytest.raises(ContractError, match="repeat"):
        validate_contingency_set(df)


@pytest.mark.parametrize("bad_id", ["BUS 01", "BUS,01", "BUS'01", "BUS_01\n", "", None])
def test_set_rejects_ids_outside_the_canonical_charset(bad_id):
    """Fullmatch, not match: `BUS_01\\n` would pass a `$`-anchored regex."""
    df = _good_set()
    df.loc[0, "contingency_id"] = bad_id
    with pytest.raises(ContractError):
        validate_contingency_set(df)


def test_set_rejects_element_ids_outside_the_canonical_charset():
    df = _good_set()
    df.at[0, "element_ids"] = ["BUS 01"]
    with pytest.raises(ContractError, match="element"):
        validate_contingency_set(df)


def test_set_contingency_id_is_not_capped_at_the_raw_name_width():
    """A pair id is longer than 12 chars by construction; the PSS/E NAME cap
    applies to bus/unit names that reach the .raw, not to contingency keys."""
    df = _good_set()
    assert len(df.loc[2, "contingency_id"]) > 12
    validate_contingency_set(df)


# --------------------------------------------------------------------------
# validate_contingency_results
# --------------------------------------------------------------------------

def test_results_accept_and_coerce():
    out = validate_contingency_results(_good_results())
    assert out["hour"].dtype == "int64"
    assert out["n_violations"].dtype == "int64"
    assert out["converged"].dtype == "bool"
    assert out["severity"].dtype == "float64"


def test_results_reject_missing_columns():
    with pytest.raises(ContractError, match="missing columns"):
        validate_contingency_results(_good_results().drop(columns=["severity"]))


def test_results_reject_converged_as_string():
    """"False" is truthy; `astype(bool)` would record a diverged case as survived."""
    df = _good_results().astype({"converged": "object"})
    df.loc[2, "converged"] = "False"
    with pytest.raises(ContractError, match="converged must be exactly True or False"):
        validate_contingency_results(df)


def test_results_reject_converged_as_int():
    df = _good_results().astype({"converged": "object"})
    df.loc[0, "converged"] = 1
    with pytest.raises(ContractError, match="converged must be exactly True or False"):
        validate_contingency_results(df)


def test_results_reject_fractional_hour_before_coercion():
    df = _good_results().astype({"hour": "float64"})
    df.loc[0, "hour"] = 19.5
    with pytest.raises(ContractError, match="hour must be integral"):
        validate_contingency_results(df)


def test_results_reject_fractional_violation_count():
    df = _good_results().astype({"n_violations": "float64"})
    df.loc[0, "n_violations"] = 0.5
    with pytest.raises(ContractError, match="n_violations"):
        validate_contingency_results(df)


def test_results_reject_negative_violation_count():
    df = _good_results()
    df.loc[0, "n_violations"] = -1
    with pytest.raises(ContractError, match="n_violations"):
        validate_contingency_results(df)


def test_results_reject_nan_severity():
    """Severity is what the ranking sorts on; NaN sorts nowhere."""
    df = _good_results()
    df.loc[0, "severity"] = np.nan
    with pytest.raises(ContractError, match="severity"):
        validate_contingency_results(df)


def test_results_reject_inf_severity():
    """inf does not survive a CSV round-trip; the sentinel is finite for a reason."""
    df = _good_results()
    df.loc[2, "severity"] = np.inf
    with pytest.raises(ContractError, match="severity"):
        validate_contingency_results(df)


def test_results_reject_negative_severity():
    df = _good_results()
    df.loc[0, "severity"] = -0.1
    with pytest.raises(ContractError, match="severity"):
        validate_contingency_results(df)


def test_results_reject_nan_physics_on_a_converged_row():
    """NaN loadings are the honest value for a DIVERGED case only."""
    df = _good_results()
    df.loc[0, "max_branch_loading_pct"] = np.nan
    with pytest.raises(ContractError, match="converged"):
        validate_contingency_results(df)


def test_results_accept_nan_physics_on_a_diverged_row():
    validate_contingency_results(_good_results())  # row 2 carries NaN physics


def test_results_reject_negative_loading():
    df = _good_results()
    df.loc[0, "max_branch_loading_pct"] = -5.0
    with pytest.raises(ContractError, match="loading"):
        validate_contingency_results(df)


def test_results_reject_min_vm_above_max_vm():
    df = _good_results()
    df.loc[0, "min_vm_pu"], df.loc[0, "max_vm_pu"] = 1.06, 0.97
    with pytest.raises(ContractError, match="min_vm_pu"):
        validate_contingency_results(df)


def test_results_reject_non_positive_voltage():
    df = _good_results()
    df.loc[0, "min_vm_pu"] = 0.0
    with pytest.raises(ContractError, match="vm_pu"):
        validate_contingency_results(df)


def test_results_diverged_row_must_carry_the_sentinel_severity():
    """THE rule: non-convergence IS maximal severity. A diverged row with a
    benign severity is the plausible wrong answer the ranking would sort last."""
    df = _good_results()
    df.loc[2, "severity"] = 0.0
    with pytest.raises(ContractError, match="NON_CONVERGED_SEVERITY"):
        validate_contingency_results(df)


def test_results_converged_row_may_not_carry_the_sentinel():
    df = _good_results()
    df.loc[0, "severity"] = NON_CONVERGED_SEVERITY
    with pytest.raises(ContractError, match="NON_CONVERGED_SEVERITY"):
        validate_contingency_results(df)


def test_results_reject_duplicate_contingency_hour():
    df = _good_results()
    df.loc[len(df)] = ["G_BUS_30", 19, True, 50.0, 0.99, 1.01, 0, 0.0]
    with pytest.raises(ContractError, match="duplicate"):
        validate_contingency_results(df)


def test_sentinel_is_a_large_finite_float():
    assert isinstance(NON_CONVERGED_SEVERITY, float)
    assert np.isfinite(NON_CONVERGED_SEVERITY)
    assert NON_CONVERGED_SEVERITY >= 1e6


# --------------------------------------------------------------------------
# validate_fault_levels
# --------------------------------------------------------------------------

def test_faults_accept_and_coerce():
    out = validate_fault_levels(_good_faults())
    assert out["ikss_ka"].dtype == "float64" and out["sk_mva"].dtype == "float64"
    assert len(out) == 3


def test_faults_reject_missing_columns():
    with pytest.raises(ContractError, match="missing columns"):
        validate_fault_levels(_good_faults().drop(columns=["case"]))


@pytest.mark.parametrize("col", ["ikss_ka", "sk_mva"])
def test_faults_reject_zero_fault_level(col):
    """0 kA at an energised bus is a coverage failure, not a result."""
    df = _good_faults()
    df.loc[0, col] = 0.0
    with pytest.raises(ContractError, match=col):
        validate_fault_levels(df)


@pytest.mark.parametrize("value", [np.nan, np.inf, -3.0])
def test_faults_reject_non_finite_or_negative(value):
    df = _good_faults()
    df.loc[0, "ikss_ka"] = value
    with pytest.raises(ContractError, match="ikss_ka"):
        validate_fault_levels(df)


def test_faults_reject_unknown_case():
    df = _good_faults()
    df.loc[0, "case"] = "typical"
    with pytest.raises(ContractError, match="case"):
        validate_fault_levels(df)


def test_faults_reject_duplicate_bus_case():
    df = _good_faults()
    df.loc[len(df)] = ["BUS_01", 11.0, 6500.0, "max"]
    with pytest.raises(ContractError, match="duplicate"):
        validate_fault_levels(df)


def test_faults_reject_null_bus():
    df = _good_faults()
    df.loc[0, "bus"] = None
    with pytest.raises(ContractError, match="bus"):
        validate_fault_levels(df)


def test_faults_reject_bus_outside_the_canonical_charset():
    df = _good_faults()
    df.loc[0, "bus"] = "BUS 01"
    with pytest.raises(ContractError, match="bus"):
        validate_fault_levels(df)
