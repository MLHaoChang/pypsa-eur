"""
A Generator's DC/AC ratio (typed-cost-units issue 02, owner Q-TC-2, gatekeeper TC-02 GO).

`p_nom` is the AC rating; `dc_ac_ratio` declares the DC module rating as a multiple of it, so a
cost row priced per MW of DC can be priced onto `p_nom`. The conditions are the tests: every
write path checks it the same way (single create and update, bulk), blank clears it to NaN, a
project that never writes it never gets the column, and the LP never reads it (the fingerprint
does not move).
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from tests.conftest import build_network

GEN = "/api/network/generators"
BULK = "/api/network/_bulk"


@pytest.fixture
def net(install_network):
    n = build_network()
    install_network(n)
    return n


def _pv(**extra):
    return {"name": "pv", "bus": "B1", "carrier": "solar", "p_nom": 100.0, **extra}


def test_a_generator_created_without_it_gets_no_column(client, net):
    assert client.post(GEN, json=_pv()).status_code == 201
    assert "dc_ac_ratio" not in net.generators.columns


def test_create_and_update_round_trip_and_blank_clears(client, net):
    assert client.post(GEN, json=_pv(dc_ac_ratio=1.25)).status_code == 201
    assert net.generators.at["pv", "dc_ac_ratio"] == 1.25
    assert math.isnan(net.generators.at["gas", "dc_ac_ratio"]), "other rows: not declared"
    assert client.put(f"{GEN}/pv", json=_pv(dc_ac_ratio=1.4)).status_code == 200
    assert net.generators.at["pv", "dc_ac_ratio"] == 1.4
    assert client.put(f"{GEN}/pv", json=_pv(dc_ac_ratio=None)).status_code == 200
    assert math.isnan(net.generators.at["pv", "dc_ac_ratio"])


@pytest.mark.parametrize("bad", [0, -1, 4, "abc", 125])
def test_the_single_edit_refuses_an_invalid_ratio(client, net, bad):
    assert client.post(GEN, json=_pv(dc_ac_ratio=1.2)).status_code == 201
    r = client.put(f"{GEN}/pv", json=_pv(dc_ac_ratio=bad))
    assert r.status_code == 422
    assert net.generators.at["pv", "dc_ac_ratio"] == 1.2


@pytest.mark.parametrize("bad,why", [(4, "at most 3"), ("abc", "DC/AC ratio"), (125, "125 % is 1.25")])
def test_the_bulk_edit_refuses_an_invalid_ratio_and_writes_nothing(client, net, bad, why):
    r = client.patch(BULK, json={"component_class": "Generator", "names": ["solar"],
                                 "updates": {"dc_ac_ratio": bad}})
    assert r.status_code == 422 and why in r.json()["detail"]
    assert "dc_ac_ratio" not in net.generators.columns, "a refused batch creates no column"


def test_the_bulk_edit_writes_and_blank_clears(client, net):
    patch = {"component_class": "Generator", "names": ["solar"]}
    assert client.patch(BULK, json={**patch, "updates": {"dc_ac_ratio": "1.3"}}).status_code == 200
    assert net.generators.at["solar", "dc_ac_ratio"] == 1.3
    assert client.patch(BULK, json={**patch, "updates": {"dc_ac_ratio": ""}}).status_code == 200
    assert math.isnan(net.generators.at["solar", "dc_ac_ratio"])


def test_the_lp_never_reads_it_so_the_fingerprint_does_not_move(client, install_network, session_ctx):
    from services.workflow_tools import _network_input_fingerprint

    install_network(build_network())
    ctx = session_ctx(client)
    before = (_network_input_fingerprint(ctx), _network_input_fingerprint(ctx, exact=True))
    ctx.network.generators["dc_ac_ratio"] = float("nan")
    ctx.network.generators.at["solar", "dc_ac_ratio"] = 1.3
    assert (_network_input_fingerprint(ctx), _network_input_fingerprint(ctx, exact=True)) == before


def test_it_round_trips_through_netcdf(tmp_path):
    import pypsa

    from services.adequacy.eh_columns import normalise_eh_columns

    n = build_network()
    n.generators["dc_ac_ratio"] = float("nan")
    n.generators.at["solar", "dc_ac_ratio"] = 1.3
    normalise_eh_columns(n)
    n.export_to_netcdf(tmp_path / "n.nc")
    back = pypsa.Network(tmp_path / "n.nc")
    assert back.generators.at["solar", "dc_ac_ratio"] == 1.3
    assert math.isnan(back.generators.at["gas", "dc_ac_ratio"])


def _csv_zip(n, folder) -> bytes:
    import io
    import zipfile

    folder.mkdir(parents=True)
    n.export_to_csv_folder(folder)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in folder.iterdir():
            zf.write(f, f.name)
    return buf.getvalue()


def test_an_import_keeps_a_valid_ratio_and_clears_an_invalid_one(client, install_network, session_ctx, tmp_path):
    """An import is not refused for a ratio, but 4 or "abc" is never stored: cleared to not
    declared, so a DC-priced row refuses rather than pricing with it."""
    install_network(build_network())
    n = build_network()
    n.add("Generator", "pv2", bus="B1", carrier="solar", p_nom=50.0)
    n.add("Generator", "pv3", bus="B1", carrier="solar", p_nom=50.0)
    n.generators["dc_ac_ratio"] = pd.Series({"solar": 1.3, "pv2": 4.0, "pv3": "abc"}, dtype=object)
    r = client.post("/api/io/import/csv", files={
        "file": ("n.zip", _csv_zip(n, tmp_path / "csv"), "application/zip")})
    assert r.status_code == 200, r.text
    gens = session_ctx(client).network.generators
    assert gens.at["solar", "dc_ac_ratio"] == 1.3
    assert math.isnan(gens.at["pv2", "dc_ac_ratio"]) and math.isnan(gens.at["pv3", "dc_ac_ratio"])
    assert math.isnan(gens.at["gas", "dc_ac_ratio"])
