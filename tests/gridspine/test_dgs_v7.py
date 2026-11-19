"""
PowerFactory DGS import, step 3 (owner decision Q6, GK A2 X1-X3): DGS 7.x
ASCII. A 7.x file keys objects by FID, marks each row with OP and may carry
MATRIX attributes; the rest of the grammar is 5.x's.

Fixture licence: ``V7`` and its 5.0 twin ``V5`` were written by hand for this
test (X2: never by our writer). They copy no third-party file; the header
shapes follow a public PowerFactory 2025 export (``FID(a:40);OP(a:1);...``,
``GPScoords:MATRIX(a)``, a General table without OP, decimal commas).
"""

import math
import re

import pandas as pd
import pytest

from gridspine.ingest.dgs_import import read_dgs
from gridspine.ingest.dgs_parse import parse_dgs_text
from gridspine.ingest.import_result import ImportFormatError
from gridspine.schema.contracts import ContractError

# The 5.0 twin: IDs 1.., in one order.
V5 = """\
$$General;ID(a:40);Descr(a:40);Val(a:40)
1;Version;5.0
$$ElmNet;ID(a:40);loc_name(a:40);frnom(r)
2;Grid;50.0
$$ElmTerm;ID(a:40);loc_name(a:40);fold_id(p);uknom(r);outserv(i)
10;HV;2;33.0;0
11;Far;2;33.0;0
12;LV;2;11.0;0
$$TypLne;ID(a:40);loc_name(a:40);uline(r);sline(r);rline(r);xline(r);bline(r);gline(r)
30;L type;33.0;0.5;0.1;0.4;3.0;0.1
$$ElmLne;ID(a:40);loc_name(a:40);fold_id(p);typ_id(p);dline(r);nlnum(i);outserv(i)
31;L1;2;30;12.5;1;0
$$TypTr2;ID(a:40);loc_name(a:40);strn(r);utrn_h(r);utrn_l(r);uktr(r);pcutr(r);pfe(r);curmg(r);nt2ag(r);dutap(r);nntap0(i);tap_side(i)
32;T type;10.0;33.0;11.0;10.0;50.0;10.0;0.5;0.0;2.5;0;0
$$ElmTr2;ID(a:40);loc_name(a:40);fold_id(p);typ_id(p);nntap(i);ntnum(i);outserv(i)
33;T2W;2;32;1;1;0
$$ElmLod;ID(a:40);loc_name(a:40);fold_id(p);plini(r);qlini(r);scale0(r);outserv(i)
40;Load;2;3.25;1.0;1.0;0
$$ElmXnet;ID(a:40);loc_name(a:40);fold_id(p);usetp(r);bustp(a:2);outserv(i)
44;Slack;2;1.0;SL;0
$$StaCubic;ID(a:40);loc_name(a:40);fold_id(p);obj_id(p);obj_bus(i)
100;c;10;31;0
101;c;11;31;1
102;c;10;33;0
103;c;12;33;1
104;c;12;40;0
105;c;10;44;0
"""

# The 7.0 twin: other keys (FIDs), other class order, OP on each class row,
# a MATRIX column and decimal commas.
V7 = """\
********************************************************************************
* hand-written DGS 7.0 fixture
********************************************************************************

$$General;FID(a:40);Descr(a:40);Val(a:255)
  901;Version;7.0

$$ElmNet;FID(a:40);OP(a:1);loc_name(a:80);frnom(r)
  902;C;Grid;50

$$StaCubic;FID(a:40);OP(a:1);loc_name(a:80);fold_id(p);obj_id(p);obj_bus(i)
  960;C;c;710;731;0
  961;C;c;711;731;1
  962;C;c;710;733;0
  963;C;c;712;733;1
  964;C;c;712;740;0
  965;C;c;710;744;0

$$ElmTerm;FID(a:40);OP(a:1);loc_name(a:80);fold_id(p);uknom(r);outserv(i);GPScoords:MATRIX(a)
  710;C;HV;902;33;0;
  711;C;Far;902;33;0;
  712;C;LV;902;11;0;

$$ElmLne;FID(a:40);OP(a:1);loc_name(a:80);fold_id(p);typ_id(p);dline(r);nlnum(i);outserv(i);GPScoords:MATRIX(a)
  731;C;L1;902;830;12,5;1;0;

$$TypLne;FID(a:40);OP(a:1);loc_name(a:80);uline(r);sline(r);rline(r);xline(r);bline(r);gline(r)
  830;C;L type;33;0,5;0,1;0,4;3;0,1

$$TypTr2;FID(a:40);OP(a:1);loc_name(a:80);strn(r);utrn_h(r);utrn_l(r);uktr(r);pcutr(r);pfe(r);curmg(r);nt2ag(r);dutap(r);nntap0(i);tap_side(i)
  832;C;T type;10;33;11;10;50;10;0,5;0;2,5;0;0

$$ElmTr2;FID(a:40);OP(a:1);loc_name(a:80);fold_id(p);typ_id(p);nntap(i);ntnum(i);outserv(i)
  733;C;T2W;902;832;1;1;0

$$ElmLod;FID(a:40);OP(a:1);loc_name(a:80);fold_id(p);plini(r);qlini(r);scale0(r);outserv(i)
  740;C;Load;902;3,25;1;1;0

$$ElmXnet;FID(a:40);OP(a:1);loc_name(a:80);fold_id(p);usetp(r);bustp(a:2);outserv(i)
  744;C;Slack;902;1;SL;0
"""


def _read(tmp_path, text, name):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return read_dgs([path], {})


def _no_lines(items):
    return sorted(re.sub(r" \(line \d+\)", "", str(x)) for x in items)


def test_a_7_0_file_maps_to_the_same_network_as_its_5_0_twin(tmp_path):
    """X1: FIDs that differ from the twin's IDs give the same network."""
    r5 = _read(tmp_path, V5, "v5.dgs")
    r7 = _read(tmp_path, V7, "v7.dgs")
    for comp in ("buses", "lines", "transformers", "loads", "generators"):
        a, b = getattr(r5.network, comp), getattr(r7.network, comp)
        assert len(a) > 0
        pd.testing.assert_frame_equal(a.sort_index(), b.sort_index())
    assert r5.report.counts == r7.report.counts
    assert _no_lines(r5.report.assumed) == _no_lines(r7.report.assumed)
    # D1a: the 7.0 twin writes decimal commas, and the report says so once
    comma = [w for w in r7.report.warnings if w.startswith("Decimal comma read")]
    assert len(comma) == 1
    assert r5.report.warnings == [w for w in r7.report.warnings if w not in comma]
    # the 7.0 file's one extra: its MATRIX columns, each listed once per class
    extra = [u for u in r7.report.unmapped if "MATRIX" in u.source_item]
    assert sorted(u.source_item for u in extra) == [
        "ElmLne column 'GPScoords:MATRIX'",
        "ElmTerm column 'GPScoords:MATRIX'",
    ]
    rest = [u for u in r7.report.unmapped if u not in extra]
    assert _no_lines(r5.report.unmapped) == _no_lines(rest)


def test_literal_values_come_from_the_7_0_file(tmp_path):
    """X2: hand values from the 7.0 text, not just "same as the twin"."""
    t = parse_dgs_text(V7, "v7.dgs")
    assert t.version == "7.0"
    assert t.tables["ElmTerm"].loc["712", "uknom"] == 11.0
    assert t.tables["ElmLne"].loc["731", "dline"] == 12.5  # decimal comma
    assert t.tables["ElmLne"].loc["731", "typ_id"] == "830"  # a pointer by FID
    assert t.tables["StaCubic"].loc["963", "fold_id"] == "712"
    assert "OP" not in t.tables["ElmLne"].columns
    assert "FID" not in t.tables["ElmLne"].columns
    assert "GPScoords:MATRIX" not in t.tables["ElmLne"].columns
    assert t.types["ElmLne"]["ID"] == "a"
    assert t.dropped == {
        "ElmTerm": ["GPScoords:MATRIX"],
        "ElmLne": ["GPScoords:MATRIX"],
    }
    n = _read(tmp_path, V7, "v7.dgs").network
    assert n.buses.at["LV", "v_nom"] == 11.0
    # L type: x' = 0.4 ohm/km over 12.5 km
    assert math.isclose(n.lines.at["L1", "x"], 0.4 * 12.5)
    assert (n.lines.at["L1", "bus0"], n.lines.at["L1", "bus1"]) == ("HV", "Far")
    assert n.loads.at["Load", "p_set"] == 3.25


def test_a_dangling_fid_is_refused_by_name():
    text = V7.replace("731;C;L1;902;830;", "731;C;L1;902;899;")
    with pytest.raises(ContractError, match=r"ElmLne\.typ_id points at ID 899"):
        parse_dgs_text(text, "v7.dgs")


def test_a_repeated_fid_is_refused():
    text = V7.replace("711;C;Far", "710;C;Far")
    with pytest.raises(ContractError, match=r"ID 710 again"):
        parse_dgs_text(text, "v7.dgs")


@pytest.mark.parametrize("op", ["U", "D", "c", ""])
def test_a_row_other_than_create_is_refused(op):
    text = V7.replace("740;C;Load", f"740;{op};Load")
    line = 1 + text.splitlines().index(f"  740;{op};Load;902;3,25;1;1;0")
    with pytest.raises(ContractError, match=rf"line {line}: ElmLod: OP"):
        parse_dgs_text(text, "v7.dgs")


def test_the_importer_names_the_file_on_a_7_x_refusal(tmp_path):
    with pytest.raises(ImportFormatError, match=r"v7\.dgs.*OP 'U'"):
        _read(tmp_path, V7.replace("740;C;Load", "740;U;Load"), "v7.dgs")


# --- X3: the version gate ----------------------------------------------------

G5 = "$$General;ID(a:40);Descr(a:40);Val(a:40)\n1;Version;5.0\n"
G7 = "$$General;FID(a:40);Descr(a:40);Val(a:40)\n1;Version;7.0\n"


@pytest.mark.parametrize(
    "text, line, words",
    [
        (G5.replace("5.0", "4.2"), 2, "version '4.2' found; only DGS 5.x and 7.x"),
        (G5.replace("5.0", "6.0"), 2, "version '6.0' found; only DGS 5.x and 7.x"),
        (G7.replace("7.0", "8.0"), 2, "version '8.0' found"),
        (G7 + "$$ElmTerm;ID(a:40);uknom(r)\n2;33\n", 3, "no FID column"),
        (G7 + "$$ElmTerm;FID(a:40);ID(a:40);uknom(r)\n2;3;33\n", 3, "an ID column"),
        (
            G5 + "$$ElmTerm;ID(a:40);FID(a:40);uknom(r)\n2;3;33\n",
            3,
            "'FID' belongs to DGS 7.x",
        ),
        (
            G5 + "$$ElmTerm;ID(a:40);OP(a:1);uknom(r)\n2;C;33\n",
            3,
            "'OP' belongs to DGS 7.x",
        ),
        (
            G5 + "$$ElmTerm;ID(a:40);uknom(r);GPScoords:MATRIX(a)\n2;33;\n",
            3,
            "'GPScoords:MATRIX' belongs to DGS 7.x",
        ),
        (G5.replace("ID(a:40)", "FID(a:40)"), 1, "'FID' belongs to DGS 7.x"),
    ],
)
def test_the_version_gate_refuses_by_name(text, line, words):
    with pytest.raises(ContractError) as e:
        parse_dgs_text(text, "x.dgs")
    assert f"DGS x.dgs, line {line}: " in str(e.value)
    assert words in str(e.value)


@pytest.mark.parametrize(
    "field, value",
    [("158,7713", 158.7713), ("-0,5", -0.5), ("1,5e-3", 0.0015), ("0,25", 0.25)],
)
def test_a_decimal_comma_reads_as_a_point(field, value):
    t = parse_dgs_text(G7 + f"$$ElmTerm;FID(a:40);OP(a:1);uknom(r)\n2;C;{field}\n")
    assert t.tables["ElmTerm"].loc["2", "uknom"] == value


@pytest.mark.parametrize("field", ["1.234,5", "1,234,5", ",5", "5,", "1,5.0"])
def test_any_other_comma_in_a_real_is_refused(field):
    with pytest.raises(ContractError, match="is not a number"):
        parse_dgs_text(G7 + f"$$ElmTerm;FID(a:40);OP(a:1);uknom(r)\n2;C;{field}\n")


def test_a_comma_in_an_integer_is_refused():
    with pytest.raises(ContractError, match="is not an integer"):
        parse_dgs_text(G7 + "$$ElmTerm;FID(a:40);OP(a:1);nlnum(i)\n2;C;1,0\n")


def test_a_matrix_drops_every_column_of_its_name():
    t = parse_dgs_text(
        G7 + "$$ElmLne;FID(a:40);OP(a:1);m:MATRIX(a);m:SIZEROW(i);m:SIZECOL(i);"
        "m:0:0(r);dline(r)\n2;C;;1;1;5;3\n"
    )
    assert list(t.tables["ElmLne"].columns) == ["dline", "_line"]
    assert t.dropped == {"ElmLne": ["m:MATRIX", "m:SIZEROW", "m:SIZECOL", "m:0:0"]}
    assert t.tables["ElmLne"].loc["2", "dline"] == 3.0


# --- D1a: decimal comma ------------------------------------------------------

# Modelled on a real export's comma reals: every real with a comma, integers bare.
COMMA = G7 + (
    "$$ElmTerm;FID(a:40);OP(a:1);loc_name(a:80);uknom(r);unknom(r);outserv(i)\n"
    "  6;C;KIL;275;158,7713;0\n"
    "  7;C;MAH;275;158,7713;0\n"
    "$$ElmShnt;FID(a:40);OP(a:1);loc_name(a:80);qcapn(r);ncapx(i)\n"
    "  5;C;R1;58,33333;1\n"
)


def test_a_comma_file_reads_and_says_so():
    t = parse_dgs_text(COMMA, "c.dgs")
    assert t.decimal_comma
    assert t.tables["ElmShnt"].loc["5", "qcapn"] == 58.33333
    assert t.tables["ElmTerm"].loc["6", "uknom"] == 275.0
    assert not parse_dgs_text(V5, "p.dgs").decimal_comma


def test_a_comma_in_a_text_field_stays_text():
    t = parse_dgs_text(COMMA.replace(";KIL;", ";Kil, north;"), "c.dgs")
    assert t.tables["ElmTerm"].loc["6", "loc_name"] == "Kil, north"


def test_a_file_mixing_comma_and_point_is_refused_naming_both():
    text = COMMA.replace("7;C;MAH;275;158,7713", "7;C;MAH;275;158.7713")
    with pytest.raises(ContractError) as e:
        parse_dgs_text(text, "c.dgs")
    msg = str(e.value)
    assert "decimal marks are mixed" in msg
    assert "ElmTerm.unknom '158,7713' (line 4)" in msg
    assert "ElmTerm.unknom '158.7713' (line 5)" in msg


def test_integers_carry_no_mark():
    """A point-free file with integer reals is not "mixed" with its comma reals."""
    t = parse_dgs_text(COMMA.replace("58,33333", "58"), "c.dgs")
    assert t.decimal_comma


def test_the_report_says_decimal_comma_once(tmp_path):
    r = _read(tmp_path, V7, "v7.dgs")
    assert sum(w.startswith("Decimal comma read") for w in r.report.warnings) == 1
    assert not any(
        w.startswith("Decimal comma")
        for w in _read(tmp_path, V5, "v5.dgs").report.warnings
    )


# --- mutant survivors --------------------------------------------------------


def test_where_reads_a_float_line_number():
    """Iterrows turns an all-number row's line into a float; where still says "line 4"."""
    t = parse_dgs_text(G7 + "$$ElmTerm;FID(a:40);OP(a:1);uknom(r)\n2;C;33\n")
    assert t.where(4.0) == "line 4"


def test_a_matrix_in_a_later_5_x_header_of_a_class_is_refused_there():
    text = (
        G5
        + "$$ElmTerm;ID(a:40);uknom(r)\n2;33\n"
        + "$$ElmTerm;ID(a:40);uknom(r);G:MATRIX(a)\n3;33;\n"
    )
    with pytest.raises(
        ContractError, match=r"line 5: ElmTerm: column 'G:MATRIX' belongs to DGS 7.x"
    ):
        parse_dgs_text(text, "x.dgs")


def test_matrix_columns_of_types_and_of_unmapped_classes_are_listed(tmp_path):
    text = V7.replace(
        "$$TypLne;FID(a:40);OP(a:1);loc_name(a:80);",
        "$$TypLne;FID(a:40);OP(a:1);M:MATRIX(a);loc_name(a:80);",
    ).replace("830;C;L type;", "830;C;;L type;")
    text += "$$IntGrf;FID(a:40);OP(a:1);loc_name(a:80);P:MATRIX(a)\n  990;C;g;\n"
    r = _read(tmp_path, text, "v7.dgs")
    cols = sorted(u.source_item for u in r.report.unmapped if "column" in u.source_item)
    assert "TypLne column 'M:MATRIX'" in cols
    assert "IntGrf column 'P:MATRIX'" in cols  # its rows are listed whole too
    assert any(u.source_item.startswith("IntGrf 'g'") for u in r.report.unmapped)


def test_a_matrix_in_a_repeated_class_header_is_listed_once():
    text = G7 + (
        "$$ElmTerm;FID(a:40);OP(a:1);uknom(r);G:MATRIX(a)\n2;C;33;\n"
        "$$ElmTerm;FID(a:40);OP(a:1);uknom(r);G:MATRIX(a)\n3;C;11;\n"
    )
    assert parse_dgs_text(text).dropped == {"ElmTerm": ["G:MATRIX"]}


def _with_matrix(text, cls, value=""):
    """``text`` with a ``G:MATRIX(r)`` column on ``cls``, every row ``value``."""
    out, here = [], False
    for line in text.splitlines():
        if line.startswith("$$"):
            here = line.startswith(f"$${cls};")
            out.append(line + (";G:MATRIX(r)" if here else ""))
        elif here and line.strip():
            out.append(f"{line};{value}")
        else:
            out.append(line)
    return "\n".join(out) + "\n"


@pytest.mark.parametrize("cls", ["ElmNet", "StaCubic"])
def test_a_matrix_column_on_a_structure_class_is_listed(tmp_path, cls):
    """A dropped matrix column is reported whatever its class."""
    res = _read(tmp_path, _with_matrix(V7, cls), "v7.dgs")
    assert f"{cls} column 'G:MATRIX'" in {u.source_item for u in res.report.unmapped}


@pytest.mark.parametrize("value", ["abc", "2.5"])
def test_a_dropped_matrix_column_is_never_read(value):
    """Neither a non-number nor a point in this comma file can refuse it there."""
    tables = parse_dgs_text(_with_matrix(V7, "ElmXnet", value), "v7.dgs")
    assert tables.dropped["ElmXnet"] == ["G:MATRIX"]
    assert tables.decimal_comma
