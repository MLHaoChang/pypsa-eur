"""Template creation edges (UX walkthrough 08): the hydrogen card made from the
card gets its Energy Hub files, and a missing example network says so in plain words."""
from __future__ import annotations

import pytest


def test_the_industrial_hydrogen_card_creates_an_energy_hub_project(client, tmp_projects_dir,
                                                                    bound_ctx):
    r = client.post("/api/projects/from_template/eh_h2_hub",
                    params={"name": "Industrial Hydrogen Hub"})
    assert r.status_code == 200, r.text
    from services import network_lengths

    with bound_ctx(client):
        project_dir = network_lengths.active_project_dir()
    assert (project_dir / "eh_template.json").is_file()


def test_a_second_create_with_the_same_name_is_refused_not_overwritten(client, tmp_projects_dir):
    first = client.post("/api/projects/from_template/eh_h2_hub", params={"name": "Hub"})
    assert first.status_code == 200, first.text
    again = client.post("/api/projects/from_template/eh_h2_hub", params={"name": "Hub"})
    assert again.status_code in (400, 409)
    assert "already exists" in again.text


def test_a_missing_example_network_names_no_developer_path(client, tmp_projects_dir):
    r = client.post("/api/projects/from_template/3bus", params={"name": "t3"})
    if r.status_code == 200:
        pytest.skip("the example network is built in this checkout")
    assert r.status_code == 404
    assert "_build.py" not in r.text and "network.nc" not in r.text
