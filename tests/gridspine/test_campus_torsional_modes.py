# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
A genset's torsional modes in the campus description (grid-compliance GC-01c).

A genset may carry ``torsional_modes_hz``: a non-empty list of ``{value,
source}`` frequencies in (0, f0) Hz. They land on ``Campus.torsional_modes``
and GC-08's campus step passes them to the sub-synchronous scan, which judges
each series resonance's complement f0 - f_er against them.
"""

import math

import pandas as pd
import pytest

from gridspine.ingest.campus import build_campus
from gridspine.schema.contracts import ContractError
from gridspine.schema.model_set import data_hash
from tests.gridspine.test_campus import campus_spec, t
from tests.gridspine.test_campus_series_capacitors import X_TOTAL, cap_spec

XC = 0.5  # ohm at f0; f_er = 50 * sqrt(XC / X_total): max 40.945 Hz, min 39.572 Hz


def _modes(spec, modes, unit="GEN1"):
    spec["campus"]["units"][unit]["torsional_modes_hz"] = modes
    return spec


def _refused(spec, match):
    with pytest.raises(ContractError, match=match):
        build_campus(spec)


def _comp(case):
    """The hand-computed complement f0 - f_er of the capacitor's resonance in ``case``."""
    return 50.0 - 50.0 * math.sqrt(XC / X_TOTAL[case])


def _screens(tmp_path, monkeypatch, spec, clearance=None):
    from gridspine.drivers import campus_study as cs

    campus = build_campus(spec)
    monkeypatch.setattr(cs, "load_run_campus", lambda run_dir: campus)
    if clearance is not None:
        code = {"subsync_torsional_clearance_hz": {"value": clearance, "clause": "test", "source": "code"}}
        monkeypatch.setattr(cs, "load_grid_code", lambda *a, **kw: code)
    out = cs.ssti_screens_campus(tmp_path)
    res = pd.read_csv(tmp_path / cs.SUBSYNC_RESONANCE_CSV)
    from gridspine.static import subsync_scan as ss

    return out, res[res["kind"] == ss.SERIES].set_index("case")


# --- the build ---------------------------------------------------------------


def test_a_gensets_modes_land_on_the_campus_and_in_the_tagged_values():
    campus = build_campus(_modes(campus_spec(), [t(12.5, "datasheet"), t(31.0)]))
    assert campus.torsional_modes == {"GEN1": [12.5, 31.0]}
    p = campus.params[(campus.params["element"] == "GEN1") & (campus.params["param"] == "torsional_mode_hz")]
    assert list(p["value"]) == [12.5, 31.0]
    assert list(p["source"]) == ["datasheet", "assumed"]


def test_a_campus_without_modes_builds_unchanged():
    spec = campus_spec()
    campus = build_campus(spec)
    assert campus.torsional_modes == {}
    assert "torsional_mode_hz" not in set(campus.params["param"])
    assert data_hash(spec) == data_hash(campus_spec())


def test_a_60_hz_campus_takes_modes_up_to_its_own_f0():
    spec = _modes(campus_spec(), [t(55.0)])
    spec["campus"]["f_hz"] = 60
    assert build_campus(spec).torsional_modes == {"GEN1": [55.0]}
    _refused(_modes(campus_spec(), [t(55.0)]), r"units\.GEN1\.torsional_modes_hz\[0\].*\(0, 50")


@pytest.mark.parametrize(
    "value",
    [-3.0, 0.0, 0, 50.0, 50, 61.0, math.nan, math.inf, -math.inf],
)
def test_a_mode_outside_zero_to_f0_or_not_finite_is_refused(value):
    _refused(_modes(campus_spec(), [t(12.0), t(value)]), r"units\.GEN1\.torsional_modes_hz\[1\]")


@pytest.mark.parametrize("value", [True, "12", None, [12.0]])
def test_a_mode_that_is_not_a_number_is_refused(value):
    _refused(_modes(campus_spec(), [t(value)]), r"units\.GEN1\.torsional_modes_hz\[0\].*number")


@pytest.mark.parametrize(
    "modes",
    [t(12.0), 12.0, "12", {"a": t(12.0)}, (t(12.0),), None],
)
def test_modes_that_are_not_a_list_are_refused(modes):
    _refused(_modes(campus_spec(), modes), r"units\.GEN1\.torsional_modes_hz.*list")


def test_an_empty_list_is_refused():
    _refused(_modes(campus_spec(), []), r"units\.GEN1\.torsional_modes_hz.*at least one")


@pytest.mark.parametrize(
    "entry, match",
    [
        (12.0, r"\[0\].*'value' and 'source'"),
        ({"value": 12.0}, r"\[0\].*'value' and 'source'"),
        ({"value": 12.0, "source": "code"}, r"\[0\].*unknown source 'code'"),
        ({"value": 12.0, "source": "assumed", "note": "x"}, r"\[0\].*'value' and 'source'"),
    ],
)
def test_an_untagged_or_mistagged_mode_is_refused(entry, match):
    _refused(_modes(campus_spec(), [entry]), r"units\.GEN1\.torsional_modes_hz" + match)


def test_a_unit_that_is_not_a_genset_may_not_carry_modes():
    spec = campus_spec()
    other = next(n for n, u in spec["campus"]["units"].items() if u["kind"] != "genset")
    _refused(_modes(spec, [t(12.0)], unit=other), rf"units\.{other}: unknown field\(s\) \['torsional_modes_hz'\]")


# --- the scan judges the capacitor's resonance against them ----------------------


def test_a_mode_near_the_complement_flags_with_the_hand_computed_margin(tmp_path, monkeypatch):
    out, series = _screens(tmp_path, monkeypatch, _modes(cap_spec(xc_ohm=t(XC)), [t(9.5, "datasheet")]))
    for case in X_TOTAL:  # margins: max 0.445 Hz, min 0.928 Hz, both below the 1 Hz default
        row = series.loc[case]
        assert row["f_comp_hz"] == pytest.approx(_comp(case), abs=0.01)
        assert row["margin_hz"] == pytest.approx(abs(_comp(case) - 9.5), abs=0.01)
        assert (row["machine"], row["mode_hz"], row["verdict"]) == ("GEN1", 9.5, "flag")
    assert out.subsync_verdict == "flag"


def test_a_mode_clear_of_the_complement_reads_clear(tmp_path, monkeypatch):
    out, series = _screens(tmp_path, monkeypatch, _modes(cap_spec(xc_ohm=t(XC)), [t(12.0), t(30.0)]))
    for case in X_TOTAL:  # margins to 12 Hz: max 2.945 Hz, min 1.572 Hz
        assert series.at[case, "margin_hz"] == pytest.approx(abs(_comp(case) - 12.0), abs=0.01)
        assert series.at[case, "mode_hz"] == 12.0
    assert set(series["verdict"]) == {"clear"}
    assert out.subsync_verdict == "clear"


def test_the_clearance_splits_flag_below_from_clear_at_or_above(tmp_path, monkeypatch):
    spec = _modes(cap_spec(xc_ohm=t(XC)), [t(9.5)])
    # 0.7 Hz lies between the max case's 0.445 Hz and the min case's 0.928 Hz
    out, series = _screens(tmp_path / "a", monkeypatch, spec, clearance=0.7)
    assert (series.at["max", "verdict"], series.at["min", "verdict"]) == ("flag", "clear")
    assert out.subsync_verdict == "flag"
    # a clearance equal to the min case's own margin reads clear there
    at = float(series.at["min", "margin_hz"])
    _, series = _screens(tmp_path / "b", monkeypatch, spec, clearance=at)
    assert series.at["min", "verdict"] == "clear"


def test_modes_of_several_gensets_take_the_nearest(tmp_path, monkeypatch):
    spec = _modes(cap_spec(xc_ohm=t(XC)), [t(20.0)])
    units = spec["campus"]["units"]
    units["GEN2"] = {**units["GEN1"], "torsional_modes_hz": [t(9.0)]}
    _, series = _screens(tmp_path, monkeypatch, spec)
    assert set(series["machine"]) == {"GEN2"}
    assert series.at["max", "margin_hz"] == pytest.approx(abs(_comp("max") - 9.0), abs=0.01)


def test_without_modes_the_resonance_still_reads_check(tmp_path, monkeypatch):
    out, series = _screens(tmp_path, monkeypatch, cap_spec(xc_ohm=t(XC)))
    assert set(series["verdict"]) == {"check"}
    assert out.subsync_verdict == "check"


def test_modes_without_a_capacitor_still_read_not_evaluated(tmp_path, monkeypatch):
    out, series = _screens(tmp_path, monkeypatch, _modes(campus_spec(), [t(12.0)]))
    assert set(series["verdict"]) == {"not evaluated"}
    assert out.subsync_verdict == "not evaluated"
