# ruff: noqa: F811  (pytest fixtures imported by name)
"""The backend readers of a MIP's bus prices (assistant-assurance 17).

On master's solve path HiGHS returns no duals for an integer (MIP) solve and
PyPSA writes 0.0 to every bus price. Each reader outside checked answers reads
the solve kind through `harness/solve_validity.mip_price_limit` and withholds
the figure, so no 0.0 is presented as a price. An LP of the same network is the
control: its prices are still read.
"""

from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from harness.solve_validity import (
    DUALS_NOT_MARGINAL,
    SOLVER_CONFIG_UNREADABLE,
    mip_price_limit,
    price_limit_without_config,
    solved_price_limit,
)
from services.solver_service import SolverConfig

CFG = SolverConfig()


def _solved(committable: bool) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=3, freq="h"))
    n.add("Bus", "el", carrier="AC")
    n.add("Bus", "h2", carrier="H2")
    n.add("Load", "d", bus="el", p_set=[5.0, 8.0, 3.0])
    n.add("Load", "h2_demand", bus="h2", p_set=1.0)
    n.add(
        "Generator",
        "g1",
        bus="el",
        p_nom=20,
        marginal_cost=10,
        committable=committable,
        p_min_pu=0.1 if committable else 0.0,
    )
    n.add("Generator", "g2", bus="el", p_nom=20, marginal_cost=30)
    n.add(
        "Link",
        "electrolysis",
        bus0="el",
        bus1="h2",
        carrier="H2 electrolysis",
        p_nom=5,
        efficiency=0.7,
        capital_cost=100.0,
    )
    status, _ = n.optimize(solver_name="highs")
    assert status == "ok"
    return n


@pytest.fixture(scope="module")
def mip():
    n = _solved(committable=True)
    assert mip_price_limit(n, CFG) == DUALS_NOT_MARGINAL
    # What the readers must never show: the solve's 0.0 "prices".
    assert (n.buses_t.marginal_price == 0.0).all().all()
    return n


@pytest.fixture(scope="module")
def lp():
    n = _solved(committable=False)
    assert mip_price_limit(n, CFG) is None
    assert (n.buses_t.marginal_price["el"] > 0).all()
    return n


def _process_unit_net() -> pypsa.Network:
    """A committable process Link: an LP under the default (`auto` linearises it
    on a short horizon), a MIP under `process_uc="binary"`. Only the config tells."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=3, freq="h"))
    n.add("Bus", ["el", "p"])
    n.add("Link", "proc", bus0="el", bus1="p", p_nom=5, committable=True)
    n.links["eh_process"] = True
    return n


def test_the_gate_reads_the_config_that_decides_commitment():
    n = _process_unit_net()
    assert mip_price_limit(n, SolverConfig()) is None
    assert mip_price_limit(n, SolverConfig(process_uc="binary")) == DUALS_NOT_MARGINAL


def test_a_stochastic_mip_is_gated_where_classify_says_stochastic():
    """`classify` reports a scenario run as "stochastic" first; its zeros are still a MIP's."""
    from harness.solve_validity import classify

    def net(committable):
        n = pypsa.Network()
        n.set_snapshots(range(3))
        n.add("Bus", "a")
        n.add("Generator", "g", bus="a", p_nom=1, committable=committable)
        n.set_scenarios({"s1": 0.5, "s2": 0.5})
        return n

    assert classify(net(True), CFG)["kind"] == "stochastic"
    assert mip_price_limit(net(True), CFG) == DUALS_NOT_MARGINAL
    assert mip_price_limit(net(False), CFG) is None


def test_the_solve_records_its_own_judgement_and_it_outlives_a_config_edit(mip, lp):
    """A1: the record from the solve wins over today's config; no record, today's."""
    n = _process_unit_net()
    recorded = {
        "last_solve_settings": {"price_limit": DUALS_NOT_MARGINAL},
        "solver_config": SolverConfig(),
    }  # edited to an LP after a binary solve
    assert solved_price_limit(recorded, n) == DUALS_NOT_MARGINAL
    unrecorded = {
        "last_solve_settings": {"voll_eur_per_mwh": 0.0},
        "solver_config": SolverConfig(process_uc="binary"),
    }
    assert solved_price_limit(unrecorded, n) == DUALS_NOT_MARGINAL
    assert (
        solved_price_limit({**unrecorded, "solver_config": SolverConfig()}, n) is None
    )
    assert solved_price_limit({}, lp) is None


def _settings_after_run_simulation(n):
    import queue
    import threading

    from services.solver_service import run_simulation

    emitted = {}
    status, _ = run_simulation(
        SolverConfig(),
        n,
        threading.Lock(),
        threading.Event(),
        queue.SimpleQueue(),
        state_update=lambda **kw: emitted.update(kw),
    )
    assert status in ("ok", "optimal")
    return emitted["last_solve_settings"]


def test_run_simulation_records_the_limit_only_on_a_mip():
    mip_n, lp_n = _solved(committable=True), _solved(committable=False)
    assert _settings_after_run_simulation(mip_n)["price_limit"] == DUALS_NOT_MARGINAL
    assert "price_limit" not in _settings_after_run_simulation(lp_n)


def test_the_prices_digest_of_a_mip_is_unavailable(mip, lp):
    from services.not_kept import WHY
    from services.results.prices_digest import prices_digest

    state = {"solver_config": CFG}
    got = prices_digest(mip, solver_state=state)
    assert got == {"status": "unavailable", "reason": DUALS_NOT_MARGINAL}
    assert "integer (MIP) solve" in WHY[DUALS_NOT_MARGINAL]
    assert prices_digest(lp, solver_state=state)["status"] == "ok"
    # A1: the solve's record decides, whatever the network reads as today.
    recorded = {**state, "last_solve_settings": {"price_limit": DUALS_NOT_MARGINAL}}
    assert prices_digest(lp, solver_state=recorded)["reason"] == DUALS_NOT_MARGINAL


def test_compare_prices_of_a_mip_are_unavailable_with_the_reason(mip, lp):
    from services.compare.prices import _compute_prices_summary

    got = _compute_prices_summary(mip, [], False, True, price_limit=DUALS_NOT_MARGINAL)
    assert not got.available and got.unavailable_reason == DUALS_NOT_MARGINAL
    assert (
        got.duration_curve == [] and got.max_price == 0.0
    )  # defaults under available=False
    assert (
        _compute_prices_summary(mip, [], False, True).unavailable_reason
        == DUALS_NOT_MARGINAL
    )
    ok = _compute_prices_summary(lp, [], False, True, price_limit=None)
    assert ok.available and ok.unavailable_reason is None and ok.max_price > 0


def test_compare_judges_each_project_by_its_own_saved_solve(tmp_path, mip, lp):
    from routers.compare import _price_limit

    assert (
        _price_limit(tmp_path, mip) == DUALS_NOT_MARGINAL
    )  # defaults: committable Generator
    assert _price_limit(tmp_path, lp) is None
    (tmp_path / "solver_config.json").write_text('{"process_uc": "binary"}')
    assert _price_limit(tmp_path, _process_unit_net()) == DUALS_NOT_MARGINAL
    # A1: the saved solve's own record decides over the saved config.
    import pickle

    (tmp_path / "results_state.pkl").write_bytes(
        pickle.dumps({"last_solve_settings": {"price_limit": DUALS_NOT_MARGINAL}})
    )
    (tmp_path / "solver_config.json").write_text('{"process_uc": "auto"}')
    assert _price_limit(tmp_path, _process_unit_net()) == DUALS_NOT_MARGINAL


def test_compare_fails_closed_when_the_saved_config_cannot_be_read(tmp_path, mip, lp):
    """A2: an unreadable config is never read as "no constraint"."""
    from routers.compare import _price_limit

    (tmp_path / "solver_config.json").write_text("{not json")
    assert _price_limit(tmp_path, _process_unit_net()) == SOLVER_CONFIG_UNREADABLE
    assert _price_limit(tmp_path, mip) == DUALS_NOT_MARGINAL  # a MIP under any config
    assert _price_limit(tmp_path, lp) is None  # no committable unit at all
    assert price_limit_without_config(lp) is None


def test_lcoh_of_a_mip_is_not_established(mip, lp):
    from services.results.lcoh import compute_lcoh

    got = compute_lcoh(mip, CFG, result_df=None)
    assert got["rows"] == [] and got["total"] is None
    assert got["unavailable_reason"] == DUALS_NOT_MARGINAL
    assert "integer (MIP) solve" in got["message"]
    assert compute_lcoh(lp, CFG, result_df=None)["rows"]
    # The route passes the solve's own judgement (A1).
    assert (
        compute_lcoh(lp, CFG, result_df=None, price_limit=DUALS_NOT_MARGINAL)["rows"]
        == []
    )
    assert compute_lcoh(mip, CFG, result_df=None, price_limit=None)["rows"]


def test_the_energy_hub_report_names_the_mip_not_missing_hydrogen(mip):
    from services.adequacy.eh_report import compute_tea

    block = compute_tea(cost_eur=100.0, served_energy_mwh=10.0, network=mip, cfg=CFG)
    assert block.lcoh_status == "not_established" and block.lcoh_eur_per_kg is None
    assert "integer (MIP) solve" in block.lcoh_note


# ── the decision-study report ────────────────────────────────────────────

from tests.test_study_report_routes import _econ_text, _full, _post, studies_on  # noqa: E402,F401
from tests.test_study_runner import FakeSolver  # noqa: E402


class _CommittableGrid(FakeSolver):
    """The study's fork with a committable grid Generator: an integer solve."""

    def __call__(self, config, n, *args, **kwargs):
        n.generators["committable"] = n.generators.index == "grid"
        return super().__call__(config, n, *args, **kwargs)


def test_the_study_report_withholds_figures_priced_at_a_mip_s_duals(
    client, api_project, studies_on, monkeypatch
):
    from services import solver_service

    monkeypatch.setattr(solver_service, "run_simulation", _CommittableGrid())
    body = _post(client, "rep-mip", _full(client, api_project, "rep-mip"))
    econ = body["sections"]["economics"]
    assert econ["status"] == "ok"
    for key in ("lcos_incl_charging", "market_revenue_at_duals"):
        assert econ["facts"][key]["value"] is None
        assert econ["facts"][key]["unavailable"] == DUALS_NOT_MARGINAL
    text = _econ_text(body)
    assert "integer (MIP) solve has no marginal prices" in text
    assert "zero profit" not in text
    codes = {d["code"] for d in body["required_disclosures"]}
    assert not any(
        c.startswith("market_revenue_at_duals_")
        and c != "market_revenue_at_duals_excluded_from_cash_flow"
        for c in codes
    )


def test_an_lp_study_report_keeps_its_figures_and_its_detail_has_no_limit(
    client, api_project, studies_on, monkeypatch
):
    """A4: a run detail without `price_limit` (every LP, every stored run) reads as before."""
    from services import solver_service

    monkeypatch.setattr(solver_service, "run_simulation", FakeSolver())
    body = _post(client, "rep-lp", _full(client, api_project, "rep-lp"))
    facts = body["sections"]["economics"]["facts"]
    assert facts["lcos_incl_charging"]["unavailable"] != DUALS_NOT_MARGINAL
    assert facts["market_revenue_at_duals"]["value"] is not None
    assert "integer (MIP) solve" not in _econ_text(body)


def test_a_stored_study_detail_without_the_key_reports_as_before(
    client, api_project, studies_on, monkeypatch, project_storage_dir
):
    """A4: a run stored before this change carries no `price_limit`; its report reads as it did."""
    from services import solver_service
    from services.study import store

    monkeypatch.setattr(solver_service, "run_simulation", _CommittableGrid())
    name = "rep-stored"
    sid = _full(client, api_project, name)
    pdir = project_storage_dir(name)
    run = store.load_aux(pdir, sid, "run")
    assert any("price_limit" in d for d in run["details"].values())
    for d in run["details"].values():
        d.pop("price_limit", None)
    store.save_aux(pdir, sid, "run", run)
    body = _post(client, name, sid)
    facts = body["sections"]["economics"]["facts"]
    assert facts["market_revenue_at_duals"]["unavailable"] != DUALS_NOT_MARGINAL
    assert facts["lcos_incl_charging"]["unavailable"] != DUALS_NOT_MARGINAL
    assert "integer (MIP) solve" not in _econ_text(body)


# ── the routes that read the solve-time settings (GK B2 A1) ──────────────

from tests.test_result_configured_fields import _run_and_join  # noqa: E402


def test_the_solved_settings_routes_read_a_mip_solve_as_they_read_an_lp(
    client, install_network, session_state
):
    """The record adds `price_limit` to `last_solve_settings` on a MIP; every reader of
    those settings reads its own keys, so the solved-settings routes answer as before."""
    from tests.test_adequacy_reserve_margin import _network

    n = _network(peaker=False)
    n.generators["committable"] = False
    n.generators.loc[n.generators.index[0], "committable"] = True
    install_network(n)
    session_state(client)["solver_config"] = SolverConfig(voll=3000.0)
    state = _run_and_join(client, session_state)
    settings = state["last_solve_settings"]
    assert (
        settings["price_limit"] == DUALS_NOT_MARGINAL
        and settings["voll_eur_per_mwh"] == 3000.0
    )
    ll = client.get("/api/results/lost_load")
    assert ll.status_code == 200, ll.text
    assert ll.json()["voll_applied"] is True and ll.json()["voll_eur_per_mwh"] == 3000.0
    ad = client.get("/api/results/adequacy")
    assert ad.status_code == 200 and ad.json()["configured"] is False
    rm = client.get("/api/results/reserve_margin")
    assert rm.status_code == 200 and rm.json() == {"configured": False}
    # The live prices reader judges by that record.
    from services.results.prices_digest import prices_digest

    assert prices_digest(n, solver_state=state)["reason"] == DUALS_NOT_MARGINAL


def test_the_live_readers_judge_by_the_solve_s_record(
    client, install_network, session_state, monkeypatch
):
    """A1 at the route and the retained digest: an LP network whose solve recorded a MIP
    (its config since edited) is withheld; without the record its prices are read.
    The network's own applied record (18a) outranks both."""
    from services import retained_results
    from services.pypsa_service import PyPSAService

    n = _solved(committable=False)
    install_network(n)
    session_state(client)["solver_config"] = SolverConfig()
    state = _run_and_join(client, session_state)
    assert "price_limit" not in state["last_solve_settings"]
    # The retained digest runs outside a request: point it at this session's run.
    monkeypatch.setattr(
        PyPSAService, "get_solver_state", classmethod(lambda cls: state)
    )
    monkeypatch.setattr(PyPSAService, "get_network", classmethod(lambda cls: n))
    monkeypatch.setattr("routers.results._result_df", lambda *a, **k: None)
    assert client.get("/api/results/lcoh").json()["rows"]
    assert retained_results._prices_digest().get("reason") != DUALS_NOT_MARGINAL

    state["last_solve_settings"] = {
        **state["last_solve_settings"],
        "price_limit": DUALS_NOT_MARGINAL,
    }
    # assistant-assurance 18a: the network's applied record (an LP) outranks the
    # persisted settings; only a run sealed before the record reads them.
    assert client.get("/api/results/lcoh").json()["rows"]
    assert retained_results._prices_digest().get("reason") != DUALS_NOT_MARGINAL
    from harness.solve_validity import APPLIED_KEY

    del n.meta[APPLIED_KEY]
    lcoh = client.get("/api/results/lcoh").json()
    assert lcoh["rows"] == [] and lcoh["unavailable_reason"] == DUALS_NOT_MARGINAL
    assert retained_results._prices_digest() == {
        "status": "unavailable",
        "reason": DUALS_NOT_MARGINAL,
    }


def test_the_not_kept_reason_is_the_gate_s_own_code():
    from services.not_kept import MIP_NO_PRICES

    assert MIP_NO_PRICES == DUALS_NOT_MARGINAL


def test_lcoh_without_an_electrolyser_stays_empty_on_a_mip(mip):
    from services.results.lcoh import compute_lcoh

    n = _solved(committable=True)
    n.remove("Link", "electrolysis")
    assert compute_lcoh(n, CFG, result_df=None) == {
        "rows": [],
        "total": None,
        "currency": "EUR",
    }


def test_a_modular_only_mip_fails_closed_without_its_config():
    """Mutant kill: a modular extendable is a MIP under any config."""
    n = pypsa.Network()
    n.set_snapshots(range(3))
    n.add("Bus", "a")
    n.add(
        "Generator", "m", bus="a", p_nom_extendable=True, p_nom_mod=5.0, p_nom_max=20.0
    )
    assert price_limit_without_config(n) == DUALS_NOT_MARGINAL


def test_the_digest_without_a_solver_state_judges_under_the_defaults(mip, lp):
    from services.results.prices_digest import prices_digest

    assert prices_digest(mip)["reason"] == DUALS_NOT_MARGINAL
    assert prices_digest(lp)["status"] == "ok"


def test_compare_s_record_decides_even_when_the_config_cannot_be_read(tmp_path, lp):
    import pickle

    from routers.compare import _price_limit

    (tmp_path / "solver_config.json").write_text("{not json")
    (tmp_path / "results_state.pkl").write_bytes(
        pickle.dumps({"last_solve_settings": {"price_limit": DUALS_NOT_MARGINAL}})
    )
    assert _price_limit(tmp_path, lp) == DUALS_NOT_MARGINAL


def test_an_lp_record_does_not_open_an_unreadable_config(tmp_path):
    """Mutant kill: a record without `price_limit` (an LP solve's) is no judgement."""
    import pickle

    from routers.compare import _price_limit

    (tmp_path / "solver_config.json").write_text("{not json")
    (tmp_path / "results_state.pkl").write_bytes(
        pickle.dumps({"last_solve_settings": {"voll_eur_per_mwh": 0.0}})
    )
    assert _price_limit(tmp_path, _process_unit_net()) == SOLVER_CONFIG_UNREADABLE
