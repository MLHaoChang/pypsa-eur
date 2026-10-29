"""LC-06 S9, LC-D17: recommend → apply → re-study through the seam.

The seam is the copilot's tools: ``campus_recommend_gfm_settings`` (a queued
job on the last campus run), ``campus_island_gfm_settings`` (its read-back),
``campus_gfm_settings_card`` (the edit card), ``campus_apply_gfm_settings``
(the write, with the approved card's digest bound as the turn loop binds it,
``edit_card.approved``), then ``campus_run_study`` and
``campus_run_island_study`` again; the frequency events are read from the
authenticated report route ``GET …/island/report`` (ticket 27: the chat tool
trims them away). The S8a hub (``test_lc06_d16_frequency_events``) with a
droop genset beside the BESS, so that E3 has a grid former left to hold the
island and a recommendation exists:

* BESS ``gfm_vsm``: 100 MW, pf 0.95, S_n = 105.263 MVA, droop 4 %, H_v 1 s.
* Genset, droop governor: 200 MW, cos φ 0.8, S = 250 MVA, droop 5 %, H_s 1.5 s.
* Limits: quasi-steady band 0.25 Hz, RoCoF 4 Hz/s over 20 ms. Before the
  apply E1, E2 (Δf −0.31 Hz) and E3 (the genset trips, BESS alone, Δf −0.49 Hz,
  RoCoF −5.9 Hz/s) fail, so the search moves both the droop and H_v.

A VSM's frequency state has M = 2·H_v·S_n/f0 and no filter constant
(``frequency._gfm_m``), so τ_f never enters and the card carries none. Hand
oracles for E2 (ΔP 40 MW), single-bus aggregate, D = 0:
* RoCoF at 0⁺ = −ΔP·f0/(2·(H_v·S_n + H_s·S_g)).
* Settled Δf = −ΔP/(K_b + K_g), K = P_n/(R·f0); with neither unit isochronous.
Tolerance ``abs=1e-6``, as in S8a: the first-step RoCoF is the balance
itself, and Δf_qss is read at t_end = 20 s, over 40 of the slowest time
constant (the governor's T_g 0.5 s) after the step. The apply moves them
by about 0.15 Hz/s and 0.1 Hz, far beyond it.

Stale: a network edit (the copilot's ``update_component`` on the genset,
then ``save_project``) between the card and the apply is refused as
``edit_preview_stale`` and writes nothing, through the chat tool and through
the HTTP apply with no digest. Known gaps (tickets 28, 29): the card's digest
does not hash the network, so the refusal comes from the recommendation's own
staleness (``campus_electrical_service._stale``); the HTTP apply skips the
digest check when none is sent.
"""
from __future__ import annotations

import json
import time
import uuid

import pytest
import yaml

from gridspine.static.gfm_tuning import GFM_BOUNDS
from harness import edit_card
from services import campus_electrical_service as ce
from services import campus_island_tuning as cit
from services import chat_tools, project_registry
from services import solve_queue as sq
from tests import test_lc06_d16_frequency_events as d16

user_and_db = d16.user_and_db
make_hub = d16.make_hub
P = d16.P
t = d16.t
TOL = 1e-6
LOAD = d16.LOAD
S_B = d16.BESS_MW / d16.PF
GEN_MW, GEN_COS, GEN_H, GEN_DROOP = 200.0, 0.8, 1.5, 5.0
H0, DROOP0 = 1.0, 4.0
GENSET = {**d16.GENSET, "governor": "droop", "droop_pct": t(GEN_DROOP)}
BOUNDS = {"droop_pct": GFM_BOUNDS.droop_pct, "H_v_s": GFM_BOUNDS.h_s}


def rocof0(h, f0=50.0):
    return -LOAD * f0 / (2.0 * (h * S_B + GEN_H * GEN_MW / GEN_COS))


def settled(droop, f0=50.0):
    return -LOAD / (d16.BESS_MW / (droop / 100.0 * f0) + GEN_MW / (GEN_DROOP / 100.0 * f0))


def _sidecar():
    side = d16._sidecar(h=H0, droop=DROOP0, genset=GENSET)
    for lim in side["requirements"]["frequency_limits"].values():
        lim["qss_band_hz"] = t(0.25)
        lim["rocof_hz_per_s"] = t(4.0)
    side["requirements"]["rocof_window_ms"] = t(20.0)
    return side


def _wait(job):
    deadline = time.time() + 300
    state = None
    while time.time() < deadline:
        state = sq.solve_queue.get_job(uuid.UUID(job["id"]))
        if state["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.2)
    assert state and state["status"] == "completed", state


def _e2(client):
    resp = client.get(f"/api/campus-electrical/{P}/island/report")
    assert resp.status_code == 200, resp.text
    return d16._first(d16._events(resp.json()), "E2")


class Hub:
    def __init__(self, project, client):
        self.project, self.client = project, client
        self.sidecar, self.study = ce._island_paths(project)
        self.campus = ce.campus_dir(project) / ce.CAMPUS_FILE
        self.applied = ce.campus_dir(project) / cit.APPLIED_FILE

    def files(self):
        return {p.name: p.read_bytes() if p.is_file() else None
                for p in (self.sidecar, self.campus, self.applied, self.study)}

    def bess(self):
        side = json.loads(self.sidecar.read_text())["units"]["bess"]
        block = yaml.safe_load(self.campus.read_text())["campus"]["units"]["BESS"]["island"]
        return side, block


@pytest.fixture
def hub(make_hub, user_and_db, client):
    """The hub run, its island study, and a ``ready`` recommendation."""
    db, user = user_and_db
    make_hub(d16._network(genset_mw=GEN_MW), _sidecar())
    chat_tools.campus_run_island_study(P)
    _wait(chat_tools.campus_recommend_gfm_settings(P))
    state = chat_tools.campus_island_gfm_settings(P)
    assert state["state"] == "ready", state["reason"]
    assert state["recommendation"]["status"] == "recommended"
    h = Hub(project_registry.find_project(db, user, P), client)
    assert h.study.is_file()
    return h


def _typed(card):
    return {r["field"]: r for r in card["rows"] if r["name"] == "BESS" and r["role"] == "typed"}


def _apply(digest):
    with edit_card.approved(digest):
        return chat_tools.campus_apply_gfm_settings(P)


def _restudy(client):
    _wait(chat_tools.campus_run_study(P, k=1, invest=False))
    chat_tools.campus_run_island_study(P)
    return _e2(client)


# ── the chain ───────────────────────────────────────────────────────────────


def test_the_applied_card_is_read_back_and_the_restudy_runs_on_it(hub):
    before = _e2(hub.client)
    assert before["rocof0_hz_per_s"] == pytest.approx(rocof0(H0), abs=TOL)
    assert before["df_qss_hz"] == pytest.approx(settled(DROOP0), abs=TOL)

    card = chat_tools.campus_gfm_settings_card(P)
    sets = _typed(card)
    assert set(sets) == {"droop_pct", "H_v_s"}                  # a VSM: no τ_f to set
    for field, row in sets.items():
        lo, hi = BOUNDS[field]
        assert lo <= row["new"] <= hi
        # 0.1 (% or s): ten times the card's display rounding (0.01), and the
        # oracles below then move by over 1e3 TOL.
        assert abs(row["new"] - row["old"]) > 0.1
    assert sets["droop_pct"]["old"] == DROOP0 and sets["H_v_s"]["old"] == H0

    out = _apply(card["digest"])
    assert {(c["field"], c["to"]) for c in out["changes"]} == {(f, r["new"]) for f, r in sets.items()}

    # Read-back: both files carry exactly the card's values, tagged assumed.
    side, block = hub.bess()
    for field, row in sets.items():
        assert side[field] == {"value": row["new"], "source": "assumed"}
        assert block[field] == {"value": row["new"], "source": "assumed"}
    assert not hub.study.is_file()                              # made with the old inputs
    applied = chat_tools.campus_island_gfm_settings(P)["applied"]
    assert applied["hub_resolve_needed"] is False               # island rows off: no hub re-solve
    assert {c["field"] for c in applied["changes"]} == set(sets)

    # The re-study runs on the applied values.
    h, droop = sets["H_v_s"]["new"], sets["droop_pct"]["new"]
    after = _restudy(hub.client)
    assert after["rocof0_hz_per_s"] == pytest.approx(rocof0(h), abs=TOL)
    assert after["df_qss_hz"] == pytest.approx(settled(droop), abs=TOL)
    # Mutations: the pre-apply settings miss both figures by far more than TOL.
    assert abs(rocof0(h) - rocof0(H0)) > 1e3 * TOL
    assert abs(settled(droop) - settled(DROOP0)) > 1e3 * TOL
    assert hub.study.is_file()


def test_without_the_restudy_the_old_figures_stand_and_no_study_is_made(hub):
    before = _e2(hub.client)
    card = chat_tools.campus_gfm_settings_card(P)
    _apply(card["digest"])
    assert not hub.study.is_file()
    now = _e2(hub.client)
    assert now["rocof0_hz_per_s"] == before["rocof0_hz_per_s"] == pytest.approx(rocof0(H0), abs=TOL)
    assert now["df_qss_hz"] == before["df_qss_hz"] == pytest.approx(settled(DROOP0), abs=TOL)
    assert chat_tools.campus_island_gfm_settings(P)["state"] != "ready"   # the campus file moved


# ── refusals: nothing written ───────────────────────────────────────────────


def _edit_network():
    chat_tools.load_project(P)
    chat_tools.update_component("Generator", "gen", {"marginal_cost": 400.0})
    chat_tools.save_project(P, force=True)


def _assert_stale(exc, reason):
    detail = exc.value.detail
    assert exc.value.status_code == 409
    assert isinstance(detail, dict) and detail.get("error_kind") == "edit_preview_stale", detail
    assert reason in detail["message"], detail                  # the reason the user sees


def test_a_network_edit_after_the_card_refuses_the_apply(hub):
    card = chat_tools.campus_gfm_settings_card(P)
    files = hub.files()
    _edit_network()
    assert hub.files() == files                                 # the edit touches none of them
    with pytest.raises(Exception) as exc:
        _apply(card["digest"])
    _assert_stale(exc, "recommend GFM settings again")          # the recommendation is stale (ticket 28)
    assert hub.files() == files


def test_a_digest_that_is_not_the_cards_refuses_the_apply(hub):
    card = chat_tools.campus_gfm_settings_card(P)
    files = hub.files()
    wrong = ("0" if card["digest"][0] != "0" else "1") + card["digest"][1:]
    with pytest.raises(Exception) as exc:
        _apply(wrong)
    _assert_stale(exc, "nothing was applied")
    assert hub.files() == files
    _apply(card["digest"])                                      # the card's own digest applies
    assert hub.files() != files


def test_the_http_apply_without_a_digest_still_refuses_a_stale_network(hub):
    """Pins today's guard while ticket 29 is open: with no digest the route
    checks no card, and the refusal is the recommendation's own staleness."""
    files = hub.files()
    _edit_network()
    resp = hub.client.post(f"/api/campus-electrical/{P}/island/gfm-settings/apply")
    assert resp.status_code == 409, resp.text
    assert hub.files() == files
