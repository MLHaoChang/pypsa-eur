"""A solved PyPSA network as the dispatch source (increment 5, D3).

The spec's producer line is "solved PyPSA network → dispatch table (identity
map)": a project's network is a source exactly when its generators ARE the
detailed grid's units — same names, same buses. Everything downstream is the
IEEE 39-bus grid, so a network that maps only partly would rank a grid that is
not the one being studied. The map is therefore checked in BOTH directions and
per bus, and every refusal names the ids.

Commitment: measured before this was written, a netcdf round-trip keeps
`generators_t.status` when any unit ever departs from 1 and writes nothing when
every unit is always on. So the producer takes the status column where the
saved network has one and infers the rest from output — exact for these units,
since a committed machine runs at ≥ 30 % of p_nom — and the manifest records
which it did. Both branches are exercised here on the same solved network.

Runtime: ONE exact 24 h unit commitment (~6 s) makes the source network; the
study of it runs with k=1 and no screening.
"""
import hashlib
import json

import pandas as pd
import pytest

from gridspine.drivers.status import stage_status
from gridspine.drivers.study import StudyConfig, run_study
from gridspine.drivers.year_study import res_cf_for
from gridspine.ingest.pandapower_source import load_case39_res, registry_from_net
from gridspine.producers.pypsa_nodal import (
    COMMITMENT_INFERRED,
    COMMITMENT_SOLVED,
    load_solved_network,
    run_uc,
    tables_from_network,
    to_dispatch_table,
    to_loads_table,
    to_pypsa,
)
from gridspine.schema.contracts import ContractError

HOURS = 24
INDEX = pd.date_range("2030-01-01", periods=HOURS, freq="h")


@pytest.fixture(scope="module")
def grid():
    net = load_case39_res()
    return net, registry_from_net(net)


@pytest.fixture(scope="module")
def solved(grid, tmp_path_factory):
    """A solved network built the way the GUI template is: datetime snapshots."""
    net, _registry = grid
    n = to_pypsa(net, snapshots=INDEX, res_cf=res_cf_for(net, HOURS))
    run_uc(n)
    path = tmp_path_factory.mktemp("source") / "network.nc"
    n.export_to_netcdf(str(path))
    return n, path


# --------------------------------------------------------------------------
# to_pypsa takes an index, not only a count
# --------------------------------------------------------------------------

def test_to_pypsa_accepts_a_snapshot_index_and_aligns_profiles_positionally(grid):
    net, _ = grid
    by_index = to_pypsa(net, snapshots=INDEX, res_cf=res_cf_for(net, HOURS))
    by_count = to_pypsa(net, snapshots=HOURS, res_cf=res_cf_for(net, HOURS))
    assert by_index.snapshots.equals(INDEX)
    assert by_index.loads_t.p_set.index.equals(INDEX)
    pd.testing.assert_frame_equal(
        by_index.loads_t.p_set.reset_index(drop=True), by_count.loads_t.p_set.reset_index(drop=True)
    )
    pd.testing.assert_frame_equal(
        by_index.generators_t.p_max_pu.reset_index(drop=True),
        by_count.generators_t.p_max_pu.reset_index(drop=True),
    )


# --------------------------------------------------------------------------
# producer: identity map
# --------------------------------------------------------------------------

def test_a_saved_network_reproduces_the_in_memory_tables(grid, solved):
    net, registry = grid
    n, path = solved
    loaded = load_solved_network(path)
    assert not loaded.generators_t.status.empty        # this solve cycles units, so status was saved
    dispatch, loads, commitment = tables_from_network(loaded, net, registry)
    pd.testing.assert_frame_equal(dispatch, to_dispatch_table(n))
    pd.testing.assert_frame_equal(loads, to_loads_table(n, net))
    assert commitment == COMMITMENT_SOLVED


def test_without_a_saved_status_commitment_is_inferred_and_the_tables_are_the_same(grid, solved):
    """A network whose units never switch saves no status frame at all."""
    net, registry = grid
    n, path = solved
    loaded = load_solved_network(path)
    loaded.generators_t["status"] = loaded.generators_t.status.iloc[:, :0]
    dispatch, _loads, commitment = tables_from_network(loaded, net, registry)
    pd.testing.assert_frame_equal(dispatch, to_dispatch_table(n))
    assert commitment == COMMITMENT_INFERRED


def test_an_unsolved_network_is_refused_before_anything_is_mapped(grid):
    net, registry = grid
    fresh = to_pypsa(net, snapshots=HOURS, res_cf=res_cf_for(net, HOURS))
    with pytest.raises(ContractError, match="not solved"):
        tables_from_network(fresh, net, registry)


def test_a_missing_unit_is_named(grid, solved):
    net, registry = grid
    _n, path = solved
    m = load_solved_network(path)      # a fresh copy: PyPSA refuses to copy a solved network
    m.remove("Generator", "G_BUS_30")
    with pytest.raises(ContractError, match="G_BUS_30"):
        tables_from_network(m, net, registry)


def test_a_unit_the_grid_does_not_have_is_named(grid, solved):
    net, registry = grid
    _n, path = solved
    m = load_solved_network(path)      # a fresh copy: PyPSA refuses to copy a solved network
    m.add("Generator", "G_ELSEWHERE", bus="BUS_01", p_nom=10.0)
    m.generators_t.p["G_ELSEWHERE"] = 0.0
    with pytest.raises(ContractError, match="G_ELSEWHERE"):
        tables_from_network(m, net, registry)


def test_a_unit_on_the_wrong_bus_is_named(grid, solved):
    net, registry = grid
    _n, path = solved
    m = load_solved_network(path)      # a fresh copy: PyPSA refuses to copy a solved network
    m.generators.loc["G_BUS_30", "bus"] = "BUS_01"
    with pytest.raises(ContractError, match="G_BUS_30.*BUS_01|BUS_01.*G_BUS_30"):
        tables_from_network(m, net, registry)


def test_a_missing_file_is_a_contract_error(tmp_path):
    with pytest.raises(ContractError, match="network.nc"):
        load_solved_network(tmp_path / "network.nc")


# --------------------------------------------------------------------------
# driver: StudyConfig.from_network
# --------------------------------------------------------------------------

def test_from_network_and_from_dispatch_are_exclusive(tmp_path):
    with pytest.raises(ContractError, match="from_network.*from_dispatch|from_dispatch.*from_network"):
        StudyConfig(outdir=tmp_path, from_dispatch=tmp_path, from_network=tmp_path / "n.nc")


def test_from_network_round_trips_through_json(tmp_path):
    c = StudyConfig(outdir=tmp_path, from_network=tmp_path / "n.nc", k=1)
    assert StudyConfig.from_json(c.to_json()) == c
    assert c.to_json()["from_network"] == str(tmp_path / "n.nc")


@pytest.fixture(scope="module")
def studied(solved, tmp_path_factory):
    n, path = solved
    out = tmp_path_factory.mktemp("studied")
    ticks = []
    config = StudyConfig(outdir=out, from_network=path, k=1, screen=False, hours=HOURS)
    result = run_study(config, progress=lambda stage, done, total: ticks.append((stage, done, total)))
    return out, result, ticks


def test_the_study_writes_the_network_dispatch_and_records_where_it_came_from(grid, solved, studied):
    net, _ = grid
    n, path = solved
    out, _result, _ticks = studied
    written = pd.read_csv(out / "dispatch.csv")
    pd.testing.assert_frame_equal(written, to_dispatch_table(n), check_dtype=False)
    manifest = json.loads((out / "manifest.json").read_text())
    src = manifest["dispatch_source"]
    assert src["network"] == str(path)
    assert src["network_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert src["hours"] == HOURS
    assert src["commitment"] == COMMITMENT_SOLVED
    assert manifest["window"] is None and manifest["overlap"] is None   # the solve did not happen here
    assert manifest["config"]["from_network"] == str(path)


def test_the_stages_tick_ingest_then_dispatch_once_then_the_rest(studied):
    _out, _result, ticks = studied
    stages = []
    for stage, _d, _t in ticks:
        if stage not in stages:
            stages.append(stage)
    assert stages[:3] == ["ingest", "dispatch", "ranking"]
    assert [(d, t) for s, d, t in ticks if s == "dispatch"] == [(1, 1)]


def test_the_status_reads_a_completed_study_with_a_selection(studied):
    out, result, _ = studied
    status = stage_status(out)
    assert status["status"] == "completed"
    assert status["stages"]["dispatch"]["state"] == "done"
    assert status["selected_hours"] == sorted(int(h) for h in result.selected["hour"])


def test_an_unsolved_source_fails_in_the_dispatch_stage_with_an_artifact(grid, tmp_path):
    net, _ = grid
    fresh = to_pypsa(net, snapshots=HOURS, res_cf=res_cf_for(net, HOURS))
    src = tmp_path / "unsolved.nc"
    fresh.export_to_netcdf(str(src))
    out = tmp_path / "out"
    with pytest.raises(ContractError, match="not solved"):
        run_study(StudyConfig(outdir=out, from_network=src, k=1, screen=False))
    error = json.loads((out / "error_dispatch.json").read_text())
    assert error["stage"] == "dispatch"
    assert not (out / "dispatch.csv").exists()
