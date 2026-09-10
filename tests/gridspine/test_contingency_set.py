"""Task 3: the contingency set, enumerated from the registry and the net.

The thing this file guards is identity, not counting. A contingency names a
branch, and the only place a branch triple `(from_bus, to_bus, ckt)` is
authored is the RAW writer's per-section counter over the SORTED bus-number
pair. `LFResult.branch_flow` reproduces it (increment 2, task 8); the
contingency set is the second consumer, and the moment it drifts a PowerFactory
contingency export stops joining onto anything. So the cross-module test
below parses the .raw the writer actually emits and asserts the key sets are
identical, duplicate-free, on both sides.
"""
import pandapower as pp
import pandas as pd
import pytest

from gridspine.handoff.raw_writer import write_raw
from gridspine.ingest.pandapower_source import load_case39, load_case39_res, registry_from_net
from gridspine.schema.contingency import validate_contingency_set
from gridspine.schema.contracts import ContractError
from gridspine.static.contingency_set import (
    EXT_GRID_EXCLUSION_LEDGER,
    branch_contingencies,
    n2_candidates,
    unit_contingencies,
)
from gridspine.static.loadflow import run_lf

# case39_res as measured: 35 lines + 11 transformers; 9 gen + 1 ext_grid + 5 sgen.
N_LINES, N_TRAFOS = 35, 11
N_BRANCHES = N_LINES + N_TRAFOS
N_GEN, N_RES = 9, 5
N_PAIRS = N_BRANCHES * (N_BRANCHES - 1) // 2   # 1035


def _raw_section(text, begin, end):
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if begin in ln) + 1
    stop = next(i for i, ln in enumerate(lines) if end in ln)
    return lines[start:stop]


def _fields(line):
    return [t.strip().strip("'").strip() for t in line.split(",")]


def _raw_branch_keys(text, name_of_num, n_trafo):
    """(from_bus, to_bus, ckt, in_service) for every record in BOTH branch sections."""
    keys = []
    for ln in _raw_section(text, "BEGIN BRANCH DATA", "END OF BRANCH DATA"):
        f = _fields(ln)                       # I, J, CKT, R, X, B, RATEA..C, GI, BI, GJ, BJ, ST
        keys.append((name_of_num[int(f[0])], name_of_num[int(f[1])], f[2], f[13] == "1"))
    trafo_lines = _raw_section(text, "BEGIN TRANSFORMER DATA", "END OF TRANSFORMER DATA")
    assert len(trafo_lines) == 4 * n_trafo
    for k in range(0, len(trafo_lines), 4):
        f = _fields(trafo_lines[k])           # I, J, K, CKT, CW, CZ, CM, MAG1, MAG2, NMETR, NAME, STAT
        keys.append((name_of_num[int(f[0])], name_of_num[int(f[1])], f[3], f[11] == "1"))
    return keys


@pytest.fixture
def net():
    return load_case39_res()


# --------------------------------------------------------------------------
# branch contingencies
# --------------------------------------------------------------------------

def test_branch_contingencies_count_and_validate(net):
    cset = branch_contingencies(net)
    validate_contingency_set(cset)
    assert len(cset) == N_BRANCHES
    assert (cset["kind"] == "branch").all()
    assert (cset["order"] == 1).all()
    assert cset["element_ids"].map(len).eq(1).all()


def test_branch_contingency_id_is_the_element_id_for_n1(net):
    cset = branch_contingencies(net)
    assert (cset["contingency_id"] == cset["element_ids"].map(lambda e: e[0])).all()


def test_branch_contingencies_carry_the_triple_and_the_pandapower_handle(net):
    """The id is a KEY; applying the outage needs the parts. Both travel together
    so nobody downstream parses a hyphenated string back into bus names."""
    cset = branch_contingencies(net)
    for col in ("from_bus", "to_bus", "ckt", "element_type", "element_index"):
        assert col in cset.columns, col
    assert set(cset["element_type"]) == {"line", "trafo"}
    assert (cset["element_type"] == "line").sum() == N_LINES
    assert (cset["element_type"] == "trafo").sum() == N_TRAFOS
    lines = cset[cset["element_type"] == "line"]
    assert sorted(lines["element_index"]) == sorted(net.line.index)


def test_branch_contingencies_are_sorted_and_deterministic(net):
    a = branch_contingencies(net)
    b = branch_contingencies(load_case39_res())
    pd.testing.assert_frame_equal(a, b)
    assert a["contingency_id"].is_monotonic_increasing
    assert a.index.equals(pd.RangeIndex(len(a)))


def test_branch_keys_map_1to1_onto_the_raw_branch_records(net, tmp_path):
    """THE cross-module contract. Parsed off the .raw the writer emits, not
    reconstructed from the same code path — otherwise a shared bug passes."""
    nums = write_raw(net, tmp_path / "c39.raw", f_hz=60.0)
    name_of_num = {n: name for name, n in nums.items()}
    raw = _raw_branch_keys((tmp_path / "c39.raw").read_text(), name_of_num, len(net.trafo))
    raw_keys = [(f, t, c) for f, t, c, _stat in raw]

    cset = branch_contingencies(net)
    set_keys = list(zip(cset["from_bus"], cset["to_bus"], cset["ckt"]))

    assert len(raw_keys) == len(set(raw_keys)) == N_BRANCHES
    assert len(set_keys) == len(set(set_keys)) == N_BRANCHES
    assert set(set_keys) == set(raw_keys)


def test_branch_keys_agree_with_lfresult_branch_flow(net):
    """Second consumer of the same identity: the increment-2 LF keying.

    The flow is solved on plain case39: `case39_res` carries 2 800 MW of
    UNDISPATCHED sgen on top of the native fleet and diverges until a
    snapshot is applied (see `test_case39_res_load_flow_converges_at_derated_
    output`). Branch identity is topology and sgens add no branches, so this
    also pins that the two fixtures key identically.
    """
    lf = run_lf(load_case39())
    assert lf.converged
    lf_keys = set(zip(lf.branch_flow["from_bus"], lf.branch_flow["to_bus"], lf.branch_flow["ckt"]))
    cset = branch_contingencies(net)
    assert set(zip(cset["from_bus"], cset["to_bus"], cset["ckt"])) == lf_keys


def test_out_of_service_branches_are_excluded_but_do_not_renumber_the_rest(net, tmp_path):
    """The writer still emits a dead branch (STAT=0) and its CKT counter still
    counts it, so the survivors' keys must be the same with it dead as alive.
    Dropping it BEFORE keying would shift every later circuit on that pair."""
    alive = branch_contingencies(net)
    dead_line, dead_trafo = net.line.index[3], net.trafo.index[2]
    net.line.at[dead_line, "in_service"] = False
    net.trafo.at[dead_trafo, "in_service"] = False

    cset = branch_contingencies(net)
    validate_contingency_set(cset)
    assert len(cset) == N_BRANCHES - 2
    assert not ((cset["element_type"] == "line") & (cset["element_index"] == dead_line)).any()
    assert not ((cset["element_type"] == "trafo") & (cset["element_index"] == dead_trafo)).any()

    survivors = alive[~(
        ((alive["element_type"] == "line") & (alive["element_index"] == dead_line))
        | ((alive["element_type"] == "trafo") & (alive["element_index"] == dead_trafo))
    )].reset_index(drop=True)
    pd.testing.assert_frame_equal(cset, survivors)

    nums = write_raw(net, tmp_path / "dead.raw", f_hz=60.0)
    name_of_num = {n: name for name, n in nums.items()}
    raw = _raw_branch_keys((tmp_path / "dead.raw").read_text(), name_of_num, len(net.trafo))
    raw_in_service = {(f, t, c) for f, t, c, stat in raw if stat}
    assert set(zip(cset["from_bus"], cset["to_bus"], cset["ckt"])) == raw_in_service


def test_parallel_circuits_get_the_writers_ckt_sequence(tmp_path):
    """case39 has no parallel circuit, so the counter is exercised on a toy net:
    three circuits on one pair, the third written to_bus-first. A counter that
    ignored the sorted key would restart at '1' and the raw would disagree."""
    toy = pp.create_empty_network(sn_mva=100.0)
    b1 = pp.create_bus(toy, vn_kv=345.0, name="BUS_01")
    b2 = pp.create_bus(toy, vn_kv=345.0, name="BUS_02")
    pp.create_ext_grid(toy, bus=b1, vm_pu=1.0, name="SLK_BUS_01")
    pp.create_load(toy, bus=b2, p_mw=50.0, q_mvar=10.0)
    for f, t in ((b1, b2), (b1, b2), (b2, b1)):
        pp.create_line_from_parameters(
            toy, from_bus=f, to_bus=t, length_km=1.0,
            r_ohm_per_km=11.9025, x_ohm_per_km=119.025, c_nf_per_km=0.0, max_i_ka=0.418,
        )
    write_raw(toy, tmp_path / "toy.raw")
    raw_ckts = [_fields(ln)[2] for ln in _raw_section(
        (tmp_path / "toy.raw").read_text(), "BEGIN BRANCH DATA", "END OF BRANCH DATA")]
    assert raw_ckts == ["1", "2", "3"]

    cset = branch_contingencies(toy).sort_values("element_index")
    assert cset["ckt"].tolist() == raw_ckts
    assert cset["contingency_id"].tolist() == ["BUS_01-BUS_02-1", "BUS_01-BUS_02-2", "BUS_02-BUS_01-3"]


def test_a_line_and_a_trafo_sharing_a_bus_pair_is_rejected(tmp_path):
    """Same collision `_branch_flow` refuses: two record sections, one flat key space."""
    toy = pp.create_empty_network(sn_mva=100.0)
    b1 = pp.create_bus(toy, vn_kv=345.0, name="BUS_01")
    b2 = pp.create_bus(toy, vn_kv=345.0, name="BUS_02")
    pp.create_ext_grid(toy, bus=b1, vm_pu=1.0, name="SLK_BUS_01")
    pp.create_line_from_parameters(
        toy, from_bus=b1, to_bus=b2, length_km=1.0,
        r_ohm_per_km=11.9025, x_ohm_per_km=119.025, c_nf_per_km=0.0, max_i_ka=0.418,
    )
    pp.create_transformer_from_parameters(
        toy, hv_bus=b1, lv_bus=b2, sn_mva=100.0, vn_hv_kv=345.0, vn_lv_kv=345.0,
        vkr_percent=0.1, vk_percent=10.0, pfe_kw=0.0, i0_percent=0.0,
    )
    with pytest.raises(ContractError, match="duplicate branch keys"):
        branch_contingencies(toy)


# --------------------------------------------------------------------------
# unit contingencies
# --------------------------------------------------------------------------

def test_unit_contingencies_cover_gen_and_res_but_not_the_slack(net):
    reg = registry_from_net(net)
    cset = unit_contingencies(reg)
    validate_contingency_set(cset)
    assert len(cset) == N_GEN + N_RES
    assert (cset["kind"] == "unit").all() and (cset["order"] == 1).all()
    ids = set(cset["contingency_id"])
    assert ids == set(reg.index[reg["kind"].isin(["gen", "res"])])
    assert "SLK_BUS_31" not in ids
    assert (cset["contingency_id"] == cset["element_ids"].map(lambda e: e[0])).all()


def test_unit_contingencies_carry_the_bus(net):
    reg = registry_from_net(net)
    cset = unit_contingencies(reg).set_index("contingency_id")
    for unit_id, row in cset.iterrows():
        assert row["bus"] == reg.at[unit_id, "bus"]


def test_unit_contingencies_reject_an_unknown_kind():
    reg = pd.DataFrame({"bus": ["BUS_01"], "kind": ["shunt"]}, index=pd.Index(["X_01"], name="unit_id"))
    with pytest.raises(ContractError, match="kind"):
        unit_contingencies(reg)


def test_ext_grid_exclusion_is_ledgered():
    text = " ".join(EXT_GRID_EXCLUSION_LEDGER) if isinstance(EXT_GRID_EXCLUSION_LEDGER, (list, tuple)) else EXT_GRID_EXCLUSION_LEDGER
    assert "ext_grid" in text and "slack" in text.lower()


# --------------------------------------------------------------------------
# N-2 candidates
# --------------------------------------------------------------------------

def test_n2_candidates_are_all_unordered_pairs(net):
    n1 = branch_contingencies(net)
    n2 = n2_candidates(n1)
    validate_contingency_set(n2)
    assert len(n2) == N_PAIRS
    assert (n2["order"] == 2).all() and (n2["kind"] == "branch").all()
    pairs = n2["element_ids"].map(tuple)
    assert pairs.is_unique
    assert pairs.map(lambda p: p[0] < p[1]).all(), "pairs are sorted, so (a,b) and (b,a) cannot both appear"
    assert set(e for p in pairs for e in p) == set(n1["contingency_id"])


def test_n2_candidate_ids_are_derived_from_the_pair(net):
    n2 = n2_candidates(branch_contingencies(net))
    assert (n2["contingency_id"] == n2["element_ids"].map(lambda p: f"{p[0]}--{p[1]}")).all()
    assert n2["contingency_id"].is_unique
    assert n2["contingency_id"].is_monotonic_increasing


def test_n2_candidates_are_deterministic(net):
    a = n2_candidates(branch_contingencies(net))
    b = n2_candidates(branch_contingencies(load_case39_res()))
    pd.testing.assert_frame_equal(a, b)


def test_n2_candidates_refuse_a_non_n1_input(net):
    n1 = branch_contingencies(net)
    with pytest.raises(ContractError, match="order"):
        n2_candidates(n2_candidates(n1))
