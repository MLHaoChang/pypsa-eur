"""
TCU-05 (`.scratch/typed-cost-units/issues/05-...`): a cost priced through a scale input (a PV
Generator's `dc_ac_ratio`, a Link's output-bus efficiency) reads stale when that input changes.

One mechanism for both: pricing records the input it scaled by (`scaled_by` on the provenance
entry), and the provenance graph (`provenance_graph.money`, moved from `provenance.staleness`,
provenance-graph 02) recomputes the current input through pricing's own
`_scale_input` and compares column and value (math.isclose rel 1e-9). GK A conditions T5-1..T5-7.
"""

from __future__ import annotations

import copy

import pandas as pd
import pypsa
import pytest

from services import provenance_graph as G
from services.asset_schema import provenance as P
from tests.test_library_defaults_pack_typed_units import (
    TYPED,
    _copy_with,
    _net,
    _solar_rows_dc,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    """`_copy_with` edits a tmp pack that the loader caches: clear it so no other module reads it."""
    from services.library.defaults_pack import loader

    yield
    loader._cached.cache_clear()
    loader._cached_region.cache_clear()


def _pv(tmp_path, monkeypatch, ratio=1.2):
    from services.library.defaults_pack.pricing import price_from_pack

    _copy_with(tmp_path, monkeypatch, _solar_rows_dc)
    n = _net()
    n.generators["dc_ac_ratio"] = float("nan")
    n.generators.at["g", "dc_ac_ratio"] = ratio
    price_from_pack(n, "generators", "g", "solar-utility", version=TYPED)
    return n


def _fc():
    """A fuel cell: priced per MW of electric OUTPUT, so scaled by bus1's `efficiency` (0.5)."""
    from services.library.defaults_pack.pricing import price_from_pack

    n = _net()
    price_from_pack(n, "links", "fc", "fuel-cell", version=TYPED)
    return n


def _stale(n, cls, name):
    return {
        f: e.get("stale")
        for f, e in G.stale_entries(n, cls, name).items()
    }


# ── recording ───────────────────────────────────────────────────────────────


def test_a_dc_priced_cost_records_its_scale_input_on_both_money_fields(
    tmp_path, monkeypatch
):
    n = _pv(tmp_path, monkeypatch)
    prov = P.get(n, "Generator", "g")
    for field in ("overnight_cost", "fom_cost"):
        assert prov[field]["scaled_by"] == {
            "column": "dc_ac_ratio",
            "value": pytest.approx(1.2),
            "technology": "solar-utility",
            "capacity_basis": "output_electric_dc",
        }
    assert "scaled_by" not in prov["lifetime"]
    assert prov["overnight_cost"]["dc_ac_ratio"] == pytest.approx(1.2), (
        "the TC-02 key is still written"
    )


def test_a_link_records_the_efficiency_column_it_was_scaled_by():
    n = _fc()
    assert (
        P.get(n, "Link", "fc")["overnight_cost"]["scaled_by"]["column"] == "efficiency"
    )
    assert P.get(n, "Link", "fc")["overnight_cost"]["scaled_by"][
        "value"
    ] == pytest.approx(0.5)


def test_a_unit_rule_records_no_scale_input_and_reads_fresh():
    from services.library.defaults_pack.pricing import price_from_pack

    n = _net()
    price_from_pack(
        n, "links", "elz", "electrolysis", version=TYPED
    )  # input_electric: scale 1
    assert "scaled_by" not in P.get(n, "Link", "elz")["overnight_cost"]
    n.links.at["elz", "efficiency"] = 0.7
    assert _stale(n, "Link", "elz") == {f: None for f in P.get(n, "Link", "elz")}


# ── the comparison (T5-1) ───────────────────────────────────────────────────


def test_an_unchanged_ratio_reads_fresh(tmp_path, monkeypatch):
    n = _pv(tmp_path, monkeypatch)
    assert _stale(n, "Generator", "g")["overnight_cost"] is None
    assert _stale(n, "Generator", "g")["fom_cost"] is None


def test_a_changed_ratio_reads_stale_on_both_money_fields(tmp_path, monkeypatch):
    n = _pv(tmp_path, monkeypatch)
    n.generators.at["g", "dc_ac_ratio"] = 1.3
    s = _stale(n, "Generator", "g")
    for field in ("overnight_cost", "fom_cost"):
        assert s[field] == {
            "field": "dc_ac_ratio",
            "recorded": pytest.approx(1.2),
            "current_field": "dc_ac_ratio",
            "current": pytest.approx(1.3),
            "reason": "scale input changed",
        }
    assert s["lifetime"] is None


def test_a_ratio_within_the_tolerance_reads_fresh(tmp_path, monkeypatch):
    n = _pv(tmp_path, monkeypatch)
    n.generators.at["g", "dc_ac_ratio"] = 1.2 * (1 + 1e-12)
    assert _stale(n, "Generator", "g")["overnight_cost"] is None
    n.generators.at["g", "dc_ac_ratio"] = 1.2 * (1 + 1e-6)
    assert _stale(n, "Generator", "g")["overnight_cost"] is not None


def test_a_changed_link_efficiency_reads_stale():
    n = _fc()
    n.links.at["fc", "efficiency"] = 0.55
    s = _stale(n, "Link", "fc")["overnight_cost"]
    assert (s["field"], s["current_field"], s["current"]) == (
        "efficiency",
        "efficiency",
        pytest.approx(0.55),
    )


def test_a_link_whose_priced_output_moves_bus_reads_stale_with_the_same_efficiency():
    """T5-1: column and value. bus1 stops being electric, bus2 (efficiency2 = 0.5) is: same value."""
    n = _fc()
    n.add("Bus", "heat", carrier="heat")
    n.links.at["fc", "bus1"] = "heat"
    n.links.at["fc", "bus2"] = "ac"
    n.links.at["fc", "efficiency2"] = 0.5
    s = _stale(n, "Link", "fc")["overnight_cost"]
    assert s["field"] == "efficiency" and s["current_field"] == "efficiency2"
    assert s["recorded"] == pytest.approx(s["current"]), (
        "the value alone would read fresh"
    )


def test_a_cleared_ratio_reads_stale_with_no_current_and_reaches_the_route_without_error(
    tmp_path, monkeypatch, client, install_network
):
    n = _pv(tmp_path, monkeypatch)
    n.generators.at["g", "dc_ac_ratio"] = float("nan")
    s = _stale(n, "Generator", "g")["overnight_cost"]
    assert (
        s["field"] == "dc_ac_ratio"
        and s["current_field"] is None
        and s["current"] is None
    )
    assert s["reason"].startswith("cannot be priced now")
    install_network(n)
    r = client.get("/api/network/asset_schema/Generator/provenance/g")
    assert r.status_code == 200
    assert r.json()["fields"]["overnight_cost"]["stale"]["current"] is None


def test_a_link_with_no_electric_output_any_more_reads_stale_not_an_error():
    n = _fc()
    n.links.at["fc", "efficiency"] = -0.1  # bus1 is now drawn from: no electric output
    s = _stale(n, "Link", "fc")["overnight_cost"]
    assert s["current"] is None and s["current_field"] is None


# ── T5-2: links scale by the static efficiency only ─────────────────────────


def test_a_link_efficiency_series_is_ignored_by_pricing_and_by_the_staleness_check():
    """Pinned: pricing reads the static `efficiency` column, never `links_t.efficiency`, and so does
    the comparison. A series that differs from the static value neither prices nor flags."""
    from services.library.defaults_pack.pricing import price_from_pack

    n = _net()
    n.set_snapshots(pd.RangeIndex(3))
    n.links_t.efficiency["fc"] = pd.Series([0.4, 0.45, 0.42], index=n.snapshots)
    out = price_from_pack(n, "links", "fc", "fuel-cell", version=TYPED)
    assert out["scale"] == 0.5
    n.links_t.efficiency["fc"] = pd.Series([0.3, 0.3, 0.3], index=n.snapshots)
    assert _stale(n, "Link", "fc")["overnight_cost"] is None


# ── T5-3: no recorded input is never fresh ──────────────────────────────────


def _strip_scaled_by(n, cls, name):
    for e in P._root(n)[cls][name].values():
        e.pop("scaled_by", None)
        e.pop("dc_ac_ratio", None)


def test_a_link_priced_before_this_reads_scale_input_not_recorded():
    n = _fc()
    _strip_scaled_by(n, "Link", "fc")
    s = _stale(n, "Link", "fc")
    want = {
        "field": None,
        "recorded": None,
        "current_field": None,
        "current": None,
        "reason": "scale input not recorded",
    }
    assert s["overnight_cost"] == want
    assert s["fom_cost"] == want, "fom_cost was priced with the same scale"
    assert s["lifetime"] is None


def test_a_legacy_dc_ac_ratio_key_is_read_as_the_recorded_input(tmp_path, monkeypatch):
    n = _pv(tmp_path, monkeypatch)
    for e in P._root(n)["Generator"]["g"].values():
        e.pop("scaled_by", None)  # TC-02 wrote `dc_ac_ratio` only, on overnight_cost
    assert _stale(n, "Generator", "g")["overnight_cost"] is None
    n.generators.at["g", "dc_ac_ratio"] = 1.3
    s = _stale(n, "Generator", "g")
    assert s["overnight_cost"]["field"] == "dc_ac_ratio"
    assert s["overnight_cost"]["recorded"] == pytest.approx(1.2)
    assert s["fom_cost"] == s["overnight_cost"]


def test_a_customised_cost_with_a_capacity_basis_left_reads_no_stale():
    n = _fc()
    P.mark_customised(n, "Link", "fc", ["overnight_cost", "fom_cost"])
    assert "capacity_basis" in P.get(n, "Link", "fc")["overnight_cost"], (
        "annotation kept"
    )
    n.links.at["fc", "efficiency"] = 0.6
    assert _stale(n, "Link", "fc")["overnight_cost"] is None
    assert _stale(n, "Link", "fc")["fom_cost"] is None


# ── T5-4: writers ──────────────────────────────────────────────────────────


def test_mark_customised_drops_the_scale_input(tmp_path, monkeypatch):
    n = _pv(tmp_path, monkeypatch)
    P.mark_customised(n, "Generator", "g", ["overnight_cost"])
    prov = P.get(n, "Generator", "g")
    assert (
        "scaled_by" not in prov["overnight_cost"]
        and "dc_ac_ratio" not in prov["overnight_cost"]
    )
    assert "scaled_by" in prov["fom_cost"], "only the fields it marks"
    n.generators.at["g", "dc_ac_ratio"] = 1.3
    assert _stale(n, "Generator", "g")["overnight_cost"] is None


def test_re_pricing_clears_stale(tmp_path, monkeypatch):
    from services.library.defaults_pack.pricing import price_from_pack

    n = _pv(tmp_path, monkeypatch)
    n.generators.at["g", "dc_ac_ratio"] = 1.3
    price_from_pack(n, "generators", "g", "solar-utility", version=TYPED)
    assert _stale(n, "Generator", "g")["overnight_cost"] is None


def test_an_edit_through_update_component_leaves_no_scale_input(
    tmp_path, monkeypatch, client, install_network
):
    n = install_network(_pv(tmp_path, monkeypatch))
    row = n.generators.loc["g"]
    body = {
        "name": "g",
        "bus": row.bus,
        "carrier": row.carrier,
        "p_nom": float(row.p_nom),
        "overnight_cost": float(row.overnight_cost) + 1.0,
    }
    assert client.put("/api/network/generators/g", json=body).status_code == 200
    prov = P.get(n, "Generator", "g")["overnight_cost"]
    assert prov["status"] == "customised" and "scaled_by" not in prov
    n.generators.at["g", "dc_ac_ratio"] = 1.3
    assert _stale(n, "Generator", "g")["overnight_cost"] is None


def test_a_bulk_equipment_table_write_leaves_no_scale_input(tmp_path, monkeypatch):
    from services import equipment_table

    n = _pv(tmp_path, monkeypatch)
    cell = {
        "name": "g",
        "field": "overnight_cost",
        "currency": "EUR",
        "money_year": 2024,
        "source": {"upload_id": "u1", "sha256": "0" * 64, "sheet": "S", "cell": "B2"},
    }
    equipment_table.stamp(
        n,
        {"component_class": "Generator", "cells": [cell]},
        {"g": {"overnight_cost": 1.0}},
        approved_by="a@b",
    )
    assert "scaled_by" not in P.get(n, "Generator", "g")["overnight_cost"]
    n.generators.at["g", "dc_ac_ratio"] = 1.3
    assert _stale(n, "Generator", "g")["overnight_cost"] is None


# ── T5-6: stored format ─────────────────────────────────────────────────────


def test_staleness_never_changes_what_get_reads(tmp_path, monkeypatch):
    n = _pv(tmp_path, monkeypatch)
    n.generators.at["g", "dc_ac_ratio"] = 1.3
    before = copy.deepcopy(P._root(n))
    G.stale_entries(n, "Generator", "g")
    assert P._root(n) == before


def test_an_asset_with_no_scale_records_reads_through_staleness_unchanged():
    n = pypsa.Network()
    n.add("Bus", "b")
    n.add("Generator", "gas", bus="b")
    P.record(n, "Generator", "gas", "marginal_cost", status="customised")
    got = P.get(n, "Generator", "gas")
    assert G.stale_entries(n, "Generator", "gas") == got


# ── routes and reference apply ──────────────────────────────────────────────


def test_both_routes_return_stale(tmp_path, monkeypatch, client, install_network):
    n = install_network(_pv(tmp_path, monkeypatch))
    n.generators.at["g", "dc_ac_ratio"] = 1.3
    one = client.get("/api/network/asset_schema/Generator/provenance/g").json()
    assert one["fields"]["overnight_cost"]["stale"]["recorded"] == pytest.approx(1.2)
    rows = client.get("/api/network/asset_costs").json()["assets"]
    g = next(r for r in rows if r["component"] == "Generator" and r["name"] == "g")
    assert g["provenance"]["overnight_cost"]["stale"]["current"] == pytest.approx(1.3)


def test_a_dc_reference_apply_records_the_ratio_and_reads_stale_after_a_change():
    from services.library import reference_apply as RA

    n = pypsa.Network()
    n.add("Bus", "b", carrier="AC")
    n.add("Generator", "pv", bus="b", carrier="solar", p_nom=10.0)
    n.generators["dc_ac_ratio"] = float("nan")
    n.generators.at["pv", "dc_ac_ratio"] = 1.3
    p = {
        "component_class": "Generator",
        "name": "pv",
        "technology": "solar-utility",
        "attrs": {"overnight_cost": 1.3e5, "lifetime": 30.0, "fom_cost": 2e3},
        "source": "ref#0/solar-utility@2030",
        "source_year": 2024,
        "replaces": None,
        "currency": "EUR",
        "currency_year": 2024,
        "scaled_by": {
            "column": "dc_ac_ratio",
            "value": 1.3,
            "technology": "solar-utility",
            "capacity_basis": "output_electric_dc",
        },
    }
    RA.stamp(n, p, approved_by="a@b")
    prov = P.get(n, "Generator", "pv")
    assert (
        prov["overnight_cost"]["scaled_by"]["value"] == 1.3
        and "scaled_by" not in prov["lifetime"]
    )
    assert _stale(n, "Generator", "pv")["overnight_cost"] is None
    n.generators.at["pv", "dc_ac_ratio"] = 1.1
    assert _stale(n, "Generator", "pv")["fom_cost"]["current"] == pytest.approx(1.1)


# ── fom_cost shares the overnight cost's scale only when it is the same pack price ──


def _legacy_pv(tmp_path, monkeypatch):
    n = _pv(tmp_path, monkeypatch)
    for e in P._root(n)["Generator"]["g"].values():
        e.pop("scaled_by", None)
    n.generators.at["g", "dc_ac_ratio"] = 1.3
    return n


def test_a_customised_fom_does_not_share_a_legacy_overnight_scale(
    tmp_path, monkeypatch
):
    n = _legacy_pv(tmp_path, monkeypatch)
    P.mark_customised(n, "Generator", "g", ["fom_cost"])  # status differs, source kept
    s = _stale(n, "Generator", "g")
    assert s["overnight_cost"] is not None and s["fom_cost"] is None


def test_a_fom_from_another_source_does_not_share_a_legacy_overnight_scale(
    tmp_path, monkeypatch
):
    n = _legacy_pv(tmp_path, monkeypatch)
    P.record(
        n, "Generator", "g", "fom_cost", status="default", source="ledger@1"
    )  # same status
    s = _stale(n, "Generator", "g")
    assert s["overnight_cost"] is not None and s["fom_cost"] is None


def test_a_fom_with_its_own_scale_input_is_judged_by_it(tmp_path, monkeypatch):
    n = _pv(tmp_path, monkeypatch)
    P._root(n)["Generator"]["g"]["fom_cost"]["scaled_by"]["value"] = (
        1.1  # overnight's stays 1.2
    )
    s = _stale(n, "Generator", "g")
    assert s["overnight_cost"] is None and s["fom_cost"]["recorded"] == pytest.approx(
        1.1
    )


def test_a_default_fom_alone_or_beside_a_customised_overnight_reads_no_stale():
    n = _fc()
    P.mark_customised(n, "Link", "fc", ["overnight_cost"])
    for e in P._root(n)["Link"]["fc"].values():
        e.pop("scaled_by", None)  # fom_cost: default, no annotation of its own
    assert _stale(n, "Link", "fc")["fom_cost"] is None
    del P._root(n)["Link"]["fc"]["overnight_cost"]
    assert _stale(n, "Link", "fc")["fom_cost"] is None


def test_a_product_priced_link_is_rechecked_with_its_technology():
    """haber-bosch prices per MW of NH3: the recorded technology names the product bus."""
    from services.library.defaults_pack.pricing import price_from_pack

    n = _net()
    price_from_pack(n, "links", "hb", "haber-bosch", version=TYPED)
    assert (
        P.get(n, "Link", "hb")["overnight_cost"]["scaled_by"]["technology"]
        == "haber-bosch"
    )
    assert _stale(n, "Link", "hb")["overnight_cost"] is None
    n.links.at["hb", "efficiency"] = 0.75
    assert _stale(n, "Link", "hb")["overnight_cost"]["current"] == pytest.approx(0.75)


@pytest.mark.parametrize(
    "bad",
    [
        "1.2",
        {"column": "dc_ac_ratio"},
        {"column": "dc_ac_ratio", "value": "x", "capacity_basis": "output_electric_dc"},
        {
            "column": "dc_ac_ratio",
            "value": float("nan"),
            "capacity_basis": "output_electric_dc",
        },
        {
            "column": "dc_ac_ratio",
            "value": True,
            "capacity_basis": "output_electric_dc",
        },
        {"column": 3, "value": 1.2, "capacity_basis": "output_electric_dc"},
        {"column": "dc_ac_ratio", "value": 1.2, "capacity_basis": None},
    ],
)
def test_a_malformed_scale_record_reads_stale_not_a_500(
    tmp_path, monkeypatch, client, install_network, bad
):
    """review-gk #1255: a record in n.meta of the wrong shape reads stale, never a KeyError."""
    n = _pv(tmp_path, monkeypatch)
    P._root(n)["Generator"]["g"]["overnight_cost"]["scaled_by"] = bad
    assert (
        _stale(n, "Generator", "g")["overnight_cost"]["reason"]
        == "scale input record malformed"
    )
    install_network(n)
    r = client.get("/api/network/asset_schema/Generator/provenance/g")
    assert r.status_code == 200
    assert (
        r.json()["fields"]["overnight_cost"]["stale"]["reason"]
        == "scale input record malformed"
    )


# ── T5-3: a Library reference applied before TCU-05 recorded no scale input ──


def _applied_before_this(monkeypatch, *, dc: bool, source=None):
    from services.library import europe_reference as R
    from services.library import reference_apply as RA

    real = R.load_europe_technology_reference
    if dc:
        monkeypatch.setattr(
            R,
            "load_europe_technology_reference",
            lambda t, y: real(t, y).model_copy(
                update={"capacity_basis_code": "output_electric_dc"}
            ),
        )
    n = pypsa.Network()
    n.add("Bus", "b", carrier="AC")
    n.add("Generator", "pv", bus="b", carrier="solar", p_nom=10.0)
    n.generators["dc_ac_ratio"] = float("nan")
    n.generators.at["pv", "dc_ac_ratio"] = 1.3
    p = {
        "component_class": "Generator",
        "name": "pv",
        "attrs": {"overnight_cost": 1.3e5, "lifetime": 30.0, "fom_cost": 2e3},
        "source": source or RA.source_id(real("solar-utility", 2030)),
        "source_year": 2024,
        "replaces": None,
        "currency": "EUR",
        "currency_year": 2024,
    }  # no scaled_by: as before TCU-05
    RA.stamp(n, p, approved_by="a@b")
    return n


def test_a_dc_reference_applied_before_this_reads_scale_input_not_recorded(monkeypatch):
    n = _applied_before_this(monkeypatch, dc=True)
    s = _stale(n, "Generator", "pv")
    for field in ("overnight_cost", "fom_cost"):
        assert (
            s[field]["reason"] == "scale input not recorded"
            and s[field]["field"] is None
        )
    assert s["lifetime"] is None


def test_an_ac_reference_applied_before_this_reads_fresh(monkeypatch):
    n = _applied_before_this(monkeypatch, dc=False)
    assert _stale(n, "Generator", "pv") == {
        f: None for f in P.get(n, "Generator", "pv")
    }


def test_a_reference_from_another_release_reads_not_recorded(monkeypatch):
    n = _applied_before_this(
        monkeypatch,
        dc=False,
        source="eu-dea-2026-08#" + "0" * 16 + "/solar-utility@2030",
    )
    assert _stale(n, "Generator", "pv")["overnight_cost"]["reason"].endswith(
        "is from another release)"
    )
    n = _applied_before_this(
        monkeypatch,
        dc=False,
        source="eu-dea-2025-01#" + "0" * 16 + "/solar-utility@2030",
    )
    assert _stale(n, "Generator", "pv")["overnight_cost"]["reason"].endswith(
        "is from another release)"
    )


def test_a_reference_that_cannot_be_read_back_reads_not_recorded(monkeypatch):
    n = _applied_before_this(
        monkeypatch,
        dc=False,
        source="eu-dea-2026-08#" + "0" * 16 + "/no-such-tech@2030",
    )
    assert _stale(n, "Generator", "pv")["overnight_cost"]["reason"].endswith(
        "cannot be read back)"
    )


def test_an_edited_reference_value_reads_fresh(monkeypatch):
    """A later edit drops the approval: the value is the user's own, not the reference's."""
    n = _applied_before_this(monkeypatch, dc=True)
    P.mark_customised(n, "Generator", "pv", ["overnight_cost", "fom_cost"])
    s = _stale(n, "Generator", "pv")
    assert s["overnight_cost"] is None and s["fom_cost"] is None


def test_a_link_reference_is_never_looked_up(monkeypatch):
    from services.library import europe_reference as R

    n = _fc()
    P.record(
        n,
        "Link",
        "fc",
        "overnight_cost",
        status="customised",
        approved_by="a@b",
        source="eu-dea-2026-08#" + "0" * 16 + "/fuel-cell@2030",
    )
    monkeypatch.setattr(
        R, "load_europe_technology_reference", lambda *a: pytest.fail("looked up")
    )
    assert _stale(n, "Link", "fc")["overnight_cost"] is None


def test_a_reference_with_the_right_hash_but_another_id_reads_not_recorded(monkeypatch):
    from services.library import europe_reference as R

    h = R.load_europe_technology_reference("solar-utility", 2030).reference_hash[:16]
    n = _applied_before_this(
        monkeypatch, dc=False, source=f"eu-other#{h}/solar-utility@2030"
    )
    assert _stale(n, "Generator", "pv")["overnight_cost"]["reason"].endswith(
        "is from another release)"
    )


def test_only_a_customised_entry_is_looked_up_as_a_reference(monkeypatch):
    n = _applied_before_this(monkeypatch, dc=True)
    for f in ("overnight_cost", "fom_cost"):
        P._root(n)["Generator"]["pv"][f]["status"] = (
            "measured"  # approval kept, not a reference apply
        )
    assert _stale(n, "Generator", "pv")["overnight_cost"] is None
