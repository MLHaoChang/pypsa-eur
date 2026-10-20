"""Control presets in the island schema (data-centre grid readiness, ticket 04).

A preset names a converter control and a UPS behaviour, so a project can say
"grid forming support" without typing a droop. The oracles:

* a sidecar without a preset validates exactly as before (old configs load
  unchanged);
* a preset fills the fields a unit does not give, tagged ``assumed``; a field
  the unit gives wins with its own source; a conflicting control is refused;
* presets respect unit kind: GFM and FFR go on a bess only, as the hub island
  LP credits them (gatekeeper, 2026-10-20);
* ``offered_services`` follows each unit's effective control and credits only
  what the island study credits;
* the campus draft carries the preset, and gives it to the converters the
  sidecar does not list.
"""
import copy

import pytest

from gridspine.ingest.campus import build_campus
from gridspine.producers.campus import draft_campus
from gridspine.schema.contracts import ContractError
from gridspine.schema.island import (
    CONVERTER_KINDS,
    FFR_SIZED,
    GFM_CONTROLS,
    PRESETS,
    SERVICES,
    ffr_sizing,
    has_grid_former,
    offered_services,
    select_preset,
    validate_island_config,
    validate_unit_island,
)
from tests.gridspine.test_campus_draft_island import built_hub, by_pypsa
from tests.gridspine.test_campus_draft_island import sidecar as hub_sidecar
from tests.gridspine.test_island_schema import sidecar, t

# ── the presets themselves ─────────────────────────────────────────────────

@pytest.mark.parametrize("name", list(PRESETS))
def test_each_preset_fills_a_valid_block_on_every_kind_it_applies_to(name):
    for kind in PRESETS[name]["kinds"]:
        extra = ffr_sizing(5.0) if PRESETS[name]["converter"]["control"] == "gfl_fw" else {}
        out = validate_unit_island("u", kind, {"preset": name, **extra})
        assert out["preset"] == name
        assert out["control"] == PRESETS[name]["converter"]["control"]
        assert set(out["sources"].values()) == {"assumed"}
    out = validate_unit_island("u", "ups", {"preset": name, "it_load": ["IT"]})
    assert out["values"]["walk_in_s"] == PRESETS[name]["ups"]["walk_in_s"]
    assert out["transfers_on_islanding"] is PRESETS[name]["ups"]["transfers_on_islanding"]


def test_only_grid_forming_support_makes_a_grid_former():
    """#332's n1_no_former counts GFM bess: one preset, and only one, is GFM."""
    gfm = [n for n, p in PRESETS.items() if p["converter"]["control"] in GFM_CONTROLS]
    assert gfm == ["grid_forming_support"]
    assert PRESETS["grid_forming_support"]["kinds"] == ("bess",)
    assert PRESETS["fast_frequency_response"]["kinds"] == ("bess",)
    assert set(PRESETS["grid_following"]["kinds"]) == set(CONVERTER_KINDS)


def test_a_field_the_unit_gives_wins_with_its_own_source():
    out = validate_unit_island("u", "bess", {"preset": "grid_forming_support", "H_v_s": t(6.0, "datasheet")})
    assert (out["values"]["H_v_s"], out["sources"]["H_v_s"]) == (6.0, "datasheet")
    assert out["sources"]["droop_pct"] == "assumed"


def test_a_control_other_than_the_presets_is_refused():
    with pytest.raises(ContractError, match="conflicts with preset 'grid_forming_support'"):
        validate_unit_island("u", "bess", {"preset": "grid_forming_support", "control": "gfm_droop"})


@pytest.mark.parametrize("kind", ["pv", "wind", "fuel_cell"])
@pytest.mark.parametrize("name", ["grid_forming_support", "fast_frequency_response"])
def test_a_gfm_or_ffr_preset_on_a_non_storage_converter_is_refused(kind, name):
    why = "grid former on a bess only" if name == "grid_forming_support" else "storage-backed"
    with pytest.raises(ContractError, match=why):
        validate_unit_island("u", kind, {"preset": name})


def test_a_preset_on_a_genset_or_load_is_refused():
    with pytest.raises(ContractError, match="converter and UPS"):
        validate_unit_island("u", "load", {"preset": "grid_following"})


def test_an_unknown_preset_is_refused_with_the_allowed_list():
    with pytest.raises(ContractError, match="grid_forming_support"):
        validate_unit_island("u", "bess", {"preset": "grid_magic"})


def test_ffr_without_its_rated_fields_is_refused_naming_ffr_sizing():
    with pytest.raises(ContractError, match="ffr_sizing"):
        validate_unit_island("u", "bess", {"preset": "fast_frequency_response"})


def test_ffr_sizing_scales_with_the_rating_and_refuses_an_unsized_unit():
    s = ffr_sizing(6.0)
    assert set(s) == set(FFR_SIZED)
    assert s["fw_p_limit_mw"]["value"] == 6.0
    assert s["fw_k_mw_per_hz"]["value"] == pytest.approx(20.0)
    for bad in (0.0, -1.0, float("nan")):
        with pytest.raises(ContractError, match="unsized"):
            ffr_sizing(bad)


# ── the sidecar: project preset, old files ─────────────────────────────────

def test_a_sidecar_without_a_preset_validates_exactly_as_before():
    """No ``preset`` key appears anywhere when none is set: old configs load
    unchanged, so every reader sees what it saw before."""
    out = validate_island_config(sidecar())
    assert "preset" not in out
    assert not any("preset" in u for u in out["units"].values())
    assert out["units"]["bess_new"]["control"] == "gfm_droop"


def test_the_project_preset_applies_by_kind_and_leaves_other_kinds_alone():
    raw, skipped = select_preset(sidecar(), "grid_forming_support")
    assert skipped == []
    out = validate_island_config(raw)
    assert out["preset"] == "grid_forming_support"
    assert out["units"]["bess_new"]["control"] == "gfm_vsm"
    assert out["units"]["bess_new"]["preset"] == "grid_forming_support"
    assert out["units"]["pv"]["control"] == "gfl_qu" and "preset" not in out["units"]["pv"]
    assert "preset" not in out["units"]["genset_1"]
    assert out["units"]["ups_battery"]["preset"] == "grid_forming_support"


def test_a_unit_preset_overrides_the_project_one_and_null_opts_out():
    raw, _ = select_preset(sidecar(), "voltage_support")
    raw["units"]["pv"]["preset"] = None
    raw["units"]["pv"].update(control="gfl_pq", pf_rated=t(0.95))
    out = validate_island_config(raw)
    assert out["units"]["pv"]["control"] == "gfl_pq" and "preset" not in out["units"]["pv"]
    assert out["units"]["bess_new"]["control"] == "gfl_qu"


def test_selecting_a_preset_keeps_what_it_does_not_govern_and_is_pure():
    raw = sidecar()
    raw["units"]["bess_new"]["neg_seq"] = t(0.1, "datasheet")
    before = copy.deepcopy(raw)
    out, _ = select_preset(raw, "grid_following")
    assert raw == before
    assert out["units"]["bess_new"] == {"kind": "bess", "neg_seq": t(0.1, "datasheet")}
    assert out["units"]["ups_battery"]["it_load"] == ["it_load"]
    cleared, _ = select_preset(out, None)
    assert "preset" not in cleared
    validate_island_config(cleared)


def _without_preset(cfg):
    units = {n: {k: v for k, v in u.items() if k != "preset"} for n, u in cfg["units"].items()}
    return {"units": units, "requirements": cfg["requirements"]}


@pytest.mark.parametrize("name", list(PRESETS))
def test_clearing_a_preset_keeps_the_island_as_it_was_as_a_custom_config(name):
    """Gatekeeper finding on #422: a clear must not leave units without the
    fields the preset gave them."""
    selected, _ = select_preset(sidecar(), name, {"bess_new": 8.0})
    cleared, _ = select_preset(selected, None)
    assert "preset" not in cleared
    assert validate_island_config(cleared) == _without_preset(validate_island_config(selected))


def test_switching_presets_then_clearing_keeps_every_unit_valid():
    """voltage_support sets the pv; grid_forming_support does not, so the pv
    keeps the Q(U) block it had."""
    a, _ = select_preset(hub_sidecar(), "voltage_support")
    a["units"]["rooftop_pv"] = {"kind": "pv"}                  # a unit only the preset fills
    va = validate_island_config(a)
    b, _ = select_preset(a, "grid_forming_support")
    vb = validate_island_config(b)
    assert vb["units"]["rooftop_pv"]["control"] == "gfl_qu" and "preset" not in vb["units"]["rooftop_pv"]
    assert vb["units"]["rooftop_pv"]["values"] == va["units"]["rooftop_pv"]["values"]
    assert vb["units"]["bess_new"]["control"] == "gfm_vsm"
    c, _ = select_preset(b, None)
    assert validate_island_config(c) == _without_preset(vb)


def test_clearing_drops_an_ffr_opt_out():
    selected, skipped = select_preset(sidecar(), "fast_frequency_response")
    assert skipped and selected["units"]["bess_new"]["preset"] is None
    cleared, _ = select_preset(selected, None)
    assert "preset" not in cleared["units"]["bess_new"]
    before = validate_island_config(sidecar())["units"]["bess_new"]
    assert validate_island_config(cleared)["units"]["bess_new"] == before    # its own block, as before


def test_ffr_without_a_rating_keeps_the_units_own_block_and_says_why():
    raw, skipped = select_preset(sidecar(), "fast_frequency_response")
    assert raw["units"]["bess_new"]["preset"] is None
    assert skipped and "bess_new" in skipped[0] and "rating" in skipped[0]
    out = validate_island_config(raw)
    assert out["units"]["bess_new"]["control"] == "gfm_droop"
    assert "fast_frequency_response" not in offered_services(out)["services"]
    sized, skipped = select_preset(sidecar(), "fast_frequency_response", {"bess_new": 8.0})
    assert skipped == []
    unit = validate_island_config(sized)["units"]["bess_new"]
    assert unit["control"] == "gfl_fw" and unit["values"]["fw_p_limit_mw"] == 8.0


# ── the grid services a project offers (ticket 02 reads these) ─────────────

def test_offered_services_follow_the_effective_control():
    plain = offered_services(validate_island_config(sidecar()))
    assert plain["preset"] is None
    assert plain["units"]["bess_new"]["services"] == ["grid_forming", "synthetic_inertia",
                                                      "frequency_containment", "voltage_control"]
    assert plain["units"]["pv"]["services"] == ["voltage_control"]
    assert "genset_1" not in plain["units"]
    gfl = offered_services(validate_island_config(select_preset(sidecar(), "grid_following")[0]))
    assert gfl["preset"] == "grid_following" and gfl["services"] == []
    assert set(gfl["services"]) <= set(SERVICES)


def test_a_legacy_gfm_on_pv_is_listed_as_not_credited():
    raw = sidecar()
    raw["units"]["pv"] = {"kind": "pv", "control": "gfm_droop", "pf_rated": t(0.95), "droop_pct": t(4.0),
                          "q_droop_pct": t(4.0), "tau_f_s": t(0.1), "i_max_pu": t(1.2), "k_gfm_min": t(1.0)}
    pv = offered_services(validate_island_config(raw))["units"]["pv"]
    assert pv["services"] == []
    assert {x["service"] for x in pv["not_credited"]} >= {"grid_forming", "synthetic_inertia"}
    assert all("bess only" in x["reason"] for x in pv["not_credited"])


def test_grid_following_on_a_bess_only_island_leaves_no_grid_former():
    raw = sidecar()
    raw["units"] = {k: v for k, v in raw["units"].items() if v["kind"] != "genset"}
    assert has_grid_former(validate_island_config(raw))
    assert not has_grid_former(validate_island_config(select_preset(raw, "grid_following")[0]))


# ── the campus draft carries the preset ────────────────────────────────────

def _draft(raw):
    d = draft_campus(built_hub(), island=validate_island_config(raw))
    return d, by_pypsa(d.spec["campus"]["units"])


def test_the_draft_carries_the_preset_into_the_campus_and_it_builds():
    raw, _ = select_preset(hub_sidecar(), "grid_forming_support")
    d, units = _draft(raw)
    bess = units["bess_new"][1]["island"]
    assert bess["preset"] == "grid_forming_support" and bess["control"] == "gfm_vsm"
    assert units["rooftop_pv"][1]["island"]["control"] == "gfl_qu"     # not a bess: the default stays
    built = build_campus(d.spec)
    assert built.island["units"][units["bess_new"][0]]["preset"] == "grid_forming_support"


def test_the_project_preset_reaches_converters_the_sidecar_does_not_list():
    raw, _ = select_preset(hub_sidecar(), "grid_following")
    d, units = _draft(raw)
    pv = units["rooftop_pv"][1]["island"]
    assert pv["control"] == "gfl_pq" and pv["preset"] == "grid_following"
    build_campus(d.spec)


def test_ffr_on_an_unlisted_bess_is_sized_from_its_drafted_rating():
    raw = hub_sidecar()
    del raw["units"]["bess_new"]
    del raw["requirements"]["ride_through_storage"]
    raw, _ = select_preset(raw, "fast_frequency_response")
    d, units = _draft(raw)
    bess = units["bess_new"][1]["island"]
    assert bess["control"] == "gfl_fw" and bess["fw_p_limit_mw"]["value"] == pytest.approx(20.0)
    build_campus(d.spec)
