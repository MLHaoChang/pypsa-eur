"""
The public generic WECC set (owner decision GC04-DEFAULTS-1, "Generic, tagged").

Oracles: each block covers its model's fields exactly (hand-listed counts),
every value is a finite number with its own citation, the set passes the
template loader's converter checks, and the citations follow GC-TEXT-1 (the
EPRI guide by title and number only, no utility host).
"""

import copy

import pytest
import yaml

from gridspine.schema.contracts import ContractError
from gridspine.templates import wecc_generic as WG

RAW = yaml.safe_load(WG.PATH.read_text())


def _load(tmp_path, edit):
    raw = copy.deepcopy(RAW)
    edit(raw)
    path = tmp_path / "g.yaml"
    path.write_text(yaml.safe_dump(raw))
    return WG.load_generic(path)


def test_the_shipped_set_loads_with_hand_listed_block_sizes():
    g = WG.load_generic()
    # REGCA1 1 ICON + 14 CON; REECA1 5 + 45 (BUSR is written by the writer);
    # REECC1 4 + 41 + the 4 SOC fields; REPCA1 3 + 27 (BUSR/BUS1/BUS2/CKT written).
    assert {k: len(v) for k, v in g.items()} == {
        "regc": 15,
        "reec": 50,
        "reecc": 49,
        "repc": 30,
    }


def test_groups_per_converter_kind():
    assert WG.groups(battery=False, plant=False) == ("regc", "reec")
    assert WG.groups(battery=True, plant=False) == ("regc", "reecc")
    assert WG.groups(battery=True, plant=True) == ("regc", "reecc", "repc")
    g = WG.load_generic()
    assert WG.converter_set(g, False, True) == g["regc"] | g["reec"] | g["repc"]


def test_the_mark_reads_as_the_owner_decided():
    assert WG.NOT_FOR_SUBMISSION == (
        "not for submission: generic WECC parameters (assumed) until OEM data"
    )
    assert WG.SOURCE == "assumed"


def test_citations_follow_gc_text_1():
    text = WG.PATH.read_text()
    assert (
        "3002006525" in text
        and "Model User Guide for Generic Renewable Energy System Models" in text
    )
    assert ".gov" not in text
    for name, block in RAW["blocks"].items():
        assert block["source"].strip(), name


@pytest.mark.parametrize(
    "edit, reason",
    [
        (lambda r: r["blocks"].pop("repc"), "blocks must be exactly"),
        (lambda r: r["blocks"]["regc"]["values"].pop("tg"), "missing \\['regc_tg'\\]"),
        (
            lambda r: r["blocks"]["reec"]["values"].update(
                foo={"value": 1, "from": "x"}
            ),
            "unknown \\['reec_foo'\\]",
        ),
        (
            lambda r: r["blocks"]["repc"].update(source=" "),
            "block repc \\(REPCA1\\) has no source",
        ),
        (
            lambda r: r["blocks"]["regc"]["values"]["tg"].pop("from"),
            "regc.regc_tg needs a value and a 'from'",
        ),
        (
            lambda r: r["blocks"]["regc"]["values"]["tg"].update(value=float("inf")),
            "regc.regc_tg must be a finite",
        ),
        (
            lambda r: r["blocks"]["regc"]["values"]["tg"].update(value=float("nan")),
            "regc.regc_tg must be a finite",
        ),
        (
            lambda r: r["blocks"]["regc"]["values"]["tg"].update(value="0.02"),
            "regc.regc_tg must be a finite",
        ),
        (
            lambda r: r["blocks"]["regc"]["values"]["tg"].update(value=True),
            "regc.regc_tg must be a finite",
        ),
        # the loader's own converter checks run on the set
        (
            lambda r: r["blocks"]["regc"]["values"]["iqrmax"].update(value=-1.0),
            "regc_iqrmax must be > 0",
        ),
        (
            lambda r: r["blocks"]["reecc"]["values"]["socmin"].update(value=0.9),
            "reec_socmin < reec_socmax",
        ),
        (
            lambda r: r["blocks"]["repc"]["values"]["qmin"].update(value=9.0),
            "repc_qmin must be < repc_qmax",
        ),
    ],
)
def test_a_bad_set_is_refused(tmp_path, edit, reason):
    with pytest.raises(ContractError, match=reason):
        _load(tmp_path, edit)
