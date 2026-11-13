"""The campus asset library (C7): a shipped catalogue of transformers,
cables, compensation and switchgear, each with capex, fixed opex and lifetime.

The library is a *cost* input to the electrical investment, so the tests hold
it to the rule of the campus file: every number is ``{value, source}``, and a
broken or untagged library is refused with the entry and field named. Costs
are reference values, not quotes, so a test pins the tag rules: only a cost
may be ``literature``, and each such value names its source row.
"""
import copy
import hashlib
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


#: sha256 of the shipped library: the defaults pack 2026-11-13 manifest pins the same bytes
#: (2026-10-23 .. 2026-10-30 pinned the library before the 220/110 kV units, 2026-11-06 .. 2026-11-10
#: before their sourced losses).
SHIPPED_SHA256 = "aea49a692aa25e2b4afbad9b9393235e287d9f80256f25854390c9acb421f709"
#: How many cost fields of each kind are cited reference values (tag literature).
LITERATURE_COUNTS = {
    ("transformers", "capex_eur"): 29,
    ("transformers", "opex_frac"): 34,
    ("transformers", "lifetime_a"): 34,
    ("cables", "capex_eur_per_km"): 11,
    ("cables", "opex_frac"): 12,
    ("cables", "lifetime_a"): 12,
    ("capacitor_banks", "capex_eur"): 9,
    ("capacitor_banks", "opex_frac"): 9,
    ("shunt_reactors", "opex_frac"): 9,
    ("switchgear", "capex_eur"): 5,
    ("switchgear", "opex_frac"): 13,
    ("switchgear", "lifetime_a"): 13,
}


def test_the_shipped_library_is_the_file_the_defaults_pack_pins():
    assert hashlib.sha256(DEFAULT_PATH.read_bytes()).hexdigest() == SHIPPED_SHA256


def test_only_a_cost_is_a_cited_reference_value_and_each_names_its_row():
    """Ratings stay assumed; a cost is literature (with a note) or assumed; the money is 2025 EUR."""
    assert LIB["discount_rate"]["source"] == "assumed"
    assert (LIB["currency"], LIB["price_year"]) == ("EUR", 2025)
    counts, tags = {}, {"literature": 0, "assumed": 1}             # the discount rate is one assumed value
    for kind in KINDS:
        for e in LIB[kind]:
            for f in FIELDS[kind]:
                assert e[f]["source"] == "assumed", (e["id"], f)
                tags["assumed"] += 1
            for f in COST[kind]:
                src = e[f]["source"]
                assert src in ("literature", "assumed"), (e["id"], f, src)
                tags[src] += 1
                if src == "literature":
                    assert e[f].get("note", "").strip(), (e["id"], f)
                    counts[(kind, f)] = counts.get((kind, f), 0) + 1
    assert counts == LITERATURE_COUNTS
    assert tags == {"literature": 190, "assumed": 490}
    header = DEFAULT_PATH.read_text().split("\n\n")[0].lower()
    assert "reference values, not quotes" in header and "vendor quotes" in header


def test_transformers_cover_the_four_voltage_pairs_in_r10_sizes():
    pairs = {}
    for t in LIB["transformers"]:
        pairs.setdefault((value(t["hv_kv"]), value(t["lv_kv"])), []).append(value(t["s_mva"]))
        assert value(t["s_mva"]) in STANDARD_MVA
    assert set(pairs) == {(220.0, 110.0), (132.0, 33.0), (110.0, 20.0), (33.0, 11.0), (20.0, 0.4)}
    # campus-library-gaps 01: R10 sizes priced from Ei R-NR-T-8-1..5 (120-315 MVA)
    assert pairs[(220.0, 110.0)] == [125.0, 160.0, 200.0, 250.0, 315.0]
    assert pairs[(132.0, 33.0)] == [20.0, 25.0, 31.5, 40.0, 50.0, 63.0, 80.0, 100.0]
    assert all(len(v) >= 4 for v in pairs.values())


def test_transformer_data_is_the_producers_typical_class_data():
    """Every unit but the 220/110 kV ones, whose losses are sourced (campus-library-gaps 03)."""
    for t in LIB["transformers"]:
        s = value(t["s_mva"])
        vk, vkr = _trafo_class(s)
        assert value(t["vk_percent"]) == vk and value(t["i0_percent"]) == I0_PERCENT, t["id"]
        if t["id"] in SOURCED_LOSSES:
            continue
        assert value(t["vkr_percent"]) == vkr, t["id"]
        assert value(t["pfe_kw"]) == pytest.approx(PFE_KW_PER_MVA * s)


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


# --------------------------------------------------------------------------
# the `literature` tag: a cost read from a published, cited reference
# --------------------------------------------------------------------------
#
# A cost may be tagged `literature`: a reference value read from a cited
# publication (a regulator's catalogue, say), not a quote. A rating may not:
# the investment copies a bought item's ratings into the campus file, whose
# tags are measured | datasheet | assumed, so a `literature` rating would make
# the invested campus file unreadable.

@pytest.mark.parametrize("kind, field", [(k, f) for k in FIELDS for f in COST[k]])
def test_a_cost_may_be_tagged_literature(tmp_path, kind, field):
    d = raw()
    e = entry(d, kind, 1)
    e[field]["source"] = "literature"
    lib = load_asset_library(write(tmp_path, d))
    assert lib[kind][1][field] == e[field]


def test_the_discount_rate_may_be_tagged_literature(tmp_path):
    d = raw()
    d["discount_rate"] = {"value": 0.05, "source": "literature", "note": "a published WACC"}
    assert load_asset_library(write(tmp_path, d))["discount_rate"]["source"] == "literature"


@pytest.mark.parametrize("kind, field", [(k, f) for k in FIELDS for f in FIELDS[k]])
def test_a_rating_tagged_literature_is_refused_naming_entry_and_field(tmp_path, kind, field):
    d = raw()
    e = entry(d, kind, 1)
    e[field]["source"] = "literature"
    refused(tmp_path, d, rf"{kind}\[{e['id']}\]\.{field}.*'literature'.*only a cost")


def test_the_unknown_source_message_lists_literature_for_a_cost_and_not_for_a_rating(tmp_path):
    d = raw()
    entry(d, "transformers")["capex_eur"]["source"] = "guessed"
    refused(tmp_path, d, r"capex_eur.*allowed \['assumed', 'datasheet', 'literature', 'measured'\]")
    d = raw()
    entry(d, "transformers")["s_mva"]["source"] = "guessed"
    refused(tmp_path, d, r"s_mva.*allowed \['assumed', 'datasheet', 'measured'\]")


def test_a_literature_cost_is_still_checked_as_a_number(tmp_path):
    d = raw()
    e = entry(d, "cables")
    e["capex_eur_per_km"] = {"value": -1, "source": "literature"}
    refused(tmp_path, d, rf"cables\[{e['id']}\].*capex_eur_per_km.*positive")
    e["capex_eur_per_km"] = {"value": float("nan"), "source": "literature"}
    refused(tmp_path, d, rf"cables\[{e['id']}\].*capex_eur_per_km.*finite number")


def test_annualised_cost_does_not_depend_on_the_tag(tmp_path):
    d = raw()
    a = load_asset_library(write(tmp_path, d))
    for kind in FIELDS:
        for f in COST[kind]:
            entry(d, kind)[f]["source"] = "literature"
    d["discount_rate"]["source"] = "literature"
    b = load_asset_library(write(tmp_path, d))
    for kind in FIELDS:
        km = 1.5 if kind == "cables" else None
        assert annualised_cost(b[kind][0], b, km) == annualised_cost(a[kind][0], a, km)


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


# --------------------------------------------------------------------------
# optional transformer fields (plan C13): vector group and tap changer
# --------------------------------------------------------------------------

def tap(**over):
    block = {"changer": "on_load", "side": "hv", "neutral": 0, "min": -9, "max": 9,
             "step_percent": {"value": 1.5, "source": "assumed", "note": "pandapower 110/20 kV std types"}}
    block.update(over)
    return block


def test_a_transformer_may_carry_a_tagged_vector_group_and_a_tap_block(tmp_path):
    d = raw()
    t = entry(d, "transformers")
    t["vector_group"] = {"value": "Dyn11", "source": "assumed", "note": "typical"}
    t["tap"] = tap()
    lib = load_asset_library(write(tmp_path, d))
    got = lib["transformers"][0]
    assert got["vector_group"] == {"value": "Dyn11", "source": "assumed", "note": "typical"}
    assert got["tap"]["step_percent"]["value"] == 1.5 and got["tap"]["min"] == -9
    # an entry without them still loads, as before
    assert "vector_group" not in lib["transformers"][1] and "tap" not in lib["transformers"][1]


@pytest.mark.parametrize("group", ["Dyn11", "YNd5", "YNyn0", "Yy0", "Dd6", "Dy1", "Dyn10"])
def test_a_vector_group_in_the_campus_notation_is_accepted(tmp_path, group):
    d = raw()
    entry(d, "transformers")["vector_group"] = {"value": group, "source": "datasheet"}
    assert load_asset_library(write(tmp_path, d))


@pytest.mark.parametrize("bad", ["Dyn12", "dyn11", "Dyn", "DYN11", "Xyn11", "Dyn011", "", 11, None])
def test_a_malformed_vector_group_is_refused_naming_entry_and_field(tmp_path, bad):
    d = raw()
    t = entry(d, "transformers")
    t["vector_group"] = {"value": bad, "source": "assumed"}
    refused(tmp_path, d, rf"transformers\[{t['id']}\]\.vector_group")


def test_a_vector_group_must_be_tagged_with_a_known_source(tmp_path):
    d = raw()
    t = entry(d, "transformers")
    t["vector_group"] = "Dyn11"
    refused(tmp_path, d, rf"transformers\[{t['id']}\]\.vector_group.*value.*source")
    d = raw()
    t = entry(d, "transformers")
    t["vector_group"] = {"value": "Dyn11", "source": "guessed"}
    refused(tmp_path, d, rf"transformers\[{t['id']}\]\.vector_group.*source")


@pytest.mark.parametrize("field, bad, match", [
    ("changer", "motorised", r"tap\.changer"),
    ("side", "mv", r"tap\.side"),
    ("neutral", 0.5, r"tap\.neutral.*whole"),
    ("min", True, r"tap\.min.*whole"),
    ("max", "9", r"tap\.max.*whole"),
    ("min", 1, r"tap.*min.*neutral.*max"),
    ("max", -1, r"tap.*min.*neutral.*max"),
    ("step_percent", 1.5, r"tap\.step_percent.*value.*source"),
    ("step_percent", {"value": 0.0, "source": "assumed"}, r"tap\.step_percent.*positive"),
    ("step_percent", {"value": -1.5, "source": "assumed"}, r"tap\.step_percent.*positive"),
    ("pos", 0, r"tap.*unknown.*pos"),                 # a library type has no position; a campus unit has
    ("speed", 1, r"tap.*unknown.*speed"),
])
def test_a_malformed_tap_block_is_refused_naming_entry_and_field(tmp_path, field, bad, match):
    d = raw()
    t = entry(d, "transformers")
    t["tap"] = tap(**{field: bad})
    refused(tmp_path, d, rf"transformers\[{t['id']}\]\.{match}")


@pytest.mark.parametrize("missing", ["changer", "side", "neutral", "min", "max", "step_percent"])
def test_a_tap_block_needs_every_field(tmp_path, missing):
    d = raw()
    t = entry(d, "transformers")
    t["tap"] = tap()
    del t["tap"][missing]
    refused(tmp_path, d, rf"transformers\[{t['id']}\]\.tap.*missing {missing}")


def test_the_tap_range_may_be_one_sided_and_the_neutral_at_an_end(tmp_path):
    d = raw()
    entry(d, "transformers")["tap"] = tap(neutral=0, min=0, max=4, changer="off_circuit", side="lv")
    assert load_asset_library(write(tmp_path, d))


def test_vector_group_and_tap_are_transformer_fields_only(tmp_path):
    d = raw()
    c = entry(d, "cables")
    c["vector_group"] = {"value": "Dyn11", "source": "assumed"}
    refused(tmp_path, d, rf"cables\[{c['id']}\].*vector_group")



#: campus-library-gaps 01: Ei's 245/123-145 kV rows, MVA -> (row, SEK of 2022).
EI_245_123 = {120.0: ("R-NR-T-8-1", 33884380), 150.0: ("R-NR-T-8-2", 38992520),
              200.0: ("R-NR-T-8-3", 46654733), 250.0: ("R-NR-T-8-4", 51762875),
              315.0: ("R-NR-T-8-5", 56871016)}
#: The R10 sizes between two rows, and their anchors (GK A, CL-01 Q3 (c)).
INTERPOLATED = {125.0: (120.0, 150.0), 160.0: (150.0, 200.0)}
#: 1 SEK (2022) in EUR (2025): ECB 2022 SEK/EUR, then the C27.1 producer price index 2022 -> 2025.
SEK_2022_TO_EUR_2025 = 119.8 / 108.6 / 10.6296


def _sek_at(s):
    """Ei's SEK at size ``s``: the row itself, or the log-log line between the two bracketing rows."""
    if s in EI_245_123:
        return EI_245_123[s][1]
    (s1, c1), (s2, c2) = ((a, EI_245_123[a][1]) for a in INTERPOLATED[s])
    return c1 * (s / s1) ** (math.log(c2 / c1) / math.log(s2 / s1))


def test_the_conversion_and_interpolation_reproduce_a_shipped_132_33_row():
    lib = {e["id"]: value(e["capex_eur"]) for e in LIB["transformers"]}
    assert lib["TR_132_33_20"] == round(12632363 * SEK_2022_TO_EUR_2025, -2)
    sek = 13438444 * (31.5 / 30) ** (math.log(14006581 / 13438444) / math.log(40 / 30))
    assert lib["TR_132_33_31p5"] == round(sek * SEK_2022_TO_EUR_2025, -2)


def test_the_220_110_kv_units_are_priced_from_their_ei_rows_with_the_class_rule_for_what_has_no_source():
    """200/250/315 MVA are straight reads of R-NR-T-8-3/4/5; 125 and 160 MVA are interpolated between the
    bracketing rows, and their notes name both anchors with their SEK. Impedance and no-load current are
    the producers' class rule, tagged assumed with a note saying no public source gives them; the losses
    are [OPTCL-TS]'s (see the next test)."""
    units = library_entries(LIB, "transformers", hv_kv=220.0, lv_kv=110.0)
    assert [value(t["s_mva"]) for t in units] == [125.0, 160.0, 200.0, 250.0, 315.0]
    for t in units:
        s = value(t["s_mva"])
        assert t["id"] == f"TR_220_110_{s:g}"
        cap = t["capex_eur"]
        assert cap["source"] == "literature" and cap["value"] == round(_sek_at(s) * SEK_2022_TO_EUR_2025, -2)
        if s in INTERPOLATED:
            assert "log-log interpolation between" in cap["note"]
            for a in INTERPOLATED[s]:
                code, sek = EI_245_123[a]
                assert f"{code} ({a:g} MVA, {sek} SEK)" in cap["note"], (t["id"], code)
        else:
            code, sek = EI_245_123[s]
            assert cap["note"].startswith(f"[EI-NVL] {code}, 245/123-145 kV {s:g} MVA, {sek} SEK (2022)")
            assert "interpolation" not in cap["note"]
        assert "linspann" in cap["note"]
        vk, _ = _trafo_class(s)
        assert value(t["vk_percent"]) == vk == 12.5
        assert "IEC 60076-5" in t["vk_percent"]["note"] and "minimum" in t["vk_percent"]["note"]
        assert t["i0_percent"] == {"value": I0_PERCENT, "source": "assumed",
                                   "note": "no public source; producers/campus.py class rule"}, t["id"]


#: [OPTCL-TS] Annexure-A section C item 32, 220/132 kV 3-phase autotransformers: MVA -> (max no-load loss,
#: max load loss at 75 degC), kW.
OPTCL_220_132 = {160.0: (30.0, 200.0), 200.0: (35.0, 260.0)}
#: An autotransformer's losses follow its equivalent winding rating S (1 - U_LV/U_HV): 220/110 over 220/132 kV.
AUTO_220_110 = (1 - 110 / 220) / (1 - 132 / 220)
#: The units whose losses are sourced and not the class rule (campus-library-gaps 03), by id.
SOURCED_LOSSES = ("TR_220_110_125", "TR_220_110_160", "TR_220_110_200", "TR_220_110_250", "TR_220_110_315")
#: Regulation (EU) 2019/1783 Annex I Table I.7, Tier 2 minimum PEI (%) at the library's sizes.
TIER2_PEI = {125.0: 99.780, 160.0: 99.790, 200.0: 99.797, 250.0: 99.797, 315.0: 99.797}


def test_the_220_110_kv_losses_are_optcls_220_132_kv_caps_for_an_autotransformer_and_meet_tier_2():
    """campus-library-gaps 03 (CLG03-SOURCE-1): 160 and 200 MVA read [OPTCL-TS] x1.25 (a 220/110 kV
    autotransformer's equivalent winding rating over a 220/132 kV one's); 125 MVA is scaled down from the
    160 MVA row and 250/315 MVA up from the 200 MVA row at constant kW/MVA. Each stays tagged assumed (a
    rating may not be literature) with a note naming the row, the winding type, the cap and the scaling,
    and its Peak Efficiency Index clears Tier 2. The class rule would miss it."""
    assert AUTO_220_110 == pytest.approx(1.25)
    units = library_entries(LIB, "transformers", hv_kv=220.0, lv_kv=110.0)
    assert tuple(t["id"] for t in units) == SOURCED_LOSSES
    for t in units:
        s = value(t["s_mva"])
        ref = 160.0 if s <= 160.0 else 200.0
        p0_ref, pk_ref = OPTCL_220_132[ref]
        assert value(t["pfe_kw"]) == round(p0_ref * AUTO_220_110 * s / ref, 2), t["id"]
        assert value(t["vkr_percent"]) == pytest.approx(pk_ref * AUTO_220_110 / (ref * 1000) * 100), t["id"]
        for f in ("vkr_percent", "pfe_kw"):
            note = t[f]["note"]
            assert t[f]["source"] == "assumed" and set(t[f]) == {"value", "source", "note"}, (t["id"], f)
            assert f"[OPTCL-TS] 220/132 kV {ref:g} MVA autotransformer" in note, (t["id"], f)
            assert "autotransformer assumed: [EI-NVL] gives no winding type" in note, (t["id"], f)
            assert "OPTCL 220/132 scaled x1.25 by co-ratio (0.50/0.40)" in note, (t["id"], f)
            assert note.endswith("; a two-winding unit carries roughly 2-2.5x these losses"), (t["id"], f)
            assert "maximum permissible" in note and "cap" in note, (t["id"], f)
            assert ("constant kW/MVA" in note) == (s != ref), (t["id"], f)
            assert note.startswith("scaled down from ") == (s < ref), (t["id"], f)
            assert ("extrapolated beyond the source's range" in note) == (s > ref), (t["id"], f)
        p0 = value(t["pfe_kw"])
        pk = value(t["vkr_percent"]) / 100 * s * 1000
        pei = 1 - 2 * math.sqrt(p0 * pk) / (s * 1000)
        assert pei * 100 >= TIER2_PEI[s], (t["id"], pei)
        _, vkr_rule = _trafo_class(s)
        rule_pei = 1 - 2 * math.sqrt(PFE_KW_PER_MVA * s * vkr_rule / 100 * s * 1000) / (s * 1000)
        assert rule_pei * 100 < TIER2_PEI[s]                      # why the class rule was replaced
    assert "[OPTCL-TS]" in DEFAULT_PATH.read_text(encoding="utf-8").split("\ntransformers:")[0]


#: The library's sha256 as the defaults packs 2026-11-06 .. 2026-11-10 pin it, before the sourced losses.
PREV_SHA256 = "5058116ad6b169fad78677e5638b957ca86ece4e30d63465538c5dc50bb6c23f"


def test_the_library_differs_from_the_previous_pin_only_in_the_220_110_losses_and_their_citation():
    """GK A C4: putting the class rule back into the five units' vkr_percent and pfe_kw, and dropping the
    [OPTCL-TS] source entry and header paragraph, gives the previous pin byte for byte."""
    text = DEFAULT_PATH.read_text(encoding="utf-8")
    src = text.index("#   [OPTCL-TS]")
    text = text[:src] + text[text.index("\n#\n", src) + 1:]
    para = text.index("# impedances follow producers/campus.py's class rule (tagged assumed). Their\n")
    end = text.index("# The no-load current has no source and follows the class rule.\n") + len(
        "# The no-load current has no source and follows the class rule.\n")
    text = (text[:para] + "# impedances and losses have no public source and follow producers/campus.py's\n"
            "# class rule (tagged assumed).\n" + text[end:])
    rule = 'source: assumed, note: "no public source; producers/campus.py class rule"}'
    lines = text.split("\n")
    unit = None
    for i, line in enumerate(lines):
        if line.startswith("  - id: "):
            unit = line[len("  - id: "):]
        if unit in SOURCED_LOSSES and line.startswith("    vkr_percent: "):
            lines[i] = "    vkr_percent: {value: 0.3, " + rule
        if unit in SOURCED_LOSSES and line.startswith("    pfe_kw: "):
            lines[i] = f"    pfe_kw: {{value: {float(unit.rsplit('_', 1)[1]):.1f}, " + rule
    assert hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest() == PREV_SHA256
