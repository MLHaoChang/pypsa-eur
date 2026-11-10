"""
provenance-graph 02, TCU-07 through the routes: the Guided ledger carries the `stale` the option fork's asset card
reads (GK B G1, G4). Apart from `test_provenance_graph.py` because it runs a study, which is slow.
"""

from __future__ import annotations

import json

import pypsa
import pytest

from services import provenance_graph as G
from tests.test_provenance_graph import _price_pv_through_its_ratio


@pytest.fixture
def studies_on(monkeypatch):
    from tests.study_s4_support import enable_studies

    yield from enable_studies(monkeypatch)


def test_the_ledger_routes_show_the_fork_card_s_stale_after_a_ratio_change(
    client, api_project, monkeypatch, project_storage_dir, studies_on
):
    """TCU-07 acceptance, through the routes: GET and PUT `/ledger` carry, for the PV price row, the `stale`
    the option fork's asset card reads (the graph's verdict on the fork network as saved)."""
    from services import solver_service
    from services.pypsa_service import PyPSAService
    from services.study import store
    from tests.study_s4_support import create_pack_study, wait_run
    from tests.test_study_expert_edits_routes import INTAKE
    from tests.test_study_runner import FakeSolver

    monkeypatch.setattr(solver_service, "run_simulation", FakeSolver())
    name = "pg02-ledger"
    api_project(f"{name}-src")
    r = create_pack_study(
        client,
        f"{name}-src",
        name,
        intake={**INTAKE, "pv": {"enabled": True, "kind": "rooftop"}},
    )
    assert r.status_code == 201, r.text
    sid = r.json()["study_id"]
    assert (
        client.post(f"/api/projects/{name}/studies/{sid}/run", json={}).status_code
        == 202
    )
    assert wait_run(client, name, sid)["status"] == "done"
    url = f"/api/projects/{name}/studies/{sid}/ledger"
    assert client.get(url).json()["stale"] == {}

    path = project_storage_dir(f"{name}-opt-bess_pv_2h") / "network.nc"
    n = pypsa.Network()
    with PyPSAService.get_netcdf_io_lock():
        PyPSAService.import_network_from_netcdf(n, path)
    _price_pv_through_its_ratio(n)
    n.generators.at["pv", "dc_ac_ratio"] = 1.3
    with PyPSAService.get_netcdf_io_lock():
        PyPSAService.export_network_to_netcdf(n, path)
    saved = pypsa.Network()
    with PyPSAService.get_netcdf_io_lock():
        PyPSAService.import_network_from_netcdf(saved, path)
    card = json.loads(
        json.dumps(G.stale_entries(saved, "Generator", "pv")["overnight_cost"]["stale"])
    )
    assert card["reason"] == "scale input changed"

    want = {
        "pv_rooftop_eur_per_kw": [
            {
                "option_id": "bess_pv_2h",
                "option_label": "Battery (2 hours) with PV",
                "component": "Generator",
                "name": "pv",
                "field": "overnight_cost",
                "stale": card,
            }
        ]
    }
    # G4: the stored study (and its ledger hash) is what a GET reads, untouched by the sibling `stale`.
    from services.study import packs

    study_file = project_storage_dir(name) / "studies" / f"{sid}.json"
    stored_bytes = study_file.read_bytes()
    got = client.get(url).json()
    assert got["stale"] == want
    assert study_file.read_bytes() == stored_bytes
    stored = store.load_study(project_storage_dir(name), sid).ledger
    assert "stale" not in stored.model_dump(mode="json")
    assert packs.ledger_hash(stored) == packs.ledger_hash(
        type(stored).model_validate(got["ledger"])
    )

    # G1: an Expert edit that rewrites the fork flips the verdict on the next GET (the snapshot cache is keyed on
    # the fork file, so it cannot serve the old one), both ways.
    _rewrite_ratio(path, 1.2)
    assert client.get(url).json()["stale"] == {}
    _rewrite_ratio(path, 1.3)
    assert client.get(url).json()["stale"] == want
    put = client.put(
        url,
        json={"rows": [{"key": "discount_rate", "value": 0.06, "unit": "per unit"}]},
    )
    assert put.status_code == 200, put.text
    assert put.json()["stale"] == want
    # A view beside the ledger: the stored rows carry no `stale`.
    assert all("stale" not in r for r in put.json()["ledger"]["rows"])
    after = store.load_study(project_storage_dir(name), sid).ledger
    assert packs.ledger_hash(after) == packs.ledger_hash(
        type(after).model_validate(put.json()["ledger"])
    )


def _rewrite_ratio(path, ratio):
    """What a saved Expert edit of the fork's DC/AC ratio leaves on disk."""
    import os
    import time

    from services.pypsa_service import PyPSAService

    n = pypsa.Network()
    with PyPSAService.get_netcdf_io_lock():
        PyPSAService.import_network_from_netcdf(n, path)
    n.generators.at["pv", "dc_ac_ratio"] = ratio
    before = path.stat().st_mtime_ns
    with PyPSAService.get_netcdf_io_lock():
        PyPSAService.export_network_to_netcdf(n, path)
    if path.stat().st_mtime_ns == before:  # a coarse clock: a real save is later
        t = time.time_ns() + 1_000_000
        os.utime(path, ns=(t, t))
