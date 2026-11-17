"""
PowerFactory DGS reader, step 1 (IMPORTS-1): the ASCII grammar as typed
tables. The writer's own output parses back to hand-counted tables; a small
hand-written file covers comments, vectors and a repeated header; each
refusal reason has one test naming its line.

Fixture licence: ``GOLDEN`` below was written by hand for this test from the
public DGS 5.x grammar; it copies no third-party file.
"""

import ast
import math
import subprocess
import sys

import pytest

from gridspine.ingest.dgs_parse import LINE_COLUMN, parse_dgs, parse_dgs_text
from gridspine.schema.contracts import ContractError
from tests.gridspine.test_dgs_writer import HARMONICS, fixture_net

GOLDEN = """\
* a hand-written DGS 5 file
$$General;ID(a:40);Descr(a:40);Val(a:40)
1;Version;5.0
$$ElmNet;ID(a:40);loc_name(a:40);frnom(r)
2;Grid;50.0
$$ElmTerm;ID(a:40);loc_name(a:40);fold_id(p);uknom(r);outserv(i)
3;HV;2;132.0;0
4;MV;2;33.0;1
* a comment between rows
$$TypHmccur;ID(a:40);loc_name(a:40);norder:SIZEROW(i);norder:0(i);norder:1(i);ampl:SIZEROW(i);ampl:0(r);ampl:1(r)
5;two;2;5;7;2;20.0;14.0
$$TypHmccur;ID(a:40);loc_name(a:40);norder:SIZEROW(i);norder:0(i);ampl:SIZEROW(i);ampl:0(r)
6;one;1;11;1;9.5;

$$ElmLod; ID(a:40) ;loc_name(a:40);fold_id(p);plini(r);qlini(r);phmc(p);
7;load;2;3.0;;5
"""


def _write(tmp_path, text, name="f.dgs", encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return path


def test_golden_file_parses_to_typed_tables():
    t = parse_dgs_text(GOLDEN)
    assert t.version == "5.0"
    assert t.counts() == {
        "General": 1,
        "ElmNet": 1,
        "ElmTerm": 2,
        "TypHmccur": 2,
        "ElmLod": 1,
    }
    term = t.tables["ElmTerm"]
    assert list(term.columns) == [
        "loc_name",
        "fold_id",
        "uknom",
        "outserv",
        LINE_COLUMN,
    ]
    assert term.loc["3"].to_dict() == {
        "loc_name": "HV",
        "fold_id": "2",
        "uknom": 132.0,
        "outserv": 0,
        LINE_COLUMN: 7,
    }
    assert term.loc["4", "uknom"] == 33.0
    assert term.loc["4", LINE_COLUMN] == 8
    hm = t.tables["TypHmccur"]
    assert hm.loc["5", "norder"] == [5, 7] and hm.loc["5", "ampl"] == [20.0, 14.0]
    assert hm.loc["6", "norder"] == [11] and hm.loc["6", "ampl"] == [9.5]
    assert t.types["TypHmccur"] == {
        "ID": "a",
        "loc_name": "a",
        "norder": "v",
        "ampl": "v",
    }
    lod = t.tables["ElmLod"].loc["7"]
    assert lod["plini"] == 3.0 and math.isnan(lod["qlini"]) and lod["phmc"] == "5"
    assert t.types["ElmLod"]["phmc"] == "p"


def test_the_writers_own_output_parses_to_hand_counted_tables(tmp_path):
    from gridspine.handoff.dgs_writer import write_dgs

    path = tmp_path / "fixture.dgs"
    write_dgs(
        fixture_net(), path, title="fixture", harmonics=HARMONICS, converters=["bess"]
    )
    t = parse_dgs(path)
    # Hand count of fixture_net: 6 buses, 3 lines, 2 transformers, 2 loads,
    # 2 static generators, 1 machine, 2 shunts, 1 external grid; spectra for
    # "drive" and "pv". Cubicles: 3*2 + 2*2 + 2 + 2 + 1 + 2 + 1 = 18.
    assert t.counts() == {
        "General": 2,
        "ElmNet": 1,
        "ElmTerm": 6,
        "TypLne": 3,
        "ElmLne": 3,
        "TypTr2": 2,
        "ElmTr2": 2,
        "ElmLod": 2,
        "ElmGenstat": 2,
        "TypSym": 1,
        "ElmSym": 1,
        "ElmShnt": 2,
        "ElmXnet": 1,
        "StaCubic": 18,
        "TypHmccur": 2,
    }
    hm = t.tables["TypHmccur"].set_index("loc_name")
    assert hm.loc["pv_spectrum", "norder"] == [4, 5, 10, 11]
    assert hm.loc["pv_spectrum", "ampl"] == [1.0, 2.0, 0.5, 1.5]
    # every pointer resolves to a held object of the expected class
    ids = {cls: set(df.index) for cls, df in t.tables.items()}
    cub = t.tables["StaCubic"]
    assert set(cub["fold_id"]) <= ids["ElmTerm"]
    assert set(t.tables["ElmLne"]["typ_id"]) == ids["TypLne"]
    lod = t.tables["ElmLod"].set_index("loc_name")
    assert lod.loc["drive", "phmc"] in ids["TypHmccur"]
    assert lod.loc["heater", "phmc"] is None


def test_a_cp1252_file_is_read(tmp_path):
    text = GOLDEN.replace(";HV;", ";Süd;")
    t = parse_dgs(_write(tmp_path, text, encoding="cp1252"))
    assert t.tables["ElmTerm"].loc["3", "loc_name"] == "Süd"


# --- refusals: one per reason, each naming its line --------------------------

GENERAL = "$$General;ID(a:40);Descr(a:40);Val(a:40)\n1;Version;5.0\n"


@pytest.mark.parametrize(
    "text, line, words",
    [
        (
            "$$General;ID(a:40);Descr(a:40);Val(a:40)\n1;Version;6.0\n",
            2,
            "version '6.0' found",
        ),
        (
            "$$General;ID(a:40);Descr(a:40);Val(a:40)\n1;Title;x\n",
            2,
            "no General table Version",
        ),
        ("1;orphan\n" + GENERAL, 1, "before any $$ class header"),
        (GENERAL + "$$ElmTerm;loc_name(a:40)\n", 3, "no ID column"),
        (GENERAL + "$$ElmTerm;ID(a:40);uknom(q)\n", 3, "unknown type"),
        (GENERAL + "$$ElmTerm;ID(a:40);uknom\n", 3, "is not name(type)"),
        (GENERAL + "$$ElmTerm;ID(a:40);x(r);x(r)\n", 3, "twice in the header"),
        (GENERAL + "$$;ID(a:40)\n", 3, "without a class name"),
        (
            GENERAL + "$$ElmTerm;ID(a:40);uknom(r)\n3;1.0;2.0\n",
            4,
            "3 fields, its header 2",
        ),
        (GENERAL + "$$ElmTerm;ID(a:40);uknom(r)\n3;abc\n", 4, "'abc' is not a number"),
        (
            GENERAL + "$$ElmTerm;ID(a:40);outserv(i)\n3;1.5\n",
            4,
            "'1.5' is not an integer",
        ),
        (GENERAL + "$$ElmTerm;ID(a:40);outserv(i)\n3;x\n", 4, "'x' is not an integer"),
        (GENERAL + "$$ElmTerm;ID(a:40);uknom(r)\n;1.0\n", 4, "without an ID"),
        (
            GENERAL + "$$ElmTerm;ID(a:40);uknom(r)\n1;1.0\n",
            4,
            "ID 1 again (first: General, line 2)",
        ),
        (GENERAL + "$$ElmTerm;ID(a:40);fold_id(p)\n3;99\n", 4, "points at ID 99"),
        (
            GENERAL + "$$T;ID(a:40);v:SIZEROW(i);v:0(r)\n3;2;1.0\n",
            4,
            "SIZEROW 2 but 1 vector columns",
        ),
        (
            GENERAL + "$$T;ID(a:40);v:SIZEROW(i);v:0(r);v:1(r)\n3;1;1.0;2.0\n",
            4,
            "values beyond SIZEROW 1",
        ),
        (
            GENERAL + "$$T;ID(a:40);v:SIZEROW(i);v:0(r);v:1(r)\n3;2;;2.0\n",
            4,
            "an empty entry within SIZEROW 2",
        ),
        (
            GENERAL + "$$T;ID(a:40);v:SIZEROW(i)\n3;1\n",
            4,
            "SIZEROW 1 but no vector columns",
        ),
        (
            GENERAL + "$$ElmTerm;ID(a:40);uknom(r)\n3;33,0\n",
            4,
            "'33,0' is not a number",
        ),
        (
            GENERAL + "$$ElmTerm;ID(a:40);outserv(i)\n3;1.0\n",
            4,
            "'1.0' is not an integer",
        ),
        (
            GENERAL + "$$T;ID(a:40);v:SIZEROW(i);v:0(r)\n3;;1.0\n",
            4,
            "T.v: SIZEROW is empty",
        ),
        (
            GENERAL + "$$T;ID(a:40);v:SIZEROW(i);v:0(r)\n3;-1;\n",
            4,
            "SIZEROW -1 but 1 vector columns",
        ),
        (
            "$$General;ID(a:40);Descr(a:40);Val(a:40)\n1;Version;\n",
            2,
            "version '' found",
        ),
    ],
)
def test_each_refusal_names_its_line(text, line, words):
    with pytest.raises(ContractError) as e:
        parse_dgs_text(text, source="x.dgs")
    assert f"DGS x.dgs, line {line}:" in str(e.value)
    assert words in str(e.value)


def test_an_empty_vector_and_a_lone_indexed_column_are_kept():
    t = parse_dgs_text(GENERAL + "$$T;ID(a:40);v:SIZEROW(i);m:0(r)\n3;0;4.0\n")
    row = t.tables["T"].loc["3"]
    assert row["v"] == [] and row["m:0"] == 4.0
    assert t.types["T"] == {"ID": "a", "v": "v", "m:0": "r"}


def test_a_vector_row_shorter_than_its_columns_is_cut_to_sizerow():
    t = parse_dgs_text(GENERAL + "$$T;ID(a:40);v:SIZEROW(i);v:0(r);v:1(r)\n3;1;7.0;\n")
    assert t.tables["T"].loc["3", "v"] == [7.0]


# --- engine cage --------------------------------------------------------------


def test_the_parser_imports_no_engine():
    """Parsed, not grepped: the docstring names the engines it avoids."""
    import gridspine.ingest.dgs_parse as mod

    tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported == {
        "__future__",
        "dataclasses",
        "math",
        "re",
        "pandas",
        "gridspine.schema.contracts",
    }


def test_importing_the_parser_loads_no_engine():
    code = (
        "import sys, gridspine.ingest.dgs_parse; "
        "print(sorted(m for m in ('pypsa', 'pandapower') if m in sys.modules))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"


def test_sizerow_zero_with_padded_columns_is_an_empty_vector():
    t = parse_dgs_text(GENERAL + "$$T;ID(a:40);v:SIZEROW(i);v:0(r);v:1(r)\n3;0;;\n")
    assert t.tables["T"].loc["3", "v"] == []


def test_a_matrix_column_count_is_kept_as_its_own_column():
    t = parse_dgs_text(
        GENERAL + "$$T;ID(a:40);v:SIZEROW(i);v:SIZECOL(i);v:0(r)\n3;1;1;2.5\n"
    )
    row = t.tables["T"].loc["3"]
    assert row["v"] == [2.5] and row["v:SIZECOL"] == 1


def test_the_writers_one_block_harmonic_layout_parses():
    """
    The GC-05 follow-up layout: one header over the union of orders, a
    source's own orders are those with a non-zero amplitude (S2 filters).
    """
    head = (
        "$$TypHmccur;ID(a:40);loc_name(a:40);norder:SIZEROW(i);norder:0(i);"
        "norder:1(i);ampl:SIZEROW(i);ampl:0(r);ampl:1(r);alpha:SIZEROW(i);"
        "alpha:0(r);alpha:1(r)\n"
    )
    t = parse_dgs_text(
        GENERAL
        + head
        + "3;a;2;5;7;2;20.0;0.0;2;1.4;0.0\n4;b;2;5;7;2;0.0;14.0;2;0.0;1.4\n"
    )
    hm = t.tables["TypHmccur"]
    assert hm.loc["3", "ampl"] == [20.0, 0.0] and hm.loc["4", "norder"] == [5, 7]
