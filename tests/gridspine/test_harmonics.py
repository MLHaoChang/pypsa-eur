"""The harmonic-distortion screen (grid-to-process issue 17, PR 1)."""
import math

import pandas as pd
import pytest

from gridspine.schema.contracts import ContractError
from gridspine.static import harmonics as hm

#: The demo planning levels the tests judge against (IEC 61000-3-6 HV, assumed).
LIMITS = hm.Limits(thd_pct=3.0, orders_pct={5: 2.0, 7: 2.0, 11: 1.5, 13: 1.5, 17: 1.7, 19: 1.5,
                                             23: 1.2, 25: 1.09})


def _units(*rows):
    cols = ["unit", "poc", "rated_mva", "harmonic_source", "harmonic_spectrum", "post_filter"]
    return pd.DataFrame([dict(zip(cols, r)) for r in rows], columns=cols)


def _one(topology, mva=15.0, sk=1000.0, spectrum="", limits=LIMITS, law="iec"):
    return hm.harmonic_table(_units(("U1", "PoC", mva, topology, spectrum, False)), {"PoC": sk},
                             limits, law=law)


# --- the default spectra, pinned (all demo) ------------------------------------

PINNED = {
    "linear": {},
    "burst": {},
    "6-pulse": {5: 30.0, 7: 12.0, 11: 7.0, 13: 5.0, 17: 3.0, 19: 2.5, 23: 2.5, 25: 2.0},
    "12-pulse": {5: 3.0, 7: 1.5, 11: 7.0, 13: 5.0, 23: 2.5, 25: 2.0},
    "18-pulse": {5: 1.5, 7: 1.0, 11: 1.0, 13: 0.8, 17: 3.0, 19: 2.5, 23: 0.5, 25: 0.5,
                 35: 1.2, 37: 1.0},
    "active-front-end": {5: 1.5, 7: 1.0, 11: 0.8, 13: 0.6, 17: 0.4, 19: 0.3, 23: 0.3, 25: 0.3,
                         29: 0.2, 31: 0.2},
    "phase-angle": {5: 25.0, 7: 12.0, 11: 8.0, 13: 6.0, 17: 4.0, 19: 3.0, 23: 2.5, 25: 2.0},
    "thyristor-stack": {5: 25.0, 7: 12.0, 11: 8.0, 13: 6.0, 17: 4.0, 19: 3.0, 23: 2.5, 25: 2.0},
}


@pytest.mark.parametrize("topology", sorted(PINNED))
def test_default_spectrum_is_pinned_and_labelled_demo(topology):
    assert hm.DEFAULT_SPECTRA[topology] == PINNED[topology]
    spectrum, source = hm.unit_spectrum(topology, None)
    assert spectrum == PINNED[topology]
    assert source == ("declared linear" if topology == "linear" else f"demo default ({topology})")


def test_every_topology_has_a_pinned_default():
    assert set(hm.DEFAULT_SPECTRA) == set(hm.TOPOLOGIES) == set(PINNED)


# --- acceptance --------------------------------------------------------------

def test_twelve_pulse_passes_where_six_pulse_fails():
    six = _one("6-pulse").units.iloc[0]
    twelve = _one("12-pulse").units.iloc[0]
    assert six["verdict"] == "fail"
    assert twelve["verdict"] == "pass"
    assert 5 in six["failing_orders"]


def test_doubling_the_fault_level_halves_every_order():
    a = _one("6-pulse", sk=800.0).units.iloc[0]
    b = _one("6-pulse", sk=1600.0).units.iloc[0]
    assert set(a["u_h_pct"]) == set(b["u_h_pct"])
    for h, u in a["u_h_pct"].items():
        assert b["u_h_pct"][h] == pytest.approx(u / 2, rel=1e-9)
    assert b["thd_pct"] == pytest.approx(a["thd_pct"] / 2, rel=1e-9)


def test_no_spectrum_uses_the_topology_default_and_says_so():
    row = _one("12-pulse").units.iloc[0]
    assert row["spectrum_source"] == "demo default (12-pulse)"
    assert "demo default (12-pulse)" in row["note"]


def test_vendor_spectrum_overrides_the_default():
    row = _one("6-pulse", spectrum="5: 1.0, 7: 0.5").units.iloc[0]
    assert row["spectrum_source"] == "vendor"
    assert set(row["u_h_pct"]) == {5, 7}
    assert row["u_h_pct"][5] == pytest.approx(5 * 1.0 * 15.0 / 1000.0, rel=1e-12)


def test_post_filter_flag_is_carried_and_said():
    t = hm.harmonic_table(_units(("U1", "PoC", 15.0, "6-pulse", "5: 1.0", True)), {"PoC": 1000.0}, LIMITS)
    row = t.units.iloc[0]
    assert bool(row["post_filter"]) is True
    assert "post-filter" in row["note"]


@pytest.mark.parametrize("mva", [None, math.nan])
def test_missing_rated_mva_is_no_input_never_skipped(mva):
    t = hm.harmonic_table(_units(("U1", "PoC", mva, "6-pulse", "", False)), {"PoC": 1000.0}, LIMITS)
    assert list(t.units["unit"]) == ["U1"]
    row = t.units.iloc[0]
    assert row["verdict"] == "unknown"
    assert row["note"].startswith("no input")
    assert "rated_mva" in row["note"]


def test_unset_source_and_no_spectrum_is_no_input():
    row = _one("").units.iloc[0]
    assert row["verdict"] == "unknown"
    assert row["note"].startswith("no input")


def test_linear_is_declared_no_emission():
    row = _one("linear").units.iloc[0]
    assert row["verdict"] == "pass"
    assert row["thd_pct"] == 0.0


# --- the summation law -----------------------------------------------------------

@pytest.mark.parametrize("h, expected", [(4, 1.0), (5, 1.4), (10, 1.4), (11, 2.0)])
def test_alpha_boundaries(h, expected):
    assert hm.alpha(h, "iec") == expected


@pytest.mark.parametrize("h", [2, 4, 5, 10, 11, 25, 50])
def test_arithmetic_is_alpha_one_everywhere(h):
    assert hm.alpha(h, "arithmetic") == 1.0


def test_summation_law_at_the_poc():
    spec = "4: 10, 5: 10, 11: 10"
    units = _units(("A", "PoC", 10.0, "", spec, False), ("B", "PoC", 10.0, "", spec, False))
    iec = hm.harmonic_table(units, {"PoC": 1000.0}, LIMITS, law="iec").pocs.iloc[0]["u_h_pct"]
    ari = hm.harmonic_table(units, {"PoC": 1000.0}, LIMITS, law="arithmetic").pocs.iloc[0]["u_h_pct"]
    single = 10.0 * 10.0 / 1000.0     # pct/100 * MVA / S_k * 100, times h below
    assert ari[4] == pytest.approx(4 * 2 * single, rel=1e-12)
    assert iec[4] == pytest.approx(ari[4], rel=1e-12)                       # alpha 1 below 5
    assert iec[5] == pytest.approx(5 * 2 ** (1 / 1.4) * single, rel=1e-12)
    assert iec[11] == pytest.approx(11 * math.sqrt(2) * single, rel=1e-12)
    assert ari[11] == pytest.approx(11 * 2 * single, rel=1e-12)


def test_bad_law_is_refused():
    with pytest.raises(ContractError, match="law"):
        _one("6-pulse", law="rms")


# --- limits -------------------------------------------------------------------------

def test_no_limits_is_unknown_with_the_value_shown():
    row = _one("6-pulse", limits=hm.Limits()).units.iloc[0]
    assert row["verdict"] == "unknown"
    assert row["thd_pct"] > 0


def test_thd_only_judges_on_thd():
    assert _one("12-pulse", limits=hm.Limits(thd_pct=3.0)).units.iloc[0]["verdict"] == "pass"
    assert _one("6-pulse", limits=hm.Limits(thd_pct=3.0)).units.iloc[0]["verdict"] == "fail"


def test_orders_only_judges_on_orders():
    row = _one("6-pulse", limits=hm.Limits(orders_pct={5: 2.0})).units.iloc[0]
    assert row["verdict"] == "fail"
    assert row["failing_orders"] == [5]
    assert row["worst_order"] == 5
    assert "order 5" in row["note"]


# --- the PoC aggregate ------------------------------------------------------------

def test_no_input_unit_blocks_a_pass_at_the_poc():
    t = hm.harmonic_table(_units(("A", "PoC", 5.0, "12-pulse", "", False),
                                 ("B", "PoC", None, "6-pulse", "", False)), {"PoC": 1000.0}, LIMITS)
    poc = t.pocs.iloc[0]
    assert poc["verdict"] == "unknown"
    assert poc["no_input_units"] == ["B"]
    assert "B" in poc["note"]
    assert list(t.units["unit"]) == ["A", "B"]


def test_known_units_that_fail_fail_the_poc_despite_a_missing_one():
    t = hm.harmonic_table(_units(("A", "PoC", 15.0, "6-pulse", "", False),
                                 ("B", "PoC", None, "6-pulse", "", False)), {"PoC": 1000.0}, LIMITS)
    assert t.pocs.iloc[0]["verdict"] == "fail"


def test_no_fault_level_is_unknown():
    t = hm.harmonic_table(_units(("A", "PoC", 15.0, "6-pulse", "", False)), {}, LIMITS)
    assert t.units.iloc[0]["verdict"] == "unknown"
    assert t.pocs.iloc[0]["verdict"] == "unknown"
    assert "short-circuit" in t.pocs.iloc[0]["note"]


# --- spectrum validation ---------------------------------------------------------

@pytest.mark.parametrize("text, match", [
    ("5:20, 5:10", "order 5 appears twice"),
    ("1:20", "order 1 is below 2"),
    ("51:1", "order 51 is above 50"),
    ("5.5:1", "'5.5' is not a whole number"),
    ("5:-1", "0 or more"),
    ("5:nan", "0 or more"),
    ("5", "order:%"),
    ("five:1", "'five' is not a whole number"),
])
def test_bad_spectrum_is_refused(text, match):
    """UX: a refusal starts capitalised and names the field by its label."""
    with pytest.raises(ContractError, match=r"^Harmonic spectrum: .*" + match):
        hm.parse_spectrum(text)


def test_spectrum_parses():
    assert hm.parse_spectrum(" 5: 20, 7:14.5 ") == {5: 20.0, 7: 14.5}
    assert hm.parse_spectrum("") == {}


def test_unknown_topology_is_refused():
    with pytest.raises(ContractError, match="^Harmonic source: '24-pulse' is not one of linear, 6-pulse"):
        _one("24-pulse")
