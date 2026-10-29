"""LC-06 S8a, LC-D16: the island frequency events through the campus Run.

The seam is the copilot's tools: ``campus_set_island_config`` (the sidecar),
``campus_draft_campus`` (the campus file from the hub), ``campus_run_study``
(the queued Run, which runs the I6 frequency check after ranking) and
the authenticated report route ``GET …/island/report`` (its ``freq_events``
table). The chat tool ``campus_island_report`` returns the same payload
trimmed under the result cap, which keeps only the first two of the 21
tables, so the events never reach the copilot there (ticket 27).

The hub: a 40 MW critical load fed by a 40 MW import, a 100 MW BESS on
``gfm_vsm`` (H_v 5 s, pf 0.95, droop 4 %), no PV, load damping D = 0 (the
engine's default). Hand oracles, single-bus aggregate:

* RoCoF at the first step: −ΔP·f0/(2·H_v·S_n), S_n = p_nom/pf_rated
  = 105.263 MVA. E2 (ΔP 40 MW): −40·50/(2·5·105.263) = −1.900 Hz/s.
* Settled Δf with no isochronous unit: −ΔP/(ΣK + D), K = p_nom/(R·f0)
  = 100/(0.04·50) = 50 MW/Hz: −0.800 Hz.
* At 60 Hz: −2.280 Hz/s and K = 41.667 MW/Hz, −0.960 Hz.
* E1 opens the PCC: ΔP is the row's own I4 AC import (40 MW plus the
  line losses, which S3 checks), and the same RoCoF formula holds on it.
* E3 trips the only grid former: nothing holds the voltage, a collapse.
* An isochronous genset beside the BESS returns Δf to 0.

Tolerance: the engine integrates with fixed-step RK4 at dt = 1 ms; the
first-step RoCoF is the balance itself (exact to rounding), and Δf_qss is
read at t_end = 20 s, 47 time constants (M/K = 0.42 s) after the step, so
both carry ``abs=1e-6``. The mutations move them by 1.9 Hz/s and 0.8 Hz.
"""
from __future__ import annotations

import json
import math
import time
import uuid

import numpy as np
import pytest
from scipy.linalg import expm

from gridspine.static import frequency as F
from services import chat_tools, project_registry
from services import solve_queue as sq
from tests import test_campus_electrical_service as _ces

user_and_db = _ces.user_and_db
P = "Freq Hub"
LOAD = 40.0
BESS_MW, PF, H_V, DROOP = 100.0, 0.95, 5.0, 4.0
TOL = 1e-6


def t(value, source="assumed"):
    return {"value": value, "source": source}


def rocof0(dp, h=H_V, f0=50.0):
    return -dp * f0 / (2.0 * h * BESS_MW / PF)


def settled(dp, droop=DROOP, f0=50.0):
    return -dp / (BESS_MW / (droop / 100.0 * f0))


def closed_form(dp, at_s, f0=50.0):
    """Δf(t) on the BESS alone: M dΔf/dt = −ΔP − K Δf, D = 0."""
    k, m = BESS_MW / (DROOP / 100.0 * f0), 2.0 * H_V * BESS_MW / PF / f0
    return -dp / k * (1.0 - math.exp(-k * at_s / m))


def _network(genset_mw=0.0):
    n = _ces.hub_network(priced=True)
    n.storage_units.loc["bess", "p_nom"] = BESS_MW
    n.generators.loc["pv", "p_nom"] = 0.0
    n.generators_t.p["pv"] = 0.0
    n.storage_units_t.p["bess"] = 0.0
    n.generators_t.p["grid_supply"] = LOAD
    n.links_t.p0["grid_import"] = LOAD / 0.99
    n.links_t.p1["grid_import"] = -LOAD
    if genset_mw:
        n.add("Generator", "gen", bus="mv", carrier="diesel", p_nom=genset_mw, marginal_cost=500.0)
        n.generators_t.p["gen"] = 0.0
    return n


def _sidecar(h=H_V, droop=DROOP, genset=None, f0=50.0):
    """Limits at ±10 % of f0 and 10 Hz/s, slack for every event here."""
    lim = {"rocof_hz_per_s": t(10.0), "f_min_hz": t(0.9 * f0), "f_max_hz": t(1.1 * f0), "qss_band_hz": t(2.0)}
    units = {"bess": {"kind": "bess", "control": "gfm_vsm", "pf_rated": t(PF), "droop_pct": t(droop),
                      "q_droop_pct": t(4.0), "H_v_s": t(h), "i_max_pu": t(1.2), "k_gfm_min": t(1.0)},
             "dc_load": {"kind": "load", "critical": True}}
    if genset is not None:
        units["gen"] = genset
    return {"units": units,
            "requirements": {"bridge_s": t(1.0), "sustained_h": t(1.0), "rocof_window_ms": t(500.0),
                             "gfm_margin": t(0.0), "genset_redundancy_n": t(0), "scenarios": ["seamless"],
                             "frequency_limits": {"transition": dict(lim), "steady_island": dict(lim)}}}


@pytest.fixture
def make_hub(user_and_db, client):
    db, user = user_and_db

    def make(network, sidecar, f_hz=None):
        row = project_registry.create_root(db, user, P)
        folder = project_registry.ensure_project_dir(row)
        network.export_to_netcdf(str(folder / "network.nc"))
        if f_hz is not None:
            (folder / "campus_electrical").mkdir(parents=True, exist_ok=True)
            (folder / "campus_electrical" / "campus_input.yaml").write_text(f"campus:\n  f_hz: {f_hz}\n")
        chat_tools.campus_set_island_config(P, sidecar)
        chat_tools.campus_draft_campus(P, overwrite=f_hz is not None)
        job = chat_tools.campus_run_study(P, k=1, invest=False)
        deadline = time.time() + 300
        while time.time() < deadline:
            state = sq.solve_queue.get_job(uuid.UUID(job["id"]))
            if state["status"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(0.2)
        assert state["status"] == "completed", state
        resp = client.get(f"/api/campus-electrical/{P}/island/report")
        assert resp.status_code == 200, resp.text
        make.report = resp.json()
        make.config = chat_tools.campus_island_config(P)["config"]
        return _events(make.report)
    return make


def _events(report):
    table = next(t for t in report["tables"] if t["id"] == "freq_events")
    cols = list(F.EVENT_COLUMNS)
    return [dict(zip(cols, row)) for row in table["rows"]]


def _first(events, event):
    return next(e for e in events if e["event"] == event and e["hour"] == 0)


# ── E1, E2 and E3 at 50 Hz ──────────────────────────────────────────────────


def test_e2_rocof_and_settled_frequency_are_the_hand_values(make_hub):
    """``rocof0_hz_per_s`` is dΔf/dt at 0⁺ (``frequency.Trace.rocof0``, the
    balance at the step), so its oracle is −ΔP·f0/(2·H·S_n); the 500 ms
    windowed RoCoF is (Δf(0.5) − 0)/0.5 on the closed form."""
    e2 = _first(make_hub(_network(), _sidecar()), "E2")
    # D = 0 and no isochronous unit, from the run, not assumed: the ledger
    # states the damping, and the read-back sidecar holds no genset.
    ledger = json.dumps(make_hub.report)
    assert "load damping D = 0" in ledger
    assert {u["kind"] for u in make_hub.config["units"].values()} == {"bess", "load"}
    assert e2["dp_mw"] == pytest.approx(LOAD, abs=TOL)
    assert e2["rocof_window_hz_per_s"] == pytest.approx(closed_form(LOAD, 0.5) / 0.5, abs=1e-5)
    assert e2["rocof0_hz_per_s"] == pytest.approx(rocof0(LOAD), abs=TOL)       # −1.900
    assert e2["df_qss_hz"] == pytest.approx(settled(LOAD), abs=TOL)            # −0.800
    assert rocof0(LOAD) == pytest.approx(-1.9, abs=1e-12) and settled(LOAD) == pytest.approx(-0.8, abs=1e-12)


def test_e1_islanding_rocof_holds_on_the_rows_own_import(make_hub):
    e1 = _first(make_hub(_network(), _sidecar()), "E1")
    # ΔP is the import plus the losses, bounded by the Link's 1 % (efficiency
    # 0.99): a ΔP drifting from the I4 flow fails here.
    assert LOAD < e1["dp_mw"] < LOAD / 0.99
    assert e1["rocof0_hz_per_s"] == pytest.approx(rocof0(e1["dp_mw"]), abs=TOL)
    assert e1["df_qss_hz"] == pytest.approx(settled(e1["dp_mw"]), abs=TOL)


def test_e3_tripping_the_only_grid_former_collapses_the_island(make_hub):
    e3 = _first(make_hub(_network(), _sidecar()), "E3")
    assert e3["unit"] == "BESS"
    assert e3["collapse_s"] == pytest.approx(0.0, abs=TOL)
    assert str(e3["ok"]).lower() == "false"
    assert "no grid-forming headroom" in e3["binding"]


# ── mutations: halved H_v doubles the RoCoF; doubled droop doubles Δf ────────


def test_halving_h_doubles_the_rocof_and_leaves_the_settled_frequency(make_hub):
    e2 = _first(make_hub(_network(), _sidecar(h=H_V / 2)), "E2")
    assert e2["rocof0_hz_per_s"] == pytest.approx(rocof0(LOAD, h=H_V / 2), abs=TOL)   # −3.800
    assert abs(rocof0(LOAD, h=H_V / 2) - rocof0(LOAD)) == pytest.approx(1.9, abs=1e-12)
    assert e2["df_qss_hz"] == pytest.approx(settled(LOAD), abs=TOL)


def test_doubling_the_droop_doubles_the_settled_frequency(make_hub):
    e2 = _first(make_hub(_network(), _sidecar(droop=2 * DROOP)), "E2")
    assert e2["df_qss_hz"] == pytest.approx(settled(LOAD, droop=2 * DROOP), abs=TOL)  # −1.600
    assert e2["rocof0_hz_per_s"] == pytest.approx(rocof0(LOAD), abs=TOL)


# ── f0 from the campus file ─────────────────────────────────────────────────


def test_at_60_hz_both_figures_scale_with_f0(make_hub):
    e2 = _first(make_hub(_network(), _sidecar(f0=60.0), f_hz=60), "E2")
    assert e2["rocof0_hz_per_s"] == pytest.approx(rocof0(LOAD, f0=60.0), abs=TOL)    # −2.280
    assert e2["df_qss_hz"] == pytest.approx(settled(LOAD, f0=60.0), abs=TOL)         # −0.960


# ── an isochronous genset returns Δf to zero ───────────────────────────────


GENSET = {"kind": "genset", "governor": "isochronous", "q_droop_pct": t(4.0), "H_s": t(1.5),
          "T_g_s": t(0.5), "ramp_pu_per_s": t(1.0), "start_s": t(10.0), "sync_s": t(5.0),
          "load_step_max_pct": t(100.0), "cos_phi_r": t(0.8), "xd_sat": t(2.0),
          "excitation": "avr_pmg", "neutral_earthing": "solid", "spinning": True, "p_min_pu": t(0.1)}


def test_an_isochronous_genset_returns_the_frequency_to_nominal(make_hub):
    """A spinning 200 MW genset (H_s 1.5 s, cos φ 0.8, S = 250 MVA) adds its
    inertia: RoCoF₀ = −40·50/(2·(5·105.263 + 1.5·250)) = −1.1095 Hz/s. Its
    PI integral (T_i 2 s) has 9 integral times by t_end to pull Δf back;
    1e-3 Hz is the residual allowed, against −0.8 for droop alone."""
    e2 = _first(make_hub(_network(genset_mw=200.0), _sidecar(genset=GENSET)), "E2")
    two_m = 2.0 * (H_V * BESS_MW / PF + 1.5 * 200.0 / 0.8)
    assert e2["rocof0_hz_per_s"] == pytest.approx(-LOAD * 50.0 / two_m, abs=TOL)
    assert e2["df_qss_hz"] == pytest.approx(0.0, abs=1e-3)     # t_end 20 s ≥ 5 T_i; band 0.1 Hz
    assert e2["nadir_hz"] < -0.1                               # not trivially flat (−0.345)
    assert abs(settled(LOAD)) > 100 * 1e-3                       # the droop-only answer, −0.8, is far off


# ── the integrator: dt halving against the closed form ─────────────────────


def test_halving_dt_cuts_the_error_against_the_closed_form():
    """E2 on the BESS alone against Δf(t) = −ΔP/K · (1 − e^(−K t / M)),
    M = 2 H_v S_n / f0, τ = M/K = 0.42 s. The error is the max over the
    trace up to 2 s (4.75 τ). The engine is fixed-step RK4, so halving dt
    cuts it ~2⁴; GK A (4) asks ≥ 0.75·2⁴ = 12, with the coarse error well
    above roundoff. Measured: ratio 16.8, coarse error 5.4e-7 Hz
    (dt 0.05 → 0.025)."""
    s_n = BESS_MW / PF
    unit = F.Unit(name="bess", model="gfm_vsm", p0=0.0, p_n=BESS_MW, s_n=s_n, r=DROOP / 100.0, h=H_V,
                  i_max_mw=1.2 * s_n)

    def error(dt):
        tr = F.simulate([unit], F.step(LOAD), t_end=2.0, dt=dt, f0=50.0)
        return max(abs(df - closed_form(LOAD, float(at))) for at, df in zip(tr.t, tr.df))

    coarse, fine = error(0.05), error(0.025)
    assert coarse > 1e-9
    assert coarse / fine >= 12.0


# ── S8b: E4, E5, E6 and the UPS walk-in ─────────────────────────────────────
#
# Each oracle is on the row's own ΔP. A droop genset: K = P_n/(R·f0), its
# inertia H·S with S = P_n/cos φ. Every ΔP here keeps Δf inside the 2 Hz
# UPS window and the ±5 Hz limits, so nothing trips.

PV = {"kind": "pv", "control": "gfl_pq", "pf_rated": t(0.95)}
DROOP_GEN = {**GENSET, "governor": "droop", "droop_pct": t(4.0), "spinning": False}
GEN_MW, GEN_H, GEN_COS = 60.0, 1.5, 0.8
UPS_MW, UPS_H = 40.0, 0.25                     # 10 MWh, protecting the 40 MW dc_load
WALK_IN = 10.0


def ups_spec(walk_in_s=WALK_IN, window_hz=2.0):
    return {"kind": "ups", "it_load": ["dc_load"], "walk_in_s": t(walk_in_s), "f_window_hz": t(window_hz),
            "transfers_on_islanding": True}


def _site(pv_mw=0.0, gen_mw=0.0, gen_p=0.0, bess_mw=BESS_MW, ups_mwh=None):
    """The S8a hub with PV at ``pv_mw``, a genset dispatched at ``gen_p``,
    the BESS at ``bess_mw`` and a UPS of ``ups_mwh``; the import is the rest."""
    n = _network(genset_mw=gen_mw)
    n.storage_units.loc["bess", "p_nom"] = bess_mw
    n.generators.loc["pv", "p_nom"] = pv_mw
    n.generators_t.p["pv"] = pv_mw
    if gen_mw:
        n.generators_t.p["gen"] = gen_p
    imp = LOAD - pv_mw - gen_p
    n.generators_t.p["grid_supply"] = imp
    n.links_t.p0["grid_import"] = imp / 0.99
    n.links_t.p1["grid_import"] = -imp
    if ups_mwh is not None:
        # full at hour 0: e(t−1) is the initial state of charge (non-cyclic)
        n.add("StorageUnit", "ups", bus="mv", carrier="battery", p_nom=UPS_MW, max_hours=ups_mwh / UPS_MW,
              state_of_charge_initial=ups_mwh)
        n.storage_units_t.p["ups"] = 0.0
        n.storage_units_t.state_of_charge["ups"] = ups_mwh
    return n


def _site_sidecar(scenarios=("seamless",), units=None, blocks=None, **kw):
    s = _sidecar(**kw)
    s["units"].update(units or {})
    s["requirements"]["scenarios"] = list(scenarios)
    if blocks is not None:
        s["requirements"]["pickup_blocks"] = t(list(blocks))
    return s


def _table(report, key):
    table = next(t for t in report["tables"] if t["id"] == f"freq_{key}")
    cols = {"ups": F.UPS_COLUMNS, "bridge": F.BRIDGE_COLUMNS, "planned": F.PLANNED_COLUMNS}[key]
    return [dict(zip(cols, row)) for row in table["rows"]]


def _gen_rocof0(dp, f0=50.0):
    return -dp * f0 / (2.0 * GEN_H * GEN_MW / GEN_COS)


def _gen_settled(dp, droop=4.0, f0=50.0):
    return -dp / (GEN_MW / (droop / 100.0 * f0))


def _gen_state(dp, at_s, droop=4.0, t_g=0.5, f0=50.0):
    """Δf(t) on the droop genset alone, by the matrix exponential: x = (Δf,
    P_m), M dΔf/dt = −ΔP + P_m, T_g dP_m/dt = −K Δf − P_m, from x = 0; the
    load-step room (100 % of P_n) never binds on a 20 MW block."""
    m, k = 2.0 * GEN_H * GEN_MW / GEN_COS / f0, GEN_MW / (droop / 100.0 * f0)
    a = np.array([[0.0, 1.0 / m], [-k / t_g, -1.0 / t_g]])
    b = np.array([-dp / m, 0.0])
    x = (expm(a * at_s) - np.eye(2)) @ np.linalg.solve(a, b)
    return float(x[0]), float((-dp + x[1]) / m)                   # Δf, dΔf/dt


def _gen_df(dp, at_s, droop=4.0):
    return _gen_state(dp, at_s, droop)[0]


def _gen_settling(dp, droop=4.0):
    """The engine's settling instant (``_settle_time``) on the closed form:
    the first t > 0 on the dt grid with Δf within 0.1 Hz of −ΔP/K and
    |RoCoF| ≤ 0.05 Hz/s (3.14 s at droop 4 %)."""
    k = 1
    while True:
        df, rocof = _gen_state(dp, k * DT_E4, droop)
        if abs(df - _gen_settled(dp, droop)) <= BAND and abs(rocof) <= SETTLE:
            return k * DT_E4
        k += 1


# ── E5: the PV lost in the island ───────────────────────────────────────────


@pytest.mark.parametrize("pv_mw", [20.0, 10.0])
def test_e5_losing_the_pv_is_a_step_of_its_output(make_hub, pv_mw):
    """A gfl_pq PV (no frequency-watt) at ``pv_mw``: ΔP is its I4 output. At
    20 MW: −20·50/(2·5·105.263) = −0.950 Hz/s and −20/50 = −0.400 Hz; at
    10 MW (the mutation; 40 MW would leave no import, which the campus
    refuses) both halve."""
    e5 = _first(make_hub(_site(pv_mw=pv_mw), _site_sidecar(units={"pv": PV})), "E5")
    assert "load damping D = 0" in json.dumps(make_hub.report)
    assert {u["kind"] for u in make_hub.config["units"].values()} == {"bess", "load", "pv"}
    assert e5["dp_mw"] == pytest.approx(pv_mw, abs=TOL)
    assert e5["rocof0_hz_per_s"] == pytest.approx(rocof0(pv_mw), abs=TOL)
    assert e5["df_qss_hz"] == pytest.approx(settled(pv_mw), abs=TOL)
    assert rocof0(20.0) == pytest.approx(-0.95, abs=1e-12) and settled(20.0) == pytest.approx(-0.4, abs=1e-12)


# ── the UPS walk-in on E1 ───────────────────────────────────────────────────


def _ups_e1(make_hub, **spec):
    make_hub(_site(ups_mwh=UPS_MW * UPS_H), _site_sidecar(units={"ups": ups_spec(**spec)}))
    return next(r for r in _table(make_hub.report, "ups") if r["event"] == "E1" and r["hour"] == 0)


@pytest.mark.parametrize("walk_in_s", [WALK_IN, WALK_IN / 2])
def test_the_ups_walk_in_energy_is_half_load_times_walk_in(make_hub, walk_in_s):
    """E1 opens the PCC with the UPS on its battery (it transfers); inside
    its 2 Hz window it walks L = 40 MW back in linearly over T, so its battery
    gives L·T/2: 40·10/2/3600 = 0.055556 MWh. The engine sums the walk-in by
    trapezoids, exact on a line. T halved (the mutation) halves it."""
    row = _ups_e1(make_hub, walk_in_s=walk_in_s)
    assert row["energy_mwh"] == pytest.approx(LOAD * walk_in_s / 2.0 / 3600.0, abs=1e-9)
    assert LOAD * WALK_IN / 2.0 / 3600.0 == pytest.approx(0.0555556, abs=1e-7)
    assert int(row["drops"]) == 0
    assert row["t_online_s"] == pytest.approx(walk_in_s, abs=2e-3)       # within dt
    assert str(row["autonomy_ok"]).lower() == "true"


def test_a_narrow_window_drops_the_ups_off_line(make_hub):
    """The mutation: a 0.1 Hz window, which the walk-in's own Δf leaves, so
    the UPS drops back to its battery and gives more than L·T/2."""
    row = _ups_e1(make_hub, window_hz=0.1)
    assert int(row["drops"]) >= 1
    assert 0.0 < row["first_drop_s"] < WALK_IN          # the walk-in's own Δf crosses 0.1 Hz
    assert row["energy_mwh"] > LOAD * WALK_IN / 2.0 / 3600.0 + 1e-6


# ── E4: the ups_bridged start ────────────────────────────────────────────────


# E4 runs the gensets alone, so the BESS here is gfl_pq: no grid former
# whose inertia would join the block's RoCoF (asserted from the read-back).
BESS_PQ = {"kind": "bess", "control": "gfl_pq", "pf_rated": t(PF)}
DT_E4, BAND, SETTLE = 5e-3, 0.1, 0.05             # e4_ups_bridged's dt, band_hz and settle_hz_per_s


def _e4(make_hub, ups_mwh=UPS_MW * UPS_H, droop=4.0, second=None):
    """``second``: a second, larger genset (``(p_nom, start_s)``), which
    N+1 drops as the largest."""
    gen = {**DROOP_GEN, "droop_pct": t(droop)}
    units = {"bess": BESS_PQ, "gen": gen, "ups": ups_spec()}
    n = _site(gen_mw=GEN_MW, ups_mwh=ups_mwh)
    sidecar = _site_sidecar(("ups_bridged",), units, blocks=(20.0, 20.0))
    if second is not None:
        n.add("Generator", "gen2", bus="mv", carrier="diesel", p_nom=second[0], marginal_cost=500.0)
        n.generators_t.p["gen2"] = 0.0
        sidecar["units"]["gen2"] = {**gen, "start_s": t(second[1])}
        sidecar["requirements"]["genset_redundancy_n"] = t(1)
    events = make_hub(n, sidecar)
    controls = {u.get("control") for u in make_hub.config["units"].values()}
    assert not controls & {"gfm_droop", "gfm_vsm"}
    blocks = sorted((e for e in events if e["event"] == "E4" and e["hour"] == 0), key=lambda e: e["block"])
    bridge = next(r for r in _table(make_hub.report, "bridge") if r["scenario"] == "ups_bridged" and r["hour"] == 0)
    return blocks, bridge


@pytest.mark.parametrize("droop", [4.0, 8.0])
def test_e4_each_block_steps_the_genset_alone_from_nominal(make_hub, droop):
    """The bus is dead; the cold 60 MW genset (H 1.5 s, cos φ 0.8, S 75 MVA)
    is ready at start + sync = 15 s and picks up 20 + 20 MW, each block from
    f0. E4's rows carry only the 500 ms windowed RoCoF (no 0⁺ column), so
    the oracle is Δf(0.5)/0.5 on the two-state closed form (``_gen_df``; its
    0⁺ value is −20·50/(2·1.5·75) = −4.444 Hz/s). Δf_qss = −20/K, K =
    60/(0.04·50) = 30 MW/Hz: −0.6667 Hz; droop 8 % (the mutation) doubles it.

    Block 2 starts from nominal, not from block 1's −0.6667 (``_e4_pickup``:
    secondary control restores f0 at block 1's settling instant, and the
    genset carries its share as P0), so its Δf_qss is −0.6667 again, not the
    cumulative −1.3333. It starts at ready + block 1's settling instant on
    the closed form, within one dt."""
    blocks, _ = _e4(make_hub, droop=droop)
    assert [int(b["block"]) for b in blocks] == [1, 2]
    assert blocks[0]["t_block_s"] == pytest.approx(15.0, abs=TOL)
    assert blocks[1]["t_block_s"] == pytest.approx(15.0 + _gen_settling(20.0, droop), abs=DT_E4 + 1e-9)
    for b in blocks:
        assert b["dp_mw"] == pytest.approx(20.0, abs=TOL)
        assert b["rocof_window_hz_per_s"] == pytest.approx(_gen_df(20.0, 0.5, droop) / 0.5, abs=1e-6)
        assert b["df_qss_hz"] == pytest.approx(_gen_settled(20.0, droop), abs=1e-4)
    assert _gen_rocof0(20.0) == pytest.approx(-4.4444444, abs=1e-6)
    assert (_gen_df(20.0, 1e-6) / 1e-6) == pytest.approx(_gen_rocof0(20.0), rel=1e-4)    # the form's own 0⁺


def test_e4_at_n_plus_1_drops_the_largest_genset_and_waits_for_the_rest(make_hub):
    """N+1 with a second, 80 MW genset starting at 20 s: it is the largest,
    so it fails to start; the 60 MW genset alone picks up the blocks with
    the same figures, ready at 15 s (not 25 s, the dropped unit's), and the
    bridge's needed time is counted on that."""
    blocks, bridge = _e4(make_hub, second=(80.0, 20.0))
    assert "gen2" in bridge["dropped"].lower()
    assert bridge["ready_s"] == pytest.approx(15.0, abs=TOL)
    assert blocks[0]["t_block_s"] == pytest.approx(15.0, abs=TOL)
    for b in blocks:
        assert b["df_qss_hz"] == pytest.approx(_gen_settled(20.0), abs=1e-4)
    assert bridge["needed_s"] == pytest.approx(max(1.0, blocks[-1]["t_block_s"]), abs=TOL)


@pytest.mark.parametrize("ups_mwh,ok", [(UPS_MW * UPS_H, True), (0.05, False)])
def test_e4_the_ups_bridges_the_start_only_with_the_energy(make_hub, ups_mwh, ok):
    """Autonomy = e·3600/L: 10 MWh at 40 MW lasts 900 s; it must last the
    ready time plus the last block's pickup. 0.05 MWh lasts 4.5 s < 15 s."""
    blocks, bridge = _e4(make_hub, ups_mwh=ups_mwh)
    assert bridge["it_mw"] == pytest.approx(LOAD, abs=TOL)
    assert bridge["autonomy_s"] == pytest.approx(bridge["e_mwh"] * 3600.0 / LOAD, rel=1e-9)
    assert bridge["e_mwh"] == pytest.approx(ups_mwh, rel=1e-6)
    assert bridge["needed_s"] == pytest.approx(max(1.0, blocks[-1]["t_block_s"]), abs=TOL)
    assert str(bridge["ok"]).lower() == str(ok).lower()


# ── E6: planned islanding ────────────────────────────────────────────────────

SPIN_GEN = {**DROOP_GEN, "spinning": True}


def _e6(make_hub, gen_mw=GEN_MW, bess_mw=30.0, ramp_pu=0.1):
    gen = {**SPIN_GEN, "ramp_pu_per_s": t(ramp_pu)}
    events = make_hub(_site(gen_mw=gen_mw, gen_p=0.1 * gen_mw, bess_mw=bess_mw),
                      _site_sidecar(("planned",), {"gen": gen}))
    row = next(r for r in _table(make_hub.report, "planned") if r["hour"] == 0)
    return row, [e for e in events if e["event"] == "E6" and e["hour"] == 0]


@pytest.mark.parametrize("ramp_pu", [0.1, 0.05])
def test_e6_the_ramp_time_is_the_genset_covering_what_the_bess_cannot(make_hub, ramp_pu):
    """The 30 MW BESS takes over at once (a converter); the genset, at p_min
    6 MW, ramps at ramp_pu·60 MW/s, so the import is covered at
    (import − 30)/(ramp_pu·60): (34.x − 30)/6 ≈ 0.7 s. Halving the ramp
    (the mutation) doubles it. The PCC then opens on ≈ 0 MW: no excursion."""
    row, e6 = _e6(make_hub, ramp_pu=ramp_pu)
    imp = row["import_mw"]
    assert LOAD - 6.0 < imp < (LOAD - 6.0) / 0.99
    assert str(row["feasible"]).lower() == "true" and row["shortfall_mw"] == pytest.approx(0.0, abs=TOL)
    assert row["ramp_s"] == pytest.approx((imp - 30.0) / (ramp_pu * GEN_MW), abs=TOL)
    assert len(e6) == 1 and abs(e6[0]["nadir_hz"]) < 1e-9


def test_e6_short_of_room_is_infeasible_with_the_shortfall(make_hub):
    """One change from the base: the genset is 5 MW, dispatched at its p_min
    0.5 MW (P0, from the hub's dispatch). Room: the BESS from P0 0 to its
    30 MW, plus the genset from 0.5 to 5 (4.5 MW, the ramp time is no limit
    at full), 34.5 MW, short of the 39.5 MW import plus losses."""
    gen_mw, p0, bess_room = 5.0, 0.5, 30.0
    row, e6 = _e6(make_hub, gen_mw=gen_mw)
    imp = row["import_mw"]
    assert LOAD - p0 < imp < (LOAD - p0) / 0.99
    assert str(row["feasible"]).lower() == "false"
    assert row["shortfall_mw"] == pytest.approx(imp - (bess_room + (gen_mw - p0)), abs=TOL)
    assert e6 == []
