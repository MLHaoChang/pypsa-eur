"""
GC-04 part 2: the PSS/E .seq writer, keyed on the RAW's identities.

Expected numbers are worked by hand from the campus fixture
(``tests.gridspine.test_campus.campus_spec``): PCC 110 kV, MV1/MV2 20 kV,
TR1/TR2 40 MVA 110/21 kV vk 12 % vkr 0.4 %, grid 3000 MVA R/X 0.1, cable CB1
2 km of 0.0754 + j0.11 ohm/km, 330 nF/km.
"""

import copy

import pytest

from gridspine.handoff.raw_writer import SBASE_MVA, write_raw
from gridspine.handoff.seq_writer import NOT_WRITTEN, OPEN_PU, connection_code, write_seq
from gridspine.ingest.campus import build_campus
from gridspine.schema.contracts import ContractError
from gridspine.static.island_sc_seq import sequence_net
from tests.gridspine.test_campus import campus_spec, t
from tests.gridspine.test_island_schema import genset
from tests.gridspine.test_raw_v35 import read_raw


def _spec():
    return copy.deepcopy(campus_spec())


def _read(path):
    """``{section: [token lists]}``, sections named by their closing terminator."""
    lines = path.read_text().splitlines()
    assert lines[-1] == "Q"
    out, cur = {}, []
    for ln in lines:
        if ln.startswith("@!") or ln.startswith("0, ") or ln == "Q":
            continue
        if ln.startswith("0 /"):
            out[ln.split("END OF ", 1)[1].split(" DATA", 1)[0]] = cur
            cur = []
        else:
            cur.append([x.strip().strip("'").strip() for x in ln.split(",")])
    return out


def _write(campus, tmp_path, **kw):
    path = tmp_path / "c.seq"
    assumed = write_seq(campus, path, **kw)
    return _read(path), assumed


# --- connection codes (winding 1 = HV) ---------------------------------------


@pytest.mark.parametrize(
    "w, cc",
    [
        (
            {"hv": "Y", "hv_earth": "solid", "lv": "D", "lv_earth": None},
            12,
        ),  # YNd: earth path on winding 1
        (
            {"hv": "D", "hv_earth": None, "lv": "Y", "lv_earth": "solid"},
            13,
        ),  # Dyn: earth path on winding 2
        (
            {"hv": "Y", "hv_earth": "resistance", "lv": "Y", "lv_earth": "solid"},
            11,
        ),  # YNyn: series path
        (
            {"hv": "Y", "hv_earth": "isolated", "lv": "D", "lv_earth": None},
            14,
        ),  # Yd unearthed: no path
        ({"hv": "D", "hv_earth": None, "lv": "D", "lv_earth": None}, 14),
        (
            {"hv": "Y", "hv_earth": "solid", "lv": "Y", "lv_earth": "isolated"},
            14,
        ),  # YNy: open, as island_sc_seq
    ],
)
def test_connection_code(w, cc):
    assert connection_code(w) == cc


@pytest.mark.parametrize("group, cc", [("YNd5", 12), ("Dyn11", 13), ("YNyn0", 11)])
def test_vector_group_reaches_the_file(tmp_path, group, cc):
    s = _spec()
    s["campus"]["transformers"]["TR1"].update(vector_group=group)
    recs, _ = _write(build_campus(s), tmp_path)
    tr = recs["ZERO SEQ. TRANSFORMER"]
    assert [r[3] for r in tr] == ["1", "2"]
    assert tr[0][6] == str(cc)
    assert tr[1][6] == "13"  # TR2 keeps the default Dyn11


def test_transformer_without_windings_takes_the_default_and_says_so(tmp_path):
    c = build_campus(_spec())
    c.windings.pop("TR1")
    recs, assumed = _write(c, tmp_path)
    assert recs["ZERO SEQ. TRANSFORMER"][0][6] == "13"
    assert any("TR1" in a and "Dyn11" in a for a in assumed)


# --- zero-sequence transformer impedance on SBASE (rating 40 MVA != 100) -------


def test_transformer_zero_sequence_on_system_base(tmp_path):
    recs, _ = _write(build_campus(_spec()), tmp_path)
    r = recs["ZERO SEQ. TRANSFORMER"][0]
    # I, J, K, CKT, CZ0, CZG: HV bus 1, LV bus 2, two-winding, system base
    assert r[:6] == ["1", "2", "0", "1", "1", "1"]
    # conv = 100/40 * (21/20)**2 = 2.75625; R = 0.004 * conv; X = sqrt((0.12 conv)**2 - R**2)
    assert float(r[9]) == pytest.approx(0.011025, rel=1e-5)
    assert float(r[10]) == pytest.approx(0.330566, rel=1e-5)
    assert [float(x) for x in (r[7], r[8], r[11], r[12])] == [
        0.0,
        0.0,
        0.0,
        0.0,
    ]  # solid / delta: no RG


def test_transformer_neutral_resistance_on_system_base(tmp_path):
    s = _spec()
    s["campus"]["transformers"]["TR2"].update(
        vector_group="Dyn11", earthing_lv="resistance", r_n_lv_ohm=t(20.0)
    )
    recs, _ = _write(build_campus(s), tmp_path)
    r = recs["ZERO SEQ. TRANSFORMER"][1]
    assert r[6] == "13"
    assert float(r[7]) == 0.0
    assert float(r[11]) == pytest.approx(
        20.0 / (20.0**2 / 100.0)
    )  # RG2 = 5 pu on 100 MVA at 20 kV


# --- machines ------------------------------------------------------------------


def test_grid_record(tmp_path):
    recs, assumed = _write(build_campus(_spec()), tmp_path)
    g = recs["GENERATOR"][0]
    # Z1 = 100/3000 pu at R/X 0.1: X = 0.0333333/sqrt(1.01) = 0.0331679, R = 0.1 X
    assert (g[0], g[1]) == ("1", "1")
    x1 = 0.0331679
    assert [float(v) for v in g[2:10]] == pytest.approx(
        [0.1 * x1, x1, x1, x1, 0.1 * x1, x1, 0.1 * x1, x1], rel=1e-5
    )
    assert (g[10], float(g[11]), float(g[12]), float(g[13])) == (
        "1",
        0.0,
        0.0,
        0.0,
    )  # CZG, ZRG, ZXG, REFDEG
    assert any("grid GRID" in a for a in assumed)


def test_grid_zero_sequence_from_the_pcc_data(tmp_path):
    s = _spec()
    s["campus"]["pcc"].update(x0x_max=t(1.5), r0x0_max=t(0.2))
    recs, assumed = _write(build_campus(s), tmp_path)
    g = recs["GENERATOR"][0]
    x0 = 1.5 * 0.0331679
    assert (float(g[8]), float(g[9])) == pytest.approx((0.2 * x0, x0), rel=1e-5)
    assert not any("grid GRID" in a for a in assumed)


def test_min_case_uses_min_short_circuit_level(tmp_path):
    recs, _ = _write(build_campus(_spec()), tmp_path, case="min")
    x1 = 100.0 / 2000.0 / (1 + 0.12**2) ** 0.5
    assert float(recs["GENERATOR"][0][3]) == pytest.approx(x1, rel=1e-5)


def test_genset_with_island_data(tmp_path):
    s = _spec()
    g = genset()
    g.update(
        x2_pu=t(0.17, "datasheet"),
        x0_pu=t(0.06, "datasheet"),
        neutral_earthing="resistance",
        r_n_ohm=t(10.0),
    )
    s["campus"]["units"]["GEN1"]["island"] = g
    recs, _ = _write(build_campus(s), tmp_path)
    gen = recs["GENERATOR"][-1]  # GEN1 is the last sgen; MV2 is bus 3
    assert (gen[0], gen[1]) == ("3", "1")
    # x''d 0.15, rx 0.07, on MBASE = 12.5 MVA; R_N 10 ohm / (20**2 / 12.5) = 0.3125 pu
    want = [
        0.0105,
        0.15,
        0.15,
        0.15,
        0.07 * 0.17,
        0.17,
        0.07 * 0.06,
        0.06,
        1,
        0.3125,
        0.0,
    ]
    assert [float(v) for v in gen[2:13]] == pytest.approx(want, rel=1e-6)


def test_genset_without_island_data_is_unearthed_and_drafted(tmp_path):
    recs, assumed = _write(build_campus(_spec()), tmp_path)
    gen = recs["GENERATOR"][-1]
    assert [float(v) for v in gen[6:10]] == pytest.approx(
        [0.0105, 0.15, 0.0042, 0.06]
    )  # x2 = x''d, x0 = 0.4 x''d
    assert float(gen[11]) == OPEN_PU
    assert any("GEN1" in a and "x0 = 0.4" in a for a in assumed)
    assert any("GEN1" in a and "unearthed" in a for a in assumed)


def test_converter_is_open_except_given_negative_sequence(tmp_path):
    recs, _ = _write(build_campus(_spec()), tmp_path)
    bess, pv = recs["GENERATOR"][1], recs["GENERATOR"][2]
    assert (bess[0], bess[1], pv[0], pv[1]) == ("2", "1", "2", "2")
    for m in (bess, pv):
        assert (
            float(m[3]) == OPEN_PU and float(m[7]) == OPEN_PU and float(m[9]) == OPEN_PU
        )


def test_machine_ids_match_the_raw(tmp_path):
    c = build_campus(_spec())
    write_raw(c.net, tmp_path / "c.raw", version=35)
    raw = (
        (tmp_path / "c.raw")
        .read_text()
        .split("BEGIN GENERATOR DATA\n", 1)[1]
        .split("\n0 /", 1)[0]
    )
    raw_ids = [
        tuple(x.strip().strip("'").strip() for x in ln.split(",")[:2])
        for ln in raw.splitlines()
    ]
    recs, _ = _write(c, tmp_path)
    assert [tuple(r[:2]) for r in recs["GENERATOR"]] == raw_ids


# --- lines -------------------------------------------------------------------


def test_line_zero_sequence_drafted(tmp_path):
    recs, assumed = _write(build_campus(_spec()), tmp_path)
    (r,) = recs["ZERO SEQ. NON-TRANSFORMER BRANCH"]
    # Zbase 20**2/100 = 4 ohm; R0 = (0.0754 + 3 * 0.727) * 2 / 4; X0 = 0.11 * 2 / 4; B0 = 2 pi 50 * 330e-9 * 2 * 4
    assert r[:3] == ["2", "3", "1"]
    assert [float(v) for v in r[3:6]] == pytest.approx(
        [1.1282, 0.055, 8.29380e-4], rel=1e-5
    )
    assert any("CB1" in a for a in assumed)


def test_line_zero_sequence_given(tmp_path):
    s = _spec()
    s["campus"]["cables"]["CB1"].update(
        r0_ohm_per_km=t(1.2, "datasheet"),
        x0_ohm_per_km=t(0.4, "datasheet"),
        c0_nf_per_km=t(200.0),
    )
    recs, assumed = _write(build_campus(s), tmp_path)
    (r,) = recs["ZERO SEQ. NON-TRANSFORMER BRANCH"]
    assert [float(v) for v in r[3:6]] == pytest.approx([0.6, 0.2, 5.02655e-4], rel=1e-5)
    assert not any("CB1" in a for a in assumed)


# --- layout ------------------------------------------------------------------

#: Every v33 section, empty ones included, in the order PSS/E 34.0 wrote a rev-33 .seq.
V33_TERMINATORS = [
    "0 / END OF GENERATOR DATA, BEGIN LOAD DATA",
    "0 / END OF LOAD DATA, BEGIN ZERO SEQ. NON-TRANSFORMER BRANCH DATA",
    "0 / END OF ZERO SEQ. NON-TRANSFORMER BRANCH DATA, BEGIN ZERO SEQ. MUTUAL DATA",
    "0 / END OF ZERO SEQ. MUTUAL DATA, BEGIN ZERO SEQ. TRANSFORMER DATA",
    "0 / END OF ZERO SEQ. TRANSFORMER DATA, BEGIN ZERO SEQ. SWITCHED SHUNT DATA",
    "0 / END OF ZERO SEQ. SWITCHED SHUNT DATA, BEGIN ZERO SEQ. FIXED SHUNT DATA",
    "0 / END OF ZERO SEQ. FIXED SHUNT DATA, BEGIN INDUCTION MACHINE DATA",
    "0 / END OF INDUCTION MACHINE DATA",
]
#: Every v35 section, empty ones included, in the order PSS/E 35.0 wrote it.
V35_TERMINATORS = [
    "0 / END OF SYSTEM-WIDE DATA, BEGIN GENERATOR DATA",
    "0 / END OF GENERATOR DATA, BEGIN LOAD DATA",
    "0 / END OF LOAD DATA, BEGIN ZERO SEQ. NON-TRANSFORMER BRANCH DATA",
    "0 / END OF ZERO SEQ. NON-TRANSFORMER BRANCH DATA, BEGIN ZERO SEQ. MUTUAL DATA",
    "0 / END OF ZERO SEQ. MUTUAL DATA, BEGIN ZERO SEQ. TRANSFORMER DATA",
    "0 / END OF ZERO SEQ. TRANSFORMER DATA, BEGIN ZERO SEQ. SWITCHED SHUNT DATA",
    "0 / END OF ZERO SEQ. SWITCHED SHUNT DATA, BEGIN ZERO SEQ. FIXED SHUNT DATA",
    "0 / END OF ZERO SEQ. FIXED SHUNT DATA, BEGIN INDUCTION MACHINE DATA",
    "0 / END OF INDUCTION MACHINE DATA, BEGIN NON-CONVENTIONAL SOURCE TABLE DATA",
    "0 / END OF NON-CONVENTIONAL SOURCE TABLE DATA, BEGIN NON-CONVENTIONAL SOURCE MACHINE DATA",
    "0 / END OF NON-CONVENTIONAL SOURCE MACHINE DATA",
]


def test_v35_layout(tmp_path):
    c = build_campus(_spec())
    write_seq(c, tmp_path / "a.seq")
    lines = (tmp_path / "a.seq").read_text().splitlines()
    assert lines[:5] == [
        "@!IC, REV",
        "0, 35   / gridspine seq export",
        "RPTFORMAT, AMPOUT=0, POLROU=0, AMPOUTZ=0, POLROUZ=0",
        "MOV, ITERATIONS=20, TOLERANCE=0.01, MOVALPHA=0.3",
        "SCMODEL, SCNRML=1",
    ]
    assert [ln for ln in lines if ln.startswith("0 /")] == V35_TERMINATORS
    assert lines[-1] == "Q"


def test_v33_layout_has_no_system_wide_and_no_refdeg(tmp_path):
    c = build_campus(_spec())
    write_seq(c, tmp_path / "a.seq", version=33)
    lines = (tmp_path / "a.seq").read_text().splitlines()
    assert lines[0] == "0, 33   / gridspine seq export"
    assert lines[1].startswith("1,'1 ',")
    assert len(lines[1].split(",")) == 13  # I, ID .. ZXG: no REFDEG
    assert [ln for ln in lines if ln.startswith("0 /")] == V33_TERMINATORS


@pytest.mark.parametrize(
    "kw, match", [({"version": 36}, "SEQ version"), ({"case": "typ"}, "SEQ case")]
)
def test_bad_arguments_refused(tmp_path, kw, match):
    with pytest.raises(ContractError, match=match):
        write_seq(build_campus(_spec()), tmp_path / "x.seq", **kw)


# --- nothing missing silently (GK A2 Q4) ------------------------------------------


@pytest.mark.parametrize("version", [33, 35])
def test_omitted_earthing_transformer_is_named_in_header_and_return(tmp_path, version):
    s = _spec()
    s["campus"]["earthing_transformers"] = {
        "ET1": {"bus": "MV2", "x0_ohm": t(15.0, "datasheet"), "r_n_ohm": t(25.0)}
    }
    path = tmp_path / "c.seq"
    assumed = write_seq(build_campus(s), path, version=version)
    header = next(ln for ln in path.read_text().splitlines() if ln.startswith("0, "))
    assert header.endswith(f"; {NOT_WRITTEN}: ET1")
    assert f"seq: earthing transformer ET1: {NOT_WRITTEN}" in assumed


def test_no_earthing_transformer_no_header_note(tmp_path):
    path = tmp_path / "c.seq"
    write_seq(build_campus(_spec()), path)
    assert path.read_text().splitlines()[1] == "0, 35   / gridspine seq export"


def test_assumed_values_are_listed(tmp_path):
    s = _spec()
    s["campus"]["units"]["GEN1"]["island"] = genset()  # neutral_earthing as genset() gives
    _, assumed = _write(build_campus(s), tmp_path)
    text = "\n".join(assumed)
    assert "seq: grid GRID: no x0x_max; X0 = X1" in text
    assert "seq: transformer TR1: vk0 = vk (assumed)" in text
    assert "seq: genset GEN1: X' = Xs = x''d" in text
    assert f"OPEN_PU = {OPEN_PU:g} pu" in text


# --- against the island draft and the RAW (GK A2 Q3, test-side reader) -------------


def test_line_zero_sequence_equals_the_island_draft(tmp_path):
    """The drafted R0 / X0 are island_sc_seq's own (max case: no temperature
    factor), taken from its sequence network, not retyped."""
    c = build_campus(_spec())
    sn = sequence_net(c, {}, island=False, case="max")
    br = next(b for b in sn.branches if b.name == "CB1")
    want = (1.0 / br.y0) * SBASE_MVA / sn.sbase  # pu on the RAW's SBASE
    recs, _ = _write(c, tmp_path)
    (r,) = recs["ZERO SEQ. NON-TRANSFORMER BRANCH"]
    assert complex(float(r[3]), float(r[4])) == pytest.approx(want, rel=1e-5)


@pytest.mark.parametrize("version", [33, 35])
def test_branch_and_transformer_ids_match_the_raw(tmp_path, version):
    c = build_campus(_spec())
    write_raw(c.net, tmp_path / "c.raw", version=version)
    _, _, raw, _ = read_raw((tmp_path / "c.raw").read_text())
    recs, _ = _write(c, tmp_path, version=version)

    def ids(rows):
        return [(r[0], r[1], r[2].strip()) for r in rows]

    assert ids(recs["ZERO SEQ. NON-TRANSFORMER BRANCH"]) == ids(raw["BRANCH"])
    seq_tr = [(r[0], r[1], r[3]) for r in recs["ZERO SEQ. TRANSFORMER"]]
    assert seq_tr == [(t[0][0], t[0][1], t[0][3].strip()) for t in raw["TRANSFORMER"]]
    assert all(t[0][2] == "0" for t in raw["TRANSFORMER"])  # two-winding, as K = 0 in the .seq
