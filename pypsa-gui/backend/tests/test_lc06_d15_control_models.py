"""LC-06 S7, LC-D15: the island control models through the native tools.

The seam is the copilot's three island tools: ``campus_set_island_config``
(write, 422 with the refusal as detail), ``campus_island_config`` (read-back)
and ``campus_run_island_study``. The project is the I2c hand case of
``test_campus_island_service.py``: a 10 MW import, a cold 20 MW genset and an
extendable BESS at 100/MW. The seamless objective is the 400 of import
energy (10 MW × 10/MWh × 4 h) plus 100 × p_nom(BESS).

Refusals name the field and the unit's own model (GK A (4), S7 option A): the
model sits in the unit's ``where``, e.g. ``units.bess (control gfm_droop)``.

Wrong-base oracles, by hand from the seamless rows (``services/solver/island.py``):

* RoCoF row: 2M ≥ f0·ΔP/RoCoF_max with M = H_v·S_n and S_n = p_nom/pf_rated.
  H_v 5 s, pf 0.95, RoCoF_max 1 Hz/s, ΔP 10 MW, f0 50 Hz:
  p_nom = 50·10·0.95/(2·5) = 47.5 MW, cost 4750 (60 Hz: 57 MW, 5700).
  H read on P (S_n = p_nom) gives 50 MW, cost 5000 (60 Hz: 6000).
* QSS row: ΣK·Δf_qss ≥ ΔP with K = p_nom/(R·f0), R = droop_pct/100. The genset
  is isochronous there, so ΣK is the BESS alone. 4 %, Δf 0.5 Hz, f0 50 Hz:
  p_nom = 10·0.04·50/0.5 = 40 MW, cost 4000. Droop read as pu (R = 4) gives
  4000 MW, cost 400,000.

The row not under test is slack (RoCoF_max or Δf_qss 1e6 Hz). Headroom needs
10 MW, MVA 0 (the critical load is 0 MW, gfm_margin 0) and energy 10 MW·1 s, so neither binds below
the 40 MW the tighter rows ask for.

Tolerance: ``abs=1e-2``. The objectives are at most 6.4e3 (4.0e4 in the
gfl case) and HiGHS solves to ~1e-7 relative, so solver noise is below
~5e-3 even there. The nearest wrong-base value is 250 away (4750 vs 5000),
2.5e4 times the tolerance.
"""
from __future__ import annotations

import copy

import pytest
from fastapi import HTTPException

from db.models import User
from gridspine.schema.island import CONTROLS, PRESETS
from services import chat_tools, project_registry
from services.adequacy.island_config import validate
from services.campus_electrical_service import CAMPUS_FILE, SUBDIR
from tests.test_island_rows import _hub

P = "Island Hub"
TOL = 1e-2


def t(value, source="assumed"):
    return {"value": value, "source": source}


_SLACK = {"rocof_hz_per_s": t(1e6), "f_min_hz": t(47.5), "f_max_hz": t(51.5), "qss_band_hz": t(1e6)}

GENSET = {"kind": "genset", "governor": "droop", "droop_pct": t(4.0), "q_droop_pct": t(4.0),
          "H_s": t(1.5), "T_g_s": t(0.5), "ramp_pu_per_s": t(0.2), "start_s": t(10.0),
          "sync_s": t(5.0), "load_step_max_pct": t(50.0), "cos_phi_r": t(0.8),
          "xd_sat": t(2.0), "excitation": "avr_pmg", "neutral_earthing": "solid"}

#: One block per control model, with the fields that model needs. The
#: sources vary so the read-back checks each {value, source} pair.
MODELS = {
    "gfl_pq": {"control": "gfl_pq", "pf_rated": t(0.95, "datasheet")},
    "gfl_qu": {"control": "gfl_qu", "pf_rated": t(0.95),
               "qu_points": t([[0.9, 0.33], [0.97, 0.0], [1.03, 0.0], [1.1, -0.33]], "measured")},
    "gfl_pf": {"control": "gfl_pf", "pf_rated": t(0.95), "cos_phi": t(0.98, "datasheet")},
    "gfl_fw": {"control": "gfl_fw", "pf_rated": t(0.95), "fw_k_mw_per_hz": t(2.0),
               "fw_deadband_hz": t(0.2, "datasheet"), "fw_delay_s": t(0.5), "fw_ramp_mw_per_s": t(5.0),
               "fw_p_limit_mw": t(3.0, "measured")},
    "gfm_droop": {"control": "gfm_droop", "pf_rated": t(0.95), "droop_pct": t(4.0, "datasheet"),
                  "q_droop_pct": t(4.0), "tau_f_s": t(0.1), "i_max_pu": t(1.2), "k_gfm_min": t(1.0)},
    "gfm_vsm": {"control": "gfm_vsm", "pf_rated": t(0.95, "measured"), "droop_pct": t(4.0),
                "q_droop_pct": t(4.0), "H_v_s": t(5.0, "datasheet"), "i_max_pu": t(1.2),
                "k_gfm_min": t(1.0)},
}
assert set(MODELS) == set(CONTROLS)


def _config(bess, genset=None, *, rocof=1e6, qss=1e6, scenarios=("ups_bridged", "seamless")):
    limits = dict(_SLACK, rocof_hz_per_s=t(rocof), qss_band_hz=t(qss))
    return {"units": {"bess": {"kind": "bess", **bess}, "crit": {"kind": "load", "critical": True},
                      "g": genset if genset is not None else dict(GENSET)},
            "requirements": {"bridge_s": t(1.0), "sustained_h": t(1.0),
                             "rocof_window_ms": t(500.0), "gfm_margin": t(0.0),
                             "genset_redundancy_n": t(0), "pickup_blocks": t([1.0]),
                             "scenarios": list(scenarios),
                             "frequency_limits": {"transition": dict(limits),
                                                  "steady_island": dict(limits)}}}


@pytest.fixture
def hub(_auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        user = db.get(User, seeded_identity["user_id"])
        project = project_registry.create_root(db, user, P)
        folder = project_registry.ensure_project_dir(project)
        _hub(load=10.0, gensets=[("g", 20.0)]).export_to_netcdf(str(folder / "network.nc"))
        yield folder


def _at_60hz(folder):
    (folder / SUBDIR).mkdir(parents=True, exist_ok=True)
    (folder / SUBDIR / CAMPUS_FILE).write_text("campus:\n  f_hz: 60\n")


def _refusal(config):
    with pytest.raises(HTTPException) as err:
        chat_tools.campus_set_island_config(P, config)
    assert err.value.status_code == 422
    return str(err.value.detail)


ENERGY = 10.0 * 10.0 * 4                 # the import's energy cost in every variant here


def _bess_cost(out):
    """100 × p_nom(BESS) in the seamless variant: its objective less the energy."""
    return out["variants"]["seamless"]["objective"] - ENERGY


def _seamless(config):
    chat_tools.campus_set_island_config(P, config)
    return chat_tools.campus_run_island_study(P)


# ── refusals name the field and the unit's own model ────────────────────────


@pytest.mark.parametrize("control, field", [
    ("gfl_pq", "pf_rated"), ("gfl_qu", "qu_points"), ("gfl_fw", "fw_delay_s"),
    ("gfm_droop", "tau_f_s"), ("gfm_droop", "droop_pct"), ("gfm_vsm", "H_v_s"),
])
def test_a_missing_field_is_refused_naming_it_and_the_model(hub, control, field):
    bess = copy.deepcopy(MODELS[control])
    del bess[field]
    assert f"island_config.units.bess (control {control}): missing ['{field}']" in _refusal(_config(bess))


@pytest.mark.parametrize("control, foreign", [
    ("gfm_droop", "H_v_s"), ("gfm_vsm", "tau_f_s"), ("gfl_pq", "droop_pct"), ("gfl_fw", "qu_points"),
])
def test_a_field_of_another_model_is_refused_naming_it_and_the_units_own_model(hub, control, foreign):
    donor = next(m for m in MODELS.values() if foreign in m)
    bess = {**copy.deepcopy(MODELS[control]), foreign: copy.deepcopy(donor[foreign])}
    detail = _refusal(_config(bess))
    assert detail.startswith(f"island_config.units.bess (control {control}): unknown field(s) ['{foreign}']")


def test_gfl_pf_needs_one_power_factor_choice_and_says_so_with_its_model(hub):
    bess = {**MODELS["gfl_pf"], "cosphi_p_points": t([[0.0, 1.0], [1.0, 0.95]])}
    assert ("island_config.units.bess (control gfl_pf): gfl_pf needs exactly one of "
            "['cos_phi', 'cosphi_p_points']") in _refusal(_config(bess))


def test_a_value_out_of_range_names_the_model_and_the_field(hub):
    bess = {**MODELS["gfm_droop"], "k_gfm_min": t(1.5)}
    assert _refusal(_config(bess)).startswith(
        "island_config.units.bess (control gfm_droop).k_gfm_min (1.5) must not exceed i_max_pu (1.2)")


def test_a_missing_control_still_lists_every_allowed_control(hub):
    bess = {k: v for k, v in MODELS["gfm_droop"].items() if k != "control"}
    assert _refusal(_config(bess)) == (
        f"island_config.units.bess: missing 'control'; allowed {list(CONTROLS)}")


def test_an_unknown_control_is_refused_with_the_allowed_list(hub):
    bess = {**MODELS["gfm_droop"], "control": "gfm_vsg"}
    assert _refusal(_config(bess)) == (
        f"island_config.units.bess.control is 'gfm_vsg'; allowed {list(CONTROLS)}")


def test_a_droop_governor_without_droop_pct_names_the_governor(hub):
    genset = {k: v for k, v in GENSET.items() if k != "droop_pct"}
    assert _refusal(_config(MODELS["gfm_droop"], genset)) == (
        "island_config.units.g (governor droop): a droop governor needs droop_pct")


def test_a_field_of_a_converter_on_an_isochronous_genset_names_the_governor(hub):
    genset = {**GENSET, "governor": "isochronous", "tau_f_s": t(0.1)}
    assert _refusal(_config(MODELS["gfm_droop"], genset)).startswith(
        "island_config.units.g (governor isochronous): unknown field(s) ['tau_f_s']")


def test_an_unknown_governor_is_refused_with_the_allowed_list(hub):
    genset = {**GENSET, "governor": "pid"}
    assert _refusal(_config(MODELS["gfm_droop"], genset)) == (
        "island_config.units.g.governor is 'pid'; allowed ['droop', 'isochronous']")


# ── read-back is exact ───────────────────────────────────────────────────────


@pytest.mark.parametrize("control", CONTROLS)
@pytest.mark.parametrize("governor", ["droop", "isochronous"])
def test_each_model_and_governor_reads_back_exactly(hub, control, governor):
    genset = {**GENSET, "governor": governor}
    if governor == "isochronous":
        del genset["droop_pct"]
    config = _config(MODELS[control], genset)
    chat_tools.campus_set_island_config(P, copy.deepcopy(config))
    back = chat_tools.campus_island_config(P)
    assert back["config"] == config                      # every {value, source}, as written
    assert back["services"]["units"]["bess"]["control"] == control
    valid = validate(back["config"])
    assert valid["units"]["bess"]["control"] == control
    assert valid["units"]["g"]["governor"] == governor
    for f, entry in MODELS[control].items():
        if f != "control":
            assert valid["units"]["bess"]["values"][f] == entry["value"], f
            assert valid["units"]["bess"]["sources"][f] == entry["source"], f


def test_a_preset_reads_back_as_written_and_expands_to_its_tagged_fields(hub):
    """The unit carries only the preset name; the validated block holds the
    preset's fields, each ``assumed``, and the study runs on them: H_v 4 s
    gives p_nom = 50·10·0.95/(2·4) = 59.375 MW, cost 5937.5."""
    config = _config({"preset": "grid_forming_support"}, rocof=1.0)
    out = _seamless(copy.deepcopy(config))
    back = chat_tools.campus_island_config(P)
    assert back["config"] == config
    assert back["services"]["units"]["bess"] == {
        "kind": "bess", "control": "gfm_vsm", "preset": "grid_forming_support",
        "services": back["services"]["units"]["bess"]["services"], "not_credited": []}
    unit = validate(back["config"])["units"]["bess"]
    expected = {f: v for f, v in PRESETS["grid_forming_support"]["converter"].items() if f != "control"}
    assert unit["values"] == expected
    assert unit["sources"] == {f: "assumed" for f in expected}
    assert _bess_cost(out) == pytest.approx(5937.5, abs=TOL)


# ── wrong-base oracles ───────────────────────────────────────────────────────


@pytest.mark.parametrize("f0, right, wrong", [(50.0, 4750.0, 5000.0), (60.0, 5700.0, 6000.0)])
def test_vsm_inertia_is_on_the_apparent_power_base(hub, f0, right, wrong):
    """H_v on S_n = p_nom/pf_rated, not on p_nom; f0 from the campus file."""
    if f0 == 60.0:
        _at_60hz(hub)
    out = _seamless(_config(MODELS["gfm_vsm"], rocof=1.0))
    assert out["variants"]["seamless"]["facts"]["instant"]["f0_hz"] == f0
    assert out["variants"]["seamless"]["status"] == "ok"
    assert abs(right - wrong) > 1e4 * TOL
    assert _bess_cost(out) == pytest.approx(right, abs=TOL)


def test_gfm_droop_gain_reads_droop_in_percent(hub):
    """K = p_nom/(R·f0) with R = droop_pct/100; the isochronous genset adds no K."""
    genset = {k: v for k, v in GENSET.items() if k != "droop_pct"}
    genset["governor"] = "isochronous"
    out = _seamless(_config(MODELS["gfm_droop"], genset, qss=0.5))
    right, wrong = 4000.0, 400_000.0
    assert out["variants"]["seamless"]["status"] == "ok"
    assert abs(right - wrong) > 1e4 * TOL
    assert _bess_cost(out) == pytest.approx(right, abs=TOL)


def test_a_droop_genset_adds_its_gain_to_the_qss_row(hub):
    """With the 20 MW genset on 4 % droop, K_g = 20/(0.04·50) = 10 MW/Hz, so
    0.5·(p/2 + 10) ≥ 10 gives p = 20 MW, 2000: the governor changes the result."""
    out = _seamless(_config(MODELS["gfm_droop"], qss=0.5))
    assert _bess_cost(out) == pytest.approx(2000.0, abs=TOL)


@pytest.mark.parametrize("control", [c for c in CONTROLS if c.startswith("gfl_")])
def test_a_grid_following_bess_does_not_enter_the_gfm_rows(hub, control):
    """A gfl BESS forms nothing, so no import may step off at the instant:
    the genset (1000/MWh) carries the 10 MW for 4 h, 40,000, and only the
    headroom row binds (the gfm_droop baseline is the next test)."""
    out = _seamless(_config(MODELS[control]))
    seamless = out["variants"]["seamless"]
    assert seamless["status"] == "ok"
    assert seamless["objective"] == pytest.approx(10.0 * 1000.0 * 4, abs=TOL)
    assert [b["row"] for b in seamless["binding"]] == ["Island-headroom-up"]


def test_a_gfm_bess_on_the_same_project_carries_the_step_itself(hub):
    """The gfm_droop BESS with both frequency rows slack needs only the 10 MW
    of headroom: 1000 at 100/MW over the energy, not the genset's 40,000."""
    out = _seamless(_config(MODELS["gfm_droop"]))
    assert _bess_cost(out) == pytest.approx(1000.0, abs=TOL)
