"""IEEE 519-2014 current TDD at the PCC (datacenter readiness 12, part 1)."""
import math

import pandas as pd
import pytest

from gridspine.schema.contracts import ContractError
from gridspine.static import current_tdd as td
from gridspine.static import harmonics as hm

STD = "ieee519-2014"


def _units(*rows, generation=None):
    cols = ["unit", "poc", "rated_mva", "harmonic_source", "harmonic_spectrum"]
    df = pd.DataFrame([dict(zip(cols, r)) for r in rows], columns=cols)
    if generation is not None:
        df["generation"] = generation
    return df


def _one(spectrum="5: 20, 7: 10", mva=10.0, i_l=20.0, sk=1000.0, kv=33.0, standard=STD, **kw):
    units = _units(("U1", "PCC", mva, "", spectrum), **kw)
    return td.tdd_table(units, {"PCC": sk}, {"PCC": i_l}, {"PCC": kv}, standard).iloc[0]


# --- the hand calculation -------------------------------------------------------

def test_tdd_matches_a_hand_calculation():
    # 10 MVA at 20 % and 10 % of rated current on a 20 MVA I_L: I_5 = 10 %, I_7 = 5 % of I_L.
    row = _one()
    assert row["i_h_pct"] == pytest.approx({5: 10.0, 7: 5.0}, rel=1e-12)
    assert row["tdd_pct"] == pytest.approx(math.sqrt(125.0), rel=1e-12)
    # Isc/IL = 1000 / 20 = 50: on the 50 boundary, so the stricter 20-50 row (TDD 8 %, odd 3..11 7 %).
    assert row["isc_il"] == pytest.approx(50.0) and row["band"] == "20-50" and row["table"] == "Table 2"
    assert row["tdd_limit_pct"] == 8.0 and row["tdd_margin_pct"] == pytest.approx(8.0 - math.sqrt(125.0))
    assert row["failing_orders"] == [5] and row["worst_order"] == 5 and row["worst_limit_pct"] == 7.0
    assert row["verdict"] == "fail"


def test_a_small_distortion_passes_and_states_its_assumptions():
    row = _one(spectrum="5: 2, 7: 1")
    assert row["verdict"] == "pass" and row["failing_orders"] == []
    assert td.STRICTER in row["note"] and td.I_L_NOTE in row["note"]
    assert row["label"] == td.LABEL


def test_two_units_sum_with_the_harmonic_screens_law():
    units = _units(("A", "PCC", 10.0, "", "5: 20"), ("B", "PCC", 10.0, "", "5: 20"))
    row = td.tdd_table(units, {"PCC": 1000.0}, {"PCC": 20.0}, {"PCC": 33.0}, STD).iloc[0]
    i5 = hm._i_h([(10.0, {5: 20.0}), (10.0, {5: 20.0})], "iec")[5]
    assert i5 == pytest.approx(2.0 * 2 ** (1 / 1.4))
    assert row["i_h_pct"][5] == pytest.approx(100.0 * i5 / 20.0)


def test_u_h_is_the_shared_current_through_the_fault_level():
    contribs = [(15.0, hm.DEFAULT_SPECTRA["6-pulse"]), (5.0, {5: 4.0, 11: 1.0})]
    i_h = hm._i_h(contribs, "iec")
    assert hm._u_h(contribs, 800.0, "iec") == {h: 100.0 * h * i / 800.0 for h, i in i_h.items()}


# --- the band: a ratio on a boundary takes the stricter row ---------------------

@pytest.mark.parametrize("bound, below, above", [
    (20.0, "<= 20", "20-50"), (50.0, "20-50", "50-100"), (100.0, "50-100", "100-1000"),
    (1000.0, "100-1000", "> 1000"),
])
def test_table_2_boundaries_from_both_sides(bound, below, above):
    assert _one(sk=20.0 * bound)["band"] == below
    assert _one(sk=20.0 * bound * (1 + 1e-9))["band"] == above


@pytest.mark.parametrize("ratio, band, tdd", [(25.0, "<= 25", 1.5), (25.0001, "25-50", 2.5),
                                              (50.0, "25-50", 2.5), (50.0001, "> 50", 3.75)])
def test_table_4_boundaries(ratio, band, tdd):
    row = _one(sk=20.0 * ratio, kv=220.0)
    assert (row["table"], row["band"], row["tdd_limit_pct"]) == ("Table 4", band, tdd)


@pytest.mark.parametrize("kv, table", [(69.0, "Table 2"), (69.001, "Table 3"), (161.0, "Table 3"),
                                       (161.001, "Table 4"), (0.4, "Table 2")])
def test_the_table_follows_the_pcc_voltage(kv, table):
    assert _one(kv=kv)["table"] == table


def test_table_3_is_about_half_of_table_2():
    assert [b[2] for b in td.TABLES[1][3]] == [2.5, 4.0, 6.0, 7.5, 10.0]


# --- the order groups ------------------------------------------------------------

@pytest.mark.parametrize("h, limit", [(2, 1.0), (3, 4.0), (10, 1.0), (11, 2.0), (17, 1.5), (23, 0.6),
                                      (34, 0.15), (35, 0.3), (49, 0.3), (50, 0.075), (1, None), (51, None)])
def test_order_groups_are_half_open_and_even_orders_take_a_quarter(h, limit):
    lowest = td.TABLES[0][3][0][1]                      # Table 2, Isc/IL <= 20
    got = td.order_limit(h, lowest)
    assert got == (None if limit is None else pytest.approx(limit))


def test_an_even_order_is_judged_on_its_quarter_limit():
    # Isc/IL 10: lowest row; order 10 limit 1 %. 10 MVA at 2.2 % on 20 MVA I_L is 1.1 % of I_L.
    row = _one(spectrum="10: 2.2", sk=200.0)
    assert row["failing_orders"] == [10] and row["worst_limit_pct"] == 1.0


# --- generation equipment --------------------------------------------------------

def test_generation_equipment_takes_the_lowest_row_with_a_note():
    row = _one(sk=20.0 * 500, generation=[True])             # Isc/IL 500 would be 100-1000
    assert row["band"] == "<= 20" and row["tdd_limit_pct"] == 5.0
    assert row["generation_units"] == ["U1"] and "generation equipment (U1)" in row["note"]
    assert _one(sk=20.0 * 500, generation=[math.nan])["band"] == "100-1000"


# --- unknown and refused -----------------------------------------------------------

def test_no_standard_is_unknown():
    row = _one(standard=None)
    assert row["verdict"] == "unknown" and row["tdd_pct"] is None
    assert "names no current-distortion standard" in row["note"]


@pytest.mark.parametrize("kw, note", [({"i_l": None}, "I_L is unknown"), ({"i_l": 0.0}, "I_L is unknown"),
                                      ({"sk": math.nan}, hm.NO_SK), ({"kv": None}, "No voltage")])
def test_a_missing_input_is_unknown(kw, note):
    row = _one(**kw)
    assert row["verdict"] == "unknown" and note in row["note"]


def test_a_unit_without_input_blocks_a_pass():
    units = _units(("A", "PCC", 10.0, "", "5: 1"), ("B", "PCC", None, "6-pulse", ""))
    row = td.tdd_table(units, {"PCC": 1000.0}, {"PCC": 20.0}, {"PCC": 33.0}, STD).iloc[0]
    assert row["no_input_units"] == ["B"] and row["verdict"] == "unknown"
    assert "a missing unit blocks a pass" in row["note"]


def test_an_unknown_standard_is_refused():
    with pytest.raises(ContractError, match="not one of ieee519-2014"):
        _one(standard="iec61000-3-12")


def test_the_profile_names_the_standard():
    std, cited = td.standard_from_profile({"current_tdd_standard": {"value": STD, "clause": "c", "source": "assumed"}})
    assert std == STD and cited == [{"key": "current_tdd_standard", "clause": "c", "source": "assumed"}]
    assert td.standard_from_profile({}) == (None, [])
