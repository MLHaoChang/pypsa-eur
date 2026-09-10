"""PowerFactory read-back against a HANDOFF BUNDLE (increment 6, spec stage 6).

The engineer imports a bundle's .raw into PowerFactory, runs the load flow,
exports the bus and branch CSVs the fixture runbook specifies, and uploads
them in the study view. Read-back joins them onto the bundle's OWN load-flow
results (`lf_bus.csv`, `lf_branch_flow.csv` — written when the bundle was
made, so nothing is re-solved and the comparison is against exactly what was
handed over) and applies the phase-1 gate per element: |Vm| within 1 %, angle
within 0.5°, P within 1 % (floored at 1 MW), Q within 5 Mvar.

Everything here is pandas on CSVs — the engine cage keeps `readback/` free of
pandapower — so the fixtures are written by hand rather than solved.
"""
import json

import pandas as pd
import pytest

from gridspine.drivers import readback as rb
from gridspine.drivers.status import stage_status
from gridspine.readback.results import (
    READBACK_JSON,
    load_bundle_lf,
    read_back,
    readback_figure,
    readback_summary,
)
from gridspine.schema.contracts import ContractError

HOUR = 19


def make_bundle(run_dir, hour=HOUR, converged=True):
    b = run_dir / f"bundle_h{hour}"
    b.mkdir(parents=True)
    pd.DataFrame({"vm_pu": [1.030, 0.985, 1.010], "va_degree": [0.0, -5.2, 3.1]},
                 index=pd.Index(["BUS_01", "BUS_02", "BUS_03"], name="bus")).to_csv(b / "lf_bus.csv", index_label="bus")
    pd.DataFrame({
        "from_bus": ["BUS_01", "BUS_02"], "to_bus": ["BUS_02", "BUS_03"], "ckt": ["1", "1"],
        "p_from_mw": [120.0, -40.0], "q_from_mvar": [10.0, -3.0], "loading_percent": [40.0, 15.0],
    }).to_csv(b / "lf_branch_flow.csv", index=False)
    (b / "manifest.json").write_text(json.dumps({"case": "case39", "hour": hour, "converged": converged, "files": []}))
    return b


def pf_bus(path, bus02_vm=0.9846):
    path.write_text(f"bus_name,vm_pu,va_degree\nBUS_01,1.0305,0.01\nBUS_02,{bus02_vm},-5.15\nBUS_03,1.0098,3.3\n")
    return path


def pf_branches(path, p2=-40.2):
    path.write_text(
        "from_bus,to_bus,ckt,p_from_mw,q_from_mvar,loading_percent\n"
        f"BUS_01,BUS_02,1,120.5,11.0,40.2\nBUS_02,BUS_03,1,{p2},-2.0,15.1\n"
    )
    return path


@pytest.fixture
def run_dir(tmp_path):
    make_bundle(tmp_path / "run")
    return tmp_path / "run"


# --------------------------------------------------------------------------
# the bundle's own load flow is the reference
# --------------------------------------------------------------------------

def test_the_bundle_load_flow_loads_with_the_shapes_pf_compare_expects(run_dir):
    lf = load_bundle_lf(run_dir / "bundle_h19")
    assert lf.converged and list(lf.bus.index) == ["BUS_01", "BUS_02", "BUS_03"]
    assert list(lf.branch_flow.columns) == ["from_bus", "to_bus", "ckt", "p_from_mw", "q_from_mvar", "loading_percent"]
    assert lf.hour == HOUR


def test_a_bundle_without_its_load_flow_files_is_refused(tmp_path):
    b = tmp_path / "bundle_h7"
    b.mkdir()
    (b / "manifest.json").write_text(json.dumps({"hour": 7, "converged": True}))
    with pytest.raises(ContractError, match="lf_bus.csv"):
        load_bundle_lf(b)


# --------------------------------------------------------------------------
# read_back: summary, files, the gate
# --------------------------------------------------------------------------

def test_read_back_writes_the_summary_the_uploads_and_the_per_element_tables(run_dir, tmp_path):
    b = run_dir / "bundle_h19"
    summary = read_back(b, pf_bus(tmp_path / "bus.csv"), pf_branches(tmp_path / "br.csv"))
    assert summary["hour"] == HOUR and summary["pass"] is True
    assert summary["bus"]["n"] == 3 and summary["bus"]["n_ok"] == 3
    assert summary["branches"]["n"] == 2 and summary["branches"]["n_ok"] == 2
    assert summary["bus"]["max_vm_rel_err"] == pytest.approx(0.0005 / 1.0305, rel=1e-3)
    assert summary["sources"]["bus_csv"]["sha256"] and summary["sources"]["branch_csv"]["sha256"]
    for name in ("pf_bus.csv", "pf_branches.csv", "readback_bus.csv", "readback_branches.csv", READBACK_JSON):
        assert (b / name).is_file(), name
    assert (b / "pf_bus.csv").read_bytes() == (tmp_path / "bus.csv").read_bytes()     # byte-for-byte, like a resume
    assert readback_summary(b) == summary


def test_one_bus_outside_the_gate_fails_the_read_back_and_names_it(run_dir, tmp_path):
    b = run_dir / "bundle_h19"
    summary = read_back(b, pf_bus(tmp_path / "bus.csv", bus02_vm=1.100))
    assert summary["pass"] is False
    assert summary["bus"]["n_ok"] == 2 and summary["bus"]["worst"] == "BUS_02"
    assert summary["branches"] is None                  # no branch export: not judged, not assumed


def test_a_branch_outside_the_gate_fails_even_when_every_bus_passes(run_dir, tmp_path):
    b = run_dir / "bundle_h19"
    summary = read_back(b, pf_bus(tmp_path / "bus.csv"), pf_branches(tmp_path / "br.csv", p2=-60.0))
    assert summary["bus"]["pass"] is True and summary["branches"]["pass"] is False
    assert summary["pass"] is False
    assert summary["branches"]["worst"] == ["BUS_02", "BUS_03", "1"]


def test_a_non_converged_bundle_is_refused_before_anything_is_written(tmp_path):
    b = make_bundle(tmp_path / "run", converged=False)
    with pytest.raises(ContractError, match="not converged"):
        read_back(b, pf_bus(tmp_path / "bus.csv"))
    assert not (b / READBACK_JSON).exists()


def test_a_bad_export_is_refused_and_an_earlier_read_back_survives(run_dir, tmp_path):
    b = run_dir / "bundle_h19"
    first = read_back(b, pf_bus(tmp_path / "bus.csv"))
    bad = tmp_path / "bad.csv"
    bad.write_text("bus_name,vm_pu,va_degree\nBUS_01,1.03,0.0\n")           # two buses missing
    with pytest.raises(ContractError, match="bus set"):
        read_back(b, bad)
    assert readback_summary(b) == first


# --------------------------------------------------------------------------
# figures: the comparison as data
# --------------------------------------------------------------------------

def test_figures_carry_both_sides_per_element_and_name_the_tolerance(run_dir, tmp_path):
    b = run_dir / "bundle_h19"
    read_back(b, pf_bus(tmp_path / "bus.csv"), pf_branches(tmp_path / "br.csv"))
    vm = readback_figure(b, "vm")
    assert vm["available"] and vm["hour"] == HOUR and vm["tolerance"] == {"vm_rel_err": 0.01}
    row = next(r for r in vm["rows"] if r["element"] == "BUS_02")
    assert row["pandapower"] == 0.985 and row["powerfactory"] == 0.9846 and row["ok"] is True
    p = readback_figure(b, "branch_p")
    assert [r["element"] for r in p["rows"]] == ["BUS_01→BUS_02 (1)", "BUS_02→BUS_03 (1)"]
    assert set(readback_figure(b, "va")["rows"][0]) == {"element", "pandapower", "powerfactory", "err", "ok"}


def test_an_unknown_figure_is_a_contract_error_and_no_read_back_is_a_typed_answer(run_dir):
    b = run_dir / "bundle_h19"
    assert readback_figure(b, "vm") == {"available": False, "name": "vm", "hour": HOUR,
                                        "reason": "no PowerFactory results uploaded for hour 19 yet"}
    with pytest.raises(ContractError, match="vm, va, branch_p, branch_q"):
        readback_figure(b, "nope")


# --------------------------------------------------------------------------
# the driver surface the backend calls
# --------------------------------------------------------------------------

def test_the_driver_finds_the_bundle_by_hour_and_reports_per_hour(run_dir, tmp_path):
    make_bundle(run_dir, hour=7)
    summary = rb.ingest_powerfactory_results(run_dir, 19, pf_bus(tmp_path / "bus.csv"))
    assert summary["hour"] == 19
    assert set(rb.readback_status(run_dir)) == {19}            # hour 7 has no read-back yet
    assert rb.readback_status(run_dir)[19]["pass"] is True
    assert rb.result_figure(run_dir, 19, "vm")["available"] is True
    assert rb.result_figure(run_dir, 7, "vm")["available"] is False


def test_an_hour_without_a_bundle_is_refused(run_dir, tmp_path):
    with pytest.raises(ContractError, match="no handoff bundle for hour 5"):
        rb.ingest_powerfactory_results(run_dir, 5, pf_bus(tmp_path / "bus.csv"))


def test_stage_status_reports_the_read_back_per_hour(run_dir, tmp_path):
    make_bundle(run_dir, hour=7)
    assert stage_status(run_dir)["readback"] == {}
    rb.ingest_powerfactory_results(run_dir, 19, pf_bus(tmp_path / "bus.csv", bus02_vm=1.1))
    status = stage_status(run_dir)["readback"]
    assert set(status) == {"19"}
    assert status["19"]["pass"] is False and status["19"]["bus"] == {"n": 3, "n_ok": 2}
    assert status["19"]["branches"] is None
