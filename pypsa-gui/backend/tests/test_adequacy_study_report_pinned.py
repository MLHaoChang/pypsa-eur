"""
S7 (review v1 N7): the adequacy write-up is pinned BYTE-IDENTICAL across the
lift of its disclosure helpers into a generic section list with a status
mapping (`services/report_sections.py`), which the decision report reuses.

`tests/golden/study_report_recorded.json` was recorded from
`services/adequacy/study_report.py::build_study_report` BEFORE the lift (set
``RECORD_STUDY_REPORT=1`` to re-record; do not, unless the write-up is meant
to change) and every scenario must serialise to exactly those bytes after it.
"""
from __future__ import annotations

import json
import os
import pathlib

import numpy as np
import pandas as pd
import pypsa

from services.adequacy import study_report as R

RECORDED = pathlib.Path(__file__).resolve().parent / "golden" / "study_report_recorded.json"


def _network(frozen: bool = False, island: bool = False) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=48, freq="h"))
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=100.0)
    n.add("Generator", "gas", bus="B1", carrier="gas", p_nom=200.0)
    if frozen:
        n.loads_t.p_set["L1"] = pd.Series(np.full(48, 100.0), index=n.snapshots)
    if island:
        n.add("Bus", "orphan")
        n.add("Load", "stranded", bus="orphan", p_set=50.0)
    return n


def _read_with(**payloads):
    def read(section: str):
        return payloads.get(section, {"status": "no_data", "kind": section,
                                      "message": "never run"})
    return read


_FULL = dict(
    adequacy={"engine": "lp_proxy", "fidelity": "deterministic_scenario", "met": True},
    reserve_margin={"margin": 0.15, "by_period": []},
    copt={"engine": "copt", "fidelity": "analytic_convolution",
          "metrics": {"lole_hours": 4.0}},
    mc={"status": "done", "result": {
        "engine": "mc", "fidelity": "sequential_mc", "warning": "one weather year",
        "metrics": {"converged": False, "n_samples": 200, "lole_ci": [1.0, 3.0]}}},
    frontier={"status": "done", "points": [{"eps": 1.0}], "warning": "a curve, not a plan"},
    coupling_loop={"status": "done", "iterations": [], "basis": "hours_per_year"},
    margin_loop={"status": "done", "iterations": [],
                 "result": {"basis": "hours_per_horizon"}},
    fmea_sweep={"status": "done", "modes": []},
)

SCENARIOS = {
    "empty": dict(n=lambda: _network(), read=_read_with()),
    "full": dict(n=lambda: _network(), read=_read_with(**_FULL),
                 campaign={"objective": "meet a three hour standard", "budget": 12}),
    "converged_mc_no_basis": dict(n=lambda: _network(), read=_read_with(
        mc={"result": {"metrics": {"converged": True, "n_samples": 500,
                                   "lole_ci": [0.5, 0.9]}}},
        margin_loop={"status": "done", "iterations": []})),
    "gaps": dict(n=lambda: _network(frozen=True, island=True), read=_read_with(
        copt={"engine": "copt", "fidelity": "analytic_convolution"}),
        health={"counts": {"unsourced": 3, "drifted": 2}}),
    "empty_marker_payload": dict(n=lambda: _network(), read=_read_with(
        adequacy={"status": "no_data", "message": "not solved"}, mc=None)),
}


def _outputs() -> dict[str, str]:
    out = {}
    for name, sc in SCENARIOS.items():
        report = R.build_study_report(sc["n"](), sc["read"], campaign=sc.get("campaign"),
                                      health=sc.get("health"))
        out[name] = json.dumps(report, sort_keys=True, default=str, indent=1)
    return out


def test_build_study_report_is_byte_identical_to_the_recording():
    got = _outputs()
    if os.environ.get("RECORD_STUDY_REPORT") == "1":
        RECORDED.write_text(json.dumps(got, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    recorded = json.loads(RECORDED.read_text(encoding="utf-8"))
    assert sorted(got) == sorted(recorded)
    for name in recorded:
        assert got[name] == recorded[name], name
