"""
provenance-graph 02, TCU-07 through the routes: the Guided ledger carries the `stale` the option fork's asset card
reads (GK B G1, G4). Apart from `test_provenance_graph.py` because it runs a study, which is slow.
"""

from __future__ import annotations

import json
import math

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


def _times(k):
    return lambda v: v * k


# Written out, not read from `asset_view.LEDGER_FIELDS`, so a dropped or added entry there (or in the walk) fails.
# Each field with the pack's conversion of the row value (`packs.battery_parts`): kEUR → EUR, % → share, the
# round trip split sqrt/sqrt.
BATTERY_ROWS = {
    "battery_inverter_eur_per_kw": [
        ("StorageUnit", "battery", "inv_power_overnight", _times(1000.0))
    ],
    "battery_inverter_lifetime_years": [
        ("StorageUnit", "battery", "inv_power_lifetime", _times(1.0))
    ],
    "battery_inverter_fom_pct_per_year": [
        ("StorageUnit", "battery", "inv_power_fom_share", _times(0.01))
    ],
    "battery_storage_eur_per_kwh": [
        ("StorageUnit", "battery", "inv_energy_overnight", _times(1000.0))
    ],
    "battery_storage_lifetime_years": [
        ("StorageUnit", "battery", "inv_energy_lifetime", _times(1.0))
    ],
    "battery_round_trip_efficiency": [
        ("StorageUnit", "battery", "efficiency_store", math.sqrt),
        ("StorageUnit", "battery", "efficiency_dispatch", math.sqrt),
    ],
    # The discount rate is the battery's and the PV's.
    "discount_rate": [
        ("StorageUnit", "battery", "discount_rate", _times(1.0)),
        ("Generator", "pv", "discount_rate", _times(1.0)),
    ],
}


def _pack(option):
    """A pack-built site network for `option` and the seeded ledger it was built from."""
    from services.study import library, packs, questions
    from tests.golden import site_fixture as SF

    defaults = library.load_defaults()
    intake = {
        **SF.site_intake(),
        "tariff": {"tariff_id": "de_industrial_illustrative"},
    }
    ledger = library.seed_ledger(questions.BESS_AT_SITE, intake, defaults)
    return packs.build_site_network(intake, ledger, option, library=defaults), ledger


def test_each_battery_ledger_row_walks_the_fields_it_compiles_to():
    """Every battery row on a network with the battery and PV: exactly the fields the pack wrote from it, each
    value the network column and the pack's conversion of the ledger value, and the verdict uncompared (a pack
    default is an input with only its stamp, which compares nothing: never fresh, GK B P1 #1170)."""
    from services.provenance_graph.explain import explain_ledger_row
    from services.study import asset_view

    n, ledger = _pack("bess_pv_2h")
    rows = {r.key: r for r in ledger.rows}
    assert {
        (m.key, m.component, m.name, m.field)
        for m in asset_view.LEDGER_FIELDS
        if m.key in BATTERY_ROWS
    } == {(k, *f[:3]) for k, fields in BATTERY_ROWS.items() for f in fields}
    for key, want in BATTERY_ROWS.items():
        out = explain_ledger_row(n, key)
        assert out["target"] == {"ledger_key": key}
        assert [
            (f["target"]["component"], f["target"]["name"], f["target"]["field"])
            for f in out["fields"]
        ] == [f[:3] for f in want], key
        for (component, name, field_name, to_store), field in zip(want, out["fields"]):
            column = float(
                getattr(n, n.components[component].list_name).at[name, field_name]
            )
            compiled = to_store(rows[key].value)
            first = field["steps"][0]
            assert first["node"] == f"input:{component}/{name}/{field_name}"
            assert first["value"] == column, key
            if key == "battery_round_trip_efficiency":  # sqrt of the row: not exact
                assert math.isclose(column, compiled, rel_tol=1e-12), key
            else:
                assert column == compiled, key
            assert first["stamp"]["status"] == "default", key
            assert field["outcome"]["state"] == "uncompared", key
        assert out["outcome"]["state"] == "uncompared", key
        assert out["outcome"]["complete"] is True, key


def test_a_battery_row_on_a_network_without_the_battery_refuses():
    from services.provenance_graph.explain import TargetUnknown, explain_ledger_row

    baseline, _ = _pack("none")
    assert "battery" not in baseline.storage_units.index
    assert "pv" not in baseline.generators.index
    for key in BATTERY_ROWS:
        with pytest.raises(TargetUnknown) as exc:
            explain_ledger_row(baseline, key)
        assert exc.value.error_kind == "why_target_unknown"
        assert exc.value.status == 404
