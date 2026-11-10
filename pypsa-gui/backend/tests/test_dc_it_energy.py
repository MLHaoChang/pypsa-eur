"""
DC business case 03: the IT energy series revenue bills (GK A, BC3-1..8).

Revenue reads the spec (IT MW × u(t) × w(t)), never the network's loads; the
unserved IT energy is the involuntary unserved facility energy ÷ PUE(T(t)).
"""
from __future__ import annotations

import ast
import pathlib
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

from models.flex_archetypes import AiWorkloadSpec, DataCentreLoadSpec, DcPhase
from services import dc_it_energy as E
from services import dc_load_profile as P

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

DC_IDS = ("eh_datacenter", "eh_datacenter_staged")
TZ = "Europe/Berlin"


def _spec(**kw) -> DataCentreLoadSpec:
    base = dict(phases=[DcPhase(cod=date(2028, 1, 1), it_mw=40.0, rack_kw=130.0)],
                utilisation="ai_training", pue_at_design=1.25,
                pue_temperature_slope_per_k=0.03, pue_design_temp_c=12.0,
                redundancy="N+1", critical_share=0.8, curtailable_share=0.1,
                ai_workload=AiWorkloadSpec())
    return DataCentreLoadSpec(**{**base, **kw})


def _week(start="2030-07-01"):
    return pd.date_range(start, periods=168, freq="h")


def _weights(snaps):
    """Non-unit, uneven weightings (BC3-2)."""
    return pd.Series(np.linspace(30.0, 70.0, len(snaps)), index=snaps)


# ── BC3-2: energy = Σ IT MW × u × w, non-unit weights, non-UTC clock ───────


def test_it_energy_is_it_mw_times_utilisation_times_the_weighting():
    spec, snaps = _spec(), _week()
    w = _weights(snaps)
    got = E.it_energy(spec, snaps, w, timezone=TZ)
    want = (40.0 * P.utilisation(spec, snaps, TZ) * w.to_numpy()).sum()
    assert got.periods.loc[2030, "it_mwh"] == pytest.approx(want, rel=1e-12)
    assert got.periods.loc[2030, "billable_it_mwh"] == pytest.approx(want, rel=1e-12)
    assert got.periods.loc[2030, "unserved_it_mwh"] == 0.0
    # The clock matters: the idle hours move with the timezone.
    utc = E.it_energy(spec, snaps, w, timezone="UTC").hourly["it_mwh"]
    assert not np.allclose(utc.to_numpy(), got.hourly["it_mwh"].to_numpy())


def test_facility_load_over_pue_is_the_it_series():
    spec, snaps = _spec(), _week()
    facility = P.hourly_profile(spec, snaps, 40.0, timezone=TZ)
    pue = P.pue(spec, P.ambient_c(spec, snaps, TZ))
    assert pue.max() > pue.min()                     # the slope binds somewhere
    got = E.it_energy(spec, snaps, 1.0, timezone=TZ).hourly["it_mwh"].to_numpy()
    assert np.allclose(facility / pue, got, rtol=1e-12, atol=0)


# ── BC3-3: the identity on the BUILT network ───────────────────────────────


@pytest.mark.parametrize("tid", DC_IDS)
def test_built_network_loads_over_pue_equal_the_it_series(tid):
    spec = T.dc_load_spec(tid, {})
    n = T.BUILDERS[tid](load_spec=spec)
    tz = T._dc_timezone(tid)
    p = n.loads_t.p_set
    steps = E._timesteps(n.snapshots)
    pue = P.pue(spec, P.ambient_c(spec, steps, tz))
    built_it = (p["it_load"] + p["cooling"]).to_numpy() / pue
    w = n.snapshot_weightings.generators
    got = E.it_energy(spec, n.snapshots, w, timezone=tz)
    assert np.allclose(built_it, got.hourly["it_mw"] * P.utilisation(spec, steps, tz),
                       rtol=1e-12, atol=0)
    by_period = pd.Series(built_it * w.to_numpy(), index=got.hourly["period"]).groupby(level=0).sum()
    assert np.allclose(by_period.to_numpy(), got.periods["it_mwh"].to_numpy(), rtol=1e-12)
    assert list(got.periods.index) == sorted(set(got.hourly["period"]))


def test_staged_contracted_kw_follows_each_period_phase():
    spec = T.dc_load_spec("eh_datacenter_staged", {})
    n = T.build_eh_datacenter_staged(load_spec=spec)
    got = E.it_energy(spec, n.snapshots, n.snapshot_weightings.generators, timezone="UTC")
    want = {y: it * 1000.0 for y, it, _pue in T.DC_PHASES}
    assert got.periods["contracted_it_kw"].to_dict() == pytest.approx(want)
    assert [ph["first_in_service"] for ph in got.phases] == [
        (y, T.SNAPSHOTS[0]) for y, _it, _pue in T.DC_PHASES]


# ── BC3-4: unserved per Q4 ─────────────────────────────────────────────────


def _solved_like(slack: dict[str, tuple[str, str, float]], tid="eh_datacenter"):
    """The template with slack rows dispatched at a fixed MW: {name: (bus,
    carrier, MW)}. Stands in for a solve's ``generators_t.p``."""
    spec = T.dc_load_spec(tid, {})
    n = T.BUILDERS[tid](load_spec=spec)
    for name, (bus, carrier, _mw) in slack.items():
        n.add("Generator", name, bus=bus, carrier=carrier, p_nom=100.0)
    p = pd.DataFrame(0.0, index=n.snapshots, columns=n.generators.index)
    for name, (_bus, _carrier, mw) in slack.items():
        p[name] = mw
    n.generators_t.p = p
    return spec, n


def _billable(spec, n, unserved=None):
    return E.it_energy(spec, n.snapshots, n.snapshot_weightings.generators, unserved,
                       timezone=T._dc_timezone("eh_datacenter"))


def test_it_bus_slack_lowers_billable_by_its_it_share_exactly():
    spec, n = _solved_like({"__voll_it_load": ("it_bus", "load_shedding", 2.0)})
    lost = E.unserved_facility_mw(n)
    assert np.allclose(lost.to_numpy(), 2.0)
    base, got = _billable(spec, n), _billable(spec, n, lost)
    steps = E._timesteps(n.snapshots)
    pue = P.pue(spec, P.ambient_c(spec, steps, None))
    want = (2.0 * n.snapshot_weightings.generators.to_numpy() / pue).sum()
    assert spec.pue_at_design > 1.0                         # ÷PUE is not a no-op here
    assert got.periods["unserved_it_mwh"].sum() == pytest.approx(want, rel=1e-12)
    assert (base.periods["billable_it_mwh"] - got.periods["billable_it_mwh"]).sum() == \
        pytest.approx(want, rel=1e-12)


def test_dc_mv_slack_counts_only_the_cooling_share_beside_the_offices():
    spec, n = _solved_like({"__voll_cooling": ("dc_mv", "load_shedding", 3.0)})
    loads = n.loads_t.p_set
    share = loads["cooling"] / (loads["cooling"] + loads["offices"])
    assert 0.0 < share.min() < 1.0                          # offices are there
    assert np.allclose(E.unserved_facility_mw(n).to_numpy(), (3.0 * share).to_numpy())


def test_curtailment_and_demand_response_lower_nothing():
    spec, n = _solved_like({
        "dc_curtailment": ("dc_mv", "dc_curtailment", 5.0),
        "__dsr_cooling": ("dc_mv", "demand_response", 1.0),
        "rooftop_extra": ("dc_mv", "solar", 4.0),
    })
    lost = E.unserved_facility_mw(n)
    assert (lost == 0.0).all()
    base, got = _billable(spec, n), _billable(spec, n, lost)
    pd.testing.assert_frame_equal(base.periods, got.periods)


def test_unserved_it_is_capped_at_the_billable_it():
    spec, n = _solved_like({"__voll_it_load": ("it_bus", "load_shedding", 1e6)})
    got = _billable(spec, n, E.unserved_facility_mw(n))
    assert (got.hourly["billable_it_mwh"] >= 0.0).all()
    assert got.periods["billable_it_mwh"].sum() == 0.0
    assert got.periods["unserved_it_mwh"].sum() == pytest.approx(got.periods["it_mwh"].sum())


def test_a_network_without_slack_has_no_unserved():
    _spec_, n = _solved_like({})
    assert (E.unserved_facility_mw(n) == 0.0).all()


# ── BC3-5: a quantity only, never priced, never in IC or adequacy facts ────


def test_unserved_is_a_quantity_never_a_price():
    spec, n = _solved_like({"__voll_it_load": ("it_bus", "load_shedding", 2.0)})
    got = _billable(spec, n, E.unserved_facility_mw(n))
    cols = [*got.periods.columns, *got.hourly.columns]
    assert not [c for c in cols if any(k in c.lower() for k in ("eur", "price", "cost", "voll"))]
    src = pathlib.Path(E.__file__).read_text()
    names = {node.id for node in ast.walk(ast.parse(src)) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(ast.parse(src)) if isinstance(node, ast.Attribute)}
    assert not {"marginal_cost", "voll", "cfg"} & names


def test_no_adequacy_or_solver_module_reads_the_it_energy():
    # The business-case engine (issue 04) reads it in services/finance and
    # carries its own no-price test; the adequacy facts and the solve never do.
    for pkg in ("services/adequacy", "services/solver"):
        for path in (BACKEND / pkg).rglob("*.py"):
            assert "dc_it_energy" not in path.read_text(), path


# ── BC3-6: workload shift moves no load ────────────────────────────────────


@pytest.mark.parametrize("tid", DC_IDS)
def test_workload_shift_window_changes_neither_it_energy_nor_the_built_loads(tid):
    plain = T.dc_load_spec(tid, {})
    shifted = T.dc_load_spec(tid, {"workload_shift_window_h": 6.0})
    a, b = T.BUILDERS[tid](load_spec=plain), T.BUILDERS[tid](load_spec=shifted)
    pd.testing.assert_frame_equal(a.loads_t.p_set, b.loads_t.p_set)
    w, tz = a.snapshot_weightings.generators, T._dc_timezone(tid)
    pd.testing.assert_frame_equal(E.it_energy(plain, a.snapshots, w, timezone=tz).periods,
                                  E.it_energy(shifted, b.snapshots, w, timezone=tz).periods)


# ── BC3-7: rack count, contracted kW, a mid-horizon COD ────────────────────


def test_rack_count_only_where_rack_kw_is_set():
    spec = _spec(phases=[DcPhase(cod=date(2030, 1, 1), it_mw=13.0, rack_kw=130.0),
                         DcPhase(cod=date(2030, 7, 4), it_mw=26.0)])
    got = E.it_energy(spec, _week(), 1.0)
    assert got.phases[0]["rack_count"] == pytest.approx(100.0)
    assert got.phases[1]["rack_count"] == E.NOT_SET
    assert [ph["it_kw"] for ph in got.phases] == [13000.0, 26000.0]


def test_a_mid_horizon_cod_bills_from_its_first_in_service_snapshot():
    spec = _spec(phases=[DcPhase(cod=date(2030, 1, 1), it_mw=10.0),
                         DcPhase(cod=date(2030, 7, 4), it_mw=30.0)])
    snaps = _week()
    w = _weights(snaps)
    got = E.it_energy(spec, snaps, w, timezone=TZ)
    cod = snaps.get_loc(pd.Timestamp("2030-07-04"))
    assert got.phases[1]["first_in_service"] == snaps[cod]
    assert got.phases[0]["first_in_service"] == snaps[0]
    it_mw = got.hourly["it_mw"].to_numpy()
    assert (it_mw[:cod] == 10.0).all() and (it_mw[cod:] == 30.0).all()
    kw = (10.0 * w.iloc[:cod].sum() + 30.0 * w.iloc[cod:].sum()) / w.sum() * 1000.0
    assert got.periods.loc[2030, "contracted_it_kw"] == pytest.approx(kw, rel=1e-12)


def test_a_plain_list_of_snapshots_reads_like_its_index():
    spec, snaps = _spec(), _week()
    a = E.it_energy(spec, list(snaps), 2.0, timezone=TZ)
    b = E.it_energy(spec, snaps, 2.0, timezone=TZ)
    pd.testing.assert_frame_equal(a.periods, b.periods)


def test_before_the_first_cod_nothing_is_in_service():
    spec = _spec(phases=[DcPhase(cod=date(2031, 1, 1), it_mw=10.0)])
    got = E.it_energy(spec, _week(), 1.0)
    assert got.periods.loc[2030].to_dict() == {
        "it_mwh": 0.0, "unserved_it_mwh": 0.0, "billable_it_mwh": 0.0, "contracted_it_kw": 0.0}
    assert got.phases[0]["first_in_service"] is None


# ── BC3-1/BC3-8: one in-service rule, the template's loads unchanged ───────


def test_the_template_and_it_energy_share_the_in_service_rule():
    spec = T.dc_load_spec("eh_datacenter", {})
    assert T._single_year_phase(spec) is E.phase_in_service(spec, T.SNAPSHOTS[0].date())
    staged = T.dc_load_spec("eh_datacenter_staged", {})
    for y, it_mw, _pue in T.DC_PHASES:
        assert E.phase_in_service(staged, date(y, 12, 31)).it_mw == it_mw
