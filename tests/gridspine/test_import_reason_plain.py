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
from gridspine.ingest.psse_import import read_psse

FIX = Path(__file__).parent / "fixtures" / "psse"
logging.getLogger("pypsa").setLevel(logging.ERROR)

#: Raw PSS/E record field codes a reason may not show.
RAW_CODE = re.compile(
    r"\b(RATE[A-C]|RATE\d+(-\d+)?|SBASE(\d-\d)?|BINIT|THRSHZ|MBASE|BASKV|BASFRQ"
    r"|PT|PB|QT|QB|VS|IREG|SWREG|SWREM|COD\d|WINDV\d|NOMV\d|CW|CZ|CM|STAT|ElmXnet)\b"
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
    """``(row, user-facing text)`` for each assumed and unmapped row."""
    for a in report.assumed:
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
    """Zero impedance, no rating, a later rating, an unrated 1:1 transformer,
    no usable maximum output and a remote regulator, each in ``assumed``."""
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
