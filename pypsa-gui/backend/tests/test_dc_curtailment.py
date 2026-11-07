"""
The data-centre curtailable load (DC-07 S2; issue 07, GK A Q1-Q3, conditions C15-C21).

Fixture economics: the grid costs 50 EUR/MWh and curtailing 5, so the LP curtails as much as
the cap (C16) and the duration's energy proxy (Q3) let it. The project's spec is injected by
patching ``project_terms`` (the read through ``dc_backup.project_spec`` is DC-06's).
"""

from __future__ import annotations

import queue
import sys
import threading
import pathlib

import numpy as np
import pandas as pd
import pypsa
import pytest

from models.flex_archetypes import DataCentreLoadSpec
from services.adequacy import slack
from services.pypsa_service import PyPSAService
from services.solver import dc_curtailment as C
from services.solver_service import SolverConfig, reserve_margin_facts, run_simulation

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

IT = np.array([75.0, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75])
COOL = np.array([25.0, 25, 10, 25, 30, 25, 25, 20, 25, 25, 25, 25])
GRID_MC = 50.0
PRICE = 5.0
SHARE = 0.2


def _network(hours: float = 1.0, cooling_bus: str = "dc_mv") -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=len(IT), freq=f"{int(hours)}h"))
    n.snapshot_weightings.loc[:, :] = hours
    n.add("Carrier", "AC")
    n.add("Bus", "dc_mv", carrier="AC")
    n.add("Bus", "it_bus", carrier="AC")
    n.add(
        "Generator",
        "grid",
        bus="dc_mv",
        carrier="AC",
        p_nom=1000.0,
        marginal_cost=GRID_MC,
    )
    n.add("Link", "ups", bus0="dc_mv", bus1="it_bus", p_nom=1000.0, efficiency=1.0)
    n.add("Load", "it_load", bus="it_bus", p_set=pd.Series(IT, index=n.snapshots))
    n.add("Load", "cooling", bus=cooling_bus, p_set=pd.Series(COOL, index=n.snapshots))
    return n


def _terms(d=None, price=PRICE, share=SHARE) -> C.Terms:
    return C.Terms(price, share, d)


def _solve(n, monkeypatch, terms, **cfg_kw):
    monkeypatch.setattr(C, "project_terms", lambda: terms)
    return _solve_with(n, monkeypatch, **cfg_kw)


def _solve_with(n, monkeypatch, **cfg_kw):
    PyPSAService.set_network(n)
    sink: dict = {}
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    status, condition = run_simulation(
        SolverConfig(voll=cfg_kw.pop("voll", 3000.0), **cfg_kw),
        n,
        PyPSAService.get_lock(),
        threading.Event(),
        log_q,
        state_update=lambda **kw: sink.update(kw),
    )
    lines = []
    while True:
        try:
            lines.append(str(log_q.get_nowait()))
        except queue.Empty:
            break
    return status, condition, sink, lines


def _curtailed(sink) -> pd.Series:
    return sink["last_lost_load"][C.META_KEY]["p_t"]


# ── C15: off is byte-equal; the price's own checks ───────────────────────────


def _spec(**kw) -> DataCentreLoadSpec:
    base = {
        "phases": [{"cod": "2030-01-01", "it_mw": 80}],
        "pue_at_design": 1.3,
        "redundancy": "N+1",
        "critical_share": 0.75,
        "curtailable_share": SHARE,
    }
    return DataCentreLoadSpec.model_validate({**base, **kw})


def test_the_price_defaults_to_none_and_is_finite_and_not_negative():
    assert _spec().curtailment_price_eur_per_mwh is None
    assert _spec(curtailment_price_eur_per_mwh=0).curtailment_price_eur_per_mwh == 0.0
    for bad in (-1.0, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            _spec(curtailment_price_eur_per_mwh=bad)


def test_no_price_or_a_zero_share_is_off():
    assert C.terms_of(_spec()) is None
    assert (
        C.terms_of(_spec(curtailable_share=0.0, curtailment_price_eur_per_mwh=5.0))
        is None
    )
    assert C.terms_of(_spec(curtailment_price_eur_per_mwh=0.0)) == C.Terms(
        0.0, SHARE, None
    )


def test_critical_plus_curtailable_above_one_is_refused():
    """C8 pin: ``_shares`` refuses it."""
    with pytest.raises(ValueError, match="critical_share \\+ curtailable_share"):
        _spec(critical_share=0.9, curtailable_share=0.2)


def _off_spec(off):
    return (
        _spec(curtailable_share=0.0, curtailment_price_eur_per_mwh=5.0)
        if off == "share0"
        else _spec()
    )


@pytest.mark.parametrize("off", ["no_price", "share0"])
def test_off_leaves_the_network_and_its_netcdf_byte_equal(monkeypatch, tmp_path, off):
    """The bind is a no-op: nothing added, network.nc byte-equal, the LP chain unchanged."""
    assert C.terms_of(_off_spec(off)) is None
    monkeypatch.setattr(C, "project_terms", lambda: C.terms_of(_off_spec(off)))
    n = _network()
    n.export_to_netcdf(tmp_path / "before.nc")
    undo: list = []
    C.apply(n, lambda _m: None, undo, {})
    marker = object()
    assert C._wrap_with_dc_curtailment(n, marker) is marker
    assert undo == []
    n.export_to_netcdf(tmp_path / "after.nc")
    assert (tmp_path / "before.nc").read_bytes() == (tmp_path / "after.nc").read_bytes()


@pytest.mark.parametrize("off", ["no_price", "share0"])
def test_off_solves_byte_equal_to_a_build_without_the_feature(
    monkeypatch, tmp_path, off
):
    """Solved through ``run_simulation`` with the spec off, and with the feature's three calls
    removed: the same LP chain, the same netCDF data and the same input fingerprint (only the
    sizing record's clock and revision differ between two solves)."""
    import json

    import xarray as xr

    monkeypatch.setattr(C, "project_terms", lambda: C.terms_of(_off_spec(off)))
    assert C._wrap_with_dc_curtailment(_network(), None) is None
    a, b = _network(), _network()
    status, _, _, _ = _solve_with(a, monkeypatch)
    assert status in ("ok", "optimal")
    monkeypatch.setattr(C, "apply", lambda *a, **k: None)
    monkeypatch.setattr(C, "_wrap_with_dc_curtailment", lambda n, fn, **k: fn)
    monkeypatch.setattr(C, "commit", lambda *a, **k: None)
    _solve_with(b, monkeypatch)
    a.export_to_netcdf(tmp_path / "a.nc")
    b.export_to_netcdf(tmp_path / "b.nc")
    da, db = xr.open_dataset(tmp_path / "a.nc"), xr.open_dataset(tmp_path / "b.nc")
    assert da.equals(db)
    ma, mb = json.loads(da.attrs["meta"]), json.loads(db.attrs["meta"])
    for m in (ma, mb):
        m.get("sizing_record", {}).pop("solved_at", None)
        m.get("sizing_record", {}).pop("revision", None)
    assert ma == mb and C.META_KEY not in ma
    assert (
        ma["assistant_solve_input_fingerprint"]
        == mb["assistant_solve_input_fingerprint"]
    )


# ── C16: the cap ─────────────────────────────────────────────────────────────


def test_the_cap_is_the_share_of_the_facility_and_never_above_cooling(monkeypatch):
    status, _, sink, _ = _solve(_network(), monkeypatch, _terms())
    assert status in ("ok", "optimal")
    p = _curtailed(sink).to_numpy()
    cap = np.minimum(SHARE * (IT + COOL), COOL)
    assert np.all(p <= cap + 1e-6)
    assert p == pytest.approx(cap, abs=1e-6)  # cheap: it runs at the cap
    assert p[2] == pytest.approx(10.0)  # the cooling load binds, not share · F
    assert C.GENERATOR not in _network().generators.index


@pytest.mark.parametrize("tid", ["eh_datacenter", "eh_datacenter_staged"])
def test_on_the_built_template_the_cap_stays_under_cooling_with_slope_and_ai_workload(
    tid,
):
    spec = T.dc_load_spec(
        tid,
        {
            "curtailable_share": 0.25,
            "curtailment_price_eur_per_mwh": 5.0,
            "pue_temperature_slope_per_k": 0.05,
        },
    )
    assert spec.ai_workload is not None and spec.pue_temperature_slope_per_k > 0
    n = T.BUILDERS[tid](load_spec=spec)
    undo: list = []
    C.apply(n, lambda _m: None, undo, {}, terms=C.terms_of(spec))
    try:
        g = n.generators.loc[C.GENERATOR]
        assert g.bus == "dc_mv" and g.carrier == slack.DC_CURTAILMENT_CARRIER
        p_max = (
            n.get_switchable_as_dense("Generator", "p_max_pu")[C.GENERATOR] * g.p_nom
        )
        cooling = n.get_switchable_as_dense("Load", "p_set")["cooling"]
        assert (p_max <= cooling + 1e-9).all()
        assert (p_max > 0).any()
    finally:
        for _kind, fn in undo:
            fn()
    assert C.GENERATOR not in n.generators.index
    assert slack.DC_CURTAILMENT_CARRIER not in n.carriers.index


# ── C18: apart from unserved energy; the refusal ─────────────────────────────


def test_the_tier_is_a_slack_but_never_involuntary():
    gens = pd.DataFrame(
        {"carrier": [slack.DC_CURTAILMENT_CARRIER, "AC"]}, index=[C.GENERATOR, "grid"]
    )
    assert slack.slack_generator_mask(gens).tolist() == [True, False]
    assert slack.involuntary_slack_mask(gens).tolist() == [False, False]
    assert slack.DC_CURTAILMENT_CARRIER not in slack.INVOLUNTARY_SLACK_CARRIERS


def test_curtailed_energy_is_reported_apart_from_unserved_energy(monkeypatch):
    n = _network()
    n.generators.at["grid", "p_nom"] = (
        90.0  # 10 MW short at the hours the cap is below it
    )
    status, _, sink, _ = _solve(n, monkeypatch, _terms(), voll=1000.0)
    assert status in ("ok", "optimal")
    cap = sink["last_lost_load"]
    assert not any(
        str(c).startswith(slack.DC_CURTAILMENT_PREFIX)
        for c in cap["lost_load_t"].columns
    )
    lost = float(cap["lost_load_total_mwh"])
    curt = cap[C.META_KEY]["total_mwh"]
    assert curt == pytest.approx(
        float(np.minimum(SHARE * (IT + COOL), COOL).sum()), rel=1e-6
    )
    short = np.clip(
        IT + COOL - 90.0 - np.minimum(SHARE * (IT + COOL), COOL), 0, None
    ).sum()
    assert lost == pytest.approx(short, abs=1e-6)
    rec = n.meta[C.META_KEY]
    assert rec["curtailed_mwh"] == pytest.approx(curt)
    assert rec["counts_as_unserved_energy"] is False


def test_the_ens_cap_never_throttles_curtailment(monkeypatch):
    n = _network()
    n.generators.at["grid", "p_nom"] = (
        80.0  # 4 MWh short once curtailment is at its cap
    )
    # VoLL 20 < the grid's 50: shedding is cheap, so only the ENS cap (40 ‱ ≈ 4.7 MWh) holds
    # it; were curtailment counted there, the cap could not be met at full curtailment.
    status, _, sink, _ = _solve(
        n, monkeypatch, _terms(), voll=20.0, ens_cap_permyriad=40.0
    )
    assert status in ("ok", "optimal")
    assert _curtailed(sink).to_numpy() == pytest.approx(
        np.minimum(SHARE * (IT + COOL), COOL), abs=1e-6
    )


def test_the_reserve_margin_never_counts_it(monkeypatch):
    n = _network()
    n.remove("Generator", "grid")
    n.add("Carrier", "gas")
    n.add(
        "Generator",
        "grid",
        bus="dc_mv",
        carrier="gas",
        p_nom=300.0,
        marginal_cost=GRID_MC,
        outage_rate_value=0.05,
        outage_rate_basis="EFORd",
        mttr_hours=24.0,
    )
    undo: list = []
    C.apply(n, lambda _m: None, undo, {}, terms=_terms())
    try:
        facts = reserve_margin_facts(n, SolverConfig(reserve_margin=0.2))
        names = {r["name"] for r in facts["stash"]["assets"]}
        assert "grid" in names and C.GENERATOR not in names
    finally:
        for _kind, fn in undo:
            fn()


def test_it_never_takes_a_curtailment_cost_term():
    n = _network()
    undo: list = []
    C.apply(n, lambda _m: None, undo, {}, terms=_terms())
    try:
        cc = n.generators.get("curtailment_cost")
        assert cc is None or not float(cc.fillna(0.0).get(C.GENERATOR, 0.0)) > 0
    finally:
        for _kind, fn in undo:
            fn()


@pytest.mark.parametrize("where", ["missing", "it_bus"])
def test_a_project_without_a_cooling_load_on_dc_mv_is_refused(monkeypatch, where):
    n = _network(cooling_bus="it_bus")
    if where == "missing":
        n.remove("Load", "cooling")
    status, condition, _, lines = _solve(n, monkeypatch, _terms())
    assert status == "error"
    assert condition.startswith(f"curtailment_refused: {C.NO_COOLING_LOAD}")
    assert C.GENERATOR not in n.generators.index


# ── Q3, C19, C21: the duration's energy proxy ────────────────────────────────


def test_runs_are_measured_in_weighted_hours():
    hourly = pd.Series(1.0, index=range(6))
    assert C.runs(hourly, 2.0, hourly.index) == [(0, 2), (1, 3), (2, 4), (3, 5)]
    two = pd.Series(2.0, index=range(6))
    assert C.runs(two, 4.0, two.index) == [(0, 2), (1, 3), (2, 4), (3, 5)]
    assert C.runs(two, 2.0, two.index) == [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)]
    assert C.runs(two, 11.0, two.index) == [(0, 5)]  # a bounded tail, but no full run:
    assert not C.fits(two, 11.0, two.index)  # the horizon is refused (C21)


def test_runs_never_cross_a_period():
    sns = pd.MultiIndex.from_product([[2030, 2035], range(3)])
    w = pd.Series(1.0, index=sns)
    assert C.runs(w, 1.0, sns) == [(0, 1), (1, 2), (3, 4), (4, 5)]


@pytest.mark.parametrize("hours,d", [(1.0, 2.0), (2.0, 4.0)])
def test_every_run_holds_its_energy_bound_and_one_binds(monkeypatch, hours, d):
    n = _network(hours=hours)
    status, _, sink, lines = _solve(n, monkeypatch, _terms(d=d))
    assert status in ("ok", "optimal")
    p = _curtailed(sink).to_numpy()
    w = np.full(len(p), hours)
    f = IT + COOL
    found = C.runs(pd.Series(w), d, pd.RangeIndex(len(p)))
    assert found
    slack_ = [
        d * SHARE * f[i : j + 1].max() - (w[i : j + 1] * p[i : j + 1]).sum()
        for i, j in found
    ]
    assert min(slack_) >= -1e-6
    assert min(slack_) == pytest.approx(0.0, abs=1e-6)
    assert p.sum() < np.minimum(SHARE * f, COOL).sum() - 1e-3  # the duration cut it
    assert any(
        f"{len(found)} energy-proxy rows" in ln and "nonzeros" in ln for ln in lines
    )
    assert n.meta[C.META_KEY]["claim_limit"] == C.CLAIM_LIMIT
    assert "consecutive hours are not enforced" in C.CLAIM_LIMIT


def test_a_duration_the_horizon_cannot_hold_is_refused(monkeypatch):
    n = _network()
    status, condition, _, _ = _solve(n, monkeypatch, _terms(d=12.0))
    assert status == "error"
    assert condition.startswith(f"curtailment_refused: {C.DURATION_EXCEEDS_HORIZON}")
    assert C.GENERATOR not in n.generators.index


def test_the_claim_limit_is_in_the_docs():
    doc = " ".join((BACKEND.parent / "CONTEXT.md").read_text().split())
    assert C.CLAIM_LIMIT in doc


def test_an_import_energy_cap_is_not_loosened(monkeypatch):
    """C17: curtailing serves load on dc_mv; it never adds room under a metered import cap."""
    n = _network()
    n.remove("Generator", "grid")
    n.add("Bus", "grid", carrier="AC")
    n.add(
        "Generator",
        "grid",
        bus="grid",
        carrier="AC",
        p_nom=1000.0,
        marginal_cost=GRID_MC,
    )
    n.add("Link", "poc", bus0="grid", bus1="dc_mv", p_nom=1000.0, efficiency=1.0)
    hours = float(n.snapshot_weightings["generators"].sum())
    cap_mwh_year = 900.0 * 8760.0 / hours  # 900 MWh over these 12 hours
    status, _, sink, _ = _solve(
        n,
        monkeypatch,
        _terms(),
        voll=1000.0,
        import_energy_cap_mwh_per_year=cap_mwh_year,
        import_energy_links=["poc"],
    )
    assert status in ("ok", "optimal")
    imported = float(n.links_t.p0["poc"].sum())
    assert imported <= 900.0 + 1e-6
    assert imported == pytest.approx(900.0, abs=1e-6)  # it binds
    curt = _curtailed(sink).to_numpy()
    assert curt == pytest.approx(np.minimum(SHARE * (IT + COOL), COOL), abs=1e-6)


# ── the spec read and the no-op edges ────────────────────────────────────────


def test_project_terms_reads_the_saved_spec(monkeypatch, tmp_path):
    """Read as DC-06 reads it: none without a project, none for a project with no DC spec, the
    spec's terms for a DC project."""
    import json
    import types

    from services.adequacy import dc_backup

    monkeypatch.setattr(
        PyPSAService, "solve_target_context", classmethod(lambda cls: None)
    )
    assert C.project_terms() is None
    ctx = types.SimpleNamespace(storage_dir=None)
    monkeypatch.setattr(
        PyPSAService, "solve_target_context", classmethod(lambda cls: ctx)
    )
    assert C.project_terms() is None
    ctx.storage_dir = str(tmp_path)
    assert C.project_terms() is None  # no template metadata
    spec = _spec(curtailment_price_eur_per_mwh=7.0, curtail_max_duration_h=3.0)
    (tmp_path / dc_backup.META_FILE).write_text(
        json.dumps(
            {"id": "eh_datacenter", "dc_load": {"spec": spec.model_dump(mode="json")}}
        )
    )
    assert C.project_terms() == C.Terms(7.0, SHARE, 3.0)


def test_terms_of_refuses_a_bad_price_by_itself():
    import types

    for bad in (-1.0, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            C.terms_of(
                types.SimpleNamespace(
                    curtailment_price_eur_per_mwh=bad, curtailable_share=0.2
                )
            )


def test_a_cooling_load_of_zero_throughout_adds_nothing():
    n = _network()
    n.loads_t.p_set["cooling"] = 0.0
    undo: list = []
    C.apply(n, lambda _m: None, undo, {}, terms=_terms())
    assert undo == [] and C.GENERATOR not in n.generators.index


def test_an_uneven_tail_longer_than_d_is_bounded_too():
    w = pd.Series([1.0, 1, 1, 1, 3])
    assert C.runs(w, 2.0, w.index) == [(0, 2), (1, 3), (2, 4), (3, 4), (4, 4)]
    assert C.fits(w, 2.0, w.index)
    assert not C.fits(pd.Series([3.0, 1.0]), 2.0, pd.RangeIndex(2))
    assert C.fits(pd.Series([1.0, 3.0]), 2.0, pd.RangeIndex(2))


def test_the_terms_enter_the_fingerprint_only_when_on(tmp_path):
    """C15: off (no spec, no price, or a share of 0) adds nothing to the input fingerprint; on, a
    price or duration edit in the saved spec moves it."""
    import json
    import types

    from services.adequacy import dc_backup
    from services.workflow_tools import _network_input_fingerprint

    n = _network()

    def fp(spec):
        meta = tmp_path / dc_backup.META_FILE
        if spec is None:
            meta.unlink(missing_ok=True)
        else:
            meta.write_text(
                json.dumps(
                    {
                        "id": "eh_datacenter",
                        "dc_load": {"spec": spec.model_dump(mode="json")},
                    }
                )
            )
        return _network_input_fingerprint(
            types.SimpleNamespace(
                network=n,
                solver_state={"solver_config": SolverConfig()},
                user_ts={},
                storage_dir=str(tmp_path),
            )
        )

    base = fp(None)
    assert fp(_spec()) == base
    assert fp(_spec(curtailable_share=0.0, curtailment_price_eur_per_mwh=5.0)) == base
    on = fp(_spec(curtailment_price_eur_per_mwh=5.0))
    assert on != base
    assert fp(_spec(curtailment_price_eur_per_mwh=6.0)) != on
    assert (
        fp(_spec(curtailment_price_eur_per_mwh=5.0, curtail_max_duration_h=4.0)) != on
    )
