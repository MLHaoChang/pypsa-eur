"""LC-06 S11, LC-D01: project identity and files across copy, rename, refused overwrite and delete.

The seam is the copilot's project tools (``list_projects``, ``save_project_a_copy``,
``rename_project``, ``save_project_as``, ``delete_project``), read back through the
project registry's row id and the files on disk, not through the tools' own replies.

Hand oracle, for a project "Alpha" of one bus, one 20 MW generator and two snapshots:
- a copy is a new project: a new id and its own directory, whose ``network.nc`` read
  with ``pypsa`` alone has the same one bus, 20 MW and two snapshots;
- a rename changes the listed name and nothing else: the id is the one the copy was
  given and the file, wherever the registry now keeps it, is byte-for-byte the one it had;
- a Save-As onto an existing name that is not the active project is refused with
  ``project_exists`` and that project's file is byte-for-byte unchanged;
- a delete removes that project's directory and its listing and no other.
"""
# ruff: noqa: F811 -- shared public harness fixtures
import pandas as pd
import pypsa
import pytest
from fastapi import HTTPException

from services import chat_tools, project_registry
from services.pypsa_service import PyPSAService
from tests.test_workflow_tools import helpers_state  # noqa: F401


@pytest.fixture
def alpha(client, session_ctx, install_network):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2026-01-01", periods=2, freq="h"))
    n.add("Bus", "B1", v_nom=110)
    n.add("Generator", "G1", bus="B1", p_nom=20, marginal_cost=10)
    n.add("Load", "L1", bus="B1", p_set=5)
    install_network(n)
    response = client.post("/api/projects/Alpha")
    assert response.status_code == 200, response.text
    binding = PyPSAService.bind_request_context(session_ctx(client))
    cell = PyPSAService.bind_turn_cell()
    try:
        yield
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


def _row(name):
    """(id, directory) of the named project, from the registry."""
    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, name)
        return str(project.id), project_registry.project_dir(project)


def _listed():
    return {p["name"]: p["id"] for p in chat_tools.list_projects()}


def _read(directory):
    n = pypsa.Network(str(directory / "network.nc"))
    return len(n.buses), float(n.generators.p_nom.sum()), len(n.snapshots)


def test_copy_rename_refused_overwrite_and_delete_keep_each_projects_id_and_files(alpha):
    alpha_id, alpha_dir = _row("Alpha")
    alpha_bytes = (alpha_dir / "network.nc").read_bytes()
    assert _read(alpha_dir) == (1, 20.0, 2)
    assert _listed() == {"Alpha": alpha_id}

    # A copy is a second project with its own id, directory and file.
    chat_tools.save_project_a_copy("Beta")
    beta_id, beta_dir = _row("Beta")
    assert beta_id != alpha_id and beta_dir != alpha_dir
    assert _read(beta_dir) == (1, 20.0, 2)
    assert _listed() == {"Alpha": alpha_id, "Beta": beta_id}
    beta_bytes = (beta_dir / "network.nc").read_bytes()

    # A rename moves the name only: same id, same bytes.
    chat_tools.rename_project("Beta", "Gamma")
    assert _listed() == {"Alpha": alpha_id, "Gamma": beta_id}
    gamma_id, gamma_dir = _row("Gamma")
    assert gamma_id == beta_id
    assert (gamma_dir / "network.nc").read_bytes() == beta_bytes
    with pytest.raises(HTTPException):
        _row("Beta")

    # Alpha is still the active project (a copy does not rebind), so a Save-As onto
    # Gamma is refused and Gamma's file is untouched.
    with pytest.raises(HTTPException) as refused:
        chat_tools.save_project_as("Gamma")
    assert refused.value.status_code == 409
    assert refused.value.detail["error_kind"] == "project_exists"
    assert (gamma_dir / "network.nc").read_bytes() == beta_bytes
    assert _listed() == {"Alpha": alpha_id, "Gamma": beta_id}

    # Deleting Gamma removes its directory and listing and leaves Alpha alone.
    assert chat_tools.delete_project("Gamma") == {"deleted": ["Gamma"], "failed": []}
    assert not gamma_dir.exists()
    assert _listed() == {"Alpha": alpha_id}
    assert (alpha_dir / "network.nc").read_bytes() == alpha_bytes
    assert _row("Alpha") == (alpha_id, alpha_dir)
