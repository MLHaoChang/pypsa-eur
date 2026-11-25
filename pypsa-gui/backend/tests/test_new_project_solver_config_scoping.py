"""A new project starts from the DEFAULT solver config, never the previous one's.

`PyPSAService.reset_network` carried the outgoing context's whole solver_state
(including `solver_config`) onto every "fresh" swap, so a blank project inherited
the previous project's tariff, finance and `multi_investment_periods`
(template-e2e issue 32). Driven through the real create routes, asserting the
`solver_config.json` the first save writes. Duplicate is not a separate route:
the client saves the open project under a new name, which keeps its own config.
"""

from __future__ import annotations

import io
import json

import pypsa
import pytest

from services.solver_service import SolverConfig

DIRTY = {"multi_investment_periods": True, "discount_rate": 0.123}


def _dirty(client) -> None:
    r = client.put("/api/simulation/solver_config", json=DIRTY)
    assert r.status_code == 200, r.text


def _saved_config(storage_dir, name: str) -> dict:
    path = storage_dir(name) / "solver_config.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _blank(client, name, **_):
    assert client.post("/api/network/reset").status_code == 200
    r = client.post(f"/api/projects/{name}", params={"force": True})
    assert r.status_code == 200, r.text


def _template(client, name, tmp_path=None, monkeypatch=None):
    import pandas as pd
    from routers import projects as projects_router

    tpl = tmp_path / "3bus"
    tpl.mkdir()
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=2, freq="h"))
    n.add("Bus", "B1")
    n.export_to_netcdf(str(tpl / "network.nc"))
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)
    r = client.post("/api/projects/from_template/3bus", params={"name": name})
    assert r.status_code == 200, r.text


def _import_bundle(client, name, **_):
    # A bundle of a default-config project, imported while the session holds
    # another project's config.
    src = client.get("/api/projects/src_bundle/bundle")
    assert src.status_code == 200, src.text
    r = client.post(
        "/api/projects/import_bundle",
        params={"name": name},
        files={"file": ("p.pypsaproj.zip", src.content, "application/zip")},
    )
    assert r.status_code == 200, r.text


@pytest.mark.parametrize(
    "create",
    [_blank, _template, _import_bundle],
    ids=["blank", "template", "bundle-import"],
)
def test_new_project_saves_default_solver_config(
    client, api_project, project_storage_dir, tmp_path, monkeypatch, create
):
    api_project("src_bundle")
    _dirty(client)
    create(client, "fresh_proj", tmp_path=tmp_path, monkeypatch=monkeypatch)
    saved = _saved_config(project_storage_dir, "fresh_proj")
    assert saved.get("multi_investment_periods", False) is False
    assert (
        saved.get("discount_rate", SolverConfig().discount_rate)
        == SolverConfig().discount_rate
    )
    cfg = client.get("/api/simulation/solver_config").json()
    assert cfg["multi_investment_periods"] is False


def test_netcdf_import_into_the_draft_keeps_the_config(client, install_network):
    from tests.conftest import build_network

    """carry="copy": the same workspace gets a new network, so its config stays."""
    install_network(build_network())
    _dirty(client)
    nc = client.get("/api/io/export/netcdf")
    assert nc.status_code == 200, nc.text
    r = client.post(
        "/api/io/import/netcdf",
        files={"file": ("n.nc", io.BytesIO(nc.content), "application/x-netcdf")},
    )
    assert r.status_code == 200, r.text
    cfg = client.get("/api/simulation/solver_config").json()
    assert cfg["multi_investment_periods"] is True


def test_in_place_replace_keeps_solver_config(client, install_network):
    """carry="same" (undo) keeps the config."""
    install_network(pypsa.Network())
    from services.pypsa_service import PyPSAService

    PyPSAService.get_solver_state()["solver_config"].multi_investment_periods = True
    PyPSAService.reset_network(carry="same")
    assert PyPSAService.get_solver_state()["solver_config"].multi_investment_periods
