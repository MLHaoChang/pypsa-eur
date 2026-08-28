import pandas as pd

from gridspine.ingest.pandapower_source import load_case39, registry_from_net
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
