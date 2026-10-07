"""The island data in a drafted campus, I1 (plan 2026-10-07-campus-island-operation).

``draft_campus(n, island=cfg)`` copies the hub sidecar's island data into the
campus file, so the hub and the campus never disagree:

* each unit named in the sidecar gets its block, under its campus id;
* a StorageUnit the sidecar calls a ``ups`` becomes a ``ups`` unit;
* names inside blocks and requirements (``it_load``,
  ``ride_through_storage``) are translated from PyPSA names to campus ids;
* a converter or genset the sidecar does not name gets a default block,
  every value tagged ``assumed``.

The oracle is the sidecar itself: what goes in must come out of
``build_campus`` under the right id, with the same values and tags.
"""
import copy

import pytest

from gridspine.ingest.campus import build_campus
from gridspine.producers.campus import DEFAULT_ISLAND, draft_campus
from gridspine.schema.contracts import ContractError
from gridspine.schema.island import validate_island_config
from tests.gridspine.test_campus_from_pypsa import hub
from tests.gridspine.test_island_schema import genset, gfm_bess, limits, t


def built_hub():
    n = hub()
    n.storage_units.loc["bess_new", "p_nom_opt"] = 20.0     # the expansion built a 20 MW battery
    return n


def sidecar():
    g = genset()
    g_dat = {**genset(), "xd_pp": t(0.12, "datasheet")}
    return {
        "units": {
            "bess_new": {"kind": "bess", **gfm_bess()},
            "genset_1": {"kind": "genset", **g_dat},
            "genset_2": {"kind": "genset", **g},
            "genset_3": {"kind": "genset", **g},
            "genset_4": {"kind": "genset", **g},
            "ups_battery": {"kind": "ups", "it_load": ["it_load"], "walk_in_s": t(15.0), "f_window_hz": t(2.0)},
            "it_load": {"kind": "load", "critical": True},
            "cooling": {"kind": "load", "critical": True, "f_trip_hz": t(47.0, "datasheet")},
        },
        "requirements": {
            "bridge_s": t(600.0), "sustained_h": t(24.0), "scenarios": ["ups_bridged", "seamless"],
            "pickup_blocks": t([15.0, 15.0, 15.0, 10.0]), "frequency_limits": limits(),
            "rocof_window_ms": t(500.0), "gfm_margin": t(0.1), "ride_through_storage": ["bess_new"],
        },
    }


def by_pypsa(units):
    return {u["pypsa_name"]: (uid, u) for uid, u in units.items()}


def drafted(cfg=None):
    return draft_campus(built_hub(), island=validate_island_config(cfg or sidecar()))


def test_without_a_sidecar_the_draft_has_no_island_data():
    c = draft_campus(built_hub()).spec["campus"]
    assert "island" not in c
    assert all("island" not in u for u in c["units"].values())


def test_named_units_get_their_blocks_under_their_campus_ids():
    c = drafted().spec["campus"]
    units = by_pypsa(c["units"])
    _, bess = units["bess_new"]
    assert bess["island"]["control"] == "gfm_droop"
    assert bess["island"]["droop_pct"] == t(4.0)
    _, cooling = units["cooling"]
    assert cooling["island"]["critical"] is True
    assert cooling["island"]["f_trip_hz"] == t(47.0, "datasheet")
    assert "island" not in units["offices"][1]           # not named, not critical: no block


def test_a_storage_unit_the_sidecar_calls_a_ups_becomes_a_ups():
    units = by_pypsa(drafted().spec["campus"]["units"])
    ups_id, ups = units["ups_battery"]
    it_id, _ = units["it_load"]
    assert ups["kind"] == "ups"
    assert set(ups) >= {"p_mw", "e_mwh", "s_mva"} and "k_sc" not in ups
    assert ups["island"]["it_load"] == [it_id]


def test_requirement_names_are_translated_to_campus_ids():
    c = drafted().spec["campus"]
    bess_id, _ = by_pypsa(c["units"])["bess_new"]
    assert c["island"]["ride_through_storage"] == [bess_id]
    assert c["island"]["sustained_h"] == t(24.0)


def test_a_sidecar_xd_pp_replaces_the_drafted_typical_value():
    units = by_pypsa(drafted().spec["campus"]["units"])
    assert units["genset_1"][1]["xd_pp"] == t(0.12, "datasheet")
    assert units["genset_2"][1]["xd_pp"]["source"] == "assumed"
    assert "xd_pp" not in units["genset_1"][1]["island"]


def test_unnamed_converters_and_gensets_get_default_blocks_tagged_assumed():
    units = by_pypsa(drafted().spec["campus"]["units"])
    pv = units["rooftop_pv"][1]["island"]
    assert pv["control"] == DEFAULT_ISLAND["pv"]["control"]
    new = units["genset_new"][1]["island"]
    assert new["governor"] == DEFAULT_ISLAND["genset"]["governor"]
    for block in (pv, new):
        tags = [v["source"] for v in block.values() if isinstance(v, dict)]
        assert tags and set(tags) == {"assumed"}


def test_the_drafted_campus_builds_with_its_island_data():
    camp = build_campus(drafted().spec)
    assert len(camp.island["units"]) == 10              # 8 named (one is the ups) + pv + genset_new
    assert camp.island["requirements"]["scenarios"] == ["ups_bridged", "seamless"]
    ups_id, _ = by_pypsa(drafted().spec["campus"]["units"])["ups_battery"]
    assert camp.units.at[ups_id, "kind"] == "ups"


def test_a_kind_the_project_contradicts_is_refused():
    s = sidecar()
    s["units"]["genset_2"] = {"kind": "bess", **gfm_bess()}
    with pytest.raises(ContractError, match="genset_2"):
        drafted(s)


def test_a_sidecar_name_the_project_does_not_have_is_refused():
    s = sidecar()
    s["units"]["genset_9"] = {"kind": "genset", **genset()}
    with pytest.raises(ContractError, match="genset_9"):
        drafted(s)


def test_a_named_unit_the_expansion_did_not_build_is_listed_and_dropped_from_names():
    n = hub()                                            # bess_new built at 0 MW
    d = draft_campus(n, island=validate_island_config(sidecar()))
    assert any("bess_new" in line and "island" in line for line in d.skipped)
    # every name it held is gone, so the key goes and the default (every bess) applies
    assert "ride_through_storage" not in d.spec["campus"]["island"]
    build_campus(d.spec)


def test_a_name_the_project_lacks_is_refused_in_names_too():
    s = sidecar()
    s["units"]["ups_battery"]["it_load"] = ["it_load", "nowhere"]
    with pytest.raises(ContractError, match="nowhere"):
        drafted(s)


def test_a_protected_load_the_sidecar_does_not_list_is_translated():
    s = sidecar()
    s["units"]["ups_battery"]["it_load"] = ["it_load", "offices"]
    units = by_pypsa(drafted(s).spec["campus"]["units"])
    assert units["ups_battery"][1]["island"]["it_load"] == [units["it_load"][0], units["offices"][0]]
