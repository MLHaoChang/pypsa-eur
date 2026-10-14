"""
OPEN-ITEMS item 8: component names are checked at the edge.

A component name flows from a request body into the network, into download
filenames, into spreadsheets and into model-facing text. Two defects were
symptoms of nothing checking it, each fixed at its sink: the untrusted-fence
bypass (#18) and the `Content-Disposition` header (#7). This file pins the
root-cause fix: one character-class rule (`services/component_names.py`)
applied wherever a NEW name enters.

Scope (owner decision, 2026-10-12): every interactive create and rename, over
HTTP and through chat tools, carriers created implicitly by a `carrier` field,
and every NEW import (netCDF, CSV, Excel, project bundle). Opening an EXISTING
project stays permissive, so nobody is locked out of saved work; the sinks keep
their own guards for those names.

The rule refuses only what no real network needs: control characters (C0, DEL,
C1, which includes tab and newline), Unicode format characters (bidi overrides,
zero-width), line and paragraph separators, lone surrogates, empty names and
names over 256 characters. PyPSA-Eur's own names carry spaces, `/`, `:`, `->`,
`+`, `@`, `#` and non-ASCII letters, and must keep working.

Every refusal test sends the SAME request with a good name first, so a red
result can only mean the name was the problem.
"""
from __future__ import annotations

import io
import zipfile

import pandas as pd
import pypsa
import pytest

# What real networks name things. None of these may be refused.
GOOD_NAMES = [
    "Bus 0", "DE0 0 urban central water tanks charger-2050", "EU gas",
    "H2 pipeline A -> B", "gas pipeline new DE1 0 <-> DE1 1", "way/123456:a-220",
    "relation/42-380", "merged_17+3", "gas@2030", "campus:TR_132_33_63#1",
    "Bus 5 (Heat)", "L1-2", "auto:3", "Grüße", "北京", "__voll_B1", "x" * 256,
    'ev"il', "../esc",
]

# Each defeats a sink or hides from a reader.
BAD_NAMES = [
    "a\nb", "a\rb", "tab\there", "nul\x00", "del\x7f", "c1\x85", "ls x",
    "ps x", "rlo‮exe.gnp", "lri⁦x", "zw​sp", "bom﻿",
    "lone\ud800", "", "   ", "x" * 257,
]
# A handful of BAD_NAMES for the (slower) route-level tests.
BAD_SAMPLE = ["a\nb", "rlo‮exe.gnp", "x" * 257]


def _two_bus_network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=3, freq="h"))
    n.add("Bus", "B1", v_nom=110.0)
    n.add("Bus", "B2", v_nom=110.0)
    n.add("Bus", "B3", v_nom=20.0)
    n.add("Carrier", "AC")
    n.add("Generator", "G1", bus="B1", p_nom=10.0)
    n.add("GlobalConstraint", "GC1", type="primary_energy", sense="<=",
          constant=1e6, carrier_attribute="co2_emissions")
    return n


# ── the rule itself ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", GOOD_NAMES)
def test_a_real_network_name_is_accepted(name):
    from services.component_names import name_problem

    assert name_problem(name) is None


@pytest.mark.parametrize("name", BAD_NAMES)
def test_a_name_that_defeats_a_sink_is_refused(name):
    from services.component_names import name_problem

    assert name_problem(name)


def test_a_non_string_name_is_refused():
    from services.component_names import name_problem

    assert name_problem(None) and name_problem(5)


# ── HTTP creates: every class ───────────────────────────────────────────────

CREATE_BODIES = {
    "buses": {"v_nom": 110.0},
    "carriers": {},
    "generators": {"bus": "B1", "p_nom": 10.0},
    "loads": {"bus": "B1", "p_set": 5.0},
    "lines": {"bus0": "B1", "bus1": "B2", "x": 0.1, "r": 0.01, "s_nom": 10.0},
    "links": {"bus0": "B1", "bus1": "B2", "p_nom": 10.0},
    "storage_units": {"bus": "B1", "p_nom": 10.0},
    "stores": {"bus": "B1", "e_nom": 10.0},
    "transformers": {"bus0": "B1", "bus1": "B3", "s_nom": 10.0, "x": 0.1},
    "shunt_impedances": {"bus": "B1"},
    "global_constraints": {"type": "primary_energy", "sense": "<=",
                           "constant": 1e6, "carrier_attribute": "co2_emissions"},
}


@pytest.mark.parametrize("attr", sorted(CREATE_BODIES))
@pytest.mark.parametrize("bad", BAD_SAMPLE)
def test_every_create_route_refuses_a_bad_name_and_adds_nothing(
        client, install_network, session_ctx, attr, bad):
    install_network(_two_bus_network())
    body = CREATE_BODIES[attr]
    good = client.post(f"/api/network/{attr}", json={"name": "Good One", **body})
    assert good.status_code == 201, (attr, good.text)

    before = list(getattr(session_ctx(client).network, attr).index)
    r = client.post(f"/api/network/{attr}", json={"name": bad, **body})
    assert r.status_code == 422, (attr, r.status_code, r.text)
    assert list(getattr(session_ctx(client).network, attr).index) == before


def test_a_bad_carrier_in_a_carrier_field_is_refused_and_nothing_is_added(
        client, install_network, session_ctx):
    install_network(_two_bus_network())
    ok = client.post("/api/network/generators",
                     json={"name": "G2", "bus": "B1", "carrier": "wind onshore"})
    assert ok.status_code == 201, ok.text

    n = session_ctx(client).network
    carriers, gens = list(n.carriers.index), list(n.generators.index)
    r = client.post("/api/network/generators",
                    json={"name": "G3", "bus": "B1", "carrier": "gas\nturbine"})
    assert r.status_code == 422, r.text
    n = session_ctx(client).network
    assert list(n.carriers.index) == carriers
    assert list(n.generators.index) == gens


def test_a_bulk_update_that_would_create_a_bad_carrier_changes_nothing(
        client, install_network, session_ctx):
    n = _two_bus_network()
    n.add("Generator", "G2", bus="B1", p_nom=5.0)
    install_network(n)
    ok = client.patch("/api/network/_bulk", json={
        "component_class": "Generator", "names": ["G1"], "updates": {"carrier": "biomass"}})
    assert ok.status_code == 200, ok.text

    n = session_ctx(client).network
    carriers = list(n.carriers.index)
    before = n.generators["carrier"].to_dict()
    # Row form is one batch per row. The first row is good and would CREATE a
    # carrier; the second is bad. Nothing may land, not even the first row.
    r = client.patch("/api/network/_bulk", json={
        "component_class": "Generator", "rows": [
            {"name": "G1", "updates": {"carrier": "geothermal"}},
            {"name": "G2", "updates": {"carrier": "bio‮mass"}},
        ]})
    assert r.status_code == 422, r.text
    n = session_ctx(client).network
    assert list(n.carriers.index) == carriers
    assert n.generators["carrier"].to_dict() == before


def test_an_existing_odd_carrier_can_still_be_referenced(
        client, install_network, session_ctx):
    """Permissive for what is already there: a carrier that came with a saved
    network is referenced, not created, so it is not re-checked."""
    n = _two_bus_network()
    n.add("Carrier", "old\tcarrier")
    install_network(n)
    r = client.post("/api/network/generators",
                    json={"name": "G2", "bus": "B1", "carrier": "old\tcarrier"})
    assert r.status_code == 201, r.text


# ── HTTP renames: every path ────────────────────────────────────────────────

@pytest.mark.parametrize("attr,old,body", [
    ("buses", "B1", {}), ("generators", "G1", {"bus": "B1"}), ("carriers", "AC", {}),
    ("global_constraints", "GC1", {}),
])
@pytest.mark.parametrize("bad", BAD_SAMPLE)
def test_every_put_rename_refuses_a_bad_name_and_renames_nothing(
        client, install_network, session_ctx, attr, old, body, bad):
    install_network(_two_bus_network())
    good = client.put(f"/api/network/{attr}/{old}", json={"name": "Renamed", **body})
    assert good.status_code == 200, (attr, good.text)

    r = client.put(f"/api/network/{attr}/Renamed", json={"name": bad, **body})
    assert r.status_code == 422, (attr, r.status_code, r.text)
    assert "Renamed" in getattr(session_ctx(client).network, attr).index


def test_a_refused_rename_does_not_leave_its_new_carrier_behind(
        client, install_network, session_ctx):
    """The name is checked before `ensure_carrier`, which would add the
    carrier: a refused PUT must leave the network exactly as it was."""
    install_network(_two_bus_network())
    carriers = list(session_ctx(client).network.carriers.index)
    r = client.put("/api/network/generators/G1",
                   json={"name": "G\n1", "bus": "B1", "carrier": "geothermal"})
    assert r.status_code == 422, r.text
    n = session_ctx(client).network
    assert list(n.carriers.index) == carriers
    assert "G1" in n.generators.index


@pytest.mark.parametrize("bad", BAD_SAMPLE)
def test_the_bus_rename_route_refuses_a_bad_name(
        client, install_network, session_ctx, bad):
    install_network(_two_bus_network())
    good = client.post("/api/network/buses/B1/rename", json={"new_name": "B1x"})
    assert good.status_code == 200, good.text

    r = client.post("/api/network/buses/B1x/rename", json={"new_name": bad})
    assert r.status_code == 422, r.text
    assert "B1x" in session_ctx(client).network.buses.index


# ── chat tools ──────────────────────────────────────────────────────────────

def test_the_create_component_tool_refuses_a_bad_name(install_network):
    from fastapi import HTTPException

    from services import chat_tools
    n = install_network(_two_bus_network())
    chat_tools.create_component("Bus", "Chat Bus", {"v_nom": 110.0})
    assert "Chat Bus" in n.buses.index

    before = list(n.buses.index)
    with pytest.raises(HTTPException) as exc:
        chat_tools.create_component("Bus", "x‮evil", {"v_nom": 110.0})
    assert exc.value.status_code == 422
    assert list(n.buses.index) == before


def test_a_batch_with_one_bad_name_creates_nothing(install_network):
    from fastapi import HTTPException

    from services import chat_tools
    n = install_network(_two_bus_network())
    ok = chat_tools.batch_create_components("Bus", [{"name": "A1"}, {"name": "A2"}])
    assert set(ok["created"]) == {"A1", "A2"}

    before = list(n.buses.index)
    with pytest.raises(HTTPException) as exc:
        chat_tools.batch_create_components(
            "Bus", [{"name": "A3"}, {"name": "A\n4"}, {"name": "A5"}])
    assert exc.value.status_code == 422
    assert list(n.buses.index) == before


def test_the_update_component_tool_refuses_a_bad_rename(install_network):
    from fastapi import HTTPException

    from services import chat_tools
    n = install_network(_two_bus_network())
    chat_tools.update_component("Generator", "G1", new_name="G1x")
    assert "G1x" in n.generators.index

    with pytest.raises(HTTPException) as exc:
        chat_tools.update_component("Generator", "G1x", new_name="G\x001")
    assert exc.value.status_code == 422
    assert "G1x" in n.generators.index


def test_the_vision_tool_skips_a_bad_name_and_keeps_the_good_ones(
        tmp_projects_dir, install_network):
    from services import chat_tools, upload_service
    from tests.test_chat_multimodal import _TINY_PNG, _VisionClient

    n = _two_bus_network()
    install_network(n, name="P")
    (tmp_projects_dir / "P").mkdir(parents=True, exist_ok=True)
    (tmp_projects_dir / "P" / "network.nc").write_bytes(b"")
    meta = upload_service.add_upload("P", _TINY_PNG, "topology.png", "image/png")
    payload = (
        '{"buses": [{"name": "V1", "px": 1, "py": 1},'
        ' {"name": "V\\n2", "px": 2, "py": 2},'
        ' {"name": "V3", "px": 3, "py": 3}],'
        ' "lines": [{"name": "VL\\u202e1", "bus0": "V1", "bus1": "V3"},'
        ' {"name": "VL2", "bus0": "V1", "bus1": "V3"}]}'
    )
    result = chat_tools.reconstruct_network_from_image(
        file_id=meta.file_id, client=_VisionClient(payload))
    assert set(result["buses_created"]) == {"V1", "V3"}
    assert result["lines_created"] == ["VL2"]
    assert "V\n2" not in n.buses.index
    assert "VL‮1" not in n.lines.index


# ── new imports ─────────────────────────────────────────────────────────────

def _netcdf_bytes(n: pypsa.Network, tmp_path) -> bytes:
    path = tmp_path / "net.nc"
    n.export_to_netcdf(path)
    return path.read_bytes()


def _with_a_bad_generator() -> pypsa.Network:
    n = _two_bus_network()
    n.add("Generator", "evil‮gnp", bus="B1", p_nom=1.0)
    return n


def test_a_netcdf_import_with_a_bad_name_is_refused(
        client, install_network, session_ctx, tmp_path):
    install_network(_two_bus_network())
    ok = client.post("/api/io/import/netcdf", files={
        "file": ("ok.nc", _netcdf_bytes(_two_bus_network(), tmp_path), "application/x-netcdf")})
    assert ok.status_code == 200, ok.text

    r = client.post("/api/io/import/netcdf", files={
        "file": ("bad.nc", _netcdf_bytes(_with_a_bad_generator(), tmp_path),
                 "application/x-netcdf")})
    assert r.status_code == 422, r.text
    assert "evil‮gnp" not in session_ctx(client).network.generators.index


def _csv_zip(n: pypsa.Network, tmp_path) -> bytes:
    folder = tmp_path / "csv"
    folder.mkdir(parents=True)
    n.export_to_csv_folder(folder)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in folder.iterdir():
            zf.write(f, f.name)
    return buf.getvalue()


def test_a_csv_import_with_a_bad_name_is_refused(
        client, install_network, session_ctx, tmp_path):
    install_network(_two_bus_network())
    ok = client.post("/api/io/import/csv", files={
        "file": ("ok.zip", _csv_zip(_two_bus_network(), tmp_path / "a"), "application/zip")})
    assert ok.status_code == 200, ok.text

    r = client.post("/api/io/import/csv", files={
        "file": ("bad.zip", _csv_zip(_with_a_bad_generator(), tmp_path / "b"),
                 "application/zip")})
    assert r.status_code == 422, r.text
    assert "evil‮gnp" not in session_ctx(client).network.generators.index


def _legacy_workbook(bus_names: list[str]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "buses"
    ws.append(["name", "v_nom"])
    for b in bus_names:
        ws.append([b, 110.0])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_an_excel_import_with_a_bad_name_is_refused(
        client, install_network, session_ctx):
    install_network(_two_bus_network())
    ok = client.post("/api/io/import/excel", files={
        "file": ("ok.xlsx", _legacy_workbook(["X1", "X2"]), "application/octet-stream")})
    assert ok.status_code == 200, ok.text

    r = client.post("/api/io/import/excel", files={
        "file": ("bad.xlsx", _legacy_workbook(["X1", "X‮2"]), "application/octet-stream")})
    assert r.status_code == 422, r.text
    assert "X‮2" not in session_ctx(client).network.buses.index


def _bundle_with(client, name: str, network: pypsa.Network, tmp_path) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(client.get(f"/api/projects/{name}/bundle").content))
    nc = _netcdf_bytes(network, tmp_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for m in src.namelist():
            zf.writestr(m, nc if m == "network.nc" else src.read(m))
    return buf.getvalue()


def test_a_bundle_with_a_bad_name_is_refused_before_the_swap(
        client, api_project, session_ctx, tmp_path):
    name = api_project("clean")
    ok = client.post("/api/projects/import_bundle?name=good_in", files={
        "file": ("g.zip", _bundle_with(client, name, _two_bus_network(), tmp_path),
                 "application/zip")})
    assert ok.status_code in (200, 201), ok.text

    before = list(session_ctx(client).network.buses.index)
    r = client.post("/api/projects/import_bundle?name=bad_in", files={
        "file": ("b.zip", _bundle_with(client, name, _with_a_bad_generator(), tmp_path),
                 "application/zip")})
    assert r.status_code == 422, r.text
    assert list(session_ctx(client).network.buses.index) == before


# ── existing projects stay openable ─────────────────────────────────────────

def test_a_saved_project_with_an_odd_name_still_opens(
        client, api_project, project_storage_dir, session_ctx):
    """Activating a project that is on disk only goes through the same load
    (`import_network_from_netcdf`) as undo and snapshot restore. It must not
    refuse what an earlier version saved."""
    from services.pypsa_service import PyPSAService
    from tests.test_activate import _evict_from_registry

    name = api_project("legacy")
    n = _two_bus_network()
    n.add("Load", "old\nload", bus="B1", p_set=1.0)
    n.export_to_netcdf(project_storage_dir(name) / "network.nc")
    api_project("other")  # make another project the active one
    _evict_from_registry(name)

    r = client.post(f"/api/projects/{name}/activate")
    assert r.status_code == 200, r.text
    assert "old\nload" in session_ctx(client).network.loads.index

    fresh = pypsa.Network()
    PyPSAService.import_network_from_netcdf(fresh, project_storage_dir(name) / "network.nc")
    assert "old\nload" in fresh.loads.index
