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


# ── AA-12: unknown AC PF convergence on /ac_pf/status ───────────────────────


def _rows(*oks):
    return [{"snapshot": f"2030-01-01T0{i}:00:00", "period": None, "ok": ok}
            for i, ok in enumerate(oks)]


def test_ac_pf_status_reports_unknown_convergence_as_null(client, install_network):
    install_network(build_network(), name="P")
    rows = _rows(True, False, None, None)
    sim_router._state_update(
        ac_pf_results={}, ac_pf_convergence={r["snapshot"]: r["ok"] for r in rows},
        ac_pf_convergence_list=rows, ac_pf_converged_count=None,
        ac_pf_unknown_count=2, ac_pf_total_snapshots=4)
    body = client.get("/api/results/ac_pf/status").json()
    assert body["converged_count"] is None
    assert body["known_converged_count"] == 1
    assert body["unknown_count"] == 2
    assert body["total_snapshots"] == 4
    assert [r["ok"] for r in body["converged_list"]] == [True, False, None, None]


def test_a_pre_aa12_state_without_unknown_count_reads_as_before(tmp_path, client):
    # A bundle saved before AA-12: every row known, no `ac_pf_unknown_count`.
    proj = tmp_path / "pre_aa12"
    proj.mkdir()
    rows = _rows(True, True, False)
    (proj / "results_state.pkl").write_bytes(pickle.dumps({
        "ac_pf_results": {}, "ac_pf_convergence": {r["snapshot"]: r["ok"] for r in rows},
        "ac_pf_convergence_list": rows, "ac_pf_converged_count": 2,
        "ac_pf_total_snapshots": 3}))
    _restore_results_state(proj, "pre_aa12")
    assert sim_router._state.get("ac_pf_unknown_count") is None
    body = client.get("/api/results/ac_pf/status").json()
    assert (body["converged_count"], body["known_converged_count"],
            body["unknown_count"], body["total_snapshots"]) == (2, 2, 0, 3)
