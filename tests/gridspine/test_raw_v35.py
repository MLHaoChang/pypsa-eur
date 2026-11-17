"""
GC-04 part 2: PSS/E RAW v35 and v36 beside the v33 default.

The field positions below were transcribed by hand from the ``@!`` header
lines of RAW files PSS/E 35.3, 35.6, 36.0 and 36.1 wrote themselves (the
licensed Program Operation Manual carries the same names). A test-side reader
parses each written file and compares it with the pandapower net itself, not
only with the v33 export, so a bug shared by all versions still fails.
"""

import csv
import io
from pathlib import Path

import numpy as np
import pytest

from gridspine.handoff.raw_writer import SBASE_MVA, trafo_impedance, write_raw
from gridspine.ingest.pandapower_source import load_case39
from gridspine.schema.contracts import ContractError

V35_TERMINATORS = [
    "0 / END OF SYSTEM-WIDE DATA, BEGIN BUS DATA",
    "0 / END OF BUS DATA, BEGIN LOAD DATA",
    "0 / END OF LOAD DATA, BEGIN FIXED SHUNT DATA",
    "0 / END OF FIXED SHUNT DATA, BEGIN GENERATOR DATA",
    "0 / END OF GENERATOR DATA, BEGIN BRANCH DATA",
    "0 / END OF BRANCH DATA, BEGIN SYSTEM SWITCHING DEVICE DATA",
    "0 / END OF SYSTEM SWITCHING DEVICE DATA, BEGIN TRANSFORMER DATA",
    "0 / END OF TRANSFORMER DATA, BEGIN AREA DATA",
    "0 / END OF AREA DATA, BEGIN TWO-TERMINAL DC DATA",
    "0 / END OF TWO-TERMINAL DC DATA, BEGIN VSC DC LINE DATA",
    "0 / END OF VSC DC LINE DATA, BEGIN IMPEDANCE CORRECTION DATA",
    "0 / END OF IMPEDANCE CORRECTION DATA, BEGIN MULTI-TERMINAL DC DATA",
    "0 / END OF MULTI-TERMINAL DC DATA, BEGIN MULTI-SECTION LINE DATA",
    "0 / END OF MULTI-SECTION LINE DATA, BEGIN ZONE DATA",
    "0 / END OF ZONE DATA, BEGIN INTER-AREA TRANSFER DATA",
    "0 / END OF INTER-AREA TRANSFER DATA, BEGIN OWNER DATA",
    "0 / END OF OWNER DATA, BEGIN FACTS DEVICE DATA",
    "0 / END OF FACTS DEVICE DATA, BEGIN SWITCHED SHUNT DATA",
    "0 / END OF SWITCHED SHUNT DATA, BEGIN GNE DATA",
    "0 / END OF GNE DATA, BEGIN INDUCTION MACHINE DATA",
    "0 / END OF INDUCTION MACHINE DATA, BEGIN SUBSTATION DATA",
    "0 / END OF SUBSTATION DATA",
]

V36_TERMINATORS = [
    "0 / END OF SYSTEM-WIDE DATA, BEGIN BUS DATA",
    "0 / END OF BUS DATA, BEGIN LOAD DATA",
    "0 / END OF LOAD DATA, BEGIN FIXED SHUNT DATA",
    "0 / END OF FIXED SHUNT DATA, BEGIN VOLTAGE DROOP CONTROL DATA",
    "0 / END OF VOLTAGE DROOP CONTROL DATA, BEGIN GENERATOR DATA",
    "0 / END OF GENERATOR DATA, BEGIN SWITCHING DEVICE RATING SET DATA",
    "0 / END OF SWITCHING DEVICE RATING SET DATA, BEGIN BRANCH DATA",
    "0 / END OF BRANCH DATA, BEGIN SYSTEM SWITCHING DEVICE DATA",
    "0 / END OF SYSTEM SWITCHING DEVICE DATA, BEGIN TRANSFORMER DATA",
    "0 / END OF TRANSFORMER DATA, BEGIN AREA DATA",
    "0 / END OF AREA DATA, BEGIN TWO-TERMINAL DC DATA",
    "0 / END OF TWO-TERMINAL DC DATA, BEGIN VSC DC LINE DATA",
    "0 / END OF VSC DC LINE DATA, BEGIN IMPEDANCE CORRECTION DATA",
    "0 / END OF IMPEDANCE CORRECTION DATA, BEGIN MULTI-TERMINAL DC DATA",
    "0 / END OF MULTI-TERMINAL DC DATA, BEGIN MULTI-SECTION LINE DATA",
    "0 / END OF MULTI-SECTION LINE DATA, BEGIN ZONE DATA",
    "0 / END OF ZONE DATA, BEGIN INTER-AREA TRANSFER DATA",
    "0 / END OF INTER-AREA TRANSFER DATA, BEGIN OWNER DATA",
    "0 / END OF OWNER DATA, BEGIN FACTS DEVICE DATA",
    "0 / END OF FACTS DEVICE DATA, BEGIN SWITCHED SHUNT DATA",
    "0 / END OF SWITCHED SHUNT DATA, BEGIN GNE DATA",
    "0 / END OF GNE DATA, BEGIN INDUCTION MACHINE DATA",
    "0 / END OF INDUCTION MACHINE DATA, BEGIN LOAD TYPE DATA",
    "0 / END OF LOAD TYPE DATA, BEGIN INTERFACE DATA",
    "0 / END OF INTERFACE DATA, BEGIN INTERFACE ELEMENT DATA",
    "0 / END OF INTERFACE ELEMENT DATA, BEGIN SUBSTATION DATA",
    "0 / END OF SUBSTATION DATA",
]

V35_SYSTEM_WIDE = [
    "GENERAL, THRSHZ=0.0001, PQBRAK=0.7, BLOWUP=5.0, MaxIsolLvls=4, CAMaxReptSln=20, ChkDupCntLbl=0",
    "GAUSS, ITMX=100, ACCP=1.6, ACCQ=1.6, ACCM=1.0, TOL=0.0001",
    "NEWTON, ITMXN=20, ACCN=1.0, TOLN=0.1, VCTOLQ=0.1, VCTOLV=0.00001, DVLIM=0.99, NDVFCT=0.99",
    "ADJUST, ADJTHR=0.005, ACCTAP=1.0, TAPLIM=0.05, SWVBND=100.0, MXTPSS=99, MXSWIM=10",
    "TYSL, ITMXTY=20, ACCTY=1.0, TOLTY=0.00001",
    "SOLVER, FNSL, ACTAPS=0, AREAIN=0, PHSHFT=0, DCTAPS=0, SWSHNT=0, FLATST=0, VARLIM=99, NONDIV=0",
    'RATING,  1, "RATE1 ", "RATING SET 1                    "',
    'RATING,  2, "RATE2 ", "RATING SET 2                    "',
    'RATING,  3, "RATE3 ", "RATING SET 3                    "',
    'RATING,  4, "RATE4 ", "RATING SET 4                    "',
    'RATING,  5, "RATE5 ", "RATING SET 5                    "',
    'RATING,  6, "RATE6 ", "RATING SET 6                    "',
    'RATING,  7, "RATE7 ", "RATING SET 7                    "',
    'RATING,  8, "RATE8 ", "RATING SET 8                    "',
    'RATING,  9, "RATE9 ", "RATING SET 9                    "',
    'RATING, 10, "RATE10", "RATING SET 10                   "',
    'RATING, 11, "RATE11", "RATING SET 11                   "',
    'RATING, 12, "RATE12", "RATING SET 12                   "',
]


# --- test-side reader --------------------------------------------------------


def _tokens(line):
    """Split a RAW record on commas, honouring single-quoted strings; strip padding."""
    row = next(csv.reader(io.StringIO(line), quotechar="'", skipinitialspace=True))
    return [t.strip() for t in row]


def read_raw(text):
    """
    ``(header tokens, system-wide lines, {section: [records]}, terminators)``.

    A section is named from the terminator that closes it. Transformer records
    are grouped four lines to a record (two-winding units only).
    """
    lines = text.splitlines()
    assert lines[-1] == "Q"
    header, body = _tokens(lines[0]), lines[3:-1]
    sections, terms, cur = {}, [], []
    for ln in body:
        if ln.startswith("0 /"):
            terms.append(ln)
            name = ln.split("END OF ", 1)[1].split(" DATA", 1)[0]
            sections[name] = cur
            cur = []
        else:
            cur.append(ln)
    system_wide = sections.pop("SYSTEM-WIDE", None)
    recs = {k: [_tokens(x) for x in v] for k, v in sections.items()}
    tr = recs.get("TRANSFORMER", [])
    recs["TRANSFORMER"] = [tr[k : k + 4] for k in range(0, len(tr), 4)]
    return header, system_wide, recs, terms


# --- network-side expectations, computed from the net, not from the writer ---


def _net_lines(net):
    """{(i, j, ckt): (r, x, b)} in pu on SBASE, straight from pandapower's line table."""
    num = {b: k + 1 for k, b in enumerate(net.bus.index)}
    out, seen = {}, {}
    for _, ln in net.line.iterrows():
        i, j = num[ln["from_bus"]], num[ln["to_bus"]]
        key = (min(i, j), max(i, j))
        seen[key] = seen.get(key, 0) + 1
        zb = net.bus.at[ln["from_bus"], "vn_kv"] ** 2 / SBASE_MVA
        r = ln["r_ohm_per_km"] * ln["length_km"] / ln["parallel"] / zb
        x = ln["x_ohm_per_km"] * ln["length_km"] / ln["parallel"] / zb
        b = (
            2
            * np.pi
            * 50.0
            * ln["c_nf_per_km"]
            * 1e-9
            * ln["length_km"]
            * ln["parallel"]
            * zb
        )
        out[(i, j, str(seen[key]))] = (r, x, b)
    return out


@pytest.fixture(scope="module")
def net():
    return load_case39()


@pytest.fixture(scope="module")
def written(net, tmp_path_factory):
    d = tmp_path_factory.mktemp("raw")
    out = {}
    for v in (33, 35, 36):
        write_raw(net, d / f"c39_v{v}.raw", version=v)
        out[v] = (d / f"c39_v{v}.raw").read_text()
    return out


# --- R1: every version against the net ---------------------------------------


@pytest.mark.parametrize("version", [33, 35, 36])
def test_counts_and_identities_match_the_net(net, written, version):
    _, _, recs, _ = read_raw(written[version])
    assert [int(r[0]) for r in recs["BUS"]] == list(range(1, len(net.bus) + 1))
    assert [r[1] for r in recs["BUS"]] == list(net.bus["name"])
    assert len(recs["LOAD"]) == len(net.load)
    assert len(recs["GENERATOR"]) == len(net.gen) + len(net.ext_grid)
    num = {b: k + 1 for k, b in enumerate(net.bus.index)}
    expect_mach = sorted(
        [num[b] for b in net.gen["bus"]] + [num[b] for b in net.ext_grid["bus"]]
    )
    assert sorted(int(r[0]) for r in recs["GENERATOR"]) == expect_mach
    assert all(r[1] == "1" for r in recs["GENERATOR"])  # case39: one machine per bus
    assert len(recs["BRANCH"]) == len(net.line)
    assert len(recs["TRANSFORMER"]) == len(net.trafo)
    hv_lv = sorted(
        (num[h], num[lv]) for h, lv in zip(net.trafo["hv_bus"], net.trafo["lv_bus"])
    )
    assert sorted((int(t[0][0]), int(t[0][1])) for t in recs["TRANSFORMER"]) == hv_lv


@pytest.mark.parametrize("version", [33, 35, 36])
def test_branch_rxb_match_the_net(net, written, version):
    _, _, recs, _ = read_raw(written[version])
    got = {
        (int(r[0]), int(r[1]), r[2]): tuple(float(v) for v in r[3:6])
        for r in recs["BRANCH"]
    }
    want = _net_lines(net)
    assert set(got) == set(want)
    for k, (r, x, b) in want.items():
        assert got[k] == pytest.approx((r, x, b), abs=6e-6), k


# --- v35/v36 against v33, field by field --------------------------------------


@pytest.mark.parametrize("version", [35, 36])
def test_same_buses_and_branches_as_v33(written, version):
    _, _, r33, _ = read_raw(written[33])
    _, _, rv, _ = read_raw(written[version])
    assert rv["BUS"] == r33["BUS"]
    assert rv["LOAD"] == r33["LOAD"]
    assert [r[:6] for r in rv["BRANCH"]] == [r[:6] for r in r33["BRANCH"]]
    for a, b in zip(rv["TRANSFORMER"], r33["TRANSFORMER"]):
        assert a[0] == b[0] and a[1] == b[1] and a[3] == b[3]


def test_v33_default_unchanged(net, tmp_path):
    write_raw(net, tmp_path / "a.raw")
    write_raw(net, tmp_path / "b.raw", version=33)
    assert (tmp_path / "a.raw").read_bytes() == (tmp_path / "b.raw").read_bytes()
    text = (tmp_path / "a.raw").read_text()
    assert "SYSTEM-WIDE" not in text and "SUBSTATION" not in text


#: Written by master's ``write_raw(load_case39())`` at master
#: 58d4eb5ae738d697236575580fd2b0d7e83615e3 (before the v35/v36 work merged).
GOLDEN_V33 = Path(__file__).parent / "data" / "case39_v33.raw"


def test_v33_default_matches_master_golden(net, tmp_path):
    """The v33 default is byte-identical to what master wrote before the
    ``version=`` argument existed (GK PR2 follow-up)."""
    write_raw(net, tmp_path / "a.raw")
    assert (tmp_path / "a.raw").read_bytes() == GOLDEN_V33.read_bytes()


# --- golden header, system-wide block and terminators -------------------------


def test_v35_golden_header(written):
    lines = written[35].splitlines()
    assert (
        lines[0] == " 0, 100.00, 35, 0, 0, 50.00"
    )  # IC, SBASE, REV, XFRRAT, NXFRAT, BASFRQ
    assert lines[1] == "gridspine export"
    assert lines[2] == "gridspine v35 export"


def test_v35_system_wide_block(written):
    _, sw, _, _ = read_raw(written[35])
    assert sw == V35_SYSTEM_WIDE


def test_v36_system_wide_adds_swd_rating_names(written):
    _, sw, _, _ = read_raw(written[36])
    assert sw[: len(V35_SYSTEM_WIDE)] == V35_SYSTEM_WIDE
    assert (
        sw[len(V35_SYSTEM_WIDE)]
        == 'SWDRATNAM,  1, "RATE1 ", 0, "MVA", "SWD RATING SET 1                "'
    )
    assert len(sw) == len(V35_SYSTEM_WIDE) + 12
    assert all(x.startswith("SWDRATNAM, ") for x in sw[len(V35_SYSTEM_WIDE) :])


def test_v35_terminators_in_order(written):
    assert read_raw(written[35])[3] == V35_TERMINATORS


def test_v36_terminators_in_order(written):
    assert read_raw(written[36])[3] == V36_TERMINATORS


def test_v36_header_rev(written):
    assert _tokens(written[36].splitlines()[0])[2] == "36"


def test_basfrq_passes_through(net, tmp_path):
    write_raw(net, tmp_path / "c.raw", f_hz=60.0, version=35)
    assert _tokens((tmp_path / "c.raw").read_text().splitlines()[0])[5] == "60.00"


# --- each v35 record difference ----------------------------------------------


def test_v35_generator_nreg_and_baslod_positions(written):
    g33 = read_raw(written[33])[2]["GENERATOR"][0]
    g35 = read_raw(written[35])[2]["GENERATOR"][0]
    # v33: I,ID,PG,QG,QT,QB,VS,IREG,MBASE,...,PT,PB,O1,F1 (20)
    # v35: I,ID,PG,QG,QT,QB,VS,IREG,NREG,MBASE,...,PT,PB,BASLOD,O1,F1 (22)
    assert len(g33) == 20 and len(g35) == 22
    assert g35[:8] == g33[:8]
    assert g35[8] == "0"  # NREG
    assert g35[9:18] == g33[8:17]  # MBASE .. PT
    assert g35[18] == g33[17]  # PB
    assert g35[19] == "0"  # BASLOD
    assert g35[20:] == g33[18:]  # O1, F1


def test_v35_branch_name_and_twelve_ratings(net, written):
    b33 = read_raw(written[33])[2]["BRANCH"][0]
    b35 = read_raw(written[35])[2]["BRANCH"][0]
    # v35: I,J,CKT,R,X,B,'NAME',RATE1..RATE12,GI,BI,GJ,BJ,STAT,MET,LEN,O1,F1
    assert len(b35) == len(b33) - 3 + 1 + 12
    assert (
        net.line["name"].iloc[0] is None and b35[6] == ""
    )  # an unnamed line: blank NAME, never 'None'
    assert b35[7:10] == b33[6:9]  # RATE1-3 = v33 RATEA-C
    assert b35[10:19] == ["0.00"] * 9
    assert b35[19:] == b33[9:]


def test_v35_branch_name_is_the_line_name(tmp_path):
    n = load_case39()
    n.line["name"] = [f"LN_{k}" for k in range(len(n.line))]
    write_raw(n, tmp_path / "n.raw", version=35)
    names = [r[6] for r in read_raw((tmp_path / "n.raw").read_text())[2]["BRANCH"]]
    assert names == [f"LN_{k}" for k in range(len(n.line))]


def test_v35_transformer_line3_ratings_and_nod1(written):
    t33 = read_raw(written[33])[2]["TRANSFORMER"][0][2]
    t35 = read_raw(written[35])[2]["TRANSFORMER"][0][2]
    # v33 L3 (17): WINDV1,NOMV1,ANG1,RATA1,RATB1,RATC1,COD1,CONT1,RMA1,...,CNXA1
    # v35 L3 (27): WINDV1,NOMV1,ANG1,RATE1-1..12,COD1,CONT1,NOD1,RMA1,...,CNXA1
    assert len(t33) == 17 and len(t35) == 27
    assert t35[:3] == t33[:3]
    assert t35[3:6] == t33[3:6]
    assert t35[6:15] == ["0.00"] * 9
    assert t35[15:17] == t33[6:8]  # COD1, CONT1
    assert t35[17] == "0"  # NOD1
    assert t35[18:] == t33[8:]


def test_v35_switching_device_section_sits_between_branch_and_transformer(written):
    terms = read_raw(written[35])[3]
    i = terms.index("0 / END OF BRANCH DATA, BEGIN SYSTEM SWITCHING DEVICE DATA")
    assert (
        terms[i + 1]
        == "0 / END OF SYSTEM SWITCHING DEVICE DATA, BEGIN TRANSFORMER DATA"
    )


@pytest.mark.parametrize("bad", [34, 37, "35", None])
def test_unknown_version_refused(net, tmp_path, bad):
    with pytest.raises(ContractError, match="RAW version"):
        write_raw(net, tmp_path / "x.raw", version=bad)


# --- trafo_impedance by hand (shared with the .seq) ---------------------------


def _trafo_net(tap_side):
    import pandapower as pp

    n = pp.create_empty_network()
    hv, lv = pp.create_bus(n, 110.0), pp.create_bus(n, 20.0)
    pp.create_transformer_from_parameters(
        n,
        hv,
        lv,
        sn_mva=40.0,
        vn_hv_kv=110.0,
        vn_lv_kv=21.0,
        vkr_percent=0.4,
        vk_percent=12.0,
        pfe_kw=0.0,
        i0_percent=0.0,
        parallel=2,
        tap_side=tap_side,
        tap_neutral=0,
        tap_min=-5,
        tap_max=5,
        tap_step_percent=2.0,
        tap_pos=1,
        tap_changer_type="Ratio",
    )
    return n


@pytest.mark.parametrize("tap_side", ["hv", "lv"])
def test_trafo_impedance_by_hand(tap_side):
    n = _trafo_net(tap_side)
    r, x, t1, t2 = trafo_impedance(n, n.trafo.iloc[0])
    lv_eff = 21.0 * (1.02 if tap_side == "lv" else 1.0)
    conv = (
        100.0 / 40.0 * (lv_eff / 20.0) ** 2 / 2
    )  # SBASE / Sn, LV base, two in parallel
    assert r == pytest.approx(0.004 * conv)
    assert x == pytest.approx(np.sqrt((0.12 * conv) ** 2 - (0.004 * conv) ** 2))
    assert (t1, t2) == pytest.approx(
        (1.02, 21.0 / 20.0) if tap_side == "hv" else (1.0, 21.0 * 1.02 / 20.0)
    )


def test_v35_nan_line_name_written_blank(tmp_path):
    n = load_case39()
    n.line["name"] = np.nan
    write_raw(n, tmp_path / "n.raw", version=35)
    assert {r[6] for r in read_raw((tmp_path / "n.raw").read_text())[2]["BRANCH"]} == {
        ""
    }
