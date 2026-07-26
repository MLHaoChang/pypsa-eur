"""
B6 increment 1 — path-scoped per-project READ endpoints.

`GET /api/projects/{project_id}/network/{component_class}` and `.../network/meta`
serve a SPECIFIC project's data (resolved via `ProjectDep`,
resolve-or-load-resident) WITHOUT moving the active foreground slot. These tests
prove:
  * a path-scoped read returns the TARGET project's data, not the active one;
  * the read does NOT change `_active` (`get_active_id` is unchanged);
  * an on-disk-but-not-resident project becomes resident after one read;
  * the meta payload reflects the target project;
  * unknown project → 404, traversing id → 400, unknown component_class → 4xx;
  * the active-network shim (`/api/network/*`) still reads the active project.

Mirrors test_solve_queue.py's `_save_project` helper (a POST to
`/api/projects/{name}` is a destructive SAVE of the active network — see
CLAUDE.md). To put project "B" on disk without leaving it active, we save B
while it's the live network, then install A as the live network again; B then
exists only on disk (NOT in `PyPSAService._contexts`), exercising the
hydrate-and-register path.
"""
from __future__ import annotations

import pandas as pd
import pypsa

from services.pypsa_service import PyPSAService


def _bus_network(bus_name: str) -> pypsa.Network:
    """Single-bus network whose bus name identifies the project."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=4, freq="h"))
    n.add("Bus", bus_name)
    n.add("Load", "L1", bus=bus_name, p_set=100.0)
    n.add("Generator", "gas", bus=bus_name, carrier="gas",
          p_nom=200.0, marginal_cost=50.0)
    return n


def _save_project(client, name: str) -> None:
    """Persist the live (active) network as a fresh on-disk project."""
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def _put_A_and_B_on_disk(client, install_network):
    """
    Two saved projects on disk with distinct buses; leaves A as the active
    foreground and B on-disk-only (not resident in _contexts).
    Returns (A_bus, B_bus).
    """
    # Save B first (B active), then install A — B is now on disk but not active.
    install_network(_bus_network("B_BUS"), name="B")
    _save_project(client, "B")
    install_network(_bus_network("A_BUS"), name="A")
    _save_project(client, "A")
    return "A_BUS", "B_BUS"


def test_path_scoped_read_targets_named_project_not_active(
    client, install_network, tmp_projects_dir, registry_key_for
):
    a_bus, b_bus = _put_A_and_B_on_disk(client, install_network)
    assert PyPSAService.get_active_id() == registry_key_for("A")

    # B's buses via the path-scoped route — must be B's, not the active A's.
    rb = client.get("/api/projects/B/network/buses")
    assert rb.status_code == 200, rb.text
    b_names = [row["name"] for row in rb.json()]
    assert b_names == [b_bus]
    assert a_bus not in b_names

    # A's buses via the path-scoped route — A's own data.
    ra = client.get("/api/projects/A/network/buses")
    assert ra.status_code == 200, ra.text
    assert [row["name"] for row in ra.json()] == [a_bus]


def test_path_scoped_reads_do_not_move_active(
    client, install_network, tmp_projects_dir, registry_key_for
):
    _put_A_and_B_on_disk(client, install_network)
    key_a = registry_key_for("A")
    assert PyPSAService.get_active_id() == key_a

    # Reading B (a not-yet-resident project) must NOT change the foreground.
    client.get("/api/projects/B/network/buses")
    assert PyPSAService.get_active_id() == key_a
    # The active network is still A's (B_BUS must not appear).
    active_buses = [row["name"] for row in client.get("/api/network/buses").json()]
    assert active_buses == ["A_BUS"]

    # Repeat reads (now resident) still don't move it.
    client.get("/api/projects/B/network/meta")
    client.get("/api/projects/A/network/links")
    assert PyPSAService.get_active_id() == key_a


def test_path_scoped_read_makes_target_resident(
    client, install_network, tmp_projects_dir, registry_key_for
):
    _put_A_and_B_on_disk(client, install_network)
    key_a, key_b = registry_key_for("A"), registry_key_for("B")
    # B is on disk but not resident yet (save doesn't register into _contexts).
    assert PyPSAService.get_context(key_b) is None

    r = client.get("/api/projects/B/network/buses")
    assert r.status_code == 200, r.text

    # After the read B is resident, but _active is unchanged (still A).
    ctx_b = PyPSAService.get_context(key_b)
    assert ctx_b is not None
    assert ctx_b.loaded_project == "B"
    assert PyPSAService.get_active_id() == key_a
    # A second read returns the SAME resident ctx (registry hit, no re-hydrate).
    client.get("/api/projects/B/network/buses")
    assert PyPSAService.get_context(key_b) is ctx_b


def test_path_scoped_meta_reflects_target_project(
    client, install_network, tmp_projects_dir, registry_key_for
):
    _put_A_and_B_on_disk(client, install_network)

    mb = client.get("/api/projects/B/network/meta")
    assert mb.status_code == 200, mb.text
    body = mb.json()
    assert body["loaded_project"] == "B"
    assert body["name"] == "B"
    assert body["bus_count"] == 1
    assert body["snapshot_count"] == 4
    # The active foreground is still A; the shim meta proves it.
    shim = client.get("/api/network/meta").json()
    assert shim["loaded_project"] == "A"
    assert PyPSAService.get_active_id() == registry_key_for("A")


def test_shim_reads_stay_active(client, install_network, tmp_projects_dir):
    """The active-network shim is byte-identical — still serves the active ctx."""
    _put_A_and_B_on_disk(client, install_network)
    # Active is A; the shim must report A regardless of any path-scoped reads.
    client.get("/api/projects/B/network/buses")  # make B resident
    assert [row["name"] for row in client.get("/api/network/buses").json()] == ["A_BUS"]
    assert client.get("/api/network/meta").json()["loaded_project"] == "A"


def test_unknown_project_returns_404(client, install_network, tmp_projects_dir):
    install_network(_bus_network("A_BUS"), name="A")
    _save_project(client, "A")
    r = client.get("/api/projects/DoesNotExist/network/buses")
    assert r.status_code == 404, r.text
    rm = client.get("/api/projects/DoesNotExist/network/meta")
    assert rm.status_code == 404, rm.text


def test_hostile_project_id_is_indistinguishable_from_absent(
    client, install_network, tmp_projects_dir
):
    """
    Was `test_bad_project_id_returns_400`; Step 0a changed the contract.

    `ProjectDep` used to resolve through `_safe_project_dir`, whose name regex
    rejected `$evil` with 400 before asking any ownership question. That split
    the response space three ways — 400 malformed / 404 absent / 200 present —
    and on the old code a well-formed name belonging to ANOTHER org landed in
    the 200 bucket. Resolution now happens in the DB within the caller's org,
    so hostile, absent, and other-tenant ids are one indistinguishable 404.

    Path traversal is still refused: `%24evil` is a single decoded segment that
    matches the route template, and a slash-based `../evil` is normalised by
    Starlette before routing, so neither reaches a filesystem join.
    """
    install_network(_bus_network("A_BUS"), name="A")
    _save_project(client, "A")

    hostile = client.get("/api/projects/%24evil/network/buses")
    absent = client.get("/api/projects/NoSuchProject/network/buses")
    assert hostile.status_code == 404, hostile.text
    assert absent.status_code == 404, absent.text
    assert hostile.json() == absent.json()


def test_unknown_component_class_returns_4xx(
    client, install_network, tmp_projects_dir
):
    install_network(_bus_network("A_BUS"), name="A")
    _save_project(client, "A")
    r = client.get("/api/projects/A/network/wombats")
    assert 400 <= r.status_code < 500, r.text
    # `carriers` is served by the shim on a bespoke route but is intentionally
    # outside the path-scoped 8-component asset allow-list.
    rc = client.get("/api/projects/A/network/carriers")
    assert 400 <= rc.status_code < 500, rc.text


def test_path_scoped_serves_multiple_component_classes(
    client, install_network, tmp_projects_dir
):
    _put_A_and_B_on_disk(client, install_network)
    # Generators + loads come back for the target project; links (none) is [].
    gens = client.get("/api/projects/B/network/generators")
    assert gens.status_code == 200, gens.text
    assert [row["name"] for row in gens.json()] == ["gas"]
    loads = client.get("/api/projects/B/network/loads")
    assert loads.status_code == 200, loads.text
    assert [row["name"] for row in loads.json()] == ["L1"]
    links = client.get("/api/projects/B/network/links")
    assert links.status_code == 200, links.text
    assert links.json() == []
