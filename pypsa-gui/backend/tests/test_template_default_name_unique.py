"""A template created without a name takes the first free default name.

`create_from_template` defaults `name` to the template's friendly name. Before
this, a second click on the same template answered 409 "Project '3-Bus
Tutorial' already exists" (the org-unique name constraint), although the
route's own comment promised to uniquify. A name the caller asks for
explicitly still 409s — only the default is ours to change.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest


@pytest.fixture
def fake_3bus(tmp_path, monkeypatch):
    # The real template .nc is a gitignored build artifact; the route only
    # copies and imports it, so a minimal network is a faithful stand-in
    # (see test_active_pointer_paths.test_create_from_template_moves_the_pointer).
    from routers import projects as projects_router

    d = tmp_path / "3bus"
    d.mkdir()
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=2, freq="h"))
    n.add("Bus", "B1")
    n.export_to_netcdf(str(d / "network.nc"))
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)


def test_repeat_clicks_get_numbered_names(client, _auth_db, fake_3bus):
    names = []
    for _ in range(3):
        r = client.post("/api/projects/from_template/3bus")
        assert r.status_code == 200, r.text
        names.append(r.json()["imported"])
    assert names == ["3-Bus Tutorial", "3-Bus Tutorial 2", "3-Bus Tutorial 3"]


def test_a_gap_is_reused(client, _auth_db, fake_3bus):
    assert client.post("/api/projects/from_template/3bus",
                       params={"name": "3-Bus Tutorial 2"}).status_code == 200
    r = client.post("/api/projects/from_template/3bus")
    assert r.json()["imported"] == "3-Bus Tutorial"
    r = client.post("/api/projects/from_template/3bus")
    assert r.json()["imported"] == "3-Bus Tutorial 3"


def test_an_explicit_name_that_exists_still_409s(client, _auth_db, fake_3bus):
    assert client.post("/api/projects/from_template/3bus",
                       params={"name": "mine"}).status_code == 200
    r = client.post("/api/projects/from_template/3bus", params={"name": "mine"})
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]
