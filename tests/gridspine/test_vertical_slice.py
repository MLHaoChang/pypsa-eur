import json
from pathlib import Path

import pandas as pd
import pytest

from gridspine.drivers.planning import run_39bus_slice
from gridspine.producers.pypsa_nodal import LOAD_SHAPE
from gridspine.readback.pf_compare import compare_lf

FIXDIR = Path(__file__).parent / "fixtures" / "powerfactory"


@pytest.fixture(scope="module")
def slice_result(tmp_path_factory):
    out = tmp_path_factory.mktemp("slice")
    return run_39bus_slice(out, hour=19), out


def test_slice_runs_and_converges(slice_result):
    res, out = slice_result
    assert res.converged
    for key in ("dispatch", "lf_bus", "lf_branch", "raw", "manifest"):
        assert res.artifacts[key].exists(), key


def test_manifest_records_assumptions(slice_result):
    res, _ = slice_result
    manifest = json.loads(res.artifacts["manifest"].read_text())
    assert manifest["hour"] == 19
    assert manifest["load_consistency"] == "per-snapshot loads artifact (increment 2)"
    ledger_text = " ".join(manifest["ledger"])
    assert "q_mvar" in ledger_text and "LOAD_SHAPE" in ledger_text


def test_non_peak_hour_is_accepted_and_load_consistent(tmp_path):
    """The retired hour-19 guard, inverted.

    Increment 1 never scaled net.load, so only hour 19 was load-consistent and
    the driver raised on anything else. Hours 8-18 still CONVERGED — the slack
    silently imported the difference, up to ~933 MW of phantom residual — which
    is why convergence was never the guard and cannot be the assertion now
    either. The loads artifact moves the demand with the hour, so the check
    that the guard has genuinely been replaced rather than merely deleted is
    the slack: it must carry losses only. Full coverage of the loads artifact
    lives in tests/gridspine/test_loads_artifact.py.
    """
    from gridspine.ingest.pandapower_source import load_case39

    res = run_39bus_slice(tmp_path, hour=8)
    assert res.converged
    served = LOAD_SHAPE[8] * float(load_case39().load["p_mw"].sum())
    assert abs(res.lf.slack_p_mw) < 0.05 * served


def test_raw_and_lf_use_same_bus_names(slice_result):
    res, _ = slice_result
    raw_text = res.artifacts["raw"].read_text()
    for bus in res.lf.bus.index:
        assert bus in raw_text


def test_powerfactory_gate(tmp_path):
    fixture = FIXDIR / "case39_h19.csv"
    if not fixture.exists():
        pytest.skip("PowerFactory fixture not yet exported (see fixtures/powerfactory/README.md)")
    res = run_39bus_slice(tmp_path, hour=19)
    cmp = compare_lf(res.lf, fixture)
    failing = cmp[~cmp["ok"]]
    assert failing.empty, f"buses outside 1%/0.5deg:\n{failing}"


def _raw_generator_records(raw_text):
    """The PG column of every record in the RAW GENERATOR DATA section."""
    lines = raw_text.splitlines()
    start = next(i for i, ln in enumerate(lines) if "BEGIN GENERATOR DATA" in ln) + 1
    end = next(i for i, ln in enumerate(lines) if "END OF GENERATOR DATA" in ln)
    return [float(ln.split(",")[2]) for ln in lines[start:end]]


def test_raw_carries_the_dispatched_hour_not_the_source_p_mw(slice_result):
    """Stage-order guard: the RAW must reflect apply_dispatch(hour=19).

    Skipping apply_dispatch leaves case39's native p_mw on net.gen, and the
    slice still converges and still names every bus — so this is the only
    assertion that sees the stage. Compare the handoff artifact against the
    dispatch artifact at the requested hour rather than against the LF, since
    the LF converges either way.
    """
    res, _ = slice_result
    table = pd.read_csv(res.artifacts["dispatch"])
    at_hour = table[(table["hour"] == 19) & (~table["unit_id"].str.startswith("SLK_"))]
    expected = sorted(round(v, 3) for v in at_hour["p_mw"])

    pg = _raw_generator_records(res.artifacts["raw"].read_text())
    actual = sorted(round(v, 3) for v in pg[: len(expected)])

    assert len(expected) > 0
    assert actual == expected


def test_stage_failure_writes_the_error_artifact_and_reraises(tmp_path, monkeypatch):
    import gridspine.drivers.planning as planning

    def boom():
        raise RuntimeError("ingest exploded")

    monkeypatch.setattr(planning, "load_case39", boom)
    with pytest.raises(RuntimeError, match="ingest exploded"):
        planning.run_39bus_slice(tmp_path, hour=19)

    err = json.loads((tmp_path / "error_ingest.json").read_text())
    assert err["stage"] == "ingest"
    assert err["element_ids"] == []
    assert "ingest exploded" in err["cause"]
