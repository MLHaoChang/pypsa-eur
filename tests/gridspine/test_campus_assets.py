"""The campus asset library (C7): a shipped catalogue of transformers,
cables, compensation and switchgear, each with capex, fixed opex and lifetime.

The library is a *cost* input to the electrical investment, so the tests hold
it to the rule of the campus file: every number is ``{value, source}``, and a
broken or untagged library is refused with the entry and field named. Costs
are order-of-magnitude placeholders, so a test pins that none of them claims
to be anything else.
"""
import copy
import math

import pytest
import yaml

from gridspine.producers.campus import PFE_KW_PER_MVA, I0_PERCENT, _trafo_class
from gridspine.schema.campus import STANDARD_MVA
from gridspine.schema.contracts import ContractError
from gridspine.templates.campus_assets import (
    DEFAULT_PATH, KINDS, annualised_cost, annuity, library_entries, load_asset_library, value,
)

LIB = load_asset_library()
RAW = yaml.safe_load(DEFAULT_PATH.read_text())

#: The numeric fields each kind must carry, besides the id and the cost block.
FIELDS = {
    "transformers": ("hv_kv", "lv_kv", "s_mva", "vk_percent", "vkr_percent", "pfe_kw", "i0_percent"),
    "cables": ("vn_kv", "cross_section_mm2", "r_ohm_per_km", "x_ohm_per_km", "c_nf_per_km", "max_i_ka"),
    "capacitor_banks": ("vn_kv", "q_mvar", "steps"),
    "shunt_reactors": ("vn_kv", "q_mvar"),
    "statcoms": ("vn_kv", "q_mvar", "losses_percent"),
    "switchgear": ("vn_kv", "ik_rated_ka", "ip_rated_ka"),
}
COST = {k: ("capex_eur_per_km" if k == "cables" else "capex_eur", "opex_frac", "lifetime_a") for k in FIELDS}
MAY_BE_ZERO = {"pfe_kw", "i0_percent", "c_nf_per_km", "opex_frac"}


def raw():
    """The shipped library cut to two entries per kind: every refusal below
    edits entry 0 or 1, and writing and re-reading all of it per case made
    this file a minute long."""
    return copy.deepcopy({k: v[:2] if k in KINDS else v for k, v in RAW.items()})


def write(tmp_path, data):
    f = tmp_path / "assets.yaml"
    f.write_text(yaml.safe_dump(data))
    return f


def refused(tmp_path, data, match):
    with pytest.raises(ContractError, match=match):
        load_asset_library(write(tmp_path, data))


def entry(data, kind, i=0):
    return data[kind][i]


# --------------------------------------------------------------------------
# the shipped library
# --------------------------------------------------------------------------

def test_the_shipped_library_loads_and_every_kind_is_populated():
    assert set(KINDS) == set(FIELDS)
    for kind in KINDS:
        assert len(LIB[kind]) >= 3, kind
    assert LIB["currency"] == "EUR" and isinstance(LIB["price_year"], int)
    assert 0 <= value(LIB["discount_rate"]) < 1


def test_ids_are_unique_across_the_whole_library():
    ids = [e["id"] for k in KINDS for e in LIB[k]]
    assert len(ids) == len(set(ids))


def test_every_entry_carries_every_field_tagged():
    for kind in KINDS:
        for e in LIB[kind]:
            for f in (*FIELDS[kind], *COST[kind]):
                assert set(e[f]) >= {"value", "source"}, (e["id"], f)


def test_no_cost_claims_to_be_anything_but_assumed():
    """Nothing here was checked against a price list or a datasheet."""
    assert LIB["discount_rate"]["source"] == "assumed"
    for kind in KINDS:
        for e in LIB[kind]:
            for f in (*FIELDS[kind], *COST[kind]):
                assert e[f]["source"] == "assumed", (e["id"], f)
    header = DEFAULT_PATH.read_text().split("\n\n")[0].lower()
    assert "order-of-magnitude" in header and "vendor quotes" in header


def test_transformers_cover_the_four_voltage_pairs_in_r10_sizes():
    pairs = {}
    for t in LIB["transformers"]:
        pairs.setdefault((value(t["hv_kv"]), value(t["lv_kv"])), []).append(value(t["s_mva"]))
        assert value(t["s_mva"]) in STANDARD_MVA
    assert set(pairs) == {(132.0, 33.0), (110.0, 20.0), (33.0, 11.0), (20.0, 0.4)}
    assert pairs[(132.0, 33.0)] == [20.0, 25.0, 31.5, 40.0, 50.0, 63.0, 80.0, 100.0]
    assert all(len(v) >= 4 for v in pairs.values())


def test_transformer_data_is_the_producers_typical_class_data():
    for t in LIB["transformers"]:
        s = value(t["s_mva"])
        vk, vkr = _trafo_class(s)
        assert value(t["vk_percent"]) == vk and value(t["vkr_percent"]) == vkr, t["id"]
        assert value(t["pfe_kw"]) == pytest.approx(PFE_KW_PER_MVA * s)
        assert value(t["i0_percent"]) == I0_PERCENT


def test_cables_compensation_and_switchgear_cover_the_mv_voltages():
    for kind in ("cables", "capacitor_banks", "shunt_reactors", "statcoms"):
        assert {value(e["vn_kv"]) for e in LIB[kind]} == {33.0, 20.0, 11.0}, kind
    assert {value(e["vn_kv"]) for e in LIB["switchgear"]} == {132.0, 110.0, 33.0, 20.0, 11.0}
    assert {value(e["ik_rated_ka"]) for e in LIB["switchgear"]} >= {25.0, 31.5, 40.0}


def test_a_bigger_cable_has_less_resistance_and_more_capacity_and_cost():
    for kv in (33.0, 20.0, 11.0):
        cs = sorted(library_entries(LIB, "cables", vn_kv=kv), key=lambda c: value(c["cross_section_mm2"]))
        assert len(cs) >= 3
        for a, b in zip(cs, cs[1:]):
            assert value(a["r_ohm_per_km"]) > value(b["r_ohm_per_km"])
            assert value(a["max_i_ka"]) < value(b["max_i_ka"])
            assert value(a["capex_eur_per_km"]) < value(b["capex_eur_per_km"])


def test_bigger_transformers_and_banks_cost_more_within_a_voltage():
    ts = library_entries(LIB, "transformers", hv_kv=132.0, lv_kv=33.0)
    caps = [value(t["capex_eur"]) for t in sorted(ts, key=lambda t: value(t["s_mva"]))]
    assert caps == sorted(caps) and len(set(caps)) == len(caps)
    for kind in ("capacitor_banks", "shunt_reactors", "statcoms"):
        es = sorted(library_entries(LIB, kind, vn_kv=20.0), key=lambda e: value(e["q_mvar"]))
        costs = [value(e["capex_eur"]) for e in es]
        assert costs == sorted(costs) and len(set(costs)) == len(costs), kind


def test_a_switchgear_peak_rating_is_the_iec_ratio_to_the_short_time_rating():
    for s in LIB["switchgear"]:
        assert value(s["ip_rated_ka"]) == pytest.approx(2.5 * value(s["ik_rated_ka"])), s["id"]


def test_a_bank_has_whole_steps_and_a_statcom_losses():
    for b in LIB["capacitor_banks"]:
        assert isinstance(value(b["steps"]), int) and value(b["steps"]) >= 1
    assert all(0 < value(s["losses_percent"]) < 5 for s in LIB["statcoms"])


# --------------------------------------------------------------------------
# refusals: each names the entry and the field
# --------------------------------------------------------------------------

def test_the_raw_library_is_itself_valid(tmp_path):
    assert load_asset_library(write(tmp_path, raw()))["currency"] == "EUR"
    assert load_asset_library(write(tmp_path, copy.deepcopy(RAW))) == LIB


@pytest.mark.parametrize("kind, field", [(k, f) for k in FIELDS for f in (*FIELDS[k], *COST[k])])
def test_a_missing_field_is_refused_naming_entry_and_field(tmp_path, kind, field):
    d = raw()
    e = entry(d, kind, 1)
    del e[field]
    refused(tmp_path, d, rf"{kind}\[{e['id']}\].*{field}")


@pytest.mark.parametrize("kind, field", [(k, f) for k in FIELDS for f in (*FIELDS[k], *COST[k])])
def test_an_untagged_value_is_refused_naming_entry_and_field(tmp_path, kind, field):
    d = raw()
    e = entry(d, kind, 1)
    e[field] = e[field]["value"]
    refused(tmp_path, d, rf"{kind}\[{e['id']}\].*{field}.*value.*source")


@pytest.mark.parametrize("kind, field", [(k, f) for k in FIELDS for f in (*FIELDS[k], *COST[k])])
def test_an_unknown_source_is_refused_naming_entry_and_field(tmp_path, kind, field):
    d = raw()
    e = entry(d, kind, 1)
    e[field]["source"] = "guessed"
    refused(tmp_path, d, rf"{kind}\[{e['id']}\].*{field}.*unknown source 'guessed'")


@pytest.mark.parametrize("kind, field", [(k, f) for k in FIELDS for f in (*FIELDS[k], *COST[k]) if f not in MAY_BE_ZERO])
@pytest.mark.parametrize("bad", [0, -1])
def test_a_non_positive_rating_or_cost_is_refused(tmp_path, kind, field, bad):
    d = raw()
    e = entry(d, kind, 1)
    e[field]["value"] = bad
    refused(tmp_path, d, rf"{kind}\[{e['id']}\].*{field}.*positive")


@pytest.mark.parametrize("kind, field", [(k, f) for k in FIELDS for f in (*FIELDS[k], *COST[k])])
@pytest.mark.parametrize("bad", ["12", float("nan"), float("inf"), True])
def test_a_value_that_is_not_a_finite_number_is_refused(tmp_path, kind, field, bad):
    d = raw()
    e = entry(d, kind, 1)
    e[field]["value"] = bad
    refused(tmp_path, d, rf"{kind}\[{e['id']}\].*{field}.*finite number")


@pytest.mark.parametrize("kind, field", [("transformers", "pfe_kw"), ("transformers", "i0_percent"),
                                         ("cables", "c_nf_per_km"), ("transformers", "capex_eur")])
def test_only_the_fields_that_may_be_zero_accept_zero_and_none_accept_negative(tmp_path, kind, field):
    d = raw()
    entry(d, kind)[field]["value"] = 0
    if field in MAY_BE_ZERO:
        assert load_asset_library(write(tmp_path, d))
    else:
        refused(tmp_path, d, field)
    entry(d, kind)[field]["value"] = -0.5
    refused(tmp_path, d, rf"{field}.*(positive|non-negative)")


def test_opex_may_be_zero_but_not_negative_and_a_lifetime_is_at_least_a_year(tmp_path):
    d = raw()
    e = entry(d, "statcoms")
    e["opex_frac"]["value"] = 0
    e["lifetime_a"]["value"] = 1
    assert load_asset_library(write(tmp_path, d))
    e["opex_frac"]["value"] = -0.01
    refused(tmp_path, d, rf"statcoms\[{e['id']}\].*opex_frac.*non-negative")
    e["opex_frac"]["value"] = 0.02
    e["lifetime_a"]["value"] = 0.99
    refused(tmp_path, d, rf"statcoms\[{e['id']}\].*lifetime_a.*at least 1")


@pytest.mark.parametrize("rate, ok", [(0.0, True), (0.07, True), (0.999, True), (1.0, False), (1.5, False), (-0.01, False)])
def test_the_discount_rate_must_lie_in_zero_to_one(tmp_path, rate, ok):
    d = raw()
    d["discount_rate"]["value"] = rate
    if ok:
        assert value(load_asset_library(write(tmp_path, d))["discount_rate"]) == rate
    else:
        refused(tmp_path, d, r"discount_rate.*\[0, 1\)")


def test_the_discount_rate_is_required_and_tagged(tmp_path):
    d = raw()
    del d["discount_rate"]
    refused(tmp_path, d, "discount_rate")
    d = raw()
    d["discount_rate"] = 0.07
    refused(tmp_path, d, "discount_rate.*value.*source")
    d = raw()
    d["discount_rate"]["source"] = "quoted"
    refused(tmp_path, d, "discount_rate.*unknown source")


@pytest.mark.parametrize("key", ["currency", "price_year"])
def test_currency_and_price_year_are_required(tmp_path, key):
    d = raw()
    del d[key]
    refused(tmp_path, d, key)


@pytest.mark.parametrize("key, bad", [("currency", ""), ("currency", 3), ("price_year", "2026"),
                                      ("price_year", 2026.5), ("price_year", 0), ("price_year", True)])
def test_a_malformed_currency_or_price_year_is_refused(tmp_path, key, bad):
    d = raw()
    d[key] = bad
    refused(tmp_path, d, key)


def test_an_unknown_kind_is_refused(tmp_path):
    d = raw()
    d["inverters"] = []
    refused(tmp_path, d, "inverters")


@pytest.mark.parametrize("kind", list(FIELDS))
def test_a_missing_or_malformed_kind_is_refused(tmp_path, kind):
    d = raw()
    del d[kind]
    refused(tmp_path, d, kind)
    d = raw()
    d[kind] = {"a": 1}
    refused(tmp_path, d, rf"{kind}.*list")


@pytest.mark.parametrize("kind", list(FIELDS))
def test_an_unknown_field_is_refused_naming_it(tmp_path, kind):
    d = raw()
    e = entry(d, kind, 1)
    e["colour"] = {"value": 1.0, "source": "assumed"}
    refused(tmp_path, d, rf"{kind}\[{e['id']}\].*colour")


def test_a_cable_priced_per_bay_and_a_transformer_priced_per_km_are_refused(tmp_path):
    d = raw()
    c = entry(d, "cables")
    c["capex_eur"] = c.pop("capex_eur_per_km")
    refused(tmp_path, d, rf"cables\[{c['id']}\].*capex_eur")
    d = raw()
    t = entry(d, "transformers")
    t["capex_eur_per_km"] = t.pop("capex_eur")
    refused(tmp_path, d, rf"transformers\[{t['id']}\].*capex_eur_per_km")


def test_a_tagged_value_with_an_unknown_key_is_refused(tmp_path):
    d = raw()
    e = entry(d, "cables")
    e["r_ohm_per_km"]["units"] = "ohm"
    refused(tmp_path, d, rf"cables\[{e['id']}\].*r_ohm_per_km.*units")
    d = raw()
    entry(d, "cables")["r_ohm_per_km"]["note"] = "a typical value"      # a note is allowed
    assert load_asset_library(write(tmp_path, d))


def test_an_entry_needs_a_string_id(tmp_path):
    d = raw()
    del d["cables"][0]["id"]
    refused(tmp_path, d, r"cables\[0\].*id")
    d = raw()
    d["cables"][0]["id"] = ""
    refused(tmp_path, d, r"cables\[0\].*id")
    d = raw()
    d["cables"][0] = "not a mapping"
    refused(tmp_path, d, r"cables\[0\].*mapping")


def test_a_duplicate_id_is_refused_even_across_kinds(tmp_path):
    d = raw()
    d["statcoms"][1]["id"] = d["statcoms"][0]["id"]
    refused(tmp_path, d, rf"duplicate id '{d['statcoms'][0]['id']}'")
    d = raw()
    d["switchgear"][0]["id"] = d["cables"][0]["id"]
    refused(tmp_path, d, rf"duplicate id '{d['cables'][0]['id']}'")


def test_a_transformer_must_step_down_and_its_copper_loss_be_below_its_impedance(tmp_path):
    for hv, lv in ((33.0, 33.0), (20.0, 33.0)):
        d = raw()
        t = entry(d, "transformers")
        t["hv_kv"]["value"], t["lv_kv"]["value"] = hv, lv
        refused(tmp_path, d, rf"transformers\[{t['id']}\].*hv_kv.*lv_kv")
    for vkr in (8.0, 9.0):                        # vk is 8 % for the first entry
        d = raw()
        t = entry(d, "transformers")
        assert value(t["vk_percent"]) == 8.0
        t["vkr_percent"]["value"] = vkr
        refused(tmp_path, d, rf"transformers\[{t['id']}\].*vkr_percent.*vk_percent")
    d = raw()
    t = entry(d, "transformers")
    t["vkr_percent"]["value"] = 7.99
    assert load_asset_library(write(tmp_path, d))


@pytest.mark.parametrize("steps", [2.5, 3.0, "2", True, 0, -1])
def test_a_bank_needs_a_whole_number_of_steps_of_at_least_one(tmp_path, steps):
    d = raw()
    b = entry(d, "capacitor_banks")
    b["steps"]["value"] = steps
    refused(tmp_path, d, rf"capacitor_banks\[{b['id']}\].*steps")


def test_a_file_that_is_not_a_mapping_or_is_missing_is_refused(tmp_path):
    f = tmp_path / "a.yaml"
    f.write_text("- 1\n- 2\n")
    with pytest.raises(ContractError, match="mapping"):
        load_asset_library(f)
    with pytest.raises(ContractError, match="not found"):
        load_asset_library(tmp_path / "missing.yaml")


def test_the_loaded_library_is_independent_of_the_next_load():
    a = load_asset_library()
    a["cables"][0]["r_ohm_per_km"]["value"] = 99.0
    assert value(load_asset_library()["cables"][0]["r_ohm_per_km"]) != 99.0


# --------------------------------------------------------------------------
# annuity and annualised cost, by hand
# --------------------------------------------------------------------------

def test_annuity_matches_the_closed_form_by_hand():
    r, n = 0.07, 40
    by_hand = r / (1 - (1 + r) ** -n)
    assert by_hand == pytest.approx(0.07500914, abs=1e-8)
    assert annuity(r, n) == pytest.approx(by_hand, rel=1e-12)
    # the same factor in the form pypsa-gui's _annuity writes it: r (1+r)^L / ((1+r)^L - 1)
    assert annuity(r, n) == pytest.approx(r * (1 + r) ** n / ((1 + r) ** n - 1), rel=1e-12)
    assert annuity(0.05, 20) == pytest.approx(0.05 / (1 - 1.05 ** -20), rel=1e-12)


def test_a_zero_rate_is_straight_line():
    assert annuity(0.0, 25) == pytest.approx(1 / 25, rel=1e-12)
    assert annuity(0, 8) == 0.125


@pytest.mark.parametrize("n", [0, -5])
def test_annuity_refuses_a_non_positive_lifetime(n):
    with pytest.raises(ValueError, match="lifetime"):
        annuity(0.07, n)


def test_annuity_refuses_a_rate_outside_zero_to_one():
    for r in (-0.01, 1.0):
        with pytest.raises(ValueError, match="rate"):
            annuity(r, 20)


def test_a_one_year_asset_costs_its_capex_with_interest():
    assert annuity(0.1, 1) == pytest.approx(1.1, rel=1e-12)


def _lib(rate=0.07):
    d = raw()
    d["discount_rate"]["value"] = rate
    return d


def test_annualised_cost_is_capex_times_annuity_plus_opex_by_hand():
    lib = _lib(0.07)
    t = {"id": "T", "capex_eur": {"value": 1_000_000, "source": "assumed"},
         "opex_frac": {"value": 0.015, "source": "assumed"}, "lifetime_a": {"value": 40, "source": "assumed"}}
    crf = 0.07 / (1 - 1.07 ** -40)
    assert annualised_cost(t, lib) == pytest.approx(1_000_000 * crf + 1_000_000 * 0.015, rel=1e-12)
    assert annualised_cost(t, lib) == pytest.approx(75_009.14 + 15_000, abs=0.01)


def test_a_zero_discount_rate_and_zero_opex_gives_straight_line_cost():
    t = {"id": "T", "capex_eur": {"value": 500_000, "source": "assumed"},
         "opex_frac": {"value": 0.0, "source": "assumed"}, "lifetime_a": {"value": 25, "source": "assumed"}}
    assert annualised_cost(t, _lib(0.0)) == pytest.approx(20_000.0)


def test_a_cable_is_priced_per_km_and_needs_a_length():
    lib = _lib(0.07)
    c = {"id": "C", "capex_eur_per_km": {"value": 100_000, "source": "assumed"},
         "opex_frac": {"value": 0.01, "source": "assumed"}, "lifetime_a": {"value": 40, "source": "assumed"}}
    crf = 0.07 / (1 - 1.07 ** -40)
    assert annualised_cost(c, lib, length_km=2.5) == pytest.approx(250_000 * crf + 250_000 * 0.01, rel=1e-12)
    with pytest.raises(ValueError, match="length_km"):
        annualised_cost(c, lib)
    for bad in (0, -1.0):
        with pytest.raises(ValueError, match="length_km"):
            annualised_cost(c, lib, length_km=bad)


def test_a_shipped_entry_costs_its_own_fields_through_the_library_rate():
    t = library_entries(LIB, "transformers", hv_kv=132.0, lv_kv=33.0, s_mva=40.0)[0]
    capex, lifetime = value(t["capex_eur"]), value(t["lifetime_a"])
    r = value(LIB["discount_rate"])
    expect = capex * (r / (1 - (1 + r) ** -lifetime)) + capex * value(t["opex_frac"])
    assert annualised_cost(t, LIB) == pytest.approx(expect, rel=1e-12)
    cab = LIB["cables"][0]
    assert annualised_cost(cab, LIB, length_km=3.0) == pytest.approx(3 * annualised_cost(cab, LIB, length_km=1.0))


# --------------------------------------------------------------------------
# library_entries
# --------------------------------------------------------------------------

def test_entries_are_listed_by_kind_and_filtered_by_value():
    t = library_entries(LIB, "transformers", hv_kv=110.0, lv_kv=20.0)
    assert t and all(value(e["hv_kv"]) == 110.0 and value(e["lv_kv"]) == 20.0 for e in t)
    assert len(library_entries(LIB, "transformers")) == len(LIB["transformers"])
    assert library_entries(LIB, "transformers", hv_kv=110.0, lv_kv=33.0) == []
    one = library_entries(LIB, "cables", id=LIB["cables"][0]["id"])
    assert one == [LIB["cables"][0]]


def test_a_filter_on_an_unknown_kind_or_field_is_refused_not_empty():
    with pytest.raises(ValueError, match="kind"):
        library_entries(LIB, "inverters")
    with pytest.raises(ValueError, match="colour"):
        library_entries(LIB, "cables", colour=1)
