"""
The importers' ``assumed`` / ``unmapped`` text is read as is in the project-io
import preview, the chat and ``import_report.json``, so it names no raw PSS/E
field code (RAW-NAMES-1, RAW-NAMES-SRC-1). The codes stay in docstrings and
code; this pins the user-facing text only.
"""

import logging
import re
from pathlib import Path

import pytest

from gridspine.handoff.raw_writer import write_raw
from gridspine.ingest.dgs_import import _TYPE_NAMES, _plain, _type_name
from gridspine.ingest.psse_import import read_psse

FIX = Path(__file__).parent / "fixtures" / "psse"
logging.getLogger("pypsa").setLevel(logging.ERROR)

#: Raw PSS/E record field codes a reason may not show.
RAW_CODE = re.compile(
    r"\b(RATE[A-C]|RATE\d+(-\d+)?|SBASE(\d-\d)?|BINIT|THRSHZ|MBASE|BASKV|BASFRQ"
    r"|PT|PB|QT|QB|VS|IREG|SWREG|SWREM|COD\d|WINDV\d|NOMV\d|CW|CZ|CM|STAT|ElmXnet|Typ[A-Z]\w*)\b"
)

#: Bundled .raw cases the reader takes; case5 is refused (CW = 0) and
#: parser_test_defaults needs its version passed.
_CASES = [
    ("case14.raw", {}),
    ("case30.raw", {}),
    ("case4_3wtf_vnom0_cw2.raw", {}),
    ("case4_3wtf_vnom0_cw3.raw", {}),
    ("parser_test_defaults.raw", {"version": 33}),
    ("three_winding_mag_test.raw", {}),
    ("three_winding_test.raw", {}),
    ("two-terminal-hvdc_test.raw", {}),
    ("two_winding_mag_test.raw", {}),
    ("vsc-hvdc_test.raw", {}),
]


def _texts(report):
    """``(row, user-facing text)``: each assumed parameter and why, each unmapped reason."""
    for a in report.assumed:
        yield f"assumed {a.asset}", a.parameter
        yield f"assumed {a.asset} / {a.parameter}", a.why
    for u in report.unmapped:
        yield f"unmapped {u.source_item}", u.reason


def _raw_codes(report):
    return [(row, t) for row, t in _texts(report) if RAW_CODE.search(t)]


@pytest.mark.parametrize("name, options", _CASES, ids=[c[0] for c in _CASES])
def test_bundled_case_reasons_name_no_raw_code(name, options):
    report = read_psse([FIX / name], options).report
    assert list(_texts(report))
    assert _raw_codes(report) == []


@pytest.mark.parametrize("version", [33, 35, 36])
def test_case39_written_reasons_name_no_raw_code(tmp_path, version):
    from gridspine.ingest.pandapower_source import load_case39

    path = tmp_path / f"c39_v{version}.raw"
    write_raw(load_case39(), path, f_hz=60.0, version=version)
    assert _raw_codes(read_psse([path], {}).report) == []


def test_features_case_reasons_name_no_raw_code(tmp_path):
    from tests.gridspine.test_psse_import import _FEATURES

    p = tmp_path / "features.raw"
    p.write_text(_FEATURES)
    assert _raw_codes(read_psse([p], {}).report) == []


@pytest.mark.parametrize("rev", [33, 35])
def test_hand_case_reasons_name_no_raw_code(tmp_path, rev):
    """
    Zero impedance, no rating, a later rating, an unrated 1:1 transformer,
    no usable maximum output and a remote regulator, each in ``assumed``.
    """
    from tests.gridspine.test_psse_import import _SWING, _case, _gen

    pad = ",''" if rev >= 35 else ""  # v35 branches carry a NAME before RATE1
    res = _case(
        tmp_path,
        rev=rev,
        BUS=["1,'A', 110.0,3", "2,'B', 110.0,2", "3,'C', 20.0,1"],
        GENERATOR=[_SWING, _gen(2, "1", ireg=1, pt=0.0)],
        BRANCH=[
            f"1,2,'1',0.0,0.0,0.0{pad},0.0,0.0,0.0",
            f"1,2,'2',0.01,0.1,0.0{pad},0.0,45.0,0.0",
            f"2,3,'1',0.01,0.1,0.0{pad},0.0",
        ],
    )
    params = {a.parameter for a in res.report.assumed}
    assert {
        "reactance (pu)",
        "thermal rating (MVA)",
        "rated power (MW)",
        "regulated bus",
    } <= params
    assert _raw_codes(res.report) == []


# ---------------------------------------------------------------- each reason, word for word
# GK A2 W2: the regex alone passes on an empty or garbled sentence.
_UNRATED_110 = (
    "the file gives no thermal rating (a normal rating of 0 means unrated), so a "
    "bound that never binds is used: 10 × the total load 0.0 MVA (at least the "
    "system base power, 100 MVA), or the 110 kV class floor if higher. Set a real "
    "rating before you read loading"
)


def _why(report):
    return {(a.asset, a.parameter): (a.value, a.why) for a in report.assumed}


def test_case14_reasons_word_for_word():
    got = _why(read_psse([FIX / "case14.raw"], {}).report)
    assert got[("Switched shunt Bus 9     LV 1", "susceptance setting")] == (
        19.0,
        "held at its initial switched-shunt setting (Mvar at 1.0 pu); "
        "voltage-controlled switching is not modelled",
    )
    assert got[("Line Bus 1     HV - Bus 2     HV 1", "thermal rating (MVA)")] == (
        2757.0,
        "the file gives no thermal rating (a normal rating of 0 means unrated), so a "
        "bound that never binds is used: 10 × the total load 275.7 MVA (at least the "
        "system base power, 100 MVA), or the 138 kV class floor if higher. Set a real "
        "rating before you read loading",
    )
    assert got[("Transformer 4-7 1", "rating (MVA)")] == (
        100.0,
        "the file gives no thermal rating; its winding impedance base power "
        "(100 MVA) is used",
    )


def test_case_header_and_bus_reasons_word_for_word():
    got = _why(read_psse([FIX / "parser_test_defaults.raw"], {"version": 33}).report)
    assert got[("case", "system base (MVA)")] == (
        100.0,
        "the file leaves the system base power empty; PSS/E's default",
    )
    assert got[("case", "base frequency (Hz)")] == (
        60.0,
        "the file leaves the base frequency empty; PSS/E's default",
    )
    assert got[("bus 1", "base voltage (kV)")] == (
        1.0,
        "the file gives a base voltage of 0; 1 kV keeps per-unit values unchanged",
    )
    assert got[("Gen Bus 1 1", "rated power (MW)")] == (
        100.0,
        "the file gives a maximum output of 9999 MW, which is no usable limit, so "
        "the machine base power is used",
    )


def test_hand_case_reasons_word_for_word(tmp_path):
    from tests.gridspine.test_psse_import import _SWING, _case, _gen

    res = _case(
        tmp_path,
        rev=35,
        BUS=["1,'A', 110.0,3", "2,'B', 110.0,2", "3,'C', 20.0,1"],
        GENERATOR=[_SWING, _gen(2, "1", ireg=1, pt=0.0)],
        BRANCH=[
            "1,2,'1',0.0,0.0,0.0,'',0.0,0.0,0.0",
            "1,2,'2',0.01,0.1,0.0,'',0.0,45.0,0.0",
            "2,3,'1',0.01,0.1,0.0,'',0.0",
        ],
    )
    got = _why(res.report)
    assert got[("Line A - B 1", "reactance (pu)")] == (
        0.0001,
        "R = X = 0 in the file (a zero-impedance jumper); PSS/E's zero-impedance "
        "threshold (0.0001 pu) is used",
    )
    assert got[("Line A - B 1", "thermal rating (MVA)")] == (1000.0, _UNRATED_110)
    assert got[("Line A - B 2", "thermal rating (MVA)")] == (
        45.0,
        "the normal rating is 0; rating 2 = 45 MVA is used, the first rating the "
        "file gives. Set the normal rating before you read loading",
    )
    assert got[("Line B - C 1", "thermal rating (MVA)")] == (
        100.0,
        "the file gives no thermal rating; the system base power (100 MVA) is used "
        "as the transformer base, so set a real rating",
    )
    assert got[("Gen B 1", "regulated bus")] == (
        "B",
        "the file regulates remote bus 1; the machine holds its own terminal at its "
        "voltage setpoint",
    )


@pytest.mark.parametrize(
    "cod, parameter, value, why",
    [
        (
            1,
            "winding 1 tap ratio",
            1.05,
            "automatic voltage control (control mode 1) of winding 1 is not "
            "modelled; held at the file's setting",
        ),
        (
            3,
            "winding 1 phase angle (deg)",
            30.0,
            "automatic active power flow (phase shift) control (control mode 3) of "
            "winding 1 is not modelled; held at the file's setting",
        ),
    ],
)
def test_transformer_control_reason_word_for_word(tmp_path, cod, parameter, value, why):
    from tests.gridspine.test_psse_import import _BUSES, _LINE, _SWING, _case

    res = _case(
        tmp_path,
        BUS=_BUSES + ["3,'C', 20.0,1"],
        GENERATOR=[_SWING],
        BRANCH=[_LINE],
        TRANSFORMER=[
            "2,3,0,'1',1,1,1,0.0,0.0,2,'T',1",
            "0.01,0.1,100.0",
            f"1.05,0.0,30.0,0.0,0.0,0.0,{cod},3,1.1,0.9",
            "1.0,0.0",
        ],
    )
    assert _why(res.report)[("T", parameter)] == (value, why)
    assert _raw_codes(res.report) == []


def test_dgs_reasons_word_for_word(tmp_path):
    from tests.gridspine.test_dgs_import import HEAD, _drop, _with, dgs_text, read_text

    rows, head = _drop("ElmGenstat", "qgini")
    rep = read_text(tmp_path, dgs_text(rows, head)).report
    got = _why(rep)
    assert got[("PV", "Reactive power (Mvar)")] == (
        0.0,
        "the file gives no reactive power (Mvar)",
    )
    assert got[("T3W", "HV vector group number")] == (
        0.0,
        "the file gives no HV vector group number",
    )
    assert got[("Grid", "active power limit (MW)")] == (
        1000.0,
        "the external grid is unbounded in the file, so a limit that never binds is "
        "used: 10 × the total load 6.3 MVA, or the 132 kV class floor if higher; its "
        "short-circuit level 1000 MVA is higher, so that is used",
    )
    assert _raw_codes(rep) == []
    head = dict(HEAD, TypLne="loc_name(a:40);uline(r);sline(r);rline(r);xline(r)")
    rows = _with(TypLne=["30;L1 type;33.0;0.5;0.1;0.4"])
    rep = read_text(tmp_path, dgs_text(rows, head), name="no_g.dgs").report
    got = _why(rep)
    assert got[("L1", "Susceptance")] == (0.0, "the file gives no susceptance")
    assert got[("L1", "Conductance")] == (0.0, "the line type gives no conductance")
    assert _raw_codes(rep) == []


@pytest.mark.parametrize(
    "label, plain",
    [
        ("Tap position", "tap position"),
        ("HV vector group number", "HV vector group number"),
        ("Magnetising current (%)", "magnetising current (%)"),
        ("MVA rating", "MVA rating"),
        ("", "value"),  # no label: a plain word, never the raw attribute
    ],
)
def test_dgs_label_reads_mid_sentence(label, plain):
    assert _plain(label) == plain


def test_three_winding_bases_word_for_word(tmp_path):
    """Each winding without a rating names its own base: 1-2, 2-3, 3-1."""
    text = (FIX / "parser_test_defaults.raw").read_text().splitlines()
    assert text[16] == ",0.1,,,0.1,,,0.1,,,,,,,"
    text[16] = ",0.1,100.0,,0.1,50.0,,0.1,30.0,,,,,,"  # SBASE1-2, 2-3, 3-1
    path = tmp_path / "bases.raw"
    path.write_text("\n".join(text) + "\n")
    got = _why(read_psse([path], {"version": 33}).report)
    for k, mva in ((1, 100.0), (2, 50.0), (3, 30.0)):
        assert got[(f"Transformer 1-2-3 1 winding {k}", "rating (MVA)")] == (
            mva,
            "the file gives no thermal rating; its winding impedance base power "
            f"({mva:g} MVA) is used",
        )


def test_dgs_absent_type_and_no_load_word_for_word(tmp_path):
    from tests.gridspine.test_dgs_import import _unmapped, _with, dgs_text, read_text

    rows = _with(ElmLne=["31;L1;2;32;10.0;2;0"])  # a transformer type, no line type
    res = read_text(tmp_path, dgs_text(rows))
    assert ("ElmLne 'L1'", "the file gives no line type for it") in _unmapped(res)
    assert _raw_codes(res.report) == []
    got = _why(read_text(tmp_path, dgs_text(), name="hand.dgs").report)
    assert got[("T3W", "No-load losses (kW)")] == (
        0.0,
        "the file gives no value for no-load losses (kW)",
    )


def test_dgs_type_names_whole():
    assert _TYPE_NAMES == {
        "TypLne": "line",
        "TypTr2": "two-winding transformer",
        "TypTr3": "three-winding transformer",
        "TypSym": "synchronous machine",
    }


def test_dgs_unlisted_type_class_reads_plain():
    # a typ() class not in _TYPE_NAMES: a plain word, never a KeyError or the class
    assert _type_name("TypAsmo") == "matching"
    assert [_type_name(c) for c in _TYPE_NAMES] == list(_TYPE_NAMES.values())


# ---------------------------------------------------------------- no value or key change
# GK A2 W4: master's rows (asset, parameter, value) / source_item, captured from
# master c898822e (before the rewording) as literals; only why/reason changed.
_CASE14_ROWS = [
    ("Switched shunt Bus 9     LV 1", "susceptance setting", 19.0),
    ("Line Bus 1     HV - Bus 2     HV 1", "thermal rating (MVA)", 2757.0),
    ("Line Bus 1     HV - Bus 5     HV 1", "thermal rating (MVA)", 2757.0),
    ("Line Bus 2     HV - Bus 3     HV 1", "thermal rating (MVA)", 2757.0),
    ("Line Bus 2     HV - Bus 4     HV 1", "thermal rating (MVA)", 2757.0),
    ("Line Bus 2     HV - Bus 5     HV 1", "thermal rating (MVA)", 2757.0),
    ("Line Bus 3     HV - Bus 4     HV 1", "thermal rating (MVA)", 2757.0),
    ("Line Bus 4     HV - Bus 5     HV 1", "thermal rating (MVA)", 2757.0),
    ("Line Bus 6     LV - Bus 11    LV 1", "thermal rating (MVA)", 2757.0),
    ("Line Bus 6     LV - Bus 12    LV 1", "thermal rating (MVA)", 2757.0),
    ("Line Bus 6     LV - Bus 13    LV 1", "thermal rating (MVA)", 2757.0),
    ("Line Bus 7     ZV - Bus 8     TV 1", "thermal rating (MVA)", 2757.0),
    ("Line Bus 7     ZV - Bus 9     LV 1", "thermal rating (MVA)", 2757.0),
    ("Line Bus 9     LV - Bus 10    LV 1", "thermal rating (MVA)", 2757.0),
    ("Line Bus 9     LV - Bus 14    LV 1", "thermal rating (MVA)", 2757.0),
    ("Line Bus 10    LV - Bus 11    LV 1", "thermal rating (MVA)", 2757.0),
    ("Line Bus 12    LV - Bus 13    LV 1", "thermal rating (MVA)", 2757.0),
    ("Line Bus 13    LV - Bus 14    LV 1", "thermal rating (MVA)", 2757.0),
    ("Transformer 4-7 1", "rating (MVA)", 100.0),
    ("Transformer 4-9 1", "rating (MVA)", 100.0),
    ("Transformer 5-6 1", "rating (MVA)", 100.0),
    ("Gen Bus 1     HV 1", "marginal cost (€/MWh)", 1.0),
    ("Gen Bus 2     HV 1", "marginal cost (€/MWh)", 1.0),
    ("Gen Bus 3     HV 1", "marginal cost (€/MWh)", 1.0),
    ("Gen Bus 6     LV 1", "marginal cost (€/MWh)", 1.0),
    ("Gen Bus 8     TV 1", "marginal cost (€/MWh)", 1.0),
]
_CASE39_ROWS = [
    ("Gen BUS_30 1", "marginal cost (€/MWh)", 1.0),
    ("Gen BUS_32 1", "marginal cost (€/MWh)", 1.0),
    ("Gen BUS_33 1", "marginal cost (€/MWh)", 1.0),
    ("Gen BUS_34 1", "marginal cost (€/MWh)", 1.0),
    ("Gen BUS_35 1", "marginal cost (€/MWh)", 1.0),
    ("Gen BUS_36 1", "marginal cost (€/MWh)", 1.0),
    ("Gen BUS_37 1", "marginal cost (€/MWh)", 1.0),
    ("Gen BUS_38 1", "marginal cost (€/MWh)", 1.0),
    ("Gen BUS_39 1", "marginal cost (€/MWh)", 1.0),
    ("Gen BUS_31 1", "marginal cost (€/MWh)", 1.0),
]


def _rows(report):
    return (
        [(a.asset, a.parameter, a.value) for a in report.assumed],
        [u.source_item for u in report.unmapped],
    )


def test_case14_rows_as_on_master():
    assert _rows(read_psse([FIX / "case14.raw"], {}).report) == (_CASE14_ROWS, [])


@pytest.mark.parametrize("version", [33, 36])
def test_case39_rows_as_on_master(tmp_path, version):
    from gridspine.ingest.pandapower_source import load_case39

    path = tmp_path / f"c39_v{version}.raw"
    write_raw(load_case39(), path, f_hz=60.0, version=version)
    assert _rows(read_psse([path], {}).report) == (_CASE39_ROWS, [])
