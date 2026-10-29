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

import math
import time
import uuid

import pytest

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
        return _events(resp.json())
    return make


def _events(report):
    table = next(t for t in report["tables"] if t["id"] == "freq_events")
    cols = list(F.EVENT_COLUMNS)
    return [dict(zip(cols, row)) for row in table["rows"]]


def _first(events, event):
    return next(e for e in events if e["event"] == event and e["hour"] == 0)


# ── E1, E2 and E3 at 50 Hz ──────────────────────────────────────────────────


def test_e2_rocof_and_settled_frequency_are_the_hand_values(make_hub):
    e2 = _first(make_hub(_network(), _sidecar()), "E2")
    assert e2["dp_mw"] == pytest.approx(LOAD, abs=TOL)
    assert e2["rocof0_hz_per_s"] == pytest.approx(rocof0(LOAD), abs=TOL)       # −1.900
    assert e2["df_qss_hz"] == pytest.approx(settled(LOAD), abs=TOL)            # −0.800
    assert rocof0(LOAD) == pytest.approx(-1.9, abs=1e-12) and settled(LOAD) == pytest.approx(-0.8, abs=1e-12)


def test_e1_islanding_rocof_holds_on_the_rows_own_import(make_hub):
    e1 = _first(make_hub(_network(), _sidecar()), "E1")
    assert LOAD < e1["dp_mw"] < LOAD / 0.99                    # the import plus the line losses
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
    assert e2["df_qss_hz"] == pytest.approx(0.0, abs=1e-3)
    assert abs(settled(LOAD)) > 100 * 1e-3                       # the droop-only answer, −0.8, is far off


# ── the integrator: dt halving against the closed form ─────────────────────


def test_halving_dt_cuts_the_error_against_the_closed_form():
    """E2 on the BESS alone: M dΔf/dt = −ΔP − K Δf, so
    Δf(t) = −ΔP/K · (1 − e^(−K t / M)), M = 2 H_v S_n / f0. The engine is
    fixed-step RK4, so the error falls ~16× per halving; the condition
    asks at least ~1.8 (first order)."""
    f0, s_n = 50.0, BESS_MW / PF
    unit = F.Unit(name="bess", model="gfm_vsm", p0=0.0, p_n=BESS_MW, s_n=s_n, r=DROOP / 100.0, h=H_V,
                  i_max_mw=1.2 * s_n)
    k, m = BESS_MW / (DROOP / 100.0 * f0), 2.0 * H_V * s_n / f0
    t_at = 1.0

    def error(dt):
        tr = F.simulate([unit], F.step(LOAD), t_end=t_at, dt=dt, f0=f0)
        exact = -LOAD / k * (1.0 - math.exp(-k * t_at / m))
        return abs(tr.df[-1] - exact)

    coarse, fine = error(0.1), error(0.05)
    assert fine < coarse
    assert coarse / fine >= 1.8
