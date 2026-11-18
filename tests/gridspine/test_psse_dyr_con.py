# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
PSS/E import 02: a .dyr and a .con read alongside the .raw (IMPORTS-1).

GK A plan GO D1-D7 in .scratch/psse-import/issues/02-dyr-con.md. The hand-written
files are the oracle (D4); the round trips through our own writers come after.
"""

import json
import logging

import pytest

from gridspine.handoff.raw_writer import _bus_numbers, write_raw
from gridspine.ingest import import_result
from gridspine.ingest.import_result import ImportFormatError
from gridspine.ingest.psse_dyr_parse import machine_id, read_dyr_text
from gridspine.ingest.psse_import import PsseNetIndex, read_psse
from gridspine.schema.contracts import ContractError
from tests.gridspine.test_psse_import import FIX, _case, _gen

logging.getLogger("pypsa").setLevel(logging.ERROR)

# Bus 1 'A' 110 kV swing, bus 2 'B' 110 kV, bus 3 'A' 20 kV: the RAW name 'A'
# repeats, so buses 1 and 3 are renamed "A #1" and "A #3" (RAW-NAMES-1).
_BUSES = ["1,'A', 110.0,3", "2,'B', 110.0,2", "3,'A', 20.0,1"]
_GENS = [_gen(1, "1"), _gen(2, "1", mbase=50.0), _gen(2, "A", mbase=60.0)]
# 1-2 is a line; 2-3 joins 110 kV and 20 kV, so it is a 1:1 transformer.
_BRANCHES = ["1,2,'1',0.01,0.1,0.0,100.0", "2,3,'1',0.01,0.1,0.0,100.0"]


@pytest.fixture
def raw(tmp_path):
    _case(tmp_path, BUS=_BUSES, GENERATOR=_GENS, BRANCH=_BRANCHES)
    return tmp_path / "c.raw"


def _import(raw, tmp_path, dyr=None, con=None):
    paths = [raw]
    if dyr is not None:
        (tmp_path / "c.dyr").write_text(dyr)
        paths.append(tmp_path / "c.dyr")
    if con is not None:
        (tmp_path / "c.con").write_text(con)
        paths.append(tmp_path / "c.con")
    return read_psse(paths, {})


def _records(res):
    return res.sidecars["psse_dynamics.json"]["records"]


def _reasons(res):
    return {u.source_item: u.reason for u in res.report.unmapped}


# ---------------------------------------------------------------- hand-written .dyr (D4)
_DYR = """\
@! one model per family, values typed by hand
1 'GENROU' 1 6.5 0.05 0.6 0.07 4.0 0.0 1.8 1.7
   0.3 0.55 0.25 0.2 0.1 0.4 /  machine at the swing bus
2 'SEXS' '1 ' 0.1 10.0 100.0 0.05 -4.0 4.0 /
2 'DEGOV1' 1 0 0.0 0.0 0.0 30.0 0.25 1.0 0.5 0.0 1.1 0.0 0.05 1.0 /
2 'REGCA1' 'A' 1 0.02 10.0 0.9 0.4 1.22 1.2 0.5 0.0 0.0 0.02 0.0 999.0 -999.0 0.7 /
2 'REPCA1' 'A' 0 1 2 '1 ' 1 0 1
   0.02 18.0 5.0 0.0 0.075 -0.1 0.0 0.0 0.0 0.1
   -0.1 0.0 0.0 0.4 -0.4 0.1 0.05 0.25 -0.006 0.006
   999.0 -999.0 1.0 0.0 0.1 0.0 0.0 /
"""

_GENROU = {
    "t_do_p": 6.5, "t_do_pp": 0.05, "t_qo_p": 0.6, "t_qo_pp": 0.07, "h_s": 4.0,
    "d": 0.0, "xd": 1.8, "xq": 1.7, "xd_p": 0.3, "xq_p": 0.55, "xd_pp": 0.25,
    "xl": 0.2, "s1": 0.1, "s12": 0.4,
}  # fmt: skip
_SEXS = {"ta_tb": 0.1, "tb": 10.0, "k": 100.0, "te": 0.05, "emin": -4.0, "emax": 4.0}
_DEGOV1 = {
    "m": 0, "t1": 0.0, "t2": 0.0, "t3": 0.0, "k": 30.0, "t4": 0.25, "t5": 1.0,
    "t6": 0.5, "td": 0.0, "tmax": 1.1, "tmin": 0.0, "droop": 0.05, "te": 1.0,
}  # fmt: skip
_REGCA1 = {
    "regc_lvplsw": 1, "regc_tg": 0.02, "regc_rrpwr": 10.0, "regc_brkpt": 0.9,
    "regc_zerox": 0.4, "regc_lvpl1": 1.22, "regc_volim": 1.2, "regc_lvpnt1": 0.5,
    "regc_lvpnt0": 0.0, "regc_iolim": 0.0, "regc_tfltr": 0.02, "regc_khv": 0.0,
    "regc_iqrmax": 999.0, "regc_iqrmin": -999.0, "regc_accel": 0.7,
}  # fmt: skip


def test_hand_written_dyr_one_model_per_family(raw, tmp_path):
    res = _import(raw, tmp_path, dyr=_DYR)
    got = {r["model"]: r for r in _records(res)}
    assert list(got) == ["GENROU", "SEXS", "DEGOV1", "REGCA1", "REPCA1"]
    assert got["GENROU"]["parameters"] == _GENROU
    assert got["SEXS"]["parameters"] == _SEXS
    assert got["DEGOV1"]["parameters"] == _DEGOV1
    assert got["REGCA1"]["parameters"] == _REGCA1
    repc = got["REPCA1"]["parameters"]
    assert [repc[k] for k in ("busr", "bus1", "bus2", "ckt")] == [0, 1, 2, "1"]
    assert type(repc["bus2"]) is int and type(got["DEGOV1"]["parameters"]["m"]) is int
    assert (repc["repc_tfltr"], repc["repc_kp"], repc["repc_dup"]) == (0.02, 18.0, 0.0)
    assert [r["line"] for r in _records(res)] == [2, 4, 5, 6, 7]
    assert [r["generator"] for r in _records(res)] == [
        "Gen A #1 1",
        "Gen B 1",
        "Gen B 1",
        "Gen B A",
        "Gen B A",
    ]
    assert res.report.unmapped == [] and res.report.warnings == []
    assert res.report.counts["Dynamic records"] == 5
    side = res.sidecars["psse_dynamics.json"]
    assert (side["format"], side["source"]) == (1, "c.dyr")
    json.dumps(side, allow_nan=False)


def test_each_record_carries_machine_base_and_bus_kv(raw, tmp_path):
    """D2: parameters are pu on MBASE, never the system base."""
    dyr = "2 'GENCLS' A 3.0 0.0 /\n2 'GENCLS' 1 4.0 0.0 /\n3 'CIM5BL' 1 0.1 /\n"
    got = _records(_import(raw, tmp_path, dyr=dyr))
    assert [(r["mbase"], r["bus_kv"]) for r in got] == [
        (60.0, 110.0),
        (50.0, 110.0),
        (None, 20.0),
    ]
    assert [r["bus_name"] for r in got] == ["B", "B", "A #3"]


# ---------------------------------------------------------------- machine IDs (D3)
@pytest.mark.parametrize("mid", ["'1'", "1", "' 1'", "'1 '", '"1"'])
def test_machine_id_quotes_and_blanks_match_one_generator(raw, tmp_path, mid):
    got = _records(_import(raw, tmp_path, dyr=f"2 'GENCLS' {mid} 3.0 0.0 /\n"))
    assert got[0]["generator"] == "Gen B 1" and got[0]["machine"] == "1"
    assert got[0]["parameters"] == {"h": 3.0, "d": 0.0}


def test_machine_id_case_is_kept(raw, tmp_path):
    res = _import(
        raw, tmp_path, dyr="2 'GENCLS' 'a' 3.0 0.0 /\n2 'GENCLS' 'A' 3.0 0.0 /\n"
    )
    assert [r["generator"] for r in _records(res)] == [None, "Gen B A"]
    assert _reasons(res) == {
        "c.dyr line 1: GENCLS at bus 2 machine 'a'": "bus 2 has no machine 'a' (it has '1', 'A')"
    }


def test_machine_id_normalisation():
    got = [
        machine_id(x)
        for x in ("'1'", "1", " ' 1' ", "'A '", "a", "''", "'", "'1\"", "A1A")
    ]
    assert got == ["1", "1", "1", "A", "a", "", "'", "'1\"", "A1A"]


# ---------------------------------------------------------------- parameter count (D1)
@pytest.mark.parametrize(
    "values, n", [("3.0 0.0 1.0", 3), ("3.0", 1)], ids=["more", "fewer"]
)
def test_known_model_with_wrong_count_keeps_values(raw, tmp_path, values, n):
    res = _import(raw, tmp_path, dyr=f"2 'GENCLS' 1 {values} /\n")
    rec = _records(res)[0]
    assert "parameters" not in rec
    assert rec["values"] == [float(v) for v in values.split()]
    assert rec["generator"] == "Gen B 1"
    assert _reasons(res) == {
        "c.dyr line 1: GENCLS at bus 2 machine '1'": (
            f"parameter count {n}, expected 2; kept as ordered values"
        )
    }


def test_unknown_model_is_one_unmapped_row_per_model(raw, tmp_path):
    dyr = "1 'USRMDL' 1 'GENSAE' 1 2 3 /\n2 'USRMDL' 1 'X' 4 /\n1 'IEEET1' 1 0.1 /\n"
    res = _import(raw, tmp_path, dyr=dyr)
    assert [r["values"] for r in _records(res)] == [
        ["GENSAE", 1, 2, 3],
        ["X", 4],
        [0.1],
    ]
    assert _reasons(res) == {
        "c.dyr: model IEEET1": "no parameter names for this model; 1 record(s) kept as "
        "ordered values (line 3)",
        "c.dyr: model USRMDL": "no parameter names for this model; 2 record(s) kept as "
        "ordered values (line 1, 2)",
    }


# ---------------------------------------------------------------- records with no generator (#1353)
def test_records_on_a_bus_without_generator_are_kept_with_one_warning(raw, tmp_path):
    dyr = "".join(f"3 'CLODBL' {k} 0.1 /\n" for k in range(1, 5))
    dyr += "".join(f"3 'CIM5BL' {k} 0.2 /\n" for k in range(1, 4))
    res = _import(raw, tmp_path, dyr=dyr)
    assert len(_records(res)) == 7
    assert all(
        r["generator"] is None and r["bus_name"] == "A #3" for r in _records(res)
    )
    assert res.report.warnings == [
        "7 dynamic record(s) in c.dyr sit on a bus with no generator and are kept "
        "unlinked (CIM5BL: 3, CLODBL: 4); lines 1 (bus 3), 2 (bus 3), 3 (bus 3), "
        "4 (bus 3), 5 (bus 3) and 2 more"
    ]


def test_warning_lists_five_without_a_tail(raw, tmp_path):
    dyr = "".join(f"3 'CLODBL' {k} 0.1 /\n" for k in range(1, 6))
    (warning,) = _import(raw, tmp_path, dyr=dyr).report.warnings
    assert warning.endswith("5 (bus 3)")


def test_three_winding_star_is_not_a_dyr_bus(tmp_path):
    (tmp_path / "t.dyr").write_text("0 'GENCLS' 1 3.0 0.0 /\n")
    res = read_psse([FIX / "three_winding_test.raw", tmp_path / "t.dyr"], {})
    assert _records(res)[0]["bus_name"] is None


def test_record_on_a_bus_not_in_the_raw_is_unmapped(raw, tmp_path):
    res = _import(raw, tmp_path, dyr="99 'GENCLS' 1 3.0 0.0 /\n")
    rec = _records(res)[0]
    assert (rec["bus_name"], rec["generator"], rec["bus_kv"]) == (None, None, None)
    assert _reasons(res) == {
        "c.dyr line 1: GENCLS at bus 99 machine '1'": "bus 99 is not in c.raw"
    }


# ---------------------------------------------------------------- refusals
@pytest.mark.parametrize(
    "text, match",
    [
        ("1 'GENCLS' 1 3.0 x0 /\n", "line 1: GENCLS value 'x0' is not a number"),
        ("1 'GENCLS' 1 3.0 0.0 /\n1 'GENCLS' 1 'abc 3.0 /\n", "line 2: unclosed quote"),
        (
            "1 'GENCLS' 1 3.0 0.0 /\n\n2 'GENCLS' 1\n 3.0 0.0\n",
            "line 3: record has no '/'",
        ),
        (
            "1 'GENCLS' 1 3.0 0.0 /\n@! again\n1 'GENCLS' '1 ' 4.0 0.0 /\n",
            "line 3: second GENCLS record for machine '1' at bus 1 \\(the first is at line 1\\)",
        ),
        ("A 'GENCLS' 1 3.0 /\n", "line 1: bus 'A' is not a bus number"),
        ("1 'GENCLS' /\n", "line 1: a record needs 'IBUS MODEL ID'"),
        ("1 'GENCLS' 1 3.0 0.0 /\n/\n", "line 2: '/' with no record before it"),
        ("1 'GENCLS' 1 '3.0' 0.0 /\n", "line 1: GENCLS h '3.0' is not a number"),
        ("1 'GENCLS' '' 3.0 0.0 /\n", "line 1: empty machine ID in GENCLS record"),
        ("1 'GENCLS' ' ' 3.0 0.0 /\n", "line 1: empty machine ID in GENCLS record"),
        ("1 '' 1 3.0 0.0 /\n", "line 1: empty model name"),
    ],
    ids=[
        "non-numeric",
        "quote",
        "slash",
        "duplicate",
        "bus",
        "short",
        "empty",
        "text-con",
        "empty-id",
        "blank-id",
        "empty-model",
    ],
)
def test_malformed_dyr_is_refused_naming_the_line(raw, tmp_path, text, match):
    with pytest.raises(ImportFormatError, match="c.dyr: " + match):
        _import(raw, tmp_path, dyr=text)


def test_reader_is_pure():
    recs = read_dyr_text(
        "1 'genrou' 1 1 2.5 /  trailing\n@! c\n2 'X' 2\n 3 /\n4 'Y' 1 /\n"
    )
    assert [(r.line, r.bus, r.model, r.machine, r.values) for r in recs] == [
        (1, 1, "GENROU", "1", (1, 2.5)),
        (3, 2, "X", "2", (3,)),
        (5, 4, "Y", "1", ()),
    ]
    assert [type(v) for v in recs[0].values] == [int, float]
    with pytest.raises(ContractError):
        read_dyr_text("1 'X' 1 /\n1 'X' 1 /\n")


# ---------------------------------------------------------------- lone files (D5)
@pytest.mark.parametrize("lone", ["c.dyr", "c.con"])
def test_lone_dyr_or_con_is_refused_naming_the_raw(tmp_path, lone):
    with pytest.raises(
        ImportFormatError, match=f"got 0: {lone}; {lone} is read onto the .raw"
    ):
        read_psse([tmp_path / lone], {})


def test_no_files_are_refused(tmp_path):
    with pytest.raises(ImportFormatError, match="exactly one .raw file, got 0: none$"):
        read_psse([], {})


def test_two_raw_files_are_refused(tmp_path):
    with pytest.raises(
        ImportFormatError, match="exactly one .raw file, got 2: a.raw, b.raw, c.dyr$"
    ):
        read_psse([tmp_path / "a.raw", tmp_path / "b.raw", tmp_path / "c.dyr"], {})


@pytest.mark.parametrize("ext", [".dyr", ".con"])
def test_two_dyr_or_con_files_are_refused(raw, tmp_path, ext):
    with pytest.raises(ImportFormatError, match=f"at most one {ext} file, got 2"):
        read_psse([raw, tmp_path / f"a{ext}", tmp_path / f"b{ext}"], {})


def test_other_files_are_listed(raw, tmp_path):
    res = read_psse([raw, tmp_path / "notes.txt"], {})
    assert _reasons(res) == {
        "notes.txt": "not a PSS/E file this import reads (.raw, .dyr, .con)"
    }


@pytest.mark.parametrize("ext", [".raw", ".dyr", ".con", ".CON"])
def test_each_psse_extension_picks_only_the_psse_importer(ext):
    assert [s.format_id for s in import_result.for_extension(ext)] == ["psse"]


def test_extensions_stay_unambiguous_across_importers():
    seen = {}
    for spec in import_result.DECLARED:
        for ext in spec.extensions:
            if ext != ".xlsx":  # shared on purpose; the user picks on screen
                assert seen.setdefault(ext, spec.format_id) == spec.format_id, ext


# ---------------------------------------------------------------- hand-written .con (D4, D6)
_CON = """\
COM hand-written; buses by number, one by its RAW name
CONTINGENCY L12
   OPEN BRANCH FROM BUS 2 TO BUS 1 CKT 1
END
CONTINGENCY T23
   OPEN BRANCH FROM BUS 2 TO BUS 3 CIRCUIT '1 '
END
CONTINGENCY G2A
   TRIP MACHINE 'A' FROM BUS 2
END
CONTINGENCY B3
   DISCONNECT BUS 3
END
CONTINGENCY BNAME
   DISCONNECT BUS 'B'
END
CONTINGENCY ANAME
   DISCONNECT BUS 'A'
END
CONTINGENCY MIX
   OPEN BRANCH FROM BUS 1 TO BUS 2 CKT 2
   REMOVE MACHINE 9 FROM BUS 2
   OPEN BRANCH FROM BUS 1 TO BUS 2
END
CONTINGENCY CL
   CLOSE BRANCH FROM BUS 1 TO BUS 2 CKT 1
END
SINGLE BRANCH IN AREA 1
END
"""


def test_hand_written_con_maps_by_bus_number(raw, tmp_path):
    res = _import(raw, tmp_path, con=_CON)
    side = res.sidecars["psse_contingencies.json"]
    got = [
        (c["contingency_id"], c["kind"], c["element_ids"],
         [(e["component"], e["name"]) for e in c["elements"]])
        for c in side["contingencies"]
    ]  # fmt: skip
    assert got == [
        ("L12", "branch", ["1-2-1"], [("Line", "Line A #1 - B 1")]),
        ("T23", "branch", ["2-3-1"], [("Transformer", "Line B - A #3 1")]),
        ("G2A", "unit", ["2-A"], [("Generator", "Gen B A")]),
        ("B3", "bus_section", ["3"], [("Bus", "A #3")]),
        ("BNAME", "bus_section", ["2"], [("Bus", "B")]),
    ]
    assert res.report.counts["Contingencies"] == 5
    json.dumps(side, allow_nan=False)


def test_every_con_statement_is_mapped_or_unmapped(raw, tmp_path):
    """D6: 11 statements by hand count = 5 mapped elements + 6 unmapped rows."""
    res = _import(raw, tmp_path, con=_CON)
    mapped = sum(
        len(c["element_ids"])
        for c in res.sidecars["psse_contingencies.json"]["contingencies"]
    )
    assert (mapped, len(res.report.unmapped)) == (5, 6)
    assert _reasons(res) == {
        "c.con line 18: DISCONNECT BUS 'A'": "more than one bus has this name in the "
        ".raw case; use its number (contingency ANAME)",
        "c.con line 21: OPEN BRANCH FROM BUS 1 TO BUS 2 CKT 2": "no branch with this "
        "circuit joins these buses (contingency MIX)",
        "c.con line 22: REMOVE MACHINE 9 FROM BUS 2": "the bus has no machine with this ID "
        "(contingency MIX)",
        "c.con line 23: OPEN BRANCH FROM BUS 1 TO BUS 2": "matched, but its contingency is "
        "left out because another element is unmapped (contingency MIX)",
        "c.con line 26: CLOSE BRANCH FROM BUS 1 TO BUS 2 CKT 1": "a PSS/E command this "
        "import does not map (contingency CL)",
        "c.con line 28: SINGLE BRANCH IN AREA 1": "a PSS/E command this import does not map",
    }
    reasons = [
        u["reason"] for u in res.sidecars["psse_contingencies.json"]["unmatched"]
    ]
    assert reasons == [
        "unsupported", "ambiguous_bus", "unknown_circuit", "unknown_machine",
        "partial", "unsupported",
    ]  # fmt: skip


def test_three_winding_legs_are_not_two_bus_branches(tmp_path):
    """A star leg has no RAW bus at the star end; the transformer is unsupported."""
    con = (
        "CONTINGENCY T2\n   OPEN BRANCH FROM BUS 1001 TO BUS 1002 CKT A\nEND\n"
        "CONTINGENCY T3\n   OPEN BRANCH FROM BUS 1001 TO BUS 1002 TO BUS 1003 CKT A\nEND\n"
        "END\n"
    )
    (tmp_path / "t.con").write_text(con)
    res = read_psse([FIX / "three_winding_test.raw", tmp_path / "t.con"], {})
    side = res.sidecars["psse_contingencies.json"]
    assert side["contingencies"] == []
    assert [u["reason"] for u in side["unmatched"]] == ["unknown_branch", "unsupported"]


def test_con_refuses_branches_that_read_as_one_element(tmp_path):
    branches = ["1,2,'&1',0.01,0.1,0.0,100.0", "1,2,'_1',0.01,0.1,0.0,100.0"]
    _case(tmp_path, BUS=_BUSES, GENERATOR=_GENS, BRANCH=branches)
    (tmp_path / "c.con").write_text("CONTINGENCY X\n   DISCONNECT BUS 2\nEND\nEND\n")
    with pytest.raises(
        ImportFormatError, match="read as one contingency element '1-2-_1'"
    ):
        read_psse([tmp_path / "c.raw", tmp_path / "c.con"], {})


def test_index_refuses_a_branch_twice(raw, tmp_path):
    """The import names two equal (I, J, CKT) branches alike, so this is built by hand."""
    n = read_psse([raw], {}).network
    n.add("Line", "copy", bus0="A #1", bus1="B", x=0.1, r=0.01)
    n.lines.at["copy", "psse_circuit"] = "1"
    with pytest.raises(
        ContractError, match="branch 1-2 circuit '1' is in the .raw twice"
    ):
        PsseNetIndex(n, {})


def test_con_swing_bus_is_not_a_bus_section(raw, tmp_path):
    res = _import(raw, tmp_path, con="CONTINGENCY S\n   DISCONNECT BUS 1\nEND\nEND\n")
    unmatched = res.sidecars["psse_contingencies.json"]["unmatched"]
    assert [u["reason"] for u in unmatched] == ["unsupported"]


def test_malformed_con_is_refused_naming_the_file(raw, tmp_path):
    with pytest.raises(
        ImportFormatError, match="c.con: line 2: not a contingency command"
    ):
        _import(raw, tmp_path, con="CONTINGENCY X\n   FLY BRANCH 1 2\nEND\nEND\n")


# ---------------------------------------------------------------- round trips through our writers
def test_con_writer_round_trip(tmp_path):
    from gridspine.handoff.contingencies import write_con
    from tests.gridspine.test_planning_criteria import _net, _set

    net = _net()
    cset = _set(net, kinds=("branch", "unit", "bus_section"))
    assert write_con(cset, net, tmp_path / "s.con") == []
    write_raw(net, tmp_path / "s.raw", f_hz=50.0)
    res = read_psse([tmp_path / "s.raw", tmp_path / "s.con"], {})
    assert res.report.unmapped == []
    num = {k: str(v) for k, v in _bus_numbers(net).items()}
    gen_at = {
        g: f"{num[net.bus.at[b, 'name']]}-1"
        for g, b in zip(net.gen["name"], net.gen["bus"])
    }

    def as_numbers(kind, ids):
        if kind == "branch":
            return [
                "-".join(
                    [
                        num[e.rsplit("-", 2)[0]],
                        num[e.rsplit("-", 2)[1]],
                        e.rsplit("-", 2)[2],
                    ]
                )
                for e in ids
            ]
        return (
            [num[i] for i in ids] if kind == "bus_section" else [gen_at[i] for i in ids]
        )

    want = sorted(
        (r.contingency_id, r.kind, as_numbers(r.kind, r.element_ids))
        for r in cset.itertuples()
    )
    got = sorted(
        (c["contingency_id"], c["kind"], c["element_ids"])
        for c in res.sidecars["psse_contingencies.json"]["contingencies"]
    )
    assert got == want


def test_dyr_writer_round_trip(tmp_path):
    from tests.gridspine.test_dyr_wecc import PLANT, POC, WT3, _write

    written = PLANT | WT3 | {"reec_pflag": 1}
    net, text = _write(tmp_path, written, POC)
    write_raw(net, tmp_path / "t.raw", f_hz=50.0)
    res = read_psse([tmp_path / "t.raw", tmp_path / "t.dyr"], {})
    assert res.report.unmapped == [] and res.report.warnings == []
    recs = _records(res)
    assert len(recs) == text.count("/")
    assert all(r["generator"] and "parameters" in r for r in recs)
    by_model = {r["model"]: r for r in recs}
    assert {"REGCA1", "REECA1", "REPCA1", "WTTQA1"} <= set(by_model)
    for model in ("REGCA1", "REECA1", "REPCA1", "WTDTA1"):
        for k, v in by_model[model]["parameters"].items():
            if k in written:
                assert v == pytest.approx(written[k], abs=1e-5), k
    assert by_model["REGCA1"]["mbase"] == 50.0
