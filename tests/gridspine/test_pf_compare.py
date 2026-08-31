from pathlib import Path

import pandas as pd
import pytest

from gridspine.readback.pf_compare import compare_branch_flows, compare_lf
from gridspine.schema.contracts import ContractError
from gridspine.static.loadflow import LFResult


def lf_result():
    bus = pd.DataFrame(
        {"vm_pu": [1.030, 0.985], "va_degree": [0.0, -5.2]},
        index=pd.Index(["BUS_01", "BUS_02"], name="bus"),
    )
    return LFResult(converged=True, bus=bus)


def test_within_tolerance_passes(tmp_path):
    csv = tmp_path / "pf.csv"
    csv.write_text("bus_name,vm_pu,va_degree\nBUS_01,1.0305,0.01\nBUS_02,0.9846,-5.15\n")
    cmp = compare_lf(lf_result(), csv)
    assert cmp["ok"].all()
    assert cmp.loc["BUS_01", "vm_rel_err"] == pytest.approx(0.0005 / 1.0305, rel=1e-3)


def test_out_of_tolerance_flagged_per_element(tmp_path):
    csv = tmp_path / "pf.csv"
    csv.write_text("bus_name,vm_pu,va_degree\nBUS_01,1.030,0.0\nBUS_02,1.100,-5.2\n")
    cmp = compare_lf(lf_result(), csv)
    assert bool(cmp.loc["BUS_01", "ok"]) and not bool(cmp.loc["BUS_02", "ok"])


def test_bus_set_mismatch_rejected(tmp_path):
    csv = tmp_path / "pf.csv"
    csv.write_text("bus_name,vm_pu,va_degree\nBUS_01,1.03,0.0\n")
    with pytest.raises(ContractError, match="bus set"):
        compare_lf(lf_result(), csv)


def test_angle_out_of_tolerance_alone_fails(tmp_path):
    """The `ok` predicate is an AND of two half-checks. The magnitude half is
    covered above; without this, dropping the angle term entirely still passes."""
    csv = tmp_path / "pf.csv"
    csv.write_text("bus_name,vm_pu,va_degree\nBUS_01,1.030,0.0\nBUS_02,0.985,-4.0\n")
    cmp = compare_lf(lf_result(), csv)
    assert cmp.loc["BUS_02", "vm_rel_err"] == pytest.approx(0.0)
    assert cmp.loc["BUS_02", "va_abs_err_deg"] == pytest.approx(1.2)
    assert bool(cmp.loc["BUS_01", "ok"]) and not bool(cmp.loc["BUS_02", "ok"])


def test_non_converged_lf_rejected(tmp_path):
    """A failed LF carries an empty bus frame, so without this guard the
    comparison reports a bus-set mismatch — blaming the fixture for what is
    really a diverged load flow."""
    csv = tmp_path / "pf.csv"
    csv.write_text("bus_name,vm_pu,va_degree\nBUS_01,1.03,0.0\nBUS_02,0.985,-5.2\n")
    with pytest.raises(ContractError, match="not converged"):
        compare_lf(LFResult(converged=False), csv)


def test_missing_required_columns_rejected(tmp_path):
    """A hand-exported CSV with the wrong header raised a bare KeyError naming
    one column; the contract names all of the ones that are actually missing."""
    csv = tmp_path / "pf.csv"
    csv.write_text("name,vm_pu\nBUS_01,1.03\n")
    with pytest.raises(ContractError, match="missing required columns"):
        compare_lf(lf_result(), csv)


# ---------------------------------------------------------------------------
# Task 8: branch-flow comparison
# ---------------------------------------------------------------------------

BRANCH_HEADER = "from_bus,to_bus,ckt,p_from_mw,q_from_mvar,loading_percent\n"


def lf_branch_result():
    """Two parallel circuits on one pair plus one branch idling near zero —
    the near-zero row is what the 1 MW denominator floor exists for."""
    bf = pd.DataFrame(
        [
            {"from_bus": "BUS_01", "to_bus": "BUS_02", "ckt": "1",
             "p_from_mw": 120.0, "q_from_mvar": 30.0, "loading_percent": 45.0},
            {"from_bus": "BUS_01", "to_bus": "BUS_02", "ckt": "2",
             "p_from_mw": 118.0, "q_from_mvar": 28.0, "loading_percent": 44.0},
            {"from_bus": "BUS_02", "to_bus": "BUS_03", "ckt": "1",
             "p_from_mw": 0.20, "q_from_mvar": -1.0, "loading_percent": 1.0},
        ]
    )
    return LFResult(converged=True, branch_flow=bf)


def test_branch_within_tolerance_passes(tmp_path):
    csv = tmp_path / "b.csv"
    csv.write_text(
        BRANCH_HEADER
        + "BUS_01,BUS_02,1,120.5,32.0,45.2\n"
        + "BUS_01,BUS_02,2,118.0,28.0,44.0\n"
        + "BUS_02,BUS_03,1,0.205,-1.4,1.0\n"
    )
    cmp = compare_branch_flows(lf_branch_result(), csv)
    assert cmp["ok"].all()
    assert cmp.loc[("BUS_01", "BUS_02", "1"), "p_rel_err"] == pytest.approx(0.5 / 120.5)
    assert cmp.loc[("BUS_01", "BUS_02", "1"), "q_abs_err_mvar"] == pytest.approx(2.0)


def test_branch_p_out_of_tolerance_flagged_per_branch(tmp_path):
    csv = tmp_path / "b.csv"
    csv.write_text(
        BRANCH_HEADER
        + "BUS_01,BUS_02,1,120.0,30.0,45.0\n"
        + "BUS_01,BUS_02,2,130.0,28.0,44.0\n"   # 9.2% P error, Q exact
        + "BUS_02,BUS_03,1,0.20,-1.0,1.0\n"
    )
    cmp = compare_branch_flows(lf_branch_result(), csv)
    assert bool(cmp.loc[("BUS_01", "BUS_02", "1"), "ok"])
    assert not bool(cmp.loc[("BUS_01", "BUS_02", "2"), "ok"])
    assert cmp.loc[("BUS_01", "BUS_02", "2"), "q_abs_err_mvar"] == pytest.approx(0.0)


def test_branch_q_out_of_tolerance_alone_fails(tmp_path):
    """`ok` is an AND of two half-checks. Without this, dropping the Q term
    entirely still passes every other branch test in this file."""
    csv = tmp_path / "b.csv"
    csv.write_text(
        BRANCH_HEADER
        + "BUS_01,BUS_02,1,120.0,30.0,45.0\n"
        + "BUS_01,BUS_02,2,118.0,40.0,44.0\n"   # P exact, Q off by 12 Mvar
        + "BUS_02,BUS_03,1,0.20,-1.0,1.0\n"
    )
    cmp = compare_branch_flows(lf_branch_result(), csv)
    assert cmp.loc[("BUS_01", "BUS_02", "2"), "p_rel_err"] == pytest.approx(0.0)
    assert cmp.loc[("BUS_01", "BUS_02", "2"), "q_abs_err_mvar"] == pytest.approx(12.0)
    assert not bool(cmp.loc[("BUS_01", "BUS_02", "2"), "ok"])
    assert bool(cmp.loc[("BUS_01", "BUS_02", "1"), "ok"])


def test_near_zero_branch_uses_the_one_mw_denominator_floor(tmp_path):
    """0.005 MW of disagreement on a 0.2 MW branch is 2.4% of the reference
    and would fail a bare relative check; against the 1 MW floor it is 0.5%.
    Removing the floor turns this row red (and every quiet branch with it)."""
    csv = tmp_path / "b.csv"
    csv.write_text(
        BRANCH_HEADER
        + "BUS_01,BUS_02,1,120.0,30.0,45.0\n"
        + "BUS_01,BUS_02,2,118.0,28.0,44.0\n"
        + "BUS_02,BUS_03,1,0.205,-1.0,1.0\n"
    )
    cmp = compare_branch_flows(lf_branch_result(), csv)
    row = cmp.loc[("BUS_02", "BUS_03", "1")]
    assert row["p_abs_err_mw"] == pytest.approx(0.005)
    assert row["p_rel_err"] == pytest.approx(0.005)      # 0.005 / max(0.205, 1.0)
    assert bool(row["ok"])


def test_branch_key_set_mismatch_rejected(tmp_path):
    csv = tmp_path / "b.csv"
    csv.write_text(
        BRANCH_HEADER
        + "BUS_01,BUS_02,1,120.0,30.0,45.0\n"
        + "BUS_01,BUS_02,2,118.0,28.0,44.0\n"
        + "BUS_02,BUS_04,1,0.20,-1.0,1.0\n"   # BUS_04 is not in the LF result
    )
    with pytest.raises(ContractError, match="branch set mismatch"):
        compare_branch_flows(lf_branch_result(), csv)


def test_branch_ckt_mismatch_alone_is_a_key_mismatch(tmp_path):
    """A parallel circuit is identified ONLY by its ckt, so a CSV that keeps
    the bus pair and loses the circuit id must not silently join on the pair."""
    csv = tmp_path / "b.csv"
    csv.write_text(
        BRANCH_HEADER
        + "BUS_01,BUS_02,1,120.0,30.0,45.0\n"
        + "BUS_01,BUS_02,3,118.0,28.0,44.0\n"   # ckt 3, LF has ckt 2
        + "BUS_02,BUS_03,1,0.20,-1.0,1.0\n"
    )
    with pytest.raises(ContractError, match="branch set mismatch"):
        compare_branch_flows(lf_branch_result(), csv)


def test_numeric_ckt_column_still_joins(tmp_path):
    """`ckt` is a RAW text field, but an all-numeric column reads back from
    pandas as int64 — comparing that against the LF's str keys would report
    every branch as missing on both sides."""
    csv = tmp_path / "b.csv"
    csv.write_text(
        BRANCH_HEADER
        + "BUS_01,BUS_02,1,120.0,30.0,45.0\n"
        + "BUS_01,BUS_02,2,118.0,28.0,44.0\n"
        + "BUS_02,BUS_03,1,0.20,-1.0,1.0\n"
    )
    assert pd.read_csv(csv)["ckt"].dtype.kind == "i"
    cmp = compare_branch_flows(lf_branch_result(), csv)
    assert len(cmp) == 3 and cmp["ok"].all()


def test_branch_missing_required_columns_rejected(tmp_path):
    csv = tmp_path / "b.csv"
    csv.write_text("from_bus,to_bus,p_from_mw\nBUS_01,BUS_02,120.0\n")
    with pytest.raises(ContractError, match="missing required columns"):
        compare_branch_flows(lf_branch_result(), csv)


def test_branch_non_converged_lf_rejected(tmp_path):
    """Same guard as the bus comparison: a diverged LF carries an empty
    branch frame, which would otherwise be reported as a key-set mismatch
    and blame the fixture for a failed load flow."""
    csv = tmp_path / "b.csv"
    csv.write_text(BRANCH_HEADER + "BUS_01,BUS_02,1,120.0,30.0,45.0\n")
    with pytest.raises(ContractError, match="not converged"):
        compare_branch_flows(LFResult(converged=False), csv)


def test_duplicate_branch_key_in_csv_rejected(tmp_path):
    """Equal key SETS hide a duplicated row; the join would then fan out."""
    csv = tmp_path / "b.csv"
    csv.write_text(
        BRANCH_HEADER
        + "BUS_01,BUS_02,1,120.0,30.0,45.0\n"
        + "BUS_01,BUS_02,1,121.0,30.0,45.0\n"
        + "BUS_01,BUS_02,2,118.0,28.0,44.0\n"
        + "BUS_02,BUS_03,1,0.20,-1.0,1.0\n"
    )
    with pytest.raises(ContractError, match="duplicate branch"):
        compare_branch_flows(lf_branch_result(), csv)


def test_runbook_no_longer_defers_the_branch_comparison():
    """The runbook told the operator the branch CSV was captured-but-unused.
    It is now the input to an automated gate, and a runbook that still says
    'compared in increment 2' invites the operator to skip the export."""
    readme = (
        Path(__file__).parent / "fixtures" / "powerfactory" / "README.md"
    ).read_text()
    assert "compared in increment 2" not in readme
    assert "increment-2 task #1" not in readme
    assert "compare_branch_flows" in readme
    # the live contract's two tolerances have to be findable in the runbook
    assert "1%" in readme and "5 Mvar" in readme
