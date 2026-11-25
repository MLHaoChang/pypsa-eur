"""
DC review issue 01, PR2b: the opt-in ``front`` stage of the EH study.

GK A's conditions: F1 one engine function gives the levels, each lever
tested and "neither" refused by name; F2 the study's seed and draws, same
seed same front; F3 the engine's own solve count is charged, partial charge
included; F4 a study without "front" is identical to master's; F5 the study
network and the foreground revision are untouched; F7 the chat stage enum.
"""
from __future__ import annotations

import hashlib
import queue
import threading

import pandas as pd
import pytest

from models.energy_hub import (
    EH_OPT_IN_STAGES,
    EH_PIPELINE_STAGES,
    AvailabilityTarget,
    default_off_grid_pack,
    default_strong_grid_pack,
    default_weak_flexible_pack,
)
from services.adequacy import eh_readiness as R
from services.adequacy import eh_study as S
from services.adequacy import front as fr
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig
from tests.eh_stage_fixtures import VOLL, certifiable_weak_network

FRONT = ("apply_pack", "ens_solve", "front", "assemble")


def _run(n, pack, *, stages, budget=30, cfg=None, store=None, mc_seed=None,
         mc_draws=None):
    PyPSAService.set_network(n)
    return S.run_eh_study(
        n, pack, cfg or SolverConfig(voll=VOLL),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=stages,
        budget_solves=budget,
        store=store,
        mc_seed=mc_seed,
        mc_draws=mc_draws,
    )


def _record(report, stage="front"):
    return next(s for s in report.pipeline.stages if s.stage == stage)


# ── opt-in (F4) ────────────────────────────────────────────────────────────


def test_front_follows_frontier_and_is_opt_in():
    order = list(EH_PIPELINE_STAGES)
    assert order.index("front") == order.index("frontier") + 1
    assert EH_OPT_IN_STAGES == ("front",)
    for pack in (default_strong_grid_pack(), default_weak_flexible_pack(),
                 default_off_grid_pack(),
                 default_strong_grid_pack().model_copy(
                     update={"frontier_default": True})):
        assert "front" not in S.default_stages_for(pack)
    assert S.validate_stages(FRONT) == FRONT
    assert "eh_front" in S.SIBLING_STORE_KEYS


def _stub_front(monkeypatch, *, lever="reserve_margin", solves=None,
                raise_after=None):
    """run_front_study replaced by a recorder: no solve."""
    calls = []

    def fake(network, lock, cfg, **kw):
        calls.append({"network": network, "cfg": cfg, **kw})
        n_solves = len(kw["levels"]) if solves is None else solves
        res = {"lever": kw["lever"], "levels": list(kw["levels"]),
               "solves": n_solves, "aborted": False,
               "points": [{"status": "ok"} for _ in kw["levels"]]}
        if raise_after is not None:
            exc = RuntimeError("boom")
            exc.front_result = {**res, "solves": raise_after}
            raise exc
        return res

    monkeypatch.setattr(fr, "run_front_study", fake)
    monkeypatch.setattr(fr, "choose_lever",
                        lambda n, req: (lever, f"stub picks {lever}"))
    return calls


def _stage_only(monkeypatch, pack, *, budget=30, cfg=None, store=None,
                mc_seed=None, mc_draws=None):
    """The front stage after a stubbed ens_solve (one solve charged)."""
    def fake_ens(st):
        st.solves += 1
        st.mark("ens_solve", "run", solves_charged=1)

    monkeypatch.setitem(S._STAGE_HANDLERS, "ens_solve", fake_ens)
    return _run(certifiable_weak_network(), pack, stages=FRONT, budget=budget,
                cfg=cfg, store=store, mc_seed=mc_seed, mc_draws=mc_draws)


# ── F1: one function gives the levels, per lever ───────────────────────────


def _pack(cap=10.0, ladder=(4.0, 2.0, 1.0, 0.5, 0.25)):
    return default_weak_flexible_pack().model_copy(update={
        # a pack needs one target: LOLE stands in when the ENS one is absent
        "availability": AvailabilityTarget(
            ens_cap_permyriad=cap, target_lole_h=None if cap else 3.0),
        "frontier_ladder": ladder,
    })


def test_front_levels_is_the_frontier_rule_on_each_lever_base():
    lad = (4.0, 2.0, 1.0, 0.5, 0.25)
    assert fr.front_levels("ens_cap", n_points=3, ladder=lad, ens_cap=10.0,
                           margin_base=None) == S.frontier_targets(
        10.0, 3, ladder=lad)
    assert fr.front_levels("reserve_margin", n_points=5, ladder=lad,
                           ens_cap=None, margin_base=0.1) == S.frontier_targets(
        0.1, 5, ladder=lad)
    # each lever reads its own base only
    assert fr.front_levels("ens_cap", n_points=2, ladder=lad, ens_cap=10.0,
                           margin_base=0.3)[0] == 10.0
    assert fr.front_levels("reserve_margin", n_points=2, ladder=lad,
                           ens_cap=10.0, margin_base=0.3)[0] == 0.3


@pytest.mark.parametrize("lever, kw, match", [
    ("ens_cap", {"ens_cap": None, "margin_base": 0.1}, "needs an ENS target"),
    ("reserve_margin", {"ens_cap": 10.0, "margin_base": None},
     "needs a base margin"),
    ("ens_cap", {"ens_cap": float("nan"), "margin_base": None},
     "positive, finite"),
    ("reserve_margin", {"ens_cap": None, "margin_base": 0.0},
     "positive, finite"),
    ("reserve_margin", {"ens_cap": None, "margin_base": float("inf")},
     "positive, finite"),
    ("voll", {"ens_cap": 10.0, "margin_base": 0.1}, "unknown reliability lever"),
])
def test_front_levels_refuses_a_missing_or_bad_base_by_name(lever, kw, match):
    with pytest.raises(fr.FrontConfigError, match=match):
        fr.front_levels(lever, n_points=3, ladder=(2.0, 1.0, 0.5), **kw)


def test_the_stage_runs_the_ens_lever_on_the_pack_target(monkeypatch):
    calls = _stub_front(monkeypatch, lever="ens_cap")
    store: dict = {}
    _stage_only(monkeypatch, _pack(10.0), store=store)
    (call,) = calls
    assert call["lever"] == "ens_cap"
    n = S.frontier_point_count(29, 30)
    assert call["levels"] == S.frontier_targets(
        10.0, n, ladder=_pack().frontier_ladder)
    assert call["co2_caps_t"] == [None]
    assert call["pack"] is not None
    assert store["eh_front"]["levels_basis"] == "the pack's ENS target"


def test_the_stage_runs_the_margin_lever_on_the_session_margin(monkeypatch):
    calls = _stub_front(monkeypatch, lever="reserve_margin")
    store: dict = {}
    _stage_only(monkeypatch, _pack(10.0), store=store,
                cfg=SolverConfig(voll=VOLL, reserve_margin=0.2))
    (call,) = calls
    assert call["lever"] == "reserve_margin"
    assert call["levels"][0] == 0.2
    assert call["levels"] == S.frontier_targets(
        0.2, S.frontier_point_count(29, 30), ladder=_pack().frontier_ladder)
    assert store["eh_front"]["levels_basis"] == "the session's reserve margin"


def test_the_stage_uses_the_named_assumed_margin_when_none_is_set(monkeypatch):
    calls = _stub_front(monkeypatch, lever="reserve_margin")
    store: dict = {}
    report = _stage_only(monkeypatch, _pack(None), store=store)
    assert calls[0]["levels"][0] == fr.FRONT_MARGIN_BASE == 0.10
    assert "assumed base margin of 0.1" in store["eh_front"]["levels_basis"]
    rec = _record(report)
    assert rec.status == "run"
    assert "reserve_margin: stub picks reserve_margin" in rec.note


def test_the_ens_lever_without_a_pack_target_is_not_established(monkeypatch):
    calls = _stub_front(monkeypatch, lever="ens_cap")
    store: dict = {}
    report = _stage_only(monkeypatch, _pack(None), store=store)
    assert calls == []
    rec = _record(report)
    assert rec.status == "skipped" and rec.solves_charged == 0
    assert rec.note == ("front not established: the ENS lever needs an ENS "
                        "target (‱ of demand)")
    assert "eh_front" not in store


def test_a_budget_short_of_two_points_is_not_run(monkeypatch):
    calls = _stub_front(monkeypatch)
    report = _stage_only(monkeypatch, _pack(10.0), budget=2)
    assert calls == []
    rec = _record(report)
    assert rec.status == "skipped"
    assert rec.note.startswith("not run: budget_solves leaves 1 solve(s)")


# ── F2: the study's seed and draws ─────────────────────────────────────────


def test_the_stage_passes_the_study_seed_and_draws(monkeypatch):
    calls = _stub_front(monkeypatch)
    _stage_only(monkeypatch, _pack(10.0), mc_seed=7, mc_draws=123)
    assert calls[0]["seed"] == 7 and calls[0]["mc_draws"] == 123
    calls.clear()
    _stage_only(monkeypatch, _pack(10.0))
    assert calls[0]["seed"] == S.DEFAULT_MC_SEED
    assert calls[0]["mc_draws"] == S.DEFAULT_MC_DRAWS


# ── F3: the engine's own count is charged ──────────────────────────────────


@pytest.mark.parametrize("solves", [3, 4])
def test_the_charge_is_the_engine_count(monkeypatch, solves):
    # 4 = three points plus a reference solve the stage did not plan
    _stub_front(monkeypatch, solves=solves)
    report = _stage_only(monkeypatch, _pack(10.0, ladder=(2.0, 1.0, 0.5)))
    rec = _record(report)
    assert rec.solves_charged == solves
    assert report.pipeline.solves_consumed == 1 + solves


def test_a_failed_front_charges_what_it_spent(monkeypatch):
    _stub_front(monkeypatch, raise_after=2)
    store: dict = {}
    report = _stage_only(monkeypatch, _pack(10.0), store=store)
    rec = _record(report)
    assert rec.status == "failed" and rec.note == "front failed: boom"
    assert rec.solves_charged == 2
    assert report.pipeline.solves_consumed == 3
    assert "eh_front" not in store


def test_a_failure_with_no_partial_charges_nothing(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("early")

    _stub_front(monkeypatch)
    monkeypatch.setattr(fr, "run_front_study", boom)
    report = _stage_only(monkeypatch, _pack(10.0))
    rec = _record(report)
    assert rec.status == "failed" and rec.solves_charged == 0
    assert report.pipeline.solves_consumed == 1


def test_the_engine_attaches_its_partial_count_to_a_raise(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("mc died")

    monkeypatch.setattr(fr, "_mc_point", broken)
    with pytest.raises(RuntimeError, match="mc died") as info:
        fr.run_front_study(certifiable_weak_network(), threading.Lock(),
                           SolverConfig(voll=VOLL), levels=[0.1],
                           lever="reserve_margin", mc_draws=5)
    assert info.value.front_result["solves"] == 1


@pytest.mark.live_solve
@pytest.mark.parametrize("caps, expected", [([None], 2), ([1e9], 3)])
def test_the_engine_counts_the_reference_solve(caps, expected):
    res = fr.run_front_study(certifiable_weak_network(), threading.Lock(),
                             SolverConfig(voll=VOLL), levels=[0.05, 0.1],
                             co2_caps_t=caps, lever="reserve_margin",
                             mc_draws=5)
    assert res["solves"] == expected == fr.solves_needed([0.05, 0.1], caps)


# ── live: the stage end to end, F2 identity, F5 untouched ──────────────────


def _frames(n):
    out = {}
    for c in n.iterate_components():
        out[c.name] = c.df.copy()
        for attr, df in c.pnl.items():
            out[f"{c.name}_t.{attr}"] = df.copy()
    return out


@pytest.mark.live_solve
def test_the_front_stage_end_to_end_leaves_the_study_network_alone(monkeypatch):
    seen = {}
    real = S._stage_front

    def watched(st):
        seen["before"] = _frames(st.network)
        seen["rev"] = PyPSAService.get_active_context().network_revision
        real(st)
        seen["after"] = _frames(st.network)

    monkeypatch.setitem(S._STAGE_HANDLERS, "front", watched)
    store: dict = {}
    n = certifiable_weak_network()
    report = _run(n, _pack(10.0, ladder=(2.0, 1.0, 0.5)), stages=FRONT,
                  budget=4, store=store, mc_draws=50)
    rec = _record(report)
    assert rec.status == "run", rec.note
    front = store["eh_front"]
    assert front["lever"] == "reserve_margin"
    # budget 4: ens_solve takes 1, the frontier's 40 % share floors at 2 points
    assert S.frontier_point_count(3, 4) == 2
    assert front["levels"] == S.frontier_targets(0.1, 2, ladder=(2.0, 1.0, 0.5))
    assert rec.solves_charged == front["solves"] == 2
    assert report.pipeline.solves_consumed == 3
    assert front["mc_budget"]["draws_per_point"] == 50
    assert front["pack_hash"] == report.pack_hash
    # F5: the study copy is read, never written; the foreground is not edited
    assert seen["before"].keys() == seen["after"].keys()
    for k in seen["before"]:
        pd.testing.assert_frame_equal(seen["before"][k], seen["after"][k],
                                      check_exact=True, obj=k)
    assert PyPSAService.get_active_context().network_revision == seen["rev"]
    # no report section: the front lives in its sibling table
    assert "front" not in report.sections


@pytest.mark.live_solve
def test_the_same_seed_gives_the_same_front():
    fronts = []
    for _ in range(2):
        store: dict = {}
        _run(certifiable_weak_network(), _pack(10.0, ladder=(2.0, 1.0)),
             stages=FRONT, budget=3, store=store, mc_draws=40, mc_seed=3)
        fronts.append(store["eh_front"]["points"])
    assert fronts[0] == fronts[1]


# ── F4: without "front", the study is master's ─────────────────────────────


def _digest(obj) -> str:
    return hashlib.sha256(obj.encode()).hexdigest()[:16]


# Literals computed on master a15fb8b9 from a fresh worktree.
MASTER_REPORT_DIGEST = "21657c6554b9b0a6"
MASTER_READINESS_DIGEST = "b4572ee04f3ec292"


@pytest.mark.live_solve
def test_a_study_without_front_is_masters():
    report = _run(certifiable_weak_network(), _pack(10.0), stages=None,
                  budget=6, mc_draws=30)
    assert "front" not in [s.stage for s in report.pipeline.stages]
    assert _digest(report.model_dump_json()) == MASTER_REPORT_DIGEST


def test_readiness_without_front_is_masters():
    import json

    n = certifiable_weak_network()
    PyPSAService.set_network(n)
    out = R.eh_readiness(n, _pack(10.0), budget_solves=30, voll=VOLL)
    assert "front" not in [r["stage"] for r in out["stages"]]
    assert _digest(json.dumps(out, sort_keys=True, default=str)) \
        == MASTER_READINESS_DIGEST


def test_readiness_prices_a_requested_front():
    n = certifiable_weak_network()
    PyPSAService.set_network(n)
    out = R.eh_readiness(n, _pack(10.0), budget_solves=30, voll=VOLL,
                         stages=FRONT)
    row = next(r for r in out["stages"] if r["stage"] == "front")
    assert row["solves"] == len(S.frontier_targets(
        1.0, S.frontier_point_count(29, 30), ladder=_pack().frontier_ladder))
    assert row["basis"] == "exact" and row["reason"] is None
