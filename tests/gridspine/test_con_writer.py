"""
GC-04 part 4: the PSS/E .con, .sub and .mon files.

The .con is read back with GC-03's importer (``read_contingency_list``), so
the two sides share one grammar: every written contingency maps back onto
the same kind and elements, with nothing unmatched. An element not on the
network is refused (GK S2); compound kinds are returned as not written.
"""

import pandapower as pp
import pandas as pd
import pytest

from gridspine.handoff.contingencies import (
    _con_label,
    _label_width,
    conmap_path,
    read_conmap,
    write_conmap,
    write_con,
    write_mon,
    write_sub,
)
from gridspine.ingest.contingency_list import read_contingency_list
from gridspine.schema.contracts import ContractError
from gridspine.static.contingency_set import (
    branch_contingencies,
    n_1_1_contingencies,
    n_m_1_contingencies,
)
from tests.gridspine.test_planning_criteria import _net, _set


def _sets(cset):
    return sorted((r.kind, tuple(r.element_ids)) for r in cset.itertuples(index=False))


@pytest.fixture
def net():
    return _net()


@pytest.fixture
def cset(net):
    return _set(net, kinds=("branch", "unit", "bus_section"))


def test_round_trip_through_gc03_reader(net, cset, tmp_path):
    assert write_con(cset, net, tmp_path / "s.con") == []
    imp = read_contingency_list(tmp_path / "s.con", net)
    assert imp.unmatched.empty and imp.excluded == []
    assert _sets(imp.contingencies) == _sets(cset)
    assert sorted(imp.contingencies["contingency_id"]) == sorted(cset["contingency_id"])


def test_hand_written_records(net, cset, tmp_path):
    """_net(): BUS_A=1, BUS_B=2, BUS_C=3, BUS_S=4; lines A-B ckt 1 and 2; G1 on BUS_C."""
    write_con(cset, net, tmp_path / "s.con")
    text = (tmp_path / "s.con").read_text()
    assert "   OPEN BRANCH FROM BUS 1 TO BUS 2 CIRCUIT '2 '\n" in text
    assert "   REMOVE MACHINE '1 ' FROM BUS 3\n" in text
    assert "   DISCONNECT BUS 4\n" in text
    assert text.endswith("END\nEND\n")
    assert text.count("CONTINGENCY ") == len(cset)


def test_an_n2_pair_is_one_block_with_two_openings(net, tmp_path):
    n1 = _set(net)
    a, b = n1["element_ids"].iloc[0][0], n1["element_ids"].iloc[1][0]
    pair = pd.DataFrame(
        {
            "contingency_id": ["N2"],
            "kind": ["branch"],
            "element_ids": [[a, b]],
            "order": [2],
        }
    )
    write_con(pair, net, tmp_path / "p.con")
    imp = read_contingency_list(tmp_path / "p.con", net)
    assert _sets(imp.contingencies) == [("branch", (a, b))]


@pytest.mark.parametrize(
    "kind, el, reason",
    [
        ("branch", "NO_SUCH_LINE", "branch NO_SUCH_LINE is not on the network"),
        ("unit", "G9", "unit G9 is not a machine on the network"),
        ("bus_section", "BUS_Z", "bus BUS_Z is not a bus on the network"),
    ],
)
def test_an_element_not_on_the_network_is_refused(net, tmp_path, kind, el, reason):
    bad = pd.DataFrame(
        {"contingency_id": ["X"], "kind": [kind], "element_ids": [[el]], "order": [1]}
    )
    with pytest.raises(ContractError, match=reason):
        write_con(bad, net, tmp_path / "x.con")
    assert not (tmp_path / "x.con").exists()


def test_bus_name_ids_round_trip_through_the_label_map(net, cset, tmp_path):
    # the schema's id charset has no length cap; only the old 32-char label limit bit
    long_id = "LINE_BUS_A_to_BUS_B_ckt_2_" + "x" * 40
    ids = [long_id, "N-1_unit_G1", "bus-C"]
    cs = cset.head(3).assign(contingency_id=ids)
    assert write_con(cs, net, tmp_path / "s.con") == []
    text = (tmp_path / "s.con").read_text()
    assert text.startswith("COM gridspine conmap s.conmap.csv\n")
    assert "CONTINGENCY C0001\n" in text and long_id not in text
    imp = read_contingency_list(tmp_path / "s.con", net)
    assert imp.unmatched.empty and imp.excluded == []
    assert list(imp.contingencies["contingency_id"]) == ids


def test_labels_skip_compound_kinds(net, tmp_path):
    cs = n_1_1_contingencies(branch_contingencies(net))
    mixed = pd.concat([cs.head(1), _set(net, kinds=("branch",)).head(2)], ignore_index=True)
    assert len(write_con(mixed, net, tmp_path / "s.con")) == 1
    assert list(read_conmap(conmap_path(tmp_path / "s.con"))) == ["C0001", "C0002"]


def test_marked_con_without_its_map_is_refused(net, cset, tmp_path):
    write_con(cset, net, tmp_path / "s.con")
    conmap_path(tmp_path / "s.con").unlink()
    with pytest.raises(ContractError, match="s.conmap.csv is needed"):
        read_contingency_list(tmp_path / "s.con", net)


def test_marked_con_as_text_without_conmap_is_refused(net, cset, tmp_path):
    write_con(cset, net, tmp_path / "s.con")
    text = (tmp_path / "s.con").read_text()
    with pytest.raises(ContractError, match="s.conmap.csv is needed"):
        read_contingency_list(text, net, fmt="con")
    imp = read_contingency_list(text, net, fmt="con", conmap=conmap_path(tmp_path / "s.con"))
    assert sorted(imp.contingencies["contingency_id"]) == sorted(cset["contingency_id"])


def test_unmarked_hand_written_con_reads_by_its_own_labels(net, tmp_path):
    (tmp_path / "h.con").write_text("CONTINGENCY L1\n   DISCONNECT BUS 4\nEND\nEND\n")
    imp = read_contingency_list(tmp_path / "h.con", net)
    assert list(imp.contingencies["contingency_id"]) == ["L1"]


def test_a_label_missing_from_the_map_is_refused_by_name(net, cset, tmp_path):
    write_con(cset, net, tmp_path / "s.con")
    mp = conmap_path(tmp_path / "s.con")
    lines = [ln for ln in mp.read_text().splitlines() if not ln.startswith("C0002,")]
    mp.write_text("\n".join(lines) + "\n")
    with pytest.raises(ContractError, match="'C0002' is not in the label map"):
        read_contingency_list(tmp_path / "s.con", net)


def test_a_map_entry_with_no_block_is_refused_by_name(net, cset, tmp_path):
    write_con(cset, net, tmp_path / "s.con")
    mp = conmap_path(tmp_path / "s.con")
    mp.write_text(mp.read_text() + "C9999,ghost\n")
    with pytest.raises(ContractError, match="'C9999' has no block"):
        read_contingency_list(tmp_path / "s.con", net)


ODD_IDS = ["a,b", 'q"uote', " lead", "trail ", "caf\u00e9 \u00fc", "x" * 80]


def test_the_map_file_round_trips_awkward_ids(tmp_path):
    pairs = [(f"C{i:04d}", cid) for i, cid in enumerate(ODD_IDS, 1)]
    write_conmap(tmp_path / "m.csv", pairs)
    assert read_conmap(tmp_path / "m.csv") == dict(pairs)
    assert read_conmap((tmp_path / "m.csv").read_text(encoding="utf-8")) == dict(pairs)


@pytest.mark.parametrize(
    "pairs, match",
    [
        ([("C1", "a"), ("C1", "b")], "duplicate label 'C1'"),
        ([("C1", "a"), ("C2", "a")], "duplicate id 'a'"),
        ([("C1", "")], "empty id"),
    ],
)
def test_the_map_refuses_by_name(tmp_path, pairs, match):
    with pytest.raises(ContractError, match=match):
        write_conmap(tmp_path / "m.csv", pairs)
    text = "label,contingency_id\n" + "".join(f"{a},{b}\n" for a, b in pairs)
    with pytest.raises(ContractError, match=match):
        read_conmap(text)


def test_the_map_refuses_a_wrong_header():
    with pytest.raises(ContractError, match="needs the header label,contingency_id"):
        read_conmap("id,label\nC1,a\n")


def test_labels_are_unique_past_9999():
    assert _label_width(9999) == 4 and _label_width(10000) == 5
    labels = {_con_label(n, _label_width(10001)) for n in range(1, 10002)}
    assert len(labels) == 10001 and _con_label(1, 5) == "C00001"


def test_the_same_set_writes_byte_identical_files(net, cset, tmp_path):
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        write_con(cset, net, tmp_path / d / "s.con")
    for f in ("s.con", "s.conmap.csv"):
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes()


def test_an_all_compound_set_writes_no_map_and_no_mark(net, tmp_path):
    cs = n_1_1_contingencies(branch_contingencies(net))
    assert len(write_con(cs, net, tmp_path / "s.con")) == len(cs)
    assert not conmap_path(tmp_path / "s.con").exists()
    assert (tmp_path / "s.con").read_text() == "END\n"


@pytest.mark.parametrize("build", [n_m_1_contingencies, n_1_1_contingencies])
def test_compound_kinds_are_returned_not_written(net, tmp_path, build):
    n1 = branch_contingencies(net)
    compound = build(n1).head(3)
    assert len(compound)
    skipped = write_con(
        pd.concat([n1.head(1), compound], ignore_index=True), net, tmp_path / "c.con"
    )
    assert skipped == list(compound["contingency_id"])
    text = (tmp_path / "c.con").read_text()
    assert text.count("CONTINGENCY ") == 1
    assert all(cid not in text for cid in skipped)


def test_sub_and_mon(net, tmp_path):
    write_sub(net, tmp_path / "s.sub")
    assert (tmp_path / "s.sub").read_text().startswith("SUBSYSTEM 'STUDY'\n   KVRANGE ")
    write_mon(tmp_path / "s.mon", 0.9, 1.1)
    text = (tmp_path / "s.mon").read_text()
    assert (
        "MONITOR VOLTAGE RANGE SUBSYSTEM 'STUDY' 0.900 1.100" in text
        and text.endswith("END\n")
    )


@pytest.fixture
def golden_net():
    """
    BUS_A=1 (slack), BUS_B=2, BUS_C=3 (20 kV); G1 and G2 both on BUS_B, so
    G2 is machine '2' on bus 2 (the RAW's per-bus counter), not '1'.
    """
    n = pp.create_empty_network(sn_mva=100)
    a, b, c = (
        pp.create_bus(n, kv, name=k)
        for k, kv in (("BUS_A", 110), ("BUS_B", 110), ("BUS_C", 20))
    )
    pp.create_ext_grid(n, a, name="SLK")
    pp.create_line_from_parameters(
        n, a, b, 10, r_ohm_per_km=0.06, x_ohm_per_km=0.4, c_nf_per_km=9, max_i_ka=1.0
    )
    pp.create_transformer_from_parameters(
        n,
        b,
        c,
        sn_mva=40,
        vn_hv_kv=110,
        vn_lv_kv=20,
        vk_percent=12,
        vkr_percent=0.4,
        pfe_kw=0,
        i0_percent=0,
    )
    pp.create_gen(n, b, p_mw=10, vm_pu=1.0, name="G1")
    pp.create_gen(n, b, p_mw=10, vm_pu=1.0, name="G2")
    return n


def test_golden_con(golden_net, tmp_path):
    cset = pd.DataFrame(
        {
            "contingency_id": ["L1", "U_G2", "B_C"],
            "kind": ["branch", "unit", "bus_section"],
            "element_ids": [["BUS_A-BUS_B-1"], ["G2"], ["BUS_C"]],
            "order": [1, 1, 1],
        }
    )
    assert write_con(cset, golden_net, tmp_path / "g.con") == []
    assert (tmp_path / "g.con").read_bytes() == (
        b"COM gridspine conmap g.conmap.csv\n"
        b"CONTINGENCY C0001\n"
        b"   OPEN BRANCH FROM BUS 1 TO BUS 2 CIRCUIT '1 '\n"
        b"END\n"
        b"CONTINGENCY C0002\n"
        b"   REMOVE MACHINE '2 ' FROM BUS 2\n"
        b"END\n"
        b"CONTINGENCY C0003\n"
        b"   DISCONNECT BUS 3\n"
        b"END\n"
        b"END\n"
    )


def test_golden_sub(golden_net, tmp_path):
    write_sub(golden_net, tmp_path / "g.sub")
    assert (tmp_path / "g.sub").read_bytes() == (
        b"SUBSYSTEM 'STUDY'\n   KVRANGE 20.000 110.000\nEND\nEND\n"
    )


def test_golden_mon(tmp_path):
    write_mon(tmp_path / "g.mon", 0.9, 1.1)
    assert (tmp_path / "g.mon").read_bytes() == (
        b"MONITOR BRANCHES IN SUBSYSTEM 'STUDY'\n"
        b"MONITOR VOLTAGE RANGE SUBSYSTEM 'STUDY' 0.900 1.100\n"
        b"END\n"
    )


@pytest.mark.parametrize(
    "lo, hi, reason",
    [
        (1.1, 0.9, "v_min < v_max"),
        (1.0, 1.0, "v_min < v_max"),
        (float("nan"), 1.1, "finite and > 0"),
        (0.0, 1.1, "finite and > 0"),
        (0.9, float("inf"), "finite and > 0"),
        (float("-inf"), 1.1, "finite and > 0"),
        ("0.9", 1.1, "finite and > 0"),
        (True, 1.1, "finite and > 0"),
        (0.9, None, "finite and > 0"),
    ],
)
def test_a_bad_monitor_band_is_refused(tmp_path, lo, hi, reason):
    with pytest.raises(ContractError, match=reason):
        write_mon(tmp_path / "m.mon", lo, hi)


@pytest.mark.parametrize("kv", [float("nan"), float("inf"), 0.0, -20.0])
def test_a_bad_sub_kv_is_refused(net, tmp_path, kv):
    bad = net.deepcopy()
    bad.bus.loc[bad.bus.index[0], "vn_kv"] = kv
    with pytest.raises(ContractError, match="every vn_kv finite and > 0"):
        write_sub(bad, tmp_path / "s.sub")


def test_an_empty_network_has_no_sub(net, tmp_path):
    empty = net.deepcopy()
    empty.bus = empty.bus.iloc[0:0]
    with pytest.raises(ContractError, match="at least one bus"):
        write_sub(empty, tmp_path / "s.sub")


def test_an_unknown_kind_is_refused_not_written_as_a_bus(net, tmp_path, monkeypatch):
    import gridspine.handoff.contingencies as C

    cset = _set(net, ("bus_section",)).head(1).copy()
    cset["kind"] = "breaker"
    monkeypatch.setattr(C, "validate_contingency_set", lambda df: df)
    with pytest.raises(ContractError, match="kind 'breaker' has no .con form"):
        write_con(cset, net, tmp_path / "c.con")
    assert not (tmp_path / "c.con").exists()
