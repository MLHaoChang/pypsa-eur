"""The reference-cost proposal for the campus asset library (2026-10-12).

``docs/superpowers/research/2026-10-12-campus-asset-reference-costs.yaml`` is a
complete asset library whose costs are read from cited public sources (see the
research note beside it). It is a proposal, not the shipped library: the
investment case's defaults pack transcribes the shipped costs and its parity
test requires the two to agree, so adopting these needs a new pack version.

The tests hold the proposal to what it claims:

* it loads, and differs from the shipped library only in its costs, their
  notes and its price year (ratings and ids are the shipped ones);
* every cost is tagged, a `literature` cost names its source in its note and
  an `assumed` one says why;
* every cited capex is the source's raw figure, converted as the header says.
  The raw figures below were transcribed from the Ei sheet a second time, by
  hand, for this test: the oracle is the source, not the generator.
"""
import math
from pathlib import Path

import pytest

from gridspine.templates.campus_assets import (
    COST_SOURCES, DEFAULT_PATH, KINDS, REQUIRED, load_asset_library, value,
)

REPO = Path(__file__).resolve().parents[2]
PROPOSAL = REPO / "docs/superpowers/research/2026-10-12-campus-asset-reference-costs.yaml"
NOTE = REPO / "docs/superpowers/research/2026-10-12-campus-asset-reference-costs.md"
LIB = load_asset_library(PROPOSAL)
SHIPPED = load_asset_library()
HEADER = PROPOSAL.read_text().split("\ndiscount_rate:")[0]

COST_FIELDS = ("capex_eur", "capex_eur_per_km", "opex_frac", "lifetime_a")
KEYS = ("[EI-NVL]", "[DEA-TRANS]", "[DEA-DIST]", "[NEP-2021]")

#: 1 SEK of 2022 in EUR of 2025: ECB 2022 annual average SEK/EUR, then escalated 2022 -> 2025 by the
#: producer price index for C27.1 (electric motors, generators, transformers and electricity distribution
#: and control apparatus): Eurostat sts_inpp_a, euro area (EA20), total market, unadjusted, index
#: 2021=100, annual values (owner's choice of index, 2026-10-12).
SEK_PER_EUR_2022 = 10.6295750972763
PPI_2022, PPI_2025 = 108.6, 119.8


def eur(sek):
    return round(sek / SEK_PER_EUR_2022 * PPI_2025 / PPI_2022 / 100) * 100


def between(s, lo, y_lo, hi, y_hi):
    """Straight line in log-log through the two neighbouring sizes."""
    return y_lo * (s / lo) ** (math.log(y_hi / y_lo) / math.log(hi / lo))


#: Ei "Normvärdeslista elnät 2024-2027", sheet NVL RP4, column "Normvärde 2022 [SEK]": id -> SEK.
EI_DIRECT = {
    # transformers 123-145/36-52 kV (R-NR-T-5-x)
    "TR_132_33_20": 12632363, "TR_132_33_25": 13029601, "TR_132_33_40": 14006581, "TR_132_33_63": 15920189,
    "TR_132_33_80": 18114444, "TR_132_33_100": 21210929,
    # transformers 123-145/12-24 kV (R-NR-T-4-x)
    "TR_110_20_16": 7664165, "TR_110_20_20": 9056806, "TR_110_20_25": 9648928, "TR_110_20_40": 11692751,
    "TR_110_20_63": 14550720,
    # transformers 24-36/12 kV (R-NR-T-1-x)
    "TR_33_11_4": 3396373, "TR_33_11_6p3": 4131638, "TR_33_11_10": 4750625, "TR_33_11_16": 8260262,
    "TR_33_11_20": 7540068, "TR_33_11_25": 10940180,
    # transformer 24/0.4 kV 1000 kVA (L-NG-T-2-1)
    "TR_20_0p4_1p0": 252237,
    # cables, buried, rural normal ground, per km (NG146xx, L-NR-KL-x)
    "CB_11_AL95": 466377, "CB_11_AL150": 495118, "CB_11_AL240": 551762, "CB_11_AL400": 754518,
    "CB_20_AL95": 503516, "CB_20_AL150": 544063, "CB_20_AL240": 606267, "CB_20_AL400": 818922,
    "CB_33_AL150": 1287680, "CB_33_AL240": 1321465, "CB_33_AL400": 1514718,
    # switchgear: one breaker feeder bay + its control equipment (R-NR-SF-x-3 or -x-5, R-NR-KF-1-1 or -2-1)
    "SG_11_25p0": 311894 + 76187, "SG_20_25p0": 365156 + 76187, "SG_33_25p0": 431735 + 233524,
    "SG_110_31p5": 3077299 + 233524, "SG_132_31p5": 3077299 + 233524,
    # capacitor banks: base cost + per Mvar x Q (R-NR-KB-1-1/1-2 at 12-24 kV, R-NR-KB-2-1/2-2 at 36-52 kV)
    "CAP_11_1M": 281406 + 44385 * 1.0, "CAP_11_2p5M": 281406 + 44385 * 2.5, "CAP_11_5M": 281406 + 44385 * 5.0,
    "CAP_20_2M": 281406 + 44385 * 2.0, "CAP_20_5M": 281406 + 44385 * 5.0, "CAP_20_10M": 281406 + 44385 * 10.0,
    "CAP_33_5M": 401118 + 51042 * 5.0, "CAP_33_10M": 401118 + 51042 * 10.0, "CAP_33_20M": 401118 + 51042 * 20.0,
}
#: Sizes between two of the source's sizes: id -> (size, (lower size, SEK), (upper size, SEK)).
EI_BETWEEN = {
    "TR_132_33_31p5": (31.5, (30, 13438444), (40, 14006581)),
    "TR_132_33_50": (50, (40, 14006581), (63, 15920189)),
    "TR_110_20_31p5": (31.5, (30, 10502113), (40, 11692751)),
    "TR_110_20_50": (50, (40, 11692751), (63, 14550720)),
    "TR_20_0p4_0p4": (0.4, (0.315, 114525), (0.5, 148100)),
    "TR_20_0p4_0p63": (0.63, (0.5, 148100), (0.8, 217721)),
}
#: No usable source: kept or derived, tagged assumed.
ASSUMED_CAPEX = {"TR_33_11_2p5", "TR_33_11_31p5", "TR_33_11_40", "TR_20_0p4_1p6", "TR_20_0p4_2p5", "CB_33_AL95",
                 "SG_132_40p0", "SG_110_40p0", "SG_33_31p5", "SG_33_40p0", "SG_20_31p5", "SG_20_40p0",
                 "SG_11_31p5", "SG_11_40p0",
                 *(e["id"] for e in SHIPPED["shunt_reactors"]), *(e["id"] for e in SHIPPED["statcoms"])}


def entries(lib):
    return {e["id"]: (kind, e) for kind in KINDS for e in lib[kind]}


def capex(e):
    return e["capex_eur_per_km" if "capex_eur_per_km" in e else "capex_eur"]


# --------------------------------------------------------------------------
# what the file is
# --------------------------------------------------------------------------

def test_it_is_a_proposal_of_reference_values_not_quotes_and_not_the_shipped_library():
    head = HEADER.lower()
    assert "proposal, not the shipped library" in head
    assert "reference values, not quotes" in head
    assert PROPOSAL.resolve() != DEFAULT_PATH.resolve()
    assert NOTE.is_file()


def test_it_is_in_2025_euro_and_keeps_the_library_discount_rate():
    assert (LIB["currency"], LIB["price_year"]) == ("EUR", 2025)
    assert LIB["discount_rate"]["value"] == SHIPPED["discount_rate"]["value"]
    assert LIB["discount_rate"]["source"] == "assumed"


def test_the_same_entries_and_ratings_as_the_shipped_library_only_the_costs_differ():
    mine, theirs = entries(LIB), entries(SHIPPED)
    assert list(mine) == list(theirs)
    for eid, (kind, e) in mine.items():
        s = theirs[eid][1]
        for f in REQUIRED[kind]:
            if f not in COST_FIELDS:
                assert e[f] == s[f], (eid, f)


# --------------------------------------------------------------------------
# every cost tagged, every tag explained
# --------------------------------------------------------------------------

@pytest.mark.parametrize("lib", [LIB, SHIPPED], ids=["proposal", "shipped"])
def test_no_entry_has_a_cost_without_a_source_tag(lib):
    for eid, (kind, e) in entries(lib).items():
        for f in REQUIRED[kind]:
            if f in COST_FIELDS:
                assert isinstance(e[f], dict) and e[f].get("source") in COST_SOURCES, (eid, f)
                assert isinstance(value(e[f]), (int, float)), (eid, f)


def test_a_literature_cost_names_its_source_and_an_assumed_one_says_why():
    for eid, (kind, e) in entries(LIB).items():
        for f in COST_FIELDS:
            if f not in e:
                continue
            t = e[f]
            if t["source"] == "literature":
                assert any(k in t["note"] for k in KEYS), (eid, f, t)
            else:
                assert t["source"] == "assumed" and len(t.get("note", "")) > 20, (eid, f, t)


def test_every_citation_key_is_listed_with_its_url_in_the_header():
    for k in KEYS:
        i = HEADER.index("#   " + k)
        assert "https://" in HEADER[i:i + 600], k
    used = {k for _, e in entries(LIB).values() for f in COST_FIELDS if f in e for k in KEYS if k in e[f].get("note", "")}
    assert used == set(KEYS)


def test_only_costs_are_tagged_literature():
    for eid, (kind, e) in entries(LIB).items():
        for f in REQUIRED[kind]:
            if f not in COST_FIELDS:
                assert e[f]["source"] == "assumed", (eid, f)


# --------------------------------------------------------------------------
# every cited number is the source's, converted as stated
# --------------------------------------------------------------------------

def test_the_conversion_factor_is_the_one_the_header_states():
    assert 1 / SEK_PER_EUR_2022 * PPI_2025 / PPI_2022 == pytest.approx(0.10378, abs=5e-6)
    for stated in ("0.10378", "10.6296", "sts_inpp_a", "C27.1", "EA20", "108.6", "119.8"):
        assert stated in HEADER, stated
    assert "HICP" not in HEADER.split("Conversion.")[1].split("Rounded")[0]


@pytest.mark.parametrize("eid", sorted(EI_DIRECT))
def test_a_cited_capex_is_the_ei_figure_converted(eid):
    t = capex(entries(LIB)[eid][1])
    assert t["source"] == "literature" and "[EI-NVL]" in t["note"]
    assert t["value"] == eur(EI_DIRECT[eid])


@pytest.mark.parametrize("eid", sorted(EI_BETWEEN))
def test_a_size_between_two_ei_sizes_is_interpolated_in_log_log(eid):
    s, (lo, y_lo), (hi, y_hi) = EI_BETWEEN[eid]
    t = capex(entries(LIB)[eid][1])
    assert t["source"] == "literature" and "interpolation" in t["note"]
    assert t["value"] == eur(between(s, lo, y_lo, hi, y_hi))
    assert eur(y_lo) <= t["value"] <= eur(y_hi)


def test_the_capex_tags_are_exactly_cited_interpolated_or_assumed():
    tags = {eid: capex(e)["source"] for eid, (_, e) in entries(LIB).items()}
    assert {e for e, s in tags.items() if s == "literature"} == set(EI_DIRECT) | set(EI_BETWEEN)
    assert {e for e, s in tags.items() if s == "assumed"} == ASSUMED_CAPEX


def test_a_reactor_and_a_statcom_keep_the_shipped_capex_for_want_of_a_source():
    for kind in ("shunt_reactors", "statcoms"):
        for e in LIB[kind]:
            s = entries(SHIPPED)[e["id"]][1]
            assert e["capex_eur"]["value"] == s["capex_eur"]["value"]
            assert "no source found" in e["capex_eur"]["note"]


def test_a_higher_short_circuit_rating_keeps_the_shipped_ratio_over_the_cited_bay():
    lib, old = entries(LIB), entries(SHIPPED)
    for kv, base in ((132, "SG_132_31p5"), (110, "SG_110_31p5"), (33, "SG_33_25p0"), (20, "SG_20_25p0"),
                     (11, "SG_11_25p0")):
        for eid, (kind, e) in lib.items():
            if kind == "switchgear" and value(e["vn_kv"]) == kv and eid != base:
                ratio = value(old[eid][1]["capex_eur"]) / value(old[base][1]["capex_eur"])
                assert e["capex_eur"]["value"] == round(value(lib[base][1]["capex_eur"]) * ratio / 100) * 100


@pytest.mark.parametrize("kind, opex, key", [
    ("cables", 0.0051, "[DEA-DIST]"), ("capacitor_banks", 0.015, "[DEA-TRANS]"),
    ("shunt_reactors", 0.015, "[DEA-TRANS]"), ("switchgear", 0.015, "[DEA-TRANS]")])
def test_the_fixed_om_share_is_the_cited_one(kind, opex, key):
    for e in LIB[kind]:
        assert e["opex_frac"] == {**e["opex_frac"], "value": opex, "source": "literature"} and key in e["opex_frac"]["note"]


def test_a_distribution_transformer_has_the_distribution_om_and_the_others_the_substation_om():
    for e in LIB["transformers"]:
        dist = value(e["hv_kv"]) == 20.0
        assert e["opex_frac"]["value"] == (0.0051 if dist else 0.015), e["id"]
        assert ("[DEA-DIST]" if dist else "[DEA-TRANS]") in e["opex_frac"]["note"]


def test_the_cited_lifetime_is_forty_years_for_transformers_cables_and_switchgear():
    for kind in ("transformers", "cables", "switchgear"):
        for e in LIB[kind]:
            assert e["lifetime_a"]["value"] == 40 and e["lifetime_a"]["source"] == "literature", e["id"]
            assert "[DEA-DIST]" in e["lifetime_a"]["note"]
    for kind in ("capacitor_banks", "shunt_reactors", "statcoms"):
        for e in LIB[kind]:
            assert e["lifetime_a"]["source"] == "assumed", e["id"]
            assert e["lifetime_a"]["value"] == entries(SHIPPED)[e["id"]][1]["lifetime_a"]["value"]

