"""
Backend-enforced loaded-project identity: the `expect` 409 guard, the first-save
claim, and the Save-As `rebind` flag. These lock in the cross-project autosave
overwrite fix (a stale-name save must be refused, not silently misdirected).
"""
from __future__ import annotations

from tests.conftest import build_network
from services.pypsa_service import PyPSAService


def test_save_with_mismatched_expect_returns_409(client, tmp_projects_dir, install_network):
    # Network is loaded as project "A"...
    install_network(build_network(), name="A")
    # ...but a save asserts it's "B" (e.g. an autosave from a stale client).
    resp = client.post("/api/projects/B", params={"expect": "B"})
    assert resp.status_code == 409
    assert "bound to project" in resp.json()["detail"].lower()
    # The wrong-named folder must NOT have been written.
    assert not (tmp_projects_dir / "B" / "network.nc").exists()


def test_save_active_project_with_matching_expect_ok(client, tmp_projects_dir, install_network):
    install_network(build_network(), name="A")
    resp = client.post("/api/projects/A", params={"expect": "A"})
    assert resp.status_code == 200
    assert (tmp_projects_dir / "A" / "network.nc").exists()


def test_first_save_claims_unbound_network(client, tmp_projects_dir, install_network):
    # Non-empty but UNBOUND network (no name) — the fresh "Untitled" case.
    install_network(build_network())  # _loaded_project stays None
    assert PyPSAService.get_loaded_project() is None
    resp = client.post("/api/projects/New", params={"expect": "New"})
    assert resp.status_code == 200
    # First save claims the network for "New".
    assert PyPSAService.get_loaded_project() == "New"
    assert client.get("/api/network/meta").json()["loaded_project"] == "New"


def test_rebind_moves_binding_to_new_name(client, tmp_projects_dir, install_network):
    install_network(build_network(), name="A")
    # Save-As to "B" with rebind=true → backend follows to "B".
    resp = client.post("/api/projects/B", params={"rebind": "true"})
    assert resp.status_code == 200
    assert client.get("/api/network/meta").json()["loaded_project"] == "B"


def test_save_a_copy_does_not_rebind(client, tmp_projects_dir, install_network):
    install_network(build_network(), name="A")
    # Save a Copy: write under a new name WITHOUT rebind → live network stays "A".
    resp = client.post("/api/projects/CopyOfA")
    assert resp.status_code == 200
    assert (tmp_projects_dir / "CopyOfA" / "network.nc").exists()
    assert client.get("/api/network/meta").json()["loaded_project"] == "A"
