"""
Imported PSS/E and DGS networks run in the app (issue psse-import 04).

The readers state a finite rating where the file leaves one unset (PSS/E
RATEA = 0) or unbounded (a DGS external grid) and mark it ``s_nom_source`` /
``p_nom_source`` = "assumed". Here the network goes through the app's own save
and reopen path, the solve's add-and-remove of slack rows, and a second save:
the markers and ratings must survive, and preflight must find no error.
The ``/api/model-import`` route test lands with project-io 04a.
"""

import math
from pathlib import Path

import pandapower.networks as pn
import pypsa
import pytest

from gridspine.handoff.dgs_writer import write_dgs
from gridspine.ingest.dgs_import import read_dgs
from gridspine.ingest.psse_import import read_psse
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig
from services.validation_service import validate_for_run

CASE14 = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "gridspine"
    / "fixtures"
    / "psse"
    / "case14.raw"
)


def _case14(tmp_path):
    return read_psse([CASE14], {}).network


def _dgs(tmp_path):
    # the issue's file: pandapower example_simple with short-circuit data
    net = pn.example_simple()
    net.ext_grid[["s_sc_max_mva", "s_sc_min_mva", "rx_max", "rx_min"]] = [
        1000,
        800,
        0.1,
        0.1,
    ]
    path = tmp_path / "simple.dgs"
    write_dgs(net, path)
    return read_dgs([path], {}).network


def _save_reopen(n, path):
    PyPSAService.export_network_to_netcdf(n, path)
    m = pypsa.Network()
    PyPSAService.import_network_from_netcdf(m, path)
    return m


def _errors(n):
    return [
        (i.code, i.name)
        for i in validate_for_run(n, SolverConfig())
        if i.severity == "error"
    ]


# (reader, component, marker column, rating column, marker counts, assumed rating)
CASES = {
    "psse case14": (
        _case14,
        "lines",
        "s_nom_source",
        "s_nom",
        {"assumed": 17},
        2757.0,
    ),
    "dgs example_simple": (
        _dgs,
        "generators",
        "p_nom_source",
        "p_nom",
        {"file": 2, "assumed": 1},
        1000.0,
    ),
}


@pytest.mark.parametrize("case", list(CASES))
def test_marker_and_rating_survive_save_solve_slacks_and_resave(case, tmp_path):
    read, comp, col, rating, counts, assumed_value = CASES[case]
    n = read(tmp_path)
    before = getattr(n, comp)[[col, rating]].copy()
    assert before[col].value_counts().to_dict() == counts
    assert set(before.loc[before[col] == "assumed", rating]) == {assumed_value}

    m = _save_reopen(n, tmp_path / "a.nc")
    # what the solve's VOLL/DSR slack rows do to the generator frame
    bus = m.buses.index[0]
    m.add("Generator", "slack probe", bus=bus, p_nom=1.0)
    m.remove("Generator", "slack probe")
    k = _save_reopen(m, tmp_path / "b.nc")  # the second save must not raise

    after = getattr(k, comp)[[col, rating]]
    assert after.to_dict() == before.to_dict()


@pytest.mark.parametrize("case", list(CASES))
def test_preflight_has_no_error_after_save_and_reopen(case, tmp_path):
    read = CASES[case][0]
    n = read(tmp_path)
    assert _errors(n) == []
    assert _errors(_save_reopen(n, tmp_path / "a.nc")) == []


def test_a_row_added_later_is_not_assumed(tmp_path):
    k = _save_reopen(_case14(tmp_path), tmp_path / "a.nc")
    first = k.lines.iloc[0]
    k.add("Line", "user line", bus0=first.bus0, bus1=first.bus1, x=1.0, s_nom=50.0)
    v = k.lines.at["user line", "s_nom_source"]
    assert v != "assumed"
    assert v == "" or (isinstance(v, float) and math.isnan(v))
