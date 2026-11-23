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


#: ``data_hash(campus_spec(), {})``, recorded on master 46f3bdc7 before GC-01c; it is also the detailed
#: variant's model hash in ``freeze(campus_spec(), {}, aggregated=False)``.
MASTER_HASH = "ab1046752ec5787004fa70665a76b4782797ffbc42acc7a3092df2c3fc9457fb"


def test_a_campus_without_modes_builds_unchanged():
    campus = build_campus(campus_spec())
    assert campus.torsional_modes == {} and campus.torsional_mode_sources == {}
    assert "torsional_mode_hz" not in set(campus.params["param"])


def test_the_freeze_and_model_hashes_without_modes_are_masters_and_modes_move_them():
    import datetime as dt

    from gridspine.ingest.aggregate import freeze

    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    ms = freeze(campus_spec(), {}, aggregated=False, now=now)
    assert ms.data_hash == MASTER_HASH
    assert dict(ms.model_hashes) == {"detailed": MASTER_HASH, "aggregated": None}
    moded = freeze(_modes(campus_spec(), [t(12.0)]), {}, aggregated=False, now=now)
    assert moded.data_hash != MASTER_HASH
    assert moded.model_hashes["detailed"] == moded.data_hash
    other = data_hash(_modes(campus_spec(), [t(12.0, "datasheet")]), {})
    assert other not in (MASTER_HASH, moded.data_hash)


def _at_60(modes):
    spec = _modes(campus_spec(), modes)
    spec["campus"]["f_hz"] = 60
    return spec


def test_a_60_hz_campus_takes_modes_below_its_own_f0_and_refuses_f0():
    assert build_campus(_at_60([t(55.0), t(59.9)])).torsional_modes == {"GEN1": [55.0, 59.9]}
    _refused(_at_60([t(60.0)]), r"^units\.GEN1\.torsional_modes_hz\[0\] must be a finite frequency in \(0, 60\) Hz, got 60\.0$")
    _refused(_modes(campus_spec(), [t(55.0)]), r"^units\.GEN1\.torsional_modes_hz\[0\] must be a finite frequency in \(0, 50\) Hz")


@pytest.mark.parametrize(
    "value",
    [-3.0, 0.0, 0, 50.0, 50, 61.0, math.nan, math.inf, -math.inf],
)
def test_a_mode_outside_zero_to_f0_or_not_finite_is_refused(value):
    _refused(_modes(campus_spec(), [t(12.0), t(value)]),
             r"^units\.GEN1\.torsional_modes_hz\[1\] must be a finite frequency in \(0, 50\) Hz")


@pytest.mark.parametrize("value", [True, "12", None, [12.0]])
def test_a_mode_that_is_not_a_number_is_refused(value):
    _refused(_modes(campus_spec(), [t(value)]), r"^units\.GEN1\.torsional_modes_hz\[0\] value must be a number")


@pytest.mark.parametrize(
    "modes",
    [t(12.0), 12.0, "12", {"a": t(12.0)}, (t(12.0),), None],
)
def test_modes_that_are_not_a_list_are_refused(modes):
    _refused(_modes(campus_spec(), modes), r"^units\.GEN1\.torsional_modes_hz: must be a list")


def test_an_empty_list_is_refused():
    _refused(_modes(campus_spec(), []), r"^units\.GEN1\.torsional_modes_hz: give at least one mode")


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
    _refused(_modes(campus_spec(), [entry]), r"^units\.GEN1\.torsional_modes_hz" + match)


def test_a_unit_that_is_not_a_genset_may_not_carry_modes():
    spec = campus_spec()
    other = next(n for n, u in spec["campus"]["units"].items() if u["kind"] != "genset")
    _refused(_modes(spec, [t(12.0)], unit=other), rf"units\.{other}: unknown field\(s\) \['torsional_modes_hz'\]")


# --- the scan judgesthe capacitor's resonance against them ----------------------
#
# Hand calculation (GC-01b's X_TOTAL): f0 - f_er = 50 - 50 * sqrt(0.5 / X_total) is 9.054943536 Hz in the max
# case and 10.428075202 Hz in the min case. The scan interpolates f_er linearly between 0.1 Hz points and X_TOTAL
# is rounded to 1e-6 ohm, so a scanned margin sits within 2e-4 Hz of these literals; the exact boundary is pinned
# below with f_er itself a literal.
COMP = {"max": 9.054943536, "min": 10.428075202}
SCAN_TOL = 2e-4


def _gen2(spec, modes=None):
    units = spec["campus"]["units"]
    units["GEN2"] = {k: v for k, v in units["GEN1"].items() if k != "torsional_modes_hz"}
    if modes is not None:
        units["GEN2"]["torsional_modes_hz"] = modes
    return spec


def test_the_hand_computed_complement_is_the_scans():
    for case, x_total in X_TOTAL.items():
        assert _comp(case) == pytest.approx(COMP[case], abs=1e-9)


def test_a_mode_near_the_complement_flags_with_the_hand_computed_margin(tmp_path, monkeypatch):
    out, series = _screens(tmp_path, monkeypatch, _modes(cap_spec(xc_ohm=t(XC)), [t(9.5, "datasheet")]))
    margins = {"max": 0.445056464, "min": 0.928075202}  # both below the 1 Hz default clearance
    for case, margin in margins.items():
        row = series.loc[case]
        assert row["f_comp_hz"] == pytest.approx(COMP[case], abs=SCAN_TOL)
        assert row["margin_hz"] == pytest.approx(margin, abs=SCAN_TOL)
        assert (row["bus"], row["machine"], row["mode_hz"], row["mode_source"], row["verdict"]) == (
            "MV1C", "GEN1", 9.5, "datasheet", "flag")
    assert out.subsync_verdict == "flag"


def test_a_mode_clear_of_the_complement_reads_clear(tmp_path, monkeypatch):
    spec = _modes(cap_spec(xc_ohm=t(XC)), [t(12.0, "measured"), t(30.0, "datasheet")])
    out, series = _screens(tmp_path, monkeypatch, spec)
    for case, margin in {"max": 2.945056464, "min": 1.571924798}.items():
        assert series.at[case, "margin_hz"] == pytest.approx(margin, abs=SCAN_TOL)
        assert (series.at[case, "mode_hz"], series.at[case, "mode_source"]) == (12.0, "measured")
        assert "assumed" not in str(series.at[case, "note"])
    assert set(series["verdict"]) == {"clear"}
    assert out.subsync_verdict == "clear"


def test_a_clear_resting_on_an_assumed_mode_says_so(tmp_path, monkeypatch):
    spec = _modes(cap_spec(xc_ohm=t(XC)), [t(12.0, "datasheet"), t(30.5, "assumed")])
    out, series = _screens(tmp_path, monkeypatch, spec, clearance=1.0)
    for case in X_TOTAL:
        row = series.loc[case]
        assert row["verdict"] == "clear" and bool(row["assumed"]) is True
        assert row["note"].startswith("clear rests on assumed torsional mode(s): GEN1 30.5 Hz; ")
    assert out.subsync_verdict == "clear"


def test_a_row_judged_on_an_assumed_mode_carries_its_source(tmp_path, monkeypatch):
    spec = _modes(cap_spec(xc_ohm=t(XC)), [t(9.5, "assumed")])
    _, series = _screens(tmp_path, monkeypatch, spec, clearance=1.0)
    assert set(series["mode_source"]) == {"assumed"} and set(series["verdict"]) == {"flag"}
    assert set(series["assumed"]) == {True}


def test_a_genset_without_modes_keeps_the_screen_from_reading_clear(tmp_path, monkeypatch):
    spec = _gen2(_modes(cap_spec(xc_ohm=t(XC)), [t(12.0, "datasheet")]))
    out, series = _screens(tmp_path, monkeypatch, spec)
    assert set(series["verdict"]) == {"check"}
    for note in series["note"]:
        assert note.startswith(
            "no torsional modes given for genset(s) ['GEN2']; the screen does not pass on the others alone; ")
    assert out.subsync_verdict == "check"


def test_a_flag_on_a_known_genset_stands_while_another_lacks_modes(tmp_path, monkeypatch):
    out, series = _screens(tmp_path, monkeypatch, _gen2(_modes(cap_spec(xc_ohm=t(XC)), [t(9.5)])))
    assert set(series["verdict"]) == {"flag"}
    assert out.subsync_verdict == "flag"


def test_modes_of_several_gensets_take_the_nearest(tmp_path, monkeypatch):
    spec = _gen2(_modes(cap_spec(xc_ohm=t(XC)), [t(20.0)]), [t(9.0, "datasheet")])
    _, series = _screens(tmp_path, monkeypatch, spec)
    assert set(series["machine"]) == {"GEN2"} and set(series["mode_source"]) == {"datasheet"}
    assert series.at["max", "margin_hz"] == pytest.approx(0.054943536, abs=SCAN_TOL)


def test_the_clearance_splits_flag_below_from_clear_above(tmp_path, monkeypatch):
    # 0.7 Hz lies between the max case's 0.445 Hz margin and the min case's 0.928 Hz
    out, series = _screens(tmp_path, monkeypatch, _modes(cap_spec(xc_ohm=t(XC)), [t(9.5)]), clearance=0.7)
    assert (series.at["max", "verdict"], series.at["min", "verdict"]) == ("flag", "clear")
    assert series.at["min", "clearance_hz"] == 0.7
    assert out.subsync_verdict == "flag"


@pytest.mark.parametrize("mode, margin, verdict", [(9.0, 0.5, "clear"), (9.0625, 0.4375, "flag")])
def test_a_margin_equal_to_the_clearance_clears_and_one_below_flags(tmp_path, monkeypatch, mode, margin, verdict):
    from gridspine.static import subsync_scan as ss

    # f_er a literal 40.5 Hz, so f0 - f_er = 9.5 Hz and every margin is exact in binary
    monkeypatch.setattr(ss, "crossings", lambda fs, z: [(40.5, 0.0)])
    _, series = _screens(tmp_path, monkeypatch, _modes(cap_spec(xc_ohm=t(XC)), [t(mode)]), clearance=0.5)
    assert set(series["f_comp_hz"]) == {9.5}
    assert set(series["margin_hz"]) == {margin}
    assert set(series["verdict"]) == {verdict}


def test_without_modes_the_resonance_still_reads_check(tmp_path, monkeypatch):
    out, series = _screens(tmp_path, monkeypatch, cap_spec(xc_ohm=t(XC)))
    assert set(series["verdict"]) == {"check"}
    assert set(series["note"]) == {"no torsional modes given; " + series["note"].iloc[0].split("; ", 1)[1]}
    assert out.subsync_verdict == "check"


def test_modes_without_a_capacitor_still_read_not_evaluated(tmp_path, monkeypatch):
    out, series = _screens(tmp_path, monkeypatch, _modes(campus_spec(), [t(12.0)]))
    assert set(series["verdict"]) == {"not evaluated"}
    assert out.subsync_verdict == "not evaluated"
