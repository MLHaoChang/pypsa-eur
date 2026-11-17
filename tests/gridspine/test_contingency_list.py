"""
TSO contingency-list import (TRGC 15): the .con subset and the CSV form,
mapped onto the net under the RAW's identity, failing closed.
"""

import pandapower as pp
import pytest

from gridspine.handoff.contingencies import write_contingencies
from gridspine.handoff.raw_writer import write_raw
from gridspine.ingest.contingency_list import UNMATCHED_COLUMNS, read_contingency_list
from gridspine.schema.contracts import ContractError
from tests.gridspine.test_planning_criteria import _net, _screen, _set

# _net(): buses BUS_A=1, BUS_B=2, BUS_C=3, BUS_S=4, BUS_D1=5, BUS_D2=6 (RAW order);
# lines A-B ckt 1 and 2, A-C, C-B, B-S, D1-D2; trafos B-D1, C-D2; G1 on BUS_C; slack on BUS_A.


def _con(*blocks, final="END"):
    return "\n".join([*blocks, final]) + "\n"


def _read(text, net=None):
    return read_contingency_list(text, net if net is not None else _net(), fmt="con")


def _sets(cset):
    return sorted((r.kind, tuple(r.element_ids)) for r in cset.itertuples(index=False))


def _reasons(imp):
    return [(r.contingency_id, r.line, r.reason) for r in imp.unmatched.itertuples(index=False)]


# --------------------------------------------------------------------------
# the .con form in PSS/E syntax
# --------------------------------------------------------------------------

POM_FIXTURE = """\
COM  TSO N-1 list, PSS/E contingency description data
CONTINGENCY 'A-B 1'
   OPEN BRANCH FROM BUS 1 TO BUS 2 CKT 1
END
CONTINGENCY 'A-B 2'                         / the second parallel circuit
   DISCONNECT LINE FROM BUS 2 TO BUS 1 CIRCUIT '2'
END
CONTINGENCY TRAFO_C
   TRIP BRANCH FROM BUS 'BUS_C' TO BUS 'BUS_D2' CKT '1 '
END
CONTINGENCY N2_AB
   OPEN BRANCH FROM BUS 1 TO BUS 2 CKT 1
   OPEN BRANCH FROM BUS 1 TO BUS 2 CKT 2
END
CONTINGENCY GEN_G1
   REMOVE MACHINE 1 FROM BUS 3
END
CONTINGENCY BUS_S
   DISCONNECT BUS 4
END
END
"""


def test_pom_fixture_maps_every_element_onto_the_net():
    imp = _read(POM_FIXTURE)
    assert imp.unmatched.empty
    by_id = {r.contingency_id: (r.kind, list(r.element_ids), r.order) for r in imp.contingencies.itertuples()}
    assert by_id == {
        "A-B_1": ("branch", ["BUS_A-BUS_B-1"], 1),
        "A-B_2": ("branch", ["BUS_A-BUS_B-2"], 1),
        "TRAFO_C": ("branch", ["BUS_C-BUS_D2-1"], 1),
        "N2_AB": ("branch", ["BUS_A-BUS_B-1", "BUS_A-BUS_B-2"], 2),
        "GEN_G1": ("unit", ["G1"], 1),
        "BUS_S": ("bus_section", ["BUS_S"], 1),
    }


def test_imported_set_screens_unchanged():
    net = _net()
    results, _report = _screen(net, _read(POM_FIXTURE, net).contingencies)
    assert set(results["contingency_id"]) == {"A-B_1", "A-B_2", "TRAFO_C", "N2_AB", "GEN_G1", "BUS_S"}


def test_omitted_circuit_is_one_and_keywords_are_case_insensitive():
    imp = _read(_con("contingency x", "open branch from bus 1 to bus 3", "end"))
    assert list(imp.contingencies.iloc[0]["element_ids"]) == ["BUS_A-BUS_C-1"]


# --------------------------------------------------------------------------
# the CSV form: write_contingencies round-trip
# --------------------------------------------------------------------------


def test_csv_round_trip_through_write_contingencies(tmp_path):
    net = _net()
    pp.create_sgen(net, 2, p_mw=5, name="W1")  # a second machine: RAW id '1' on BUS_B
    cset = _set(net, ("branch", "unit", "bus_section"))
    path = tmp_path / "contingencies.csv"
    write_contingencies(cset, net, path)
    imp = read_contingency_list(path, net)
    assert imp.unmatched.empty
    assert _sets(imp.contingencies) == _sets(cset)
    assert sorted(imp.contingencies["contingency_id"]) == sorted(cset["contingency_id"])


def test_csv_round_trip_keeps_n2_rows(tmp_path):
    from gridspine.static.contingency_set import n2_candidates

    net = _net()
    n1 = _set(net)
    cset = n2_candidates(n1).head(3)
    path = tmp_path / "c.csv"
    write_contingencies(cset, net, path)
    imp = read_contingency_list(path, net)
    assert imp.unmatched.empty and _sets(imp.contingencies) == _sets(cset)


def test_csv_unknown_bus_number_is_listed_with_its_line(tmp_path):
    net = _net()
    path = tmp_path / "c.csv"
    write_contingencies(_set(net), net, path)
    lines = path.read_text().splitlines()
    # row 3 of the file (second data row): point it at a bus that does not exist
    cells = lines[2].split(",")
    head = lines[0].split(",")
    cells[head.index("from_bus_number")] = "99"
    lines[2] = ",".join(cells)
    path.write_text("\n".join(lines) + "\n")
    imp = read_contingency_list(path, net)
    assert [(r.line, r.reason) for r in imp.unmatched.itertuples()] == [(3, "unknown_bus")]
    assert len(imp.contingencies) == len(lines) - 2


# --------------------------------------------------------------------------
# C1: the same numbering as write_raw, and no guessing on names
# --------------------------------------------------------------------------


def _raw_records(path):
    """(bus records, branch keys, machine keys) from the RAW's own sections."""
    lines = path.read_text().splitlines()
    sec, buses, branches, machines = "bus", [], [], []
    for line in lines[3:]:
        if line.startswith("0 /") or line.strip() == "0":
            if "BEGIN" in line:
                sec = line.split("BEGIN", 1)[1].strip().split(" DATA")[0].lower()
            else:
                sec = None
            continue
        f = [x.strip() for x in line.split(",")]
        if sec == "bus":
            buses.append((int(f[0]), f[1].strip("'").strip()))
        elif sec == "generator":
            machines.append((int(f[0]), f[1].strip("'").strip()))
        elif sec == "branch":
            branches.append((int(f[0]), int(f[1]), f[2].strip("'").strip()))
    return buses, branches, machines


def test_numbering_matches_write_raw(tmp_path):
    net = _net()
    raw = tmp_path / "net.raw"
    write_raw(net, raw)
    buses, branches, machines = _raw_records(raw)
    assert len(buses) == len(net.bus) and branches and machines
    blocks = [f"CONTINGENCY B{i}\n DISCONNECT BUS '{name}'\nEND" for i, (_n, name) in enumerate(buses)]
    blocks += [f"CONTINGENCY N{i}\n DISCONNECT BUS {n}\nEND" for i, (n, _name) in enumerate(buses)]
    blocks += [f"CONTINGENCY L{k}\n OPEN BRANCH FROM BUS {i} TO BUS {j} CKT '{c}'\nEND"
               for k, (i, j, c) in enumerate(branches)]
    blocks += [f"CONTINGENCY M{k}\n REMOVE MACHINE '{m}' FROM BUS {i}\nEND" for k, (i, m) in enumerate(machines)]
    imp = _read(_con(*blocks), net)
    got = imp.contingencies.set_index("contingency_id")["element_ids"].map(tuple).to_dict()
    # bus by name and by number land on the same bus; only the slack's are unsupported
    for k, (_n, name) in enumerate(buses):
        if name == "BUS_A":
            assert f"B{k}" not in got and f"N{k}" not in got
        else:
            assert got[f"B{k}"] == got[f"N{k}"] == (name,)
    assert {f"L{k}" for k in range(len(branches))} <= set(got)
    assert set(_reasons(imp)) == {(c, ln, "unsupported") for c, ln, _ in _reasons(imp)}
    # every RAW line (i, j, ckt) maps to the branch with those bus numbers
    num = {name: n for n, name in buses}
    for k, (i, j, _c) in enumerate(branches):
        f, t, _ckt = got[f"L{k}"][0].rsplit("-", 2)
        assert {num[f], num[t]} == {i, j}


def test_name_on_two_buses_is_ambiguous_never_the_first():
    net = _net()
    net.bus.at[5, "name"] = "BUS_S"  # BUS_D2 renamed: two buses named BUS_S
    imp = _read(_con("CONTINGENCY X\n DISCONNECT BUS 'BUS_S'\nEND", "CONTINGENCY Y\n OPEN BRANCH FROM BUS 1 TO BUS 3\nEND"), net)
    assert _reasons(imp) == [("X", 2, "ambiguous_bus")]
    assert list(imp.contingencies["contingency_id"]) == ["Y"]


def test_line_and_transformer_sharing_a_circuit_id_are_refused_not_guessed():
    net = _net()
    lt = dict(r_ohm_per_km=0.2, x_ohm_per_km=0.35, c_nf_per_km=10, max_i_ka=1.0)
    pp.create_line_from_parameters(net, 1, 4, 5, **lt)  # BUS_B-BUS_D1 line, RAW ckt '1' as the trafo
    with pytest.raises(ContractError, match=r"duplicate branch keys"):
        _read(_con("CONTINGENCY X\n OPEN BRANCH FROM BUS 2 TO BUS 5 CKT 1\nEND"), net)


# --------------------------------------------------------------------------
# C2: each refusal and each unmatched reason, with its line number
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text, match", [
    (_con("CONTINGENCY X\n OPEN BRANCH FROM 1 TO BUS 2\nEND"), r"line 2: expected 'FROM BUS"),
    (_con("CONTINGENCY X\n OPEN BRANCH FROM BUS 1 TO BUS 2 CKT\nEND"), r"line 2: expected 'CKT"),
    (_con("CONTINGENCY X\n OPEN BRANCH FROM BUS one TO BUS 2\nEND"), r"line 2: bus 'one' is neither"),
    (_con("CONTINGENCY X\n REMOVE MACHINE 1 BUS 3\nEND"), r"line 2: expected 'REMOVE MACHINE"),
    (_con("CONTINGENCY X\n DISCONNECT BUS\nEND"), r"line 2: expected 'DISCONNECT BUS"),
    (_con("CONTINGENCY X\n FROBNICATE BUS 3\nEND"), r"line 2: not a contingency command"),
    (_con("OPEN BRANCH FROM BUS 1 TO BUS 2"), r"line 1: expected CONTINGENCY or END"),
    (_con("CONTINGENCY\nEND"), r"line 1: CONTINGENCY without a label"),
    (_con("CONTINGENCY X\n OPEN BRANCH FROM BUS 1 TO BUS 2\nEND", final="END\nCONTINGENCY Y"),
     r"line 5: text after the final END"),
], ids=["no-bus-keyword", "ckt-without-id", "bus-not-a-number", "machine-without-from",
        "bus-without-id", "unknown-verb", "statement-outside-block", "no-label", "after-final-end"])
def test_malformed_syntax_raises_naming_the_line(text, match):
    with pytest.raises(ContractError, match=match):
        _read(text)


def test_end_missing_at_eof_raises():
    with pytest.raises(ContractError, match=r"END missing at the end of the file"):
        _read("CONTINGENCY X\n OPEN BRANCH FROM BUS 1 TO BUS 2\nEND\n")
    with pytest.raises(ContractError, match=r"line 3: CONTINGENCY inside contingency 'X' \(line 1\); END missing"):
        _read("CONTINGENCY X\n OPEN BRANCH FROM BUS 1 TO BUS 2\nCONTINGENCY Y\n")
    with pytest.raises(ContractError, match=r"line 1: contingency 'Y' has no END"):
        _read("CONTINGENCY Y\n OPEN BRANCH FROM BUS 1 TO BUS 2\n")


def test_empty_contingency_block_raises():
    with pytest.raises(ContractError, match=r"line 3: contingency 'E' \(line 2\) is empty"):
        _read(_con("COM x\nCONTINGENCY E\nEND"))


def test_duplicate_label_raises_naming_both_lines():
    with pytest.raises(ContractError, match=r"line 4: contingency label 'X' repeats the one at line 1"):
        _read(_con("CONTINGENCY X\n DISCONNECT BUS 4\nEND", "CONTINGENCY X\n DISCONNECT BUS 5\nEND"))
    with pytest.raises(ContractError, match=r"both read as 'A_B'"):
        _read(_con("CONTINGENCY 'A B'\n DISCONNECT BUS 4\nEND", "CONTINGENCY 'A/B'\n DISCONNECT BUS 5\nEND"))


@pytest.mark.parametrize("stmt, reason", [
    ("DISCONNECT BUS 99", "unknown_bus"),
    ("DISCONNECT BUS 'NOWHERE'", "unknown_bus"),
    ("OPEN BRANCH FROM BUS 1 TO BUS 2 CKT 7", "unknown_circuit"),
    ("OPEN BRANCH FROM BUS 1 TO BUS 4", "unknown_branch"),
    ("REMOVE MACHINE 2 FROM BUS 3", "unknown_machine"),
    ("REMOVE MACHINE 1 FROM BUS 4", "unknown_machine"),
])
def test_unknown_element_is_listed_with_its_line(stmt, reason):
    imp = _read(_con("COM x", "CONTINGENCY X", f"   {stmt}", "END"))
    assert _reasons(imp) == [("X", 3, reason)]
    assert imp.contingencies.empty and imp.excluded == ["X"]
    assert list(imp.unmatched.columns) == UNMATCHED_COLUMNS


@pytest.mark.parametrize("stmt", [
    "CLOSE BRANCH FROM BUS 1 TO BUS 2 CKT 1",
    "SET BUS 3 LOAD TO 10 MW",
    "CHANGE BUS 3 LOAD BY 10 PERCENT",
    "MOVE 10 MW LOAD FROM BUS 2 TO BUS 3",
    "REMOVE LOAD 1 FROM BUS 2",
    "OPEN BRANCH FROM BUS 2 TO BUS 5 TO BUS 6 CKT 1",
    "REMOVE MACHINE 1 FROM BUS 1",
    "DISCONNECT BUS 1",
])
def test_valid_psse_command_outside_the_subset_is_unsupported_not_malformed(stmt):
    imp = _read(_con("CONTINGENCY X", f" {stmt}", " OPEN BRANCH FROM BUS 1 TO BUS 3", "END"))
    assert _reasons(imp) == [("X", 2, "unsupported"), ("X", 3, "partial")]
    assert imp.contingencies.empty


def test_top_level_automatic_contingencies_are_listed_unsupported():
    imp = _read("SINGLE BRANCH IN SUBSYSTEM AREA1\nINCLUDE other.con\n"
                + _con("CONTINGENCY X\n OPEN BRANCH FROM BUS 1 TO BUS 3\nEND"))
    assert _reasons(imp) == [("", 1, "unsupported"), ("", 2, "unsupported")]
    assert list(imp.contingencies["contingency_id"]) == ["X"]


def test_combination_with_no_contingency_set_form_is_not_representable():
    imp = _read(_con("CONTINGENCY X", " OPEN BRANCH FROM BUS 1 TO BUS 3", " REMOVE MACHINE 1 FROM BUS 3", "END"))
    assert _reasons(imp) == [("X", 1, "not_representable")]
    assert imp.contingencies.empty


def test_format_must_be_con_or_csv():
    with pytest.raises(ContractError, match=r"format must be 'con' or 'csv'"):
        read_contingency_list("x", _net(), fmt="txt")


# --------------------------------------------------------------------------
# C3: an unmatched element excludes its whole contingency, never the others
# --------------------------------------------------------------------------


def test_one_unmatched_branch_is_listed_and_the_rest_still_screens():
    net = _net()
    text = _con(
        "CONTINGENCY GOOD_1\n OPEN BRANCH FROM BUS 1 TO BUS 3\nEND",
        "CONTINGENCY MIXED\n OPEN BRANCH FROM BUS 1 TO BUS 2 CKT 1\n OPEN BRANCH FROM BUS 1 TO BUS 2 CKT 9\nEND",
        "CONTINGENCY GOOD_2\n DISCONNECT BUS 4\nEND",
    )
    imp = _read(text, net)
    assert _reasons(imp) == [("MIXED", 6, "unknown_circuit"), ("MIXED", 5, "partial")]
    assert imp.excluded == ["MIXED"]
    results, _ = _screen(net, imp.contingencies)
    screened = set(results["contingency_id"])
    assert screened == {"GOOD_1", "GOOD_2"}
    # the matched half of MIXED (A-B circuit 1) is not screened as a lesser outage
    assert "MIXED" not in screened
    assert not any("BUS_A-BUS_B-1" in list(e) for e in imp.contingencies["element_ids"])


@pytest.mark.parametrize("text, match", [
    ("contingency_id,order\nX,1\n", r"line 1: contingency CSV is missing column\(s\) \['kind'\]"),
    ("contingency_id,kind,bus\nX,shunt,BUS_S\n", r"line 2: kind 'shunt' is not"),
    ("contingency_id,kind,bus\nX,bus_section,BUS_S\nX,bus_section,BUS_B\n", r"line 3: contingency_id 'X' repeats the one at line 2"),
    ("contingency_id,kind,bus\n,bus_section,BUS_S\n", r"line 2: empty contingency_id"),
    ("contingency_id,kind,bus_number\nX,bus_section,2.5\n", r"line 2: bus_number '2.5' is not a bus number"),
    ("contingency_id,kind,bus_number\nX,unit,3\n", r"line 2: a unit row needs machine_id"),
    ("contingency_id,kind,order,element_ids\nX,branch,2,BUS_A-BUS_B-1\n", r"line 2: a branch row of order 2 needs two"),
], ids=["missing-kind", "bad-kind", "duplicate-id", "empty-id", "fractional-bus", "unit-no-id", "n2-one-id"])
def test_malformed_csv_raises_naming_the_line(text, match):
    with pytest.raises(ContractError, match=match):
        read_contingency_list(text, _net(), fmt="csv")


def test_csv_by_bus_name_without_numbers_maps_like_the_numbers():
    text = ("contingency_id,kind,from_bus,to_bus,ckt,bus,machine_id\n"
            "L,branch,BUS_C,BUS_A,1,,\nU,unit,,,,BUS_C,1\nS,bus_section,,,,BUS_S,\n")
    imp = read_contingency_list(text, _net(), fmt="csv")
    assert imp.unmatched.empty
    assert _sets(imp.contingencies) == [("branch", ("BUS_A-BUS_C-1",)), ("bus_section", ("BUS_S",)), ("unit", ("G1",))]
