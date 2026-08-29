import pandas as pd
import pytest

from gridspine.readback.pf_compare import compare_lf
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
