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


def _run(
    n, pack, *, stages, budget=30, cfg=None, store=None, mc_seed=None, mc_draws=None
):
    PyPSAService.set_network(n)
    return S.run_eh_study(
        n,
        pack,
        cfg or SolverConfig(voll=VOLL),
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


def test_front_follows_frontier_and_is_opt_in(stub_front):
    order = list(EH_PIPELINE_STAGES)
    assert order.index("front") == order.index("frontier") + 1
    assert EH_OPT_IN_STAGES == ("front",)
    for pack in (
        default_strong_grid_pack(),
        default_weak_flexible_pack(),
        default_off_grid_pack(),
        default_strong_grid_pack().model_copy(update={"frontier_default": True}),
    ):
        assert "front" not in S.default_stages_for(pack)
    assert S.validate_stages(FRONT) == FRONT
    assert "eh_front" in S.SIBLING_STORE_KEYS


@pytest.fixture(scope="module")
def real_record():
    """A record the real writer saved (review-gk #1427): run_front_study on
    the fixture network, two margin levels, a few draws."""
    return fr.run_front_study(
        certifiable_weak_network(),
        threading.Lock(),
        SolverConfig(voll=VOLL),
        levels=[0.05, 0.1],
        lever="reserve_margin",
        mc_draws=5,
    )


@pytest.fixture
def stub_front(monkeypatch, real_record):
    """run_front_study replaced by a recorder that returns a copy of the real
    record (its lever, levels and, when given, solve count set to the call's):
    no solve in the stage tests."""
    import copy

    def install(*, lever="reserve_margin", solves=None, raise_after=None):
        calls = []

        def fake(network, lock, cfg, **kw):
            calls.append({"network": network, "cfg": cfg, **kw})
            res = copy.deepcopy(real_record)
            res.update(lever=kw["lever"], levels=list(kw["levels"]))
            if solves is not None:
                res["solves"] = solves
            if raise_after is not None:
                exc = RuntimeError("boom")
                exc.front_result = {**res, "solves": raise_after}
                raise exc
            return res

        monkeypatch.setattr(fr, "run_front_study", fake)
        monkeypatch.setattr(
            fr, "choose_lever", lambda n, req: (lever, f"stub picks {lever}")
        )
        return calls

    return install


def _stage_only(
    monkeypatch, pack, *, budget=30, cfg=None, store=None, mc_seed=None, mc_draws=None
):
    """The front stage after a stubbed ens_solve (one solve charged)."""

    def fake_ens(st):
        st.solves += 1
        st.mark("ens_solve", "run", solves_charged=1)

    monkeypatch.setitem(S._STAGE_HANDLERS, "ens_solve", fake_ens)
    return _run(
        certifiable_weak_network(),
        pack,
        stages=FRONT,
        budget=budget,
        cfg=cfg,
        store=store,
        mc_seed=mc_seed,
        mc_draws=mc_draws,
    )


# ── F1: one function gives the levels, per lever ───────────────────────────


def _pack(cap=10.0, ladder=(4.0, 2.0, 1.0, 0.5, 0.25)):
    return default_weak_flexible_pack().model_copy(
        update={
            # a pack needs one target: LOLE stands in when the ENS one is absent
            "availability": AvailabilityTarget(
                ens_cap_permyriad=cap, target_lole_h=None if cap else 3.0
            ),
            "frontier_ladder": ladder,
        }
    )


def test_front_levels_is_the_frontier_rule_on_each_lever_base():
    lad = (4.0, 2.0, 1.0, 0.5, 0.25)
    assert fr.front_levels(
        "ens_cap", n_points=3, ladder=lad, ens_cap=10.0, margin_base=None
    ) == S.frontier_targets(10.0, 3, ladder=lad)
    assert fr.front_levels(
        "reserve_margin", n_points=5, ladder=lad, ens_cap=None, margin_base=0.1
    ) == S.frontier_targets(0.1, 5, ladder=lad)
    # each lever reads its own base only
    assert (
        fr.front_levels(
            "ens_cap", n_points=2, ladder=lad, ens_cap=10.0, margin_base=0.3
        )[0]
        == 10.0
    )
    assert (
        fr.front_levels(
            "reserve_margin", n_points=2, ladder=lad, ens_cap=10.0, margin_base=0.3
        )[0]
        == 0.3
    )


@pytest.mark.parametrize(
    "lever, kw, match",
    [
        ("ens_cap", {"ens_cap": None, "margin_base": 0.1}, "needs an ENS target"),
        (
            "reserve_margin",
            {"ens_cap": 10.0, "margin_base": None},
            "needs a base margin",
        ),
        ("ens_cap", {"ens_cap": float("nan"), "margin_base": None}, "positive, finite"),
        ("reserve_margin", {"ens_cap": None, "margin_base": 0.0}, "positive, finite"),
        (
            "reserve_margin",
            {"ens_cap": None, "margin_base": float("inf")},
            "positive, finite",
        ),
        ("voll", {"ens_cap": 10.0, "margin_base": 0.1}, "unknown reliability lever"),
    ],
)
def test_front_levels_refuses_a_missing_or_bad_base_by_name(lever, kw, match):
    with pytest.raises(fr.FrontConfigError, match=match):
        fr.front_levels(lever, n_points=3, ladder=(2.0, 1.0, 0.5), **kw)


def test_the_stage_runs_the_ens_lever_on_the_pack_target(monkeypatch, stub_front):
    calls = stub_front(lever="ens_cap")
    store: dict = {}
    _stage_only(monkeypatch, _pack(10.0), store=store)
    (call,) = calls
    assert call["lever"] == "ens_cap"
    n = S.frontier_point_count(29, 30)
    assert call["levels"] == S.frontier_targets(10.0, n, ladder=_pack().frontier_ladder)
    assert call["co2_caps_t"] == [None]
    assert call["pack"] is not None
    assert store["eh_front"]["levels_basis"] == "the pack's ENS target"
    assert store["eh_front"]["margin_base"] is None


def test_the_stage_runs_the_margin_lever_on_the_session_margin(monkeypatch, stub_front):
    calls = stub_front(lever="reserve_margin")
    store: dict = {}
    _stage_only(
        monkeypatch,
        _pack(10.0),
        store=store,
        cfg=SolverConfig(voll=VOLL, reserve_margin=0.2),
    )
    (call,) = calls
    assert call["lever"] == "reserve_margin"
    assert call["levels"][0] == 0.2
    assert call["levels"] == S.frontier_targets(
        0.2, S.frontier_point_count(29, 30), ladder=_pack().frontier_ladder
    )
    assert store["eh_front"]["levels_basis"] == "the session's reserve margin"
    # a set margin wins, and the payload says so
    assert store["eh_front"]["margin_base"] == {
        "value": 0.2,
        "assumed": False,
        "basis": "the session's reserve margin",
    }


def test_the_stage_uses_the_named_assumed_margin_when_none_is_set(
    monkeypatch, stub_front, real_record
):
    calls = stub_front(lever="reserve_margin")
    store: dict = {}
    report = _stage_only(monkeypatch, _pack(None), store=store)
    assert calls[0]["levels"][0] == fr.FRONT_MARGIN_BASE == 0.10
    assert store["eh_front"]["margin_base"] == {
        "value": 0.10,
        "assumed": True,
        "basis": "assumed: front default, not a project setting",
    }
    assert store["eh_front"]["levels_basis"] == (
        "assumed: front default, not a project setting"
    )
    rec = _record(report)
    assert rec.status == "run"
    assert "reserve_margin: stub picks reserve_margin" in rec.note
    # the note counts the solved points of the real record
    pts = real_record["points"]
    n_ok = sum(p["status"] == "ok" for p in pts)
    assert n_ok >= 1
    assert rec.note.startswith(f"{n_ok} of {len(pts)} points solved")


def test_the_ens_lever_without_a_pack_target_is_not_established(
    monkeypatch, stub_front
):
    calls = stub_front(lever="ens_cap")
    store: dict = {}
    report = _stage_only(monkeypatch, _pack(None), store=store)
    assert calls == []
    rec = _record(report)
    assert rec.status == "skipped" and rec.solves_charged == 0
    assert rec.note == (
        "front not established: the ENS lever needs an ENS target (‱ of demand)"
    )
    assert "eh_front" not in store


@pytest.mark.parametrize(
    "margin, match",
    [
        (-0.1, "the reserve_margin base -0.1 must be a positive, finite number"),
        (float("nan"), "the reserve_margin base nan must be a positive, finite"),
        # 2 × the ladder's 4 = 8 lies past MAX_MARGIN: check_front_request names it
        (2.0, "reliability level 8.0 must be a positive number"),
    ],
)
def test_a_margin_ladder_out_of_range_is_refused_by_name(monkeypatch, margin, match):
    monkeypatch.setattr(fr, "choose_lever", lambda n, req: ("reserve_margin", "stub"))
    store: dict = {}
    report = _stage_only(
        monkeypatch,
        _pack(10.0),
        store=store,
        cfg=SolverConfig(voll=VOLL, reserve_margin=margin),
    )
    rec = _record(report)
    assert rec.status == "skipped" and rec.solves_charged == 0
    assert rec.note.startswith("front not established: ")
    assert match in rec.note
    assert "eh_front" not in store
    assert report.pipeline.solves_consumed == 1


def test_a_budget_short_of_two_points_is_not_run(monkeypatch, stub_front):
    calls = stub_front()
    report = _stage_only(monkeypatch, _pack(10.0), budget=2)
    assert calls == []
    rec = _record(report)
    assert rec.status == "skipped"
    assert rec.note.startswith("not run: budget_solves leaves 1 solve(s)")


# ── F2: the study's seed and draws ─────────────────────────────────────────


def test_the_stage_passes_the_study_seed_and_draws(monkeypatch, stub_front):
    calls = stub_front()
    _stage_only(monkeypatch, _pack(10.0), mc_seed=7, mc_draws=123)
    assert calls[0]["seed"] == 7 and calls[0]["mc_draws"] == 123
    calls.clear()
    _stage_only(monkeypatch, _pack(10.0))
    assert calls[0]["seed"] == S.DEFAULT_MC_SEED
    assert calls[0]["mc_draws"] == S.DEFAULT_MC_DRAWS


# ── F3: the engine's own count is charged ──────────────────────────────────


@pytest.mark.parametrize("solves", [3, 4])
def test_the_charge_is_the_engine_count(monkeypatch, solves, stub_front):
    # 4 = three points plus a reference solve the stage did not plan
    stub_front(solves=solves)
    report = _stage_only(monkeypatch, _pack(10.0, ladder=(2.0, 1.0, 0.5)))
    rec = _record(report)
    assert rec.solves_charged == solves
    assert report.pipeline.solves_consumed == 1 + solves


def test_a_failed_front_charges_what_it_spent(monkeypatch, stub_front):
    stub_front(raise_after=2)
    store: dict = {}
    report = _stage_only(monkeypatch, _pack(10.0), store=store)
    rec = _record(report)
    assert rec.status == "failed" and rec.note == "front failed: boom"
    assert rec.solves_charged == 2
    assert report.pipeline.solves_consumed == 3
    assert "eh_front" not in store


def test_a_failure_with_no_partial_charges_nothing(monkeypatch, stub_front):
    def boom(*a, **k):
        raise RuntimeError("early")

    stub_front()
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
        fr.run_front_study(
            certifiable_weak_network(),
            threading.Lock(),
            SolverConfig(voll=VOLL),
            levels=[0.1],
            lever="reserve_margin",
            mc_draws=5,
        )
    assert info.value.front_result["solves"] == 1


@pytest.mark.live_solve
@pytest.mark.parametrize("caps, expected", [([None], 2), ([1e9], 3)])
def test_the_engine_counts_the_reference_solve(caps, expected):
    res = fr.run_front_study(
        certifiable_weak_network(),
        threading.Lock(),
        SolverConfig(voll=VOLL),
        levels=[0.05, 0.1],
        co2_caps_t=caps,
        lever="reserve_margin",
        mc_draws=5,
    )
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
    report = _run(
        n,
        _pack(10.0, ladder=(2.0, 1.0, 0.5)),
        stages=FRONT,
        budget=4,
        store=store,
        mc_draws=50,
    )
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
        pd.testing.assert_frame_equal(
            seen["before"][k], seen["after"][k], check_exact=True, obj=k
        )
    assert PyPSAService.get_active_context().network_revision == seen["rev"]
    # no report section: the front lives in its sibling table
    assert "front" not in report.sections


@pytest.mark.live_solve
def test_the_same_seed_gives_the_same_front_and_another_seed_does_not():
    def mc(seed):
        store: dict = {}
        _run(
            certifiable_weak_network(),
            _pack(10.0, ladder=(2.0, 1.0)),
            stages=FRONT,
            budget=3,
            store=store,
            mc_draws=40,
            mc_seed=seed,
        )
        assert store["eh_front"]["mc_budget"]["seed"] == seed
        return store["eh_front"]["points"]

    first = mc(3)
    assert mc(3) == first
    assert [p["mc"] for p in mc(4)] != [p["mc"] for p in first]


# ── F4: without "front", the study is master's ─────────────────────────────


def _digest(obj) -> str:
    return hashlib.sha256(obj.encode()).hexdigest()[:16]


# Literals computed on master a15fb8b9 from a fresh worktree.
MASTER_REPORT_DIGEST = "21657c6554b9b0a6"
MASTER_READINESS_DIGEST = "b4572ee04f3ec292"


@pytest.mark.live_solve
def test_a_study_without_front_is_masters():
    report = _run(
        certifiable_weak_network(), _pack(10.0), stages=None, budget=6, mc_draws=30
    )
    assert "front" not in [s.stage for s in report.pipeline.stages]
    # master's report had no valued_to_year (template-e2e 28, None on this single-period run): leave it out, so
    # the digest still pins every field master wrote.
    assert report.valued_to_year is None
    # Nor the TEA block's cost-of-ammonia fields and currency (template-e2e 28a): pinned here by value.
    new = {"lcoa_eur_per_t", "lcoa_status", "lcoa_note", "currency"}
    skipped = {
        "lcoa_eur_per_t": None,
        "lcoa_status": "skipped",
        "lcoa_note": "Cost of ammonia skipped: the network has no ammonia-synthesis Links",
        "currency": None,
    }
    assert {k: getattr(report.tea, k) for k in new} == skipped
    assert {k: report.sections["tea"].payload[k] for k in new} == skipped
    assert (
        _digest(
            report.model_dump_json(
                exclude={
                    "valued_to_year": True,
                    "tea": new,
                    "sections": {"tea": {"payload": new}},
                }
            )
        )
        == MASTER_REPORT_DIGEST
    )


def test_readiness_without_front_is_masters():
    import json

    n = certifiable_weak_network()
    PyPSAService.set_network(n)
    out = R.eh_readiness(n, _pack(10.0), budget_solves=30, voll=VOLL)
    assert "front" not in [r["stage"] for r in out["stages"]]
    assert (
        _digest(json.dumps(out, sort_keys=True, default=str)) == MASTER_READINESS_DIGEST
    )


def _readiness(pack, *, voll=VOLL, budget=30):
    n = certifiable_weak_network()
    PyPSAService.set_network(n)
    out = R.eh_readiness(n, pack, budget_solves=budget, voll=voll, stages=FRONT)
    return next(r for r in out["stages"] if r["stage"] == "front")


def test_readiness_prices_a_requested_front_on_the_margin_lever():
    # the fixture's LP has no outage series, so the margin lever runs; its
    # preflight and the session margin are read at run time: a ceiling
    row = _readiness(_pack(10.0))
    # 29 solves left of 30 → 12 points allowed; the ladder has 5 factors
    assert row["solves"] == 5
    assert row["basis"] == "upper_bound" and row["reason"] is None


def test_readiness_mirrors_the_stage_on_the_ens_lever(monkeypatch):
    monkeypatch.setattr(fr, "choose_lever", lambda n, req: ("ens_cap", "stub"))
    row = _readiness(_pack(10.0))
    assert row["solves"] == 5
    assert _readiness(_pack(10.0, ladder=(2.0, 1.0, 0.5)))["solves"] == 3
    assert row["basis"] == "exact" and row["reason"] is None
    row = _readiness(_pack(None))
    assert row["solves"] == 0
    assert row["reason"] == (
        "front not established: the ENS lever needs an ENS target (‱ of demand)"
    )
    row = _readiness(_pack(10.0), voll=0.0)
    assert row["solves"] == 0
    assert row["reason"] == (
        "the front's ENS lever needs VOLL > 0 (the session VOLL is 0)"
    )


# ── a requested front shows in every stage list; no apply from eh_front ───


def test_a_requested_front_has_its_row_everywhere(monkeypatch, stub_front):
    from harness import catalogue

    stub_front()
    report = _stage_only(monkeypatch, _pack(10.0))
    assert _record(report).status == "run"
    n = certifiable_weak_network()
    PyPSAService.set_network(n)
    out = R.eh_readiness(n, _pack(10.0), budget_solves=30, voll=VOLL, stages=FRONT)
    assert [r["stage"] for r in out["stages"]].count("front") == 1
    assert "front" in catalogue.EH_STAGE_ENUM
    assert catalogue.EH_STAGE_ENUM == list(EH_PIPELINE_STAGES)


def test_study_start_clears_an_old_eh_front(monkeypatch, stub_front):
    stub_front()
    store = {"eh_front": {"stale": True}}
    _stage_only(monkeypatch, _pack(10.0), store=store)
    assert "stale" not in store["eh_front"]
    store = {"eh_front": {"stale": True}}
    _run(
        certifiable_weak_network(),
        _pack(10.0),
        stages=("apply_pack", "ens_solve", "assemble"),
        budget=2,
        store=store,
    )
    assert "eh_front" not in store


def test_apply_refuses_when_only_the_study_front_exists(
    client, install_network, session_state
):
    install_network(certifiable_weak_network())
    state = session_state(client)
    state.pop("front", None)
    state["eh_front"] = {"points": [{"status": "ok"}], "solves": 1}
    r = client.post(
        "/api/results/front/apply", json={"index": 0, "expected_revision": 0}
    )
    assert r.status_code == 404
    assert "no front study has been run" in r.text


def test_readiness_needs_room_for_two_points():
    # budget 2: ens_solve leaves 1 → no front; budget 3 leaves 2 → two points
    row = _readiness(_pack(10.0), budget=2)
    assert row["solves"] == 0
    assert row["reason"] == "budget leaves room for fewer than two points"
    row = _readiness(_pack(10.0), budget=3)
    assert row["solves"] == 2 and row["reason"] is None


def test_the_margin_lever_does_not_need_voll():
    row = _readiness(_pack(10.0), voll=0.0)
    assert row["solves"] == 5 and row["reason"] is None


def test_a_zero_margin_is_off_so_the_assumed_base_applies(monkeypatch, stub_front):
    # SolverConfig.reserve_margin: None/0 = off (GK A (38), #2004 ask 2)
    calls = stub_front(lever="reserve_margin")
    store: dict = {}
    _stage_only(
        monkeypatch,
        _pack(10.0),
        store=store,
        cfg=SolverConfig(voll=VOLL, reserve_margin=0.0),
    )
    assert calls[0]["levels"][0] == fr.FRONT_MARGIN_BASE
    assert store["eh_front"]["margin_base"]["assumed"] is True


def test_a_network_swap_clears_eh_front():
    from services.project_context import RESULT_STATE_KEYS, cleared_result_state

    assert "eh_front" in RESULT_STATE_KEYS
    assert cleared_result_state()["eh_front"] is None
    PyPSAService.set_network(certifiable_weak_network())
    PyPSAService.get_active_context().solver_state["eh_front"] = {"points": []}
    PyPSAService.reset_network()
    assert PyPSAService.get_active_context().solver_state.get("eh_front") is None


@pytest.mark.live_solve
def test_the_stage_with_the_co2_goal_on_adds_no_goal_cap_row(monkeypatch):
    # DC review 02 C6 at the stage (DC 01 landed second): the session's CO2
    # limit goal reaches the stage's config, yet no front point carries a goal
    # cap row, so none is refused as a duplicate of the front's own cap.
    # Under the import cap (#1998) the weak network's whole fleet tops out at
    # 140.5 MW firm, short of the 144.0 MW a 20 % margin on its 120 MW peak
    # needs: that point is refused by the margin preflight, by name, and the
    # 10 % point solves. Neither is the goal's doing.
    import dataclasses

    from services import validation_service as V
    from services.solver import co2_cap

    limits = []
    real_apply = co2_cap.apply

    def spy(n, cfg, phase, captured):
        limits.append(cfg.co2_limit_t_per_yr)
        return real_apply(n, cfg, phase, captured)

    seen = {}
    real_front = fr.run_front_study

    def front_spy(n, lock, cfg, **kw):
        seen["limit"] = cfg.co2_limit_t_per_yr
        seen["codes"] = {
            m: [
                i.code
                for i in V._check_reserve_margin(
                    n,
                    dataclasses.replace(cfg, reserve_margin=m, co2_limit_t_per_yr=None),
                )
            ]
            for m in (0.1, 0.2)
        }
        seen["unreachable"] = [
            i.message
            for i in V._check_reserve_margin(
                n, dataclasses.replace(cfg, reserve_margin=0.2, co2_limit_t_per_yr=None)
            )
        ]
        limits.clear()
        return real_front(n, lock, cfg, **kw)

    monkeypatch.setattr(co2_cap, "apply", spy)
    monkeypatch.setattr(fr, "run_front_study", front_spy)
    store: dict = {}
    report = _run(
        certifiable_weak_network(),
        _pack(10.0, ladder=(2.0, 1.0)),
        stages=FRONT,
        budget=3,
        store=store,
        cfg=SolverConfig(voll=VOLL, co2_limit_t_per_yr=1e9),
        mc_draws=5,
    )
    rec = _record(report)
    assert rec.status == "run", rec.note
    assert seen["limit"] == 1e9
    assert limits and limits == [None] * len(limits)
    by_level = {p["level"]: p["status"] for p in store["eh_front"]["points"]}
    assert {round(k, 9): v for k, v in by_level.items()} == {
        0.1: "ok",
        0.2: "validation_failed",
    }
    assert seen["codes"][0.1] == []
    assert seen["codes"][0.2] == ["reserve_margin_unreachable"]
    assert "144.0 MW" in seen["unreachable"][0] and "140.5 MW" in seen["unreachable"][0]
