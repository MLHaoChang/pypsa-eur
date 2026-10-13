"""
EH study isolation + pipeline honesty (post-seal e2e bugfixes, 2026-09-25).

Found by driving the real (unstubbed) pipeline over HTTP after the #52 seal:

1. The study patched the pack's ENS cap onto the SESSION ``SolverConfig`` and
   never restored it — the user's next ordinary solve silently ran ENS-capped.
2. A session ENS cap that was already set won over the pack's, while the
   report header (and the achieved-‱ inversion) used the pack's — the
   achieved ENS was off by the ratio of the two caps.
3. ``ens_solve`` solved the shared network under the pack overlay and published
   its side-results into the foreground result state — after an ``off_grid``
   study ``/results/adequacy`` described an islanded solve the user never ran
   (frontier's ``_restore_base`` rule: a study leaves the foreground alone).
4. ``budget_solves`` was recorded but never enforced (budget 2 → 4 solves), so
   the chat campaign charge for ``eh_study`` was not a ceiling either.
5. Unknown stage names were accepted and reported a no-op study as ``done``.
6. A failed (e.g. infeasible) ``ens_solve`` was reported as a user ABORT with
   unreached stages left ``pending``, and no reason on target/cost.
7. Sibling tables from a previous study (``eh_levers`` / ``eh_dtc`` …) stayed
   in result state and were served next to a newer report.
"""
from __future__ import annotations

import queue
import threading
import time

import pytest

from models.energy_hub import (
    AvailabilityTarget,
    DtcConfig,
    default_off_grid_pack,
    default_strong_grid_pack,
    default_weak_flexible_pack,
)
from services.adequacy import eh_study as S
from services.solver_service import SolverConfig
from tests.test_energy_hub_mvp_b import _off_grid_mvp_b_network, _weak_mvp_b_network
from tests.test_energy_hub_study import _ens_bind_network

STUDY_URL = "/api/results/eh_study"


def _run(n, pack, cfg, **kw):
    from services.pypsa_service import PyPSAService
    PyPSAService.set_network(n)
    kw.setdefault("stop_event", threading.Event())
    return S.run_eh_study(
        n, pack, cfg,
        lock=PyPSAService.get_lock(),
        log_queue=queue.SimpleQueue(),
        **kw,
    )


def _strong_pack(cap: float = 1000.0):
    return default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=cap),
    })


# ── 1–3: isolation ──────────────────────────────────────────────────────────


@pytest.mark.live_solve
def test_study_does_not_mutate_session_solver_config():
    cfg = SolverConfig(voll=150.0)
    assert cfg.ens_cap_permyriad is None
    report = _run(_ens_bind_network(), _strong_pack(), cfg,
                  stages=("apply_pack", "ens_solve", "assemble"))
    assert report.completeness["target"] == "ok"
    assert cfg.ens_cap_permyriad is None


@pytest.mark.live_solve
def test_pack_target_wins_over_session_cap_and_achieved_is_consistent():
    # _ens_bind_network: 1200 MWh demand; VoLL 150 < backup 200 €/MWh, so the
    # LP sheds up to whatever cap it is given — the cap binds exactly.
    cfg = SolverConfig(voll=150.0, ens_cap_permyriad=5000.0)
    report = _run(_ens_bind_network(), _strong_pack(1000.0), cfg,
                  stages=("apply_pack", "ens_solve", "assemble"))
    system = report.sections["target"].payload["system"]
    assert system["cap_mwh"] == pytest.approx(120.0, rel=1e-6)
    assert report.ens_cap_permyriad == pytest.approx(1000.0)
    assert report.achieved_ens_permyriad == pytest.approx(1000.0, rel=1e-4)
    assert cfg.ens_cap_permyriad == 5000.0


@pytest.mark.live_solve
def test_study_leaves_shared_network_and_foreground_state_alone():
    n = _off_grid_mvp_b_network()
    before_links = n.links.copy(deep=True)
    had_opt = "p_nom_opt" in n.generators.columns and n.generators[
        "p_nom_opt"].notna().any() and (n.generators["p_nom_opt"] != 0).any()
    published: list[str] = []
    pack = default_off_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=5000.0),
    })
    report = _run(n, pack, SolverConfig(voll=500.0),
                  stages=("apply_pack", "ens_solve", "assemble"),
                  state_update=lambda **kw: published.extend(kw))
    assert report.completeness["target"] == "ok"
    assert "adequacy_report" not in published
    assert "last_lost_load" not in published
    assert n.links.equals(before_links)
    # The foreground network was never solved by the study.
    assert not had_opt
    assert n.generators_t.p.empty


# ── 4: budget ───────────────────────────────────────────────────────────────


@pytest.mark.live_solve
def test_budget_solves_is_a_ceiling():
    n = _off_grid_mvp_b_network()
    pack = default_off_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=5000.0),
        "mc_certify_required": False,
    })
    report = _run(n, pack, SolverConfig(voll=500.0), stages=None,
                  budget_solves=2)
    assert report.pipeline.solves_consumed <= 2
    levers = next(r for r in report.pipeline.stages if r.stage == "levers")
    assert "budget" in (levers.note or "")
    assert report.completeness["levers"] == "not_established"
    assert "budget" in (report.sections["levers"].note or "")


@pytest.mark.live_solve
def test_budget_exhausted_before_stage_skips_it():
    n = _off_grid_mvp_b_network()
    pack = default_off_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=5000.0),
    })
    report = _run(n, pack, SolverConfig(voll=500.0), stages=None,
                  budget_solves=1)
    assert report.pipeline.solves_consumed == 1
    levers = next(r for r in report.pipeline.stages if r.stage == "levers")
    assert levers.status == "skipped"
    assert "budget" in (levers.note or "")
    assert report.completeness["levers"] == "not_established"


# ── 5: stage validation ─────────────────────────────────────────────────────


def test_driver_rejects_unknown_stage():
    with pytest.raises(ValueError, match="bogus"):
        _run(_ens_bind_network(), _strong_pack(), SolverConfig(voll=150.0),
             stages=("apply_pack", "ens_solve", "bogus"))


def test_driver_requires_apply_pack_and_ens_solve():
    with pytest.raises(ValueError, match="apply_pack"):
        _run(_ens_bind_network(), _strong_pack(), SolverConfig(voll=150.0),
             stages=("ens_solve", "assemble"))


def _setup_http(client, install_network, n, **cfg):
    install_network(n)
    body = {"solver_name": "highs", "voll": 150.0}
    body.update(cfg)
    r = client.put("/api/simulation/solver_config", json=body)
    assert r.status_code == 200, r.text


def _poll(client, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(STUDY_URL).json()
        if body.get("status") != "running":
            return body
        time.sleep(0.05)
    raise AssertionError("eh_study never finished")


@pytest.mark.parametrize("stages", [["bogus"], ["ens_solve", "assemble"]])
def test_http_rejects_invalid_stage_lists(client, install_network, stages):
    _setup_http(client, install_network, _ens_bind_network())
    r = client.post(STUDY_URL, json={"archetype": "strong_grid",
                                     "stages": stages})
    assert r.status_code == 422, r.text
    assert client.get(STUDY_URL).status_code == 204


def test_chat_schema_enumerates_stages():
    from models.energy_hub import EH_PIPELINE_STAGES
    from services.chat_tools_schema import TOOLS
    tool = next(t for t in TOOLS if t["name"] == "run_eh_study")
    items = tool["input_schema"]["properties"]["stages"]["items"]
    assert items.get("enum") == list(EH_PIPELINE_STAGES)


# ── 6: failed ens_solve is not an abort ─────────────────────────────────────


@pytest.mark.live_solve
def test_infeasible_ens_solve_is_failed_not_aborted():
    # Default weak pack: 10 ‱ with a 50 MW import cap on a 100 MW hub → the
    # ENS cap cannot be met with the fixed fleet → infeasible LP.
    n = _weak_mvp_b_network()
    report = _run(
        n, default_weak_flexible_pack(), SolverConfig(voll=500.0), stages=None,
        dtc_config=DtcConfig(critical_bus_ids=["crit"],
                             islanding_contingencies=["import_poc"]),
    )
    assert report.pipeline.aborted is False
    statuses = {r.stage: r.status for r in report.pipeline.stages}
    assert "pending" not in statuses.values()
    assert statuses["ens_solve"] == "failed"
    note = report.sections["target"].note or ""
    assert "infeasible" in note
    assert report.completeness["levers"] == "not_established"
    assert "ens_solve" in (report.sections["levers"].note or "")


@pytest.mark.live_solve
def test_http_failed_ens_solve_record_is_failed_with_report(
        client, install_network):
    _setup_http(client, install_network, _weak_mvp_b_network(), voll=500.0)
    r = client.post(STUDY_URL, json={"archetype": "weak_flexible"})
    assert r.status_code == 200, r.text
    body = _poll(client)
    assert body["status"] == "failed"
    assert "infeasible" in (body["error"] or "")
    assert body["report"] is not None
    assert body["report"]["pipeline"]["aborted"] is False


@pytest.mark.live_solve
def test_user_abort_marks_unreached_stages_skipped_not_pending():
    class _AbortAfterSolve(threading.Event):
        def __init__(self):
            super().__init__()
            self.checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks > 2   # allow apply_pack + ens_solve

    n = _off_grid_mvp_b_network()
    pack = default_off_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=5000.0),
    })
    report = _run(n, pack, SolverConfig(voll=500.0), stages=None,
                  stop_event=_AbortAfterSolve())
    assert report.pipeline.aborted is True
    statuses = {r.stage: r.status for r in report.pipeline.stages}
    assert "pending" not in statuses.values()


# ── 7: stale sibling tables ─────────────────────────────────────────────────


@pytest.mark.live_solve
def test_new_study_clears_previous_sibling_tables():
    store = {
        "eh_redundancy_comparison": {"options": [{"scenario_id": "old"}]},
        "eh_lever_comparison": {"options": [{"kind": "old"}]},
        "eh_dtc_stress": {"contingencies": [{"contingency": "old"}]},
        "eh_dtc_planning": {"contingencies": [{"contingency": "old"}]},
    }
    _run(_ens_bind_network(), _strong_pack(), SolverConfig(voll=150.0),
         stages=None, store=store)
    for key in ("eh_redundancy_comparison", "eh_lever_comparison",
                "eh_dtc_stress", "eh_dtc_planning"):
        assert key not in store, key
    assert "eh_reference_design_report" in store
