"""The vertical slice for a client's own tables (increment 7).

Everything else about this source is tested against a STUBBED registry, which
proves the pieces validate and nothing more. This file is the only place that
runs `run_study` with `from_external` against the real IEEE 39-bus grid and
asks whether a handoff bundle comes out the other end — the question the four
source branches in `run_study` exist to answer, and one that a stub cannot.

Screening is ON here, unlike the increment-5 `from_project` slice. That is
deliberate: `year_study` writes the full `bundle_h<hour>/` — the `.raw`, the
`.dyr`, the contingency set and the ledger — only in the second pass that
screening gates (`year_study.py:583`), and with it off the bare `.raw` at the top
level is "the last touch of this hour". The bundle is what a client is actually
handed, so a slice that skipped screening would prove the stages run and not that
the deliverable appears. `k=1` and two hours keep the N-1 pass affordable, and the
fixture is module-scoped so it runs once.

Why the fixture is derived from the grid rather than hard-coded
--------------------------------------------------------------
The unit ids and load buses come from `registry_from_net` and the net's own load
table, not from fifteen names typed into this file. Hard-coding them would make
the test fail for the WRONG reason the day case39's naming changes — and the
producer's whole job is to check a client's names against the grid's, so a
fixture that cannot drift out of agreement with the grid is not testing that.
The VALUES are chosen here: a flat, balanced dispatch that converges.

What this still does not prove
------------------------------
That a real client export parses. The CSVs are written here, so this is the
shape of the contract end to end, not evidence about anyone's market model — the
limit increment 6 recorded for its hand-written PowerFactory bundles, and it
closes the same way, when a client file lands.
"""
import json

import pandas as pd
import pytest

from gridspine.drivers.status import stage_status
from gridspine.drivers.study import StudyConfig, run_study
from gridspine.ingest.pandapower_source import load_case39_res, registry_from_net

HOURS = 2


@pytest.fixture(scope="module")
def grid():
    net = load_case39_res()
    return net, registry_from_net(net)


@pytest.fixture(scope="module")
def client_tables(grid, tmp_path_factory):
    """A flat two-hour dispatch and the matching demand, as a client would send.

    Each unit carries its share of the hour's demand so the snapshot balances
    without leaning on the external grid's slack — the load flow has to converge
    for the later stages to have anything to rank.
    """
    net, registry = grid
    out = tmp_path_factory.mktemp("client")

    load_buses = list(net.bus.loc[net.load.bus, "name"].astype(str))
    load_p = list(net.load["p_mw"].astype(float))
    load_q = list(net.load["q_mvar"].astype(float))
    total_p = sum(load_p)

    units = list(registry.index.astype(str))
    share = total_p / len(units)

    dispatch = pd.DataFrame([
        {"unit_id": u, "hour": h, "p_mw": round(share, 3), "q_mvar": 0.0, "status": 1}
        for h in range(HOURS) for u in units
    ])
    loads = pd.DataFrame([
        {"bus": b, "hour": h, "p_mw": p, "q_mvar": q}
        for h in range(HOURS) for b, p, q in zip(load_buses, load_p, load_q)
    ])

    dispatch_path = out / "client_dispatch.csv"
    loads_path = out / "client_loads.csv"
    dispatch.to_csv(dispatch_path, index=False)
    loads.to_csv(loads_path, index=False)
    return dispatch_path, loads_path, dispatch, loads


@pytest.fixture(scope="module")
def studied(client_tables, tmp_path_factory):
    dispatch_path, loads_path, _d, _l = client_tables
    out = tmp_path_factory.mktemp("external-studied")
    ticks = []
    config = StudyConfig(
        outdir=out, from_external=dispatch_path, from_external_loads=loads_path,
        k=1, screen=True, hours=HOURS,
    )
    result = run_study(
        config, progress=lambda stage, done, total: ticks.append((stage, done, total)),
    )
    return out, result, ticks


def test_a_client_csv_reaches_a_handoff_bundle(studied):
    """The whole point: a file the client wrote becomes a PSS/E bundle, with no
    PyPSA solve anywhere in between."""
    out, result, _ticks = studied
    assert len(result.selected) >= 1
    for hour in result.selected["hour"]:
        bundle = out / f"bundle_h{int(hour)}"
        assert (bundle / "manifest.json").is_file()
        assert list(bundle.glob("*.raw")), f"no .raw in {bundle.name}"
        assert list(bundle.glob("*.dyr")), f"no .dyr in {bundle.name}"


def test_the_artifacts_are_the_clients_tables_round_tripped(studied, client_tables):
    """`dispatch.csv` and `loads.csv` are the stage boundary. What the client
    sent has to be what the later stages read — modulo the contract's dtypes."""
    out, _result, _ticks = studied
    _dp, _lp, dispatch, loads = client_tables
    written = pd.read_csv(out / "dispatch.csv")
    assert len(written) == len(dispatch)
    assert sorted(written["unit_id"].unique()) == sorted(dispatch["unit_id"].unique())
    written_loads = pd.read_csv(out / "loads.csv")
    assert len(written_loads) == len(loads)
    assert sorted(written_loads["bus"].unique()) == sorted(loads["bus"].unique())


def test_the_manifest_names_both_files_and_their_digests(studied, client_tables):
    """Traceability is the reason the provenance record carries two digests: a
    bundle handed to a client has to say which tables it came from."""
    import hashlib

    out, _result, _ticks = studied
    dispatch_path, loads_path, _d, _l = client_tables
    src = json.loads((out / "manifest.json").read_text())["dispatch_source"]
    assert src["external"] == str(dispatch_path)
    assert src["loads"] == str(loads_path)
    assert src["external_sha256"] == hashlib.sha256(dispatch_path.read_bytes()).hexdigest()
    assert src["loads_sha256"] == hashlib.sha256(loads_path.read_bytes()).hexdigest()
    assert src["hours"] == HOURS


def test_the_dispatch_stage_ticks_once_because_nothing_was_solved(studied):
    """A generated year ticks per UC window. An external source has no solve, so
    one tick — the same shape `from_network` produces."""
    _out, _result, ticks = studied
    stages = []
    for stage, _d, _t in ticks:
        if stage not in stages:
            stages.append(stage)
    assert stages[:3] == ["ingest", "dispatch", "ranking"]
    assert [(d, t) for s, d, t in ticks if s == "dispatch"] == [(1, 1)]


def test_the_status_reads_a_completed_study_from_the_artifacts(studied):
    out, result, _ticks = studied
    status = stage_status(out)
    assert status["status"] == "completed"
    assert status["stages"]["dispatch"]["state"] == "done"
    assert status["selected_hours"] == sorted(int(h) for h in result.selected["hour"])


def test_the_config_in_the_manifest_records_the_source_it_ran_from(studied, client_tables):
    out, _result, _ticks = studied
    dispatch_path, loads_path, _d, _l = client_tables
    config = json.loads((out / "manifest.json").read_text())["config"]
    assert config["from_external"] == str(dispatch_path)
    assert config["from_external_loads"] == str(loads_path)
    assert config["from_network"] is None and config["from_dispatch"] is None
