"""
results_state.pkl schema versioning: new saves write a versioned envelope;
loads accept the versioned form, still load legacy bare-dict bundles, and skip
unknown/future schema versions instead of misreading them.
"""
from __future__ import annotations

import pickle

from tests.conftest import build_network
from routers import simulation as sim_router
from routers.projects import _restore_results_state


def test_save_writes_versioned_envelope(
    client, tmp_projects_dir, install_network, project_storage_dir
):
    install_network(build_network(), name="P")
    # Seed a side-result so the pickle is actually written (save skips it when
    # every results-state key is None).
    sim_router._state_update(ac_pf_converged_count=7)

    resp = client.post("/api/projects/P", params={"expect": "P"})
    assert resp.status_code == 200

    pkl = project_storage_dir("P") / "results_state.pkl"
    assert pkl.exists()
    payload = pickle.loads(pkl.read_bytes())
    assert payload["__schema__"] == 1
    assert payload["data"]["ac_pf_converged_count"] == 7


def test_restore_loads_legacy_bare_dict(tmp_path):
    # A bundle saved BEFORE versioning: a bare dict, no "__schema__".
    proj = tmp_path / "legacy"
    proj.mkdir()
    (proj / "results_state.pkl").write_bytes(pickle.dumps({"ac_pf_converged_count": 9}))

    _restore_results_state(proj, "legacy")
    assert sim_router._state.get("ac_pf_converged_count") == 9


def test_restore_skips_unknown_future_schema(tmp_path):
    proj = tmp_path / "future"
    proj.mkdir()
    (proj / "results_state.pkl").write_bytes(
        pickle.dumps({"__schema__": 99, "data": {"ac_pf_converged_count": 9}})
    )

    _restore_results_state(proj, "future")
    # Unknown version → skipped (NOT misread); the key stays at its reset None.
    assert sim_router._state.get("ac_pf_converged_count") is None
