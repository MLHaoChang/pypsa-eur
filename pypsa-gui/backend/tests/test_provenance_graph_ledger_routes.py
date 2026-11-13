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
    first = client.get(url).json()
    assert first["stale"] == {}
    assert first["stale_unchecked"] == []

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

    # GK B F1 (P1): a fork the ledger cannot check is named in `stale_unchecked`, never read as fresh, and
    # contributes no made-up `stale`; GET and PUT alike, and never a 500.
    unchecked = [
        {"option_id": "bess_pv_2h", "option_label": "Battery (2 hours) with PV"}
    ]
    good = path.read_bytes()
    path.write_bytes(b"not a netcdf file")
    got = client.get(url)
    assert got.status_code == 200, got.text
    assert got.json()["stale_unchecked"] == unchecked
    assert got.json()["stale"] == {}
    put = client.put(
        url,
        json={"rows": [{"key": "discount_rate", "value": 0.07, "unit": "per unit"}]},
    )
    assert put.status_code == 200, put.text
    assert put.json()["stale_unchecked"] == unchecked
    assert put.json()["stale"] == {}

    # A graph build that raises on a readable fork lands in the same catch (moved under it).
    import os
    import time

    from services.study import asset_view

    path.write_bytes(good)
    t = time.time_ns() + 2_000_000
    os.utime(path, ns=(t, t))  # a new file key, so the snapshot is rebuilt

    def broken_snapshot(n):
        raise RuntimeError("graph build failed")

    monkeypatch.setattr(asset_view, "asset_snapshot", broken_snapshot)
    got = client.get(url)
    assert got.status_code == 200, got.text
    # The other forks' files are unchanged, so their cached snapshots stand; only the rewritten one is rebuilt.
    assert got.json()["stale_unchecked"] == unchecked
    assert got.json()["stale"] == {}


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


# ── provenance-graph 11: the battery rows walk the fields they compile to ──────


@pytest.fixture(scope="module")
def battery_pack():
    """A pack-built site network with the battery and PV, the ledger it was built from, and the grid-only
    baseline (no battery) from the same ledger."""
    from services.study import library, packs, questions
    from tests.golden import site_fixture as SF

    defaults = library.load_defaults()
    intake = {
        **SF.site_intake(),
        "tariff": {"tariff_id": "de_industrial_illustrative"},
    }
    ledger = library.seed_ledger(questions.BESS_AT_SITE, intake, defaults)
    build = lambda option: packs.build_site_network(  # noqa: E731
        intake, ledger, option, library=defaults
    )
    return build("bess_pv_2h"), ledger, build("none")


def _battery_keys():
    from services.study import asset_view

    return sorted(
        {m.key for m in asset_view.LEDGER_FIELDS if m.component == "StorageUnit"}
    )


def test_each_battery_ledger_row_walks_the_fields_it_compiles_to(battery_pack):
    """Every battery row of `asset_view.LEDGER_FIELDS`, on a network that has the battery: the fields walked are
    the row's fields on this network, each value is the network column and the pack's conversion of the ledger
    value, and the verdict is uncompared (a pack default compares nothing; never fresh)."""
    from services.provenance_graph.explain import explain_ledger_row
    from services.study import asset_view

    n, ledger, _ = battery_pack
    rows = {r.key: r for r in ledger.rows}
    keys = _battery_keys()
    assert len(keys) == 7  # the round trip is one row, two fields
    for key in keys:
        want = [
            m
            for m in asset_view.LEDGER_FIELDS
            if m.key == key
            and m.name in getattr(n, n.components[m.component].list_name).index
        ]
        out = explain_ledger_row(n, key)
        assert out["target"] == {"ledger_key": key}
        assert [f["target"] for f in out["fields"]] == [
            {"component": m.component, "name": m.name, "field": m.field}
            for m in want
        ], key
        for m, field in zip(want, out["fields"]):
            column = getattr(n, n.components[m.component].list_name).at[
                m.name, m.field
            ]
            first = field["steps"][0]
            assert first["node"] == f"input:{m.component}/{m.name}/{m.field}"
            assert first["value"] == pytest.approx(column), key
            assert column == pytest.approx(m.to_store(rows[key].value)), key
            assert first["stamp"]["status"] == "default", key
            assert field["outcome"]["state"] == "uncompared", key
        assert out["outcome"]["state"] == "uncompared", key
        assert out["outcome"]["complete"] is True, key
    # The round trip compiles to both halves; the discount rate to the battery and the PV.
    assert len(explain_ledger_row(n, "battery_round_trip_efficiency")["fields"]) == 2
    assert [
        f["target"]["component"]
        for f in explain_ledger_row(n, "discount_rate")["fields"]
    ] == ["StorageUnit", "Generator"]


def test_a_battery_row_on_a_network_without_the_battery_refuses(battery_pack):
    from services.provenance_graph.explain import TargetUnknown, explain_ledger_row

    _, _, baseline = battery_pack
    assert "battery" not in baseline.storage_units.index
    for key in _battery_keys():
        with pytest.raises(TargetUnknown) as exc:
            explain_ledger_row(baseline, key)
        assert exc.value.error_kind == "why_target_unknown"
        assert exc.value.status == 404
