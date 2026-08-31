import pandapower as pp
import pandas as pd
import pytest

from gridspine.handoff.raw_writer import write_raw
from gridspine.ingest.pandapower_source import load_case39, registry_from_net
from gridspine.schema.contracts import ContractError
from gridspine.static.loadflow import LFResult, apply_dispatch, run_lf


def dispatch_all_on(net, registry):
    rows = []
    for unit_id, rec in registry.iterrows():
        if rec["kind"] == "gen":
            i = net.gen.index[net.gen["name"] == unit_id][0]
            p = float(net.gen.at[i, "p_mw"])
        else:
            p = 0.0
        rows.append({"unit_id": unit_id, "hour": 0, "p_mw": p, "q_mvar": 0.0, "status": 1})
    return pd.DataFrame(rows)


def test_lf_converges_with_native_dispatch():
    net = load_case39()
    reg = registry_from_net(net)
    apply_dispatch(net, dispatch_all_on(net, reg), hour=0, registry=reg)
    res = run_lf(net)
    assert isinstance(res, LFResult) and res.converged
    assert set(res.bus.index) == set(net.bus["name"])
    assert res.bus["vm_pu"].between(0.8, 1.2).all()


def test_offline_unit_is_out_of_service():
    net = load_case39()
    reg = registry_from_net(net)
    table = dispatch_all_on(net, reg)
    victim = table.loc[table["unit_id"].str.startswith("G_"), "unit_id"].iloc[0]
    table.loc[table["unit_id"] == victim, ["status", "p_mw"]] = [0, 0.0]
    apply_dispatch(net, table, hour=0, registry=reg)
    i = net.gen.index[net.gen["name"] == victim][0]
    assert not bool(net.gen.at[i, "in_service"])


def test_nonconvergence_is_a_result_not_a_crash():
    net = load_case39()
    net.load["p_mw"] *= 25.0  # absurd loading
    res = run_lf(net)
    assert res.converged is False


# ---------------------------------------------------------------------------
# Task 8: branch-flow keys and the cross-module CKT contract
# ---------------------------------------------------------------------------

def _raw_section(text, begin, end):
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if begin in ln) + 1
    stop = next(i for i, ln in enumerate(lines) if end in ln)
    return lines[start:stop]


def _fields(line):
    return [t.strip().strip("'").strip() for t in line.split(",")]


def test_branch_flow_filled_from_res_line_and_res_trafo():
    net = load_case39()
    reg = registry_from_net(net)
    apply_dispatch(net, dispatch_all_on(net, reg), hour=0, registry=reg)
    res = run_lf(net)
    assert res.converged
    bf = res.branch_flow
    assert list(bf.columns) == [
        "from_bus", "to_bus", "ckt", "p_from_mw", "q_from_mvar", "loading_percent"
    ]
    assert len(bf) == len(net.line) + len(net.trafo)
    # Line rows come first, in net.line order, carrying the FROM-end flow.
    assert bf["p_from_mw"].iloc[: len(net.line)].to_numpy() == pytest.approx(
        net.res_line["p_from_mw"].to_numpy()
    )
    assert bf["q_from_mvar"].iloc[: len(net.line)].to_numpy() == pytest.approx(
        net.res_line["q_from_mvar"].to_numpy()
    )
    # Transformer rows follow, oriented HV -> LV exactly as the RAW writes them.
    assert bf["p_from_mw"].iloc[len(net.line):].to_numpy() == pytest.approx(
        net.res_trafo["p_hv_mw"].to_numpy()
    )
    assert bf["from_bus"].iloc[len(net.line):].tolist() == [
        net.bus.at[b, "name"] for b in net.trafo["hv_bus"]
    ]


def test_diverged_lf_has_an_empty_branch_flow():
    net = load_case39()
    net.load["p_mw"] *= 25.0
    res = run_lf(net)
    assert res.converged is False
    assert res.branch_flow.empty


def test_branch_flow_keys_map_1to1_onto_the_raw_branch_records(tmp_path):
    """THE cross-module contract: pf_compare keys a branch on
    (from_bus, to_bus, ckt), and the only place that triple is authored is
    the RAW writer's per-record-type `_IdCounter` over the SORTED bus-number
    pair. run_lf reproduces that convention; if the two ever drift, a
    PowerFactory branch export keyed off the RAW stops joining onto the LF
    result and the comparison degrades to a bare key-set mismatch.

    Asserting equal SETS alone would pass with duplicates on either side, so
    both sides are checked duplicate-free and the counts pinned as well.
    """
    net = load_case39()
    nums = write_raw(net, tmp_path / "c39.raw")
    name_of_num = {n: name for name, n in nums.items()}
    text = (tmp_path / "c39.raw").read_text()

    raw_keys = []
    for ln in _raw_section(text, "BEGIN BRANCH DATA", "END OF BRANCH DATA"):
        f = _fields(ln)                       # I, J, CKT, ...
        raw_keys.append((name_of_num[int(f[0])], name_of_num[int(f[1])], f[2]))
    trafo_lines = _raw_section(text, "BEGIN TRANSFORMER DATA", "END OF TRANSFORMER DATA")
    assert len(trafo_lines) == 4 * len(net.trafo)
    for k in range(0, len(trafo_lines), 4):
        f = _fields(trafo_lines[k])           # I, J, K, CKT, ...
        raw_keys.append((name_of_num[int(f[0])], name_of_num[int(f[1])], f[3]))

    res = run_lf(net)
    assert res.converged
    lf_keys = list(
        zip(res.branch_flow["from_bus"], res.branch_flow["to_bus"], res.branch_flow["ckt"])
    )

    assert len(raw_keys) == len(net.line) + len(net.trafo)
    assert len(set(raw_keys)) == len(raw_keys), "RAW branch keys are not unique"
    assert len(set(lf_keys)) == len(lf_keys), "LF branch keys are not unique"
    assert set(lf_keys) == set(raw_keys)
    assert len(lf_keys) == len(raw_keys)


def test_parallel_branches_get_the_writers_ckt_sequence(tmp_path):
    """case39 has no parallel circuit, so the counter is exercised on a toy
    net — otherwise every CKT is '1' and a writer that ignored the counter
    entirely would still satisfy the case39 contract test above."""
    net = pp.create_empty_network(sn_mva=100.0)
    b1 = pp.create_bus(net, vn_kv=345.0, name="BUS_01")
    b2 = pp.create_bus(net, vn_kv=345.0, name="BUS_02")
    pp.create_ext_grid(net, bus=b1, vm_pu=1.0, name="SLK_BUS_01")
    pp.create_load(net, bus=b2, p_mw=50.0, q_mvar=10.0)
    for _ in range(2):
        pp.create_line_from_parameters(
            net, from_bus=b1, to_bus=b2, length_km=1.0,
            r_ohm_per_km=11.9025, x_ohm_per_km=119.025,
            c_nf_per_km=0.0, max_i_ka=0.418,
        )
    # third circuit written the other way round: same SORTED key, so the
    # counter must keep counting rather than restart at '1'.
    pp.create_line_from_parameters(
        net, from_bus=b2, to_bus=b1, length_km=1.0,
        r_ohm_per_km=11.9025, x_ohm_per_km=119.025,
        c_nf_per_km=0.0, max_i_ka=0.418,
    )
    write_raw(net, tmp_path / "t.raw")
    raw_ckts = [_fields(ln)[2] for ln in
                _raw_section((tmp_path / "t.raw").read_text(),
                             "BEGIN BRANCH DATA", "END OF BRANCH DATA")]
    res = run_lf(net)
    assert res.converged
    assert raw_ckts == ["1", "2", "3"]
    assert res.branch_flow["ckt"].tolist() == raw_ckts


def test_line_and_trafo_sharing_a_bus_pair_is_rejected_not_silently_merged():
    """The RAW keeps branch and transformer records in separate sections with
    independent CKT counters, so a line and a transformer between the same
    bus pair are both legally CKT '1'. LFResult.branch_flow has ONE flat key
    space, and there the collision would make the comparison join the line's
    flow against the transformer's row. It has to fail loudly instead.

    Nothing in case39 or the toy nets builds this shape, so without this test
    the guard is unexercised and could be deleted with the suite still green.
    """
    net = pp.create_empty_network(sn_mva=100.0)
    b1 = pp.create_bus(net, vn_kv=345.0, name="BUS_01")
    b2 = pp.create_bus(net, vn_kv=345.0, name="BUS_02")
    pp.create_ext_grid(net, bus=b1, vm_pu=1.0, name="SLK_BUS_01")
    pp.create_load(net, bus=b2, p_mw=50.0, q_mvar=10.0)
    pp.create_line_from_parameters(
        net, from_bus=b1, to_bus=b2, length_km=1.0,
        r_ohm_per_km=11.9025, x_ohm_per_km=119.025,
        c_nf_per_km=0.0, max_i_ka=0.418,
    )
    pp.create_transformer_from_parameters(
        net, hv_bus=b1, lv_bus=b2, sn_mva=200.0,
        vn_hv_kv=345.0, vn_lv_kv=345.0,
        vkr_percent=1.0, vk_percent=10.0, pfe_kw=0.0, i0_percent=0.0,
        shift_degree=0.0, name="T1",
    )
    with pytest.raises(ContractError, match="duplicate branch keys"):
        run_lf(net)
