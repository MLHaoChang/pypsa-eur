# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""assistant-assurance 18: the solve records the solver options it applied,
and `solve_validity` reads that record instead of reconstructing it. A run
sealed before the record existed keeps the reconstruction mirror."""

from __future__ import annotations

import contextlib

import pytest

from harness import solve_validity as sv
from tests.test_solve_validity import _cfg, _committable, _lp, _modular, _solve


def _sealed(n, *, solver="highs", options=None, linearised=False, mode=None):
    n.meta[sv.APPLIED_KEY] = sv.applied_record(
        solver,
        {} if options is None else options,
        {"linearized_unit_commitment": True} if linearised else {},
        mode,
    )
    return n


# ── the record wins over the mirror ──────────────────────────────────────────


def test_the_recorded_gap_is_reported_when_the_mirror_would_say_otherwise():
    # The config would rebuild 0.02; the solver was handed 0.05.
    n = _sealed(_committable(), options={"mip_rel_gap": 0.05})
    assert sv.applied_gap(n, _cfg()) == 0.05
    assert sv.classify(n, _cfg())["gap"] == 0.05


def test_a_recorded_solve_without_a_gap_reports_none_though_the_config_has_one():
    n = _sealed(_committable(), options={})
    assert sv.applied_gap(n, _cfg()) is None
    assert sv.classify(n, _cfg())["optimal"] is False


def test_the_recorded_solver_picks_the_gap_key():
    n = _sealed(
        _committable(), solver="gurobi", options={"MIPGap": 0.03, "mip_rel_gap": 0.5}
    )
    assert sv.applied_gap(n, _cfg(solver_name="highs")) == 0.03


def test_an_unmapped_recorded_solver_has_no_gap():
    n = _sealed(_committable(), solver="nosuch", options={"mip_rel_gap": 0.05})
    assert sv.applied_gap(n, _cfg()) is None


@pytest.mark.parametrize("value", [True, "0.05", float("nan"), -0.1, float("inf")])
def test_a_recorded_gap_that_is_not_a_finite_non_negative_number_is_none(value):
    n = _committable()
    n.meta[sv.APPLIED_KEY] = {
        "v": 1,
        "solver": "highs",
        "options": {"mip_rel_gap": value},
        "dropped": [],
        "linearized_unit_commitment": False,
        "process_uc_mode": None,
    }
    assert sv.applied_gap(n, _cfg()) is None


def test_a_recorded_linearised_solve_is_an_lp_whatever_the_config_says():
    # The island's linearised UC (services/solver/island.py) relaxes every
    # committable unit; the mirror, reading only process_uc, says MIP.
    n = _sealed(_committable(), linearised=True)
    assert sv.is_mip(n, _cfg(process_uc="binary")) is False
    assert sv.classify(n, _cfg(process_uc="binary"))["kind"] == "lp"


def test_a_recorded_binary_solve_is_a_mip():
    n = _sealed(_committable(), options={"mip_rel_gap": 0.02})
    assert sv.is_mip(n, _cfg()) is True


def test_a_recorded_linearised_solve_with_a_modular_asset_is_still_a_mip():
    assert sv.is_mip(_sealed(_modular(), linearised=True), _cfg()) is True


def test_a_recorded_solve_without_committable_units_is_an_lp():
    assert sv.is_mip(_sealed(_lp()), _cfg()) is False


def test_the_recorded_plain_mode_leaves_only_other_committable_units_binary(
    monkeypatch,
):
    n = _sealed(_committable(), mode="plain")
    monkeypatch.setattr(sv, "other_committable", lambda _n: (0, 0))
    assert sv.is_mip(n, _cfg()) is False
    monkeypatch.setattr(sv, "other_committable", lambda _n: (1, 0))
    assert sv.is_mip(n, _cfg()) is True


def test_the_recorded_mode_wins_over_the_config_process_uc(monkeypatch):
    # The mirror would recompute "binary" from the config; the solve ran plain.
    n = _sealed(_committable(), mode="plain")
    monkeypatch.setattr(sv, "other_committable", lambda _n: (0, 0))
    monkeypatch.setattr(sv, "planning_uc_mode", lambda *_a: "binary")
    assert sv.is_mip(n, _cfg(process_uc="binary")) is False


# ── a malformed record fails closed; no record keeps the mirror ──────────────


_VALID = {
    "v": 1,
    "solver": "highs",
    "options": {"mip_rel_gap": 0.0},
    "dropped": [],
    "linearized_unit_commitment": False,
    "process_uc_mode": None,
}


def test_the_valid_base_record_reads():
    n = _committable()
    n.meta[sv.APPLIED_KEY] = dict(_VALID)
    assert sv.applied_record_of(n) == _VALID
    assert sv.applied_gap(n, _cfg()) == 0.0


@pytest.mark.parametrize(
    "record",
    [
        "x",
        {**_VALID, "v": 2},
        {k: v for k, v in _VALID.items() if k != "v"},
        {**_VALID, "solver": 3},
        {**_VALID, "options": []},
        {**_VALID, "dropped": None},
        {k: v for k, v in _VALID.items() if k != "dropped"},
        {**_VALID, "linearized_unit_commitment": "no"},
        {**_VALID, "process_uc_mode": 7},
    ],
)
def test_a_malformed_record_reads_as_an_unrecorded_mip(record):
    n = _committable()
    n.meta[sv.APPLIED_KEY] = record
    cfg = _cfg(solver_options={"mip_rel_gap": 0.0})  # the mirror would say optimal
    assert sv.applied_record_of(n) is sv.MALFORMED
    assert sv.applied_gap(n, cfg) is None
    assert sv.is_mip(n, _cfg(process_uc="linearised")) is True
    assert sv.classify(n, cfg)["optimal"] is False


def test_a_run_sealed_before_the_record_keeps_the_mirror():
    n = _committable()
    assert sv.APPLIED_KEY not in n.meta
    assert sv.applied_record_of(n) is None
    assert sv.applied_gap(n, _cfg()) == 0.02
    assert sv.applied_gap(n, _cfg(solver_options={"mip_rel_gap": 0.1})) == 0.1


def test_an_object_without_a_meta_dict_has_no_record():
    assert sv.applied_record_of(object()) is None


# ── the record's shape ───────────────────────────────────────────────────────


def test_the_record_keeps_scalar_options_only():
    rec = sv.applied_record(
        "HiGHS",
        {
            "mip_rel_gap": 0.1,
            "presolve": "on",
            "threads": 4,
            "log": True,
            "bad": float("nan"),
            "obj": object(),
            5: 1.0,
        },
        {"linearized_unit_commitment": True},
        "linearised",
    )
    assert rec == {
        "v": 1,
        "solver": "HiGHS",
        "options": {"mip_rel_gap": 0.1, "presolve": "on", "threads": 4, "log": True},
        "dropped": ["bad", "obj", "5"],
        "linearized_unit_commitment": True,
        "process_uc_mode": "linearised",
    }


def test_a_credential_like_option_keeps_its_name_but_not_its_value():
    rec = sv.applied_record(
        "gurobi",
        {
            "WLSSECRET": "s3cr3t",
            "CloudSecretKey": "abc",
            "WLSACCESSID": "id-1",
            "LicenseID": 1234,
            "api_token": "t",
            "Password": "p",
            "MIPGap": 0.05,
            "Threads": 2,
        },
        {},
        None,
    )
    assert rec["options"] == {
        "WLSSECRET": sv.REDACTED,
        "CloudSecretKey": sv.REDACTED,
        "WLSACCESSID": sv.REDACTED,
        "LicenseID": sv.REDACTED,
        "api_token": sv.REDACTED,
        "Password": sv.REDACTED,
        "MIPGap": 0.05,
        "Threads": 2,
    }
    n = _committable()
    n.meta[sv.APPLIED_KEY] = rec
    assert sv.applied_gap(n, _cfg(solver_name="gurobi")) == 0.05


@pytest.mark.parametrize(
    "solver",
    sorted(
        __import__(
            "services.solver.assumptions", fromlist=["_MIP_KEYS_BY_SOLVER"]
        )._MIP_KEYS_BY_SOLVER
    ),
)
def test_no_solver_gap_key_reads_as_a_credential(solver):
    from services.solver.assumptions import _MIP_KEYS_BY_SOLVER

    gap_key = _MIP_KEYS_BY_SOLVER[solver][0]
    assert sv.applied_record(solver, {gap_key: 0.01}, {}, None)["options"] == {
        gap_key: 0.01
    }


def test_a_dropped_gap_is_named_and_reads_as_not_recorded():
    rec = sv.applied_record(
        "highs", {"mip_rel_gap": [0.1], "callback": object()}, {}, None
    )
    assert rec["dropped"] == ["mip_rel_gap", "callback"]
    assert rec["options"] == {}
    n = _committable()
    n.meta[sv.APPLIED_KEY] = rec
    cls = sv.classify(n, _cfg())  # the mirror would say 0.02
    assert (cls["kind"], cls["gap"], cls["optimal"]) == ("mip", None, False)
    assert sv.delta_limit(1.0, cls, 100.0, cls, 100.0) == sv.GAP_NOT_RECORDED


def test_the_record_reads_back_after_a_netcdf_round_trip(tmp_path):
    import pypsa

    n = _sealed(
        _committable(),
        options={"mip_rel_gap": 0.05},
        linearised=True,
        mode="linearised",
    )
    path = tmp_path / "n.nc"
    n.export_to_netcdf(path)
    back = pypsa.Network(path)
    assert sv.applied_record_of(back) == n.meta[sv.APPLIED_KEY]


# ── the solve writes it, on the path it took ─────────────────────────────────


@pytest.mark.live_solve
def test_the_solve_records_the_options_it_passed(reset_backend, monkeypatch):
    live, cfg, kwargs = _solve(
        _committable(),
        {
            "solver_name": "highs",
            "mip_gap": 0.01,
            "solver_options": {"mip_rel_gap": 0.1},
        },
        monkeypatch,
    )
    rec = live.meta[sv.APPLIED_KEY]
    assert rec["solver"] == "highs"
    assert rec["options"] == kwargs["solver_options"]
    assert rec["linearized_unit_commitment"] is False
    assert sv.applied_gap(live, cfg) == 0.1


@pytest.mark.live_solve
def test_the_island_path_records_its_linearised_commitment(reset_backend, monkeypatch):
    import services.solver_service as ss

    @contextlib.contextmanager
    def island_uc(_n, _cfg):
        yield {"linearized_unit_commitment": True}

    monkeypatch.setattr(ss, "_island_solve_uc", island_uc)
    live, cfg, kwargs = _solve(
        _committable(), {"solver_name": "highs", "mip_gap": 0.02}, monkeypatch
    )
    assert kwargs["linearized_unit_commitment"] is True
    assert live.meta[sv.APPLIED_KEY]["linearized_unit_commitment"] is True
    # The mirror, blind to the island, would call this a MIP at gap 0.02.
    assert sv.classify(live, cfg)["kind"] == "lp"


@pytest.mark.live_solve
def test_a_solve_whose_provenance_fails_drops_the_record(reset_backend, monkeypatch):
    import services.workflow_tools as wt

    def boom(*_a, **_k):
        raise RuntimeError("no provenance")

    monkeypatch.setattr(wt, "_record_solve_inputs", boom)
    stale = _sealed(_committable(), options={"mip_rel_gap": 0.9})  # a previous solve's
    live, _cfg_, _kw = _solve(stale, {"solver_name": "highs"}, monkeypatch)
    assert sv.APPLIED_KEY not in live.meta


def _digests(live, cfg):
    from services.dispatch_status import dispatch_digest
    from services.pypsa_service import PyPSAService
    from services.workflow_tools import _network_input_fingerprint

    ctx = PyPSAService.build_context()
    ctx.network = live
    ctx.solver_state["solver_config"] = cfg
    return (
        _network_input_fingerprint(ctx),
        _network_input_fingerprint(ctx, exact=True),
        dispatch_digest(live),
    )


@pytest.mark.live_solve
def test_the_record_leaves_the_fingerprints_and_dispatch_digest_unchanged(
    reset_backend, monkeypatch
):
    live, cfg, _kw = _solve(_committable(), {"solver_name": "highs"}, monkeypatch)
    assert sv.APPLIED_KEY in live.meta
    with_record = _digests(live, cfg)
    # The solved network does not read as stale with its record on it.
    assert with_record[:2] == (
        live.meta["assistant_solve_input_fingerprint"],
        live.meta["assistant_solve_exact_input_fingerprint"],
    )
    live.meta.pop(sv.APPLIED_KEY)
    assert _digests(live, cfg) == with_record


@pytest.mark.live_solve
def test_a_failed_solve_drops_the_previous_record(reset_backend, monkeypatch):
    import queue
    import threading

    from routers import simulation as sim_router
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation
    from tests.conftest import install_network_into_backend

    def infeasible(self, *a, **k):
        return "warning", "infeasible"

    monkeypatch.setattr(
        __import__("pypsa").optimization.OptimizationAccessor, "__call__", infeasible
    )
    install_network_into_backend(_sealed(_committable(), options={"mip_rel_gap": 0.9}))
    live = PyPSAService.get_network()
    assert sv.APPLIED_KEY in live.meta
    cfg = SolverConfig(solver_name="highs")
    sim_router._state["solver_config"] = cfg
    status, _cond = run_simulation(
        cfg,
        live,
        PyPSAService.get_lock(),
        threading.Event(),
        queue.SimpleQueue(),
        state_update=lambda **k: None,
    )
    assert status not in ("ok", "optimal")
    assert sv.APPLIED_KEY not in live.meta


def test_an_integer_option_stays_an_integer():
    rec = sv.applied_record("highs", {"threads": 4, "mip_rel_gap": 0.01}, {}, None)
    assert type(rec["options"]["threads"]) is int
    assert type(rec["options"]["mip_rel_gap"]) is float


def test_no_options_and_no_uc_keywords_make_an_empty_record():
    rec = sv.applied_record("highs", None, None, None)
    assert rec["options"] == {} and rec["dropped"] == []
    assert rec["linearized_unit_commitment"] is False


# ── 18a: the price readers judge by the record too ───────────────────────────


def test_solved_price_limit_reads_the_record_over_the_persisted_settings():
    # The settings and config say a binary MIP; the solve that wrote `n`
    # ran linearised, so its prices are an LP's.
    n = _sealed(_committable(), linearised=True)
    state = {
        "last_solve_settings": {"price_limit": sv.DUALS_NOT_MARGINAL},
        "solver_config": _cfg(process_uc="binary"),
    }
    assert sv.solved_price_limit(state, n) is None


def test_solved_price_limit_reads_a_recorded_mip_over_an_lp_config():
    n = _sealed(_committable(), mode="binary")
    state = {
        "last_solve_settings": {"voll_eur_per_mwh": 0.0},
        "solver_config": _cfg(process_uc="linearised"),
    }
    assert sv.solved_price_limit(state, n) == sv.DUALS_NOT_MARGINAL
    assert sv.solved_price_limit({}, n) == sv.DUALS_NOT_MARGINAL


def test_mip_price_limit_reads_the_record_over_the_config():
    lp_run = _sealed(_committable(), linearised=True)
    assert sv.mip_price_limit(lp_run, _cfg(process_uc="binary")) is None
    mip_run = _sealed(_committable(), mode="binary")
    assert sv.mip_price_limit(mip_run, _cfg(process_uc="linearised")) == (
        sv.DUALS_NOT_MARGINAL
    )


def test_price_limit_without_config_reads_the_record():
    # No config is needed once the solve recorded what it applied.
    assert (
        sv.price_limit_without_config(_sealed(_committable(), linearised=True)) is None
    )
    assert sv.price_limit_without_config(_sealed(_committable(), mode="binary")) == (
        sv.DUALS_NOT_MARGINAL
    )
    assert sv.price_limit_without_config(_sealed(_modular(), linearised=True)) == (
        sv.DUALS_NOT_MARGINAL
    )
    assert sv.price_limit_without_config(_sealed(_lp())) is None


@pytest.mark.parametrize(
    "net, kw, other, expected",
    [
        (_committable, {"linearised": True}, (0, 0), None),
        (_committable, {"mode": "binary"}, (0, 0), sv.DUALS_NOT_MARGINAL),
        (_committable, {"mode": "plain"}, (0, 0), None),
        (_committable, {"mode": "plain"}, (1, 0), sv.DUALS_NOT_MARGINAL),
        (_modular, {"linearised": True}, (0, 0), sv.DUALS_NOT_MARGINAL),
        (_lp, {}, (0, 0), None),
    ],
)
def test_a_valid_record_never_reads_as_an_unreadable_config(
    monkeypatch, net, kw, other, expected
):
    monkeypatch.setattr(sv, "other_committable", lambda _n: other)
    n = _sealed(net(), **kw)
    assert sv.price_limit_without_config(n) == expected
    assert sv.price_limit_without_config(n) != sv.SOLVER_CONFIG_UNREADABLE


@pytest.mark.parametrize("record", [None, "malformed"])
def test_a_run_without_a_valid_record_keeps_the_settings_and_config_path(record):
    from tests.test_mip_price_readers import _process_unit_net

    n = _process_unit_net()  # an LP under the default config, a MIP under binary
    if record:
        n.meta[sv.APPLIED_KEY] = {**_VALID, "v": 2}
    recorded = {
        "last_solve_settings": {"price_limit": sv.DUALS_NOT_MARGINAL},
        "solver_config": _cfg(),
    }
    assert sv.solved_price_limit(recorded, n) == sv.DUALS_NOT_MARGINAL
    unrecorded = {"last_solve_settings": {}, "solver_config": _cfg()}
    # No record: the config decides (an LP). Malformed: fails closed.
    assert sv.solved_price_limit(unrecorded, n) == (
        sv.DUALS_NOT_MARGINAL if record else None
    )
    assert sv.price_limit_without_config(n) == (
        sv.DUALS_NOT_MARGINAL if record else sv.SOLVER_CONFIG_UNREADABLE
    )


@pytest.mark.live_solve
def test_the_real_island_path_seals_its_linearised_commitment(monkeypatch):
    """The island sidecar's linearised UC, through `run_simulation` and the real
    `island.solve_uc` (only the sidecar load is stubbed): the record says
    linearised, so the committable genset's prices read as an LP's."""
    from services.solver_service import SolverConfig
    from tests.test_island_rows import _hub, _run, _units
    from tests.test_island_study import _committed_genset, _uc

    n = _hub(load=10.0, gensets=[("g", 6.0)])
    n.storage_units.at["bess", "p_nom_max"] = 7.0
    n.generators.at["g", "committable"] = True  # the mirror alone would say MIP
    island = _uc(_units(gensets={"g": _committed_genset()}))
    cfg = SolverConfig(island_constraints=True)
    status, condition, _ = _run(n, cfg, monkeypatch, island)
    assert status in ("ok", "optimal"), condition
    assert sv.applied_record_of(n)["linearized_unit_commitment"] is True
    assert sv.solved_price_limit({"solver_config": cfg}, n) is None
    assert sv.price_limit_without_config(n) is None
    del n.meta[sv.APPLIED_KEY]
    assert sv.solved_price_limit({"solver_config": cfg}, n) == sv.DUALS_NOT_MARGINAL


def test_a_malformed_record_defers_to_the_persisted_settings():
    # No committable or modular unit, so only the settings can say MIP; a
    # malformed record must not be read as a valid LP record.
    n = _lp()
    n.meta[sv.APPLIED_KEY] = {**_VALID, "v": 2}
    state = {"last_solve_settings": {"price_limit": sv.DUALS_NOT_MARGINAL}}
    assert sv.solved_price_limit(state, n) == sv.DUALS_NOT_MARGINAL
