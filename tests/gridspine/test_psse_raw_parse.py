"""
PSS/E RAW reader (``gridspine.ingest.psse_raw_parse``): records, defaults,
refusals and unit conversions, on licensed third-party files (PowerModels.jl
test data, BSD-3, see ``fixtures/psse/README.md``) and on our own exports.

Hand values are computed here from the numbers in the file with the formula
written out, never retyped from the reader's output.
"""

import math
from pathlib import Path

import numpy as np
import pytest

from gridspine.handoff.raw_writer import write_raw
from gridspine.ingest.psse_raw_parse import (
    _split,
    branch_physical,
    load_at_nominal,
    read_raw,
    read_raw_text,
    shunt_siemens,
    transformer_windings,
    winding_ratio,
)
from gridspine.schema.contracts import ContractError

FIX = Path(__file__).parent / "fixtures" / "psse"


def _counts(case):
    return {k: len(v) for k, v in case.tables.items() if len(v)}


# ---------------------------------------------------------------- free format
@pytest.mark.parametrize(
    "line, expected",
    [
        ("1,'BUS A', 138.0", ["1", "BUS A", "138.0"]),
        ("1 'BUS A'  138.0", ["1", "BUS A", "138.0"]),
        ("1 , 'B,C' , 2", ["1", "B,C", "2"]),
        ("1,,3", ["1", None, "3"]),
        ("1, ,3", ["1", None, "3"]),
        ("1,'',3", ["1", "", "3"]),
        ("1,2 / a comment, with 'quotes'", ["1", "2"]),
        ("1,'a/b',2", ["1", "a/b", "2"]),
        ('1,"dq",2', ["1", "dq", "2"]),
        ("1,2,", ["1", "2"]),  # a trailing empty field is a left-off one
    ],
)
def test_split(line, expected):
    assert _split(line, 1) == expected


def test_unclosed_quote_names_the_line():
    with pytest.raises(ContractError, match="line 7: unclosed quote"):
        _split("1,'abc", 7)


# ---------------------------------------------------------------- hand-checked counts
@pytest.mark.parametrize(
    "name, expected",
    [
        # counted by hand from the files
        (
            "case14",
            {
                "bus": 14,
                "load": 11,
                "generator": 5,
                "branch": 17,
                "transformer": 3,
                "switched_shunt": 1,
                "area": 1,
                "zone": 1,
                "owner": 1,
            },
        ),
        (
            "case30",
            {
                "bus": 30,
                "load": 21,
                "generator": 6,
                "branch": 34,
                "transformer": 7,
                "switched_shunt": 2,
                "area": 1,
                "zone": 1,
                "owner": 1,
            },
        ),
        (
            "case5",
            {
                "bus": 5,
                "load": 3,
                "generator": 5,
                "branch": 5,
                "transformer": 4,
                "area": 1,
                "zone": 1,
                "owner": 1,
            },
        ),
        ("three_winding_test", {"bus": 3, "load": 1, "generator": 2, "transformer": 5}),
        (
            "two_winding_mag_test",
            {"bus": 2, "load": 2, "generator": 2, "transformer": 1},
        ),
    ],
)
def test_hand_checked_counts(name, expected):
    case = read_raw(FIX / f"{name}.raw")
    assert case.version == 33
    assert _counts(case) == expected
    assert case.unmapped == []


def test_case14_values_by_hand():
    c = read_raw(FIX / "case14.raw")
    bus = c.tables["bus"].set_index("I")
    assert (
        bus.at[1, "NAME"] == "Bus 1     HV"
        and bus.at[1, "IDE"] == 3
        and bus.at[1, "BASKV"] == 138.0
    )
    br = c.tables["branch"].iloc[
        0
    ]  # 1-2: R 1.938E-2, X 5.917E-2, B 5.28E-2 pu on 100 MVA, 138 kV
    phys = branch_physical(c, br)
    zb = 138.0**2 / 100.0
    assert phys["r_ohm"] == pytest.approx(1.938e-2 * zb)
    assert phys["x_ohm"] == pytest.approx(5.917e-2 * zb)
    assert phys["b_siemens"] == pytest.approx(5.28e-2 / zb)
    gen = c.tables["generator"].iloc[0]
    assert (gen["PG"], gen["QT"], gen["QB"], gen["VS"], gen["PT"]) == (
        232.4,
        10.0,
        0.0,
        1.06,
        332.4,
    )
    sw = c.tables["switched_shunt"].iloc[0]
    assert (sw["I"], sw["BINIT"], sw["N1"], sw["B1"]) == (9, 19.0, 1, 19.0)
    tr = c.tables["transformer"].iloc[0]  # 4-7, WINDV1 0.978, X 0.20912, CZ=CW=1
    (w,) = transformer_windings(c, tr)
    assert (w["bus"], w["to"]) == (4, 7)
    assert w["tap"] == pytest.approx(0.978 / 1.0)
    assert w["x_pu"] == pytest.approx(0.20912 * 1.0**2)


# ---------------------------------------------------------------- not mapped, never dropped
@pytest.mark.parametrize(
    "name, words",
    [("two-terminal-hvdc_test", "two-terminal dc"), ("vsc-hvdc_test", "vsc dc line")],
)
def test_dc_links_listed_as_not_mapped(name, words):
    case = read_raw(FIX / f"{name}.raw")
    assert len(case.unmapped) == 1
    item, reason = case.unmapped[0]
    assert item.startswith(f"{words} record at line ")
    assert "no DC link mapping" in reason


def test_defaults_file_every_section_accounted():
    case = read_raw_text((FIX / "parser_test_defaults.raw").read_text(), version=33)
    assert _counts(case) == {
        "bus": 3,
        "load": 1,
        "fixed_shunt": 1,
        "generator": 1,
        "branch": 1,
        "transformer": 1,
        "area": 1,
        "zone": 1,
        "owner": 1,
        "switched_shunt": 1,
    }
    kinds = sorted(
        item.split(" record")[0].split(" data")[0] for item, _ in case.unmapped
    )
    assert kinds == [
        "facts device",
        "impedance correction",
        "inter-area transfer",
        "multi-section line",
        "multi-terminal dc",
        "two-terminal dc",
        "vsc dc line",
    ]
    assert (
        "case",
        "system base (MVA)",
        100.0,
        "SBASE empty; PSS/E default",
    ) in case.assumed
    assert any(
        a[1] == "base voltage (kV)" for a in case.assumed
    )  # BASKV empty -> 0 -> assumed


def test_defaults_take_psse_values():
    case = read_raw_text((FIX / "parser_test_defaults.raw").read_text(), version=33)
    ld = case.tables["load"].iloc[0]
    assert (ld["ID"], ld["STATUS"], ld["SCALE"]) == ("1", 1, 1)
    g = case.tables["generator"].iloc[0]
    assert (g["QT"], g["QB"], g["VS"], g["MBASE"], g["STAT"], g["ZX"]) == (
        9999.0,
        -9999.0,
        1.0,
        100.0,
        1,
        1.0,
    )
    tr = case.tables["transformer"].iloc[0]
    assert (
        tr["CW"],
        tr["CZ"],
        tr["CM"],
        tr["WINDV1"],
        tr["WINDV2"],
        tr["SBASE1-2"],
    ) == (1, 1, 1, 1.0, 1.0, 100.0)


def test_out_of_range_codes_assumed_not_silent():
    case = read_raw(FIX / "case5.raw")
    names = [(a[1], a[2]) for a in case.assumed]
    assert ("winding ratio units (CW)", 1) in names
    assert ("impedance units (CZ)", 1) in names
    assert ("magnetising admittance units (CM)", 1) in names


# ---------------------------------------------------------------- refusals
_MINI = """ 0, 100.00, {rev}, 0, 0, 50.00
title
second
1,'A', 110.0,3
2,'B', 110.0,1
0 / END OF BUS DATA
{load}
0
0
0
{branch}
0
{tail}"""


def _mini(
    rev=33,
    load="2,'1',1,1,1,10.0,2.0",
    branch="1,2,'1',0.01,0.1,0.02\n0",
    tail="0\n" * 13 + "Q",
):
    return _MINI.format(rev=rev, load=load, branch=branch, tail=tail)


def test_mini_reads():
    c = read_raw_text(_mini())
    assert _counts(c) == {"bus": 2, "load": 1, "branch": 1}


@pytest.mark.parametrize("rev", [32, 34, 37])
def test_unsupported_version_refused(rev):
    with pytest.raises(ContractError, match=f"version {rev} not supported"):
        read_raw_text(_mini(rev=rev))


def test_empty_rev_needs_a_version():
    text = _mini().replace(" 0, 100.00, 33,", " 0, 100.00, ,", 1)
    with pytest.raises(ContractError, match="REV"):
        read_raw_text(text)
    assert read_raw_text(text, version=33).version == 33


def test_unknown_bus_refused_with_line():
    with pytest.raises(ContractError, match="line 7: load refers to bus 9"):
        read_raw_text(_mini(load="9,'1',1,1,1,10.0,2.0"))


def test_duplicate_bus_refused():
    text = _mini().replace("2,'B', 110.0,1", "1,'B', 110.0,1")
    with pytest.raises(ContractError, match="bus number 1 repeated"):
        read_raw_text(text)


def test_bad_number_names_field_and_line():
    with pytest.raises(ContractError, match="line 7: load PL is not a number"):
        read_raw_text(_mini(load="2,'1',1,1,1,ten,2.0"))


def test_missing_required_branch_x():
    with pytest.raises(ContractError, match="branch field X is missing"):
        read_raw_text(_mini(branch="1,2,'1',0.01\n0"))


def test_missing_terminator_refused():
    with pytest.raises(ContractError, match="ends inside"):
        read_raw_text(_mini(tail="0\n0\n"))


def test_data_after_last_section_refused():
    with pytest.raises(ContractError, match="data after the last section"):
        read_raw_text(_mini(tail="0\n" * 13 + "5,6\nQ"))


def test_truncated_transformer_refused():
    text = _mini(branch="1,2,'1',0.01,0.1,0.02\n0\n1,2,0,'1',1,1,1\n0.0,0.1,100\n1.0")
    with pytest.raises(ContractError, match="needs 4 lines"):
        read_raw_text(text)


# ---------------------------------------------------------------- unit conversions
def test_shunt_and_load_conversions():
    assert shunt_siemens(110.0, 1.21, 12.1) == pytest.approx(
        (1.21 / 110**2, 12.1 / 110**2)
    )
    ld = {"PL": 10.0, "QL": 2.0, "IP": 1.0, "IQ": 0.5, "YP": 0.25, "YQ": -0.75}
    assert load_at_nominal(ld) == pytest.approx((10.0 + 1.0 + 0.25, 2.0 + 0.5 + 0.75))


@pytest.mark.parametrize(
    "cw, windv, nomv, kv, expected",
    [
        (1, 1.05, 0.0, 110.0, 1.05),
        (2, 115.5, 0.0, 110.0, 115.5 / 110.0),
        (3, 1.05, 121.0, 110.0, 1.05 * 121.0 / 110.0),
        (3, 1.05, 0.0, 110.0, 1.05),
    ],
)
def test_winding_ratio(cw, windv, nomv, kv, expected):
    assert winding_ratio(cw, windv, nomv, kv) == pytest.approx(expected)


def _two_winding(
    cz=1,
    cw=1,
    cm=1,
    mag=(0.0, 0.0),
    z=(0.01, 0.1),
    sb=100.0,
    w1=(1.05, 0.0),
    w2=(1.02, 0.0),
):
    text = _mini(
        branch=(
            "1,2,'1',0.01,0.1,0.02\n0\n"
            f"1,2,0,'1',{cw},{cz},{cm},{mag[0]},{mag[1]},2,'T',1\n"
            f"{z[0]},{z[1]},{sb}\n"
            f"{w1[0]},{w1[1]},30.0,50.0,50.0,50.0\n"
            f"{w2[0]},{w2[1]}"
        )
    )
    c = read_raw_text(text)
    return c, transformer_windings(c, c.tables["transformer"].iloc[0])[0]


def test_two_winding_cz1_cw1_pag_4_21b():
    _, w = _two_winding()
    assert w["tap"] == pytest.approx(1.05 / 1.02)
    assert (w["r_pu"], w["x_pu"]) == pytest.approx((0.01 * 1.02**2, 0.1 * 1.02**2))
    assert w["shift_deg"] == 30.0 and w["rate_mva"] == 50.0 and w["in_service"]


def test_two_winding_cz2_rebases():
    _, w = _two_winding(cz=2, sb=40.0, w1=(1.05, 121.0))
    scale = 100.0 / 40.0 * (121.0 / 110.0) ** 2
    assert (w["r_pu"], w["x_pu"]) == pytest.approx(
        (0.01 * scale * 1.02**2, 0.1 * scale * 1.02**2)
    )


def test_two_winding_cz3_from_load_loss():
    _, w = _two_winding(cz=3, z=(20000.0, 0.12), sb=40.0)
    r = 20000.0 * 1e-6 / 40.0
    x = math.sqrt(0.12**2 - r**2)
    scale = 100.0 / 40.0
    assert (w["r_pu"], w["x_pu"]) == pytest.approx(
        (r * scale * 1.02**2, x * scale * 1.02**2)
    )


def test_two_winding_cw2_kv():
    _, w = _two_winding(cw=2, w1=(115.5, 0.0), w2=(112.2, 0.0))
    t1, t2 = 115.5 / 110.0, 112.2 / 110.0
    assert w["tap"] == pytest.approx(t1 / t2)
    assert w["x_pu"] == pytest.approx(0.1 * t2**2)


def test_two_winding_cw3_nomv():
    _, w = _two_winding(cw=3, w1=(1.0, 121.0), w2=(1.0, 99.0))
    t1, t2 = 121.0 / 110.0, 99.0 / 110.0
    assert w["tap"] == pytest.approx(t1 / t2)
    assert w["x_pu"] == pytest.approx(0.1 * t2**2)


def test_magnetising_cm1_passes_through():
    _, w = _two_winding(mag=(0.001, -0.004))
    assert (w["g_pu"], w["b_pu"]) == (0.001, -0.004)


def test_magnetising_cm2_from_loss_and_current():
    _, w = _two_winding(cm=2, mag=(30000.0, 0.01), sb=40.0, w1=(1.0, 121.0))
    vs = (110.0 / 121.0) ** 2
    g = 30000.0 * 1e-6 / 100.0 * vs
    y = 0.01 * 40.0 / 100.0 * vs
    assert w["g_pu"] == pytest.approx(g)
    assert w["b_pu"] == pytest.approx(-math.sqrt(y**2 - g**2))


def test_cz3_impedance_below_resistance_refused():
    with pytest.raises(ContractError, match="below its resistance"):
        _two_winding(cz=3, z=(2e6, 0.01), sb=40.0)


def test_two_winding_mag_file():
    c = read_raw(FIX / "two_winding_mag_test.raw")
    (w,) = transformer_windings(c, c.tables["transformer"].iloc[0])
    assert (w["bus"], w["to"]) == (2, 1)
    assert (w["g_pu"], w["b_pu"]) == (5.0e-3, 6.74e-3)
    assert w["tap"] == pytest.approx(1.05 / 1.0) and w["shift_deg"] == -1.0


# ---------------------------------------------------------------- three-winding
def test_three_winding_star_and_status():
    c = read_raw(FIX / "three_winding_test.raw")
    z12, z23, z31 = (4.5e-3, 0.2), (7e-4, 0.3), (7e-4, 0.2)
    star = [
        (
            (z12[k] + z31[k] - z23[k]) / 2,
            (z23[k] + z12[k] - z31[k]) / 2,
            (z31[k] + z23[k] - z12[k]) / 2,
        )
        for k in (0, 1)
    ]
    out_by_stat = {0: {1, 2, 3}, 1: set(), 2: {2}, 3: {3}, 4: {1}}
    for _, tr in c.tables["transformer"].iterrows():
        ws = transformer_windings(c, tr)
        assert [w["bus"] for w in ws] == [1001, 1002, 1003] and all(
            w["to"] is None for w in ws
        )
        for w in ws:
            assert w["r_pu"] == pytest.approx(star[0][w["winding"] - 1])
            assert w["x_pu"] == pytest.approx(star[1][w["winding"] - 1])
        assert [w["tap"] for w in ws] == pytest.approx([1.1, 1.0, 1.0])
        assert {w["winding"] for w in ws if not w["in_service"]} == out_by_stat[
            tr["STAT"]
        ]
    assert sorted(c.tables["transformer"]["STAT"]) == [0, 1, 2, 3, 4]


def test_three_winding_mag_on_winding_one():
    c = read_raw(FIX / "three_winding_mag_test.raw")
    ws = transformer_windings(c, c.tables["transformer"].iloc[0])
    assert [(w["g_pu"], w["b_pu"]) for w in ws] == [
        (5e-3, 6.74e-3),
        (0.0, 0.0),
        (0.0, 0.0),
    ]


@pytest.mark.parametrize(
    "name, taps",
    [
        ("case4_3wtf_vnom0_cw2", (400.0 / 380.0, 120.0 / 110.0, 30.0 / 30.0)),
        ("case4_3wtf_vnom0_cw3", (1.0, 1.0, 1.0)),  # NOMV = 0 means the bus base
    ],
)
def test_three_winding_cw2_cw3_cz2(name, taps):
    c = read_raw(FIX / f"{name}.raw")
    tr = c.tables["transformer"].iloc[0]
    ws = transformer_windings(c, tr)
    assert [w["tap"] for w in ws] == pytest.approx(taps)
    # CZ = 2 with NOMV = 0: each pair re-based by SBASE / SBASEij only
    z12 = (0.0026 * 100 / 350, 0.209984 * 100 / 350)
    z23 = (0.0016 * 100 / 50, 0.0699817 * 100 / 50)
    z31 = (0.0016 * 100 / 50, 0.0999872 * 100 / 50)
    assert ws[0]["x_pu"] == pytest.approx((z12[1] + z31[1] - z23[1]) / 2)
    assert ws[1]["x_pu"] == pytest.approx((z23[1] + z12[1] - z31[1]) / 2)
    assert ws[2]["x_pu"] == pytest.approx((z31[1] + z23[1] - z12[1]) / 2)
    assert ws[2]["shift_deg"] == -30.0
    assert (tr["VMSTAR"], tr["ANSTAR"]) == (0.95174, -0.001)


# ---------------------------------------------------------------- our own exports
@pytest.fixture(scope="module")
def case39():
    from gridspine.ingest.pandapower_source import load_case39_res

    return load_case39_res()


@pytest.mark.parametrize("version", [33, 35, 36])
def test_case39_export_reads_back(case39, tmp_path, version):
    path = tmp_path / "c39.raw"
    write_raw(case39, path, f_hz=60.0, version=version)
    c = read_raw(path)
    assert (c.version, c.sbase, c.basfrq) == (version, 100.0, 60.0)
    net = case39
    assert _counts(c) == {
        "bus": len(net.bus),
        "load": len(net.load),
        "branch": len(net.line),
        "transformer": len(net.trafo),
        "generator": len(net.gen) + len(net.ext_grid) + len(net.sgen),
    }
    assert c.unmapped == [] and c.assumed == []
    assert list(c.tables["bus"]["NAME"]) == list(net.bus["name"])
    assert np.allclose(c.tables["bus"]["BASKV"], net.bus["vn_kv"])
    p, q = load_at_nominal(c.tables["load"])
    assert np.allclose(p, net.load["p_mw"], atol=1e-3) and np.allclose(
        q, net.load["q_mvar"], atol=1e-3
    )
    for (_, ln), (_, br) in zip(net.line.iterrows(), c.tables["branch"].iterrows()):
        phys = branch_physical(c, br)
        assert phys["r_ohm"] == pytest.approx(
            ln["r_ohm_per_km"] * ln["length_km"] / ln["parallel"], rel=1e-4, abs=1e-4
        )
        assert phys["x_ohm"] == pytest.approx(
            ln["x_ohm_per_km"] * ln["length_km"] / ln["parallel"], rel=1e-4, abs=1e-4
        )
        b = (
            2
            * math.pi
            * 60.0
            * ln["c_nf_per_km"]
            * 1e-9
            * ln["length_km"]
            * ln["parallel"]
        )
        assert phys["b_siemens"] == pytest.approx(b, rel=1e-4, abs=1e-8)
    if version >= 35:
        assert list(c.tables["branch"]["NAME"]) == [
            str(n)[:40] for n in net.line["name"].fillna("")
        ]
