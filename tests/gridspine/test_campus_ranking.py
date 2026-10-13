"""Critical hours for a campus (plan C3).

The case39 ranking asks transmission questions: inertia, IBR share, N-1
severity. A campus behind one PCC asks other ones. The hours that size its
plant are:

* the most power drawn through the PCC (``max_import_mw``);
* the most power pushed back into the grid (``max_export_mw``);
* the most consumption on the campus, loads plus battery charging
  (``max_consumption_mw``);
* the most renewable output, the voltage-rise hour (``max_renewable_mw``);
* the most power through each transformer (``max_trafo_<NAME>_mw``).

The transformer flow is the net injection of everything downstream of it:
a lossless estimate that is exact in P for a radial campus. Selection is the
same top-k union with boundary-tie spreading as case39 (``ranking.select``),
made within each investment period.
"""
import ast

import pandas as pd
import pytest

from gridspine.ranking.campus import campus_metrics, select_campus_hours
from gridspine.schema.contracts import ContractError


def t(v):
    return {"value": v, "source": "assumed"}


def spec(parallel=False):
    """PCC --TR1--> MV1 (PV, BESS, office load) --TR2--> LV (DC load)."""
    trafo = lambda hv, lv, kv_hv, kv_lv: {"hv_bus": hv, "lv_bus": lv, "sn_mva": t(40.0), "vn_hv_kv": t(kv_hv),
                                          "vn_lv_kv": t(kv_lv), "vk_percent": t(10.0), "vkr_percent": t(0.5),
                                          "pfe_kw": t(20.0), "i0_percent": t(0.1)}
    trafos = {"TR1": trafo("PCC", "MV1", 110.0, 20.0), "TR2": trafo("MV1", "LV", 20.0, 0.4)}
    if parallel:
        trafos["TR1B"] = trafo("PCC", "MV1", 110.0, 20.0)
    return {"campus": {
        "pcc": {"bus": "PCC", "vn_kv": 110.0, "vm_pu": t(1.0), "sk_max_mva": t(2000.0),
                "sk_min_mva": t(2000.0), "rx_max": t(0.1), "rx_min": t(0.1)},
        "buses": {"MV1": {"vn_kv": 20.0}, "LV": {"vn_kv": 0.4}},
        "transformers": trafos,
        "units": {
            "PV": {"kind": "pv", "bus": "MV1", "p_mw": t(20.0), "s_mva": t(21.0), "k_sc": t(1.2), "rx_sc": t(0.1)},
            "BESS": {"kind": "bess", "bus": "MV1", "p_mw": t(10.0), "e_mwh": t(20.0), "s_mva": t(10.5),
                     "k_sc": t(1.2), "rx_sc": t(0.1)},
            "OFFICE": {"kind": "load", "bus": "MV1", "p_mw": t(2.0), "pf": t(0.98)},
            "DC": {"kind": "load", "bus": "LV", "p_mw": t(30.0), "pf": t(0.98)},
        },
    }}


#: one period of six hours; hand-chosen so each criterion has a known winner
#:          h:   0     1     2     3     4     5
PV =       [0.0,  5.0, 20.0, 18.0,  2.0,  0.0]
BESS =     [-10.0, 0.0, -5.0,  0.0, 10.0,  0.0]   # charging is negative
OFFICE =   [-1.0, -2.0, -1.0, -1.0, -1.0, -1.0]
DC =       [-20.0, -21.0, -6.0, -18.0, -29.0, -25.0]


def tables(period=2030, scale=1.0):
    rows = []
    for uid, series in (("PV", PV), ("BESS", BESS), ("OFFICE", OFFICE), ("DC", DC)):
        for h, p in enumerate(series):
            rows.append({"unit_id": uid, "period": period, "hour": h, "p_mw": p * scale, "status": 1})
    hourly = pd.DataFrame(rows)
    net = hourly.groupby("hour")["p_mw"].sum()
    pcc = pd.DataFrame({"period": period, "hour": range(6), "weight": 1.0, "import_mw": (-net).to_numpy()})
    return hourly, pcc


def test_metrics_by_hand():
    hourly, pcc = tables()
    m = campus_metrics(hourly, pcc, spec()).loc[2030]
    # hour 0: injections 0 - 10 - 1 - 20 = -31 -> import 31, consumption 31, renewable 0
    assert m.at[0, "import_mw"] == 31.0 and m.at[0, "export_mw"] == 0.0
    assert m.at[0, "consumption_mw"] == 31.0 and m.at[0, "renewable_mw"] == 0.0
    # hour 2: 20 - 5 - 1 - 6 = +8 -> export 8
    assert m.at[2, "import_mw"] == -8.0 and m.at[2, "export_mw"] == 8.0
    # TR1 carries everything below the PCC: |net campus injection|
    assert m.at[0, "trafo_TR1_mw"] == 31.0 and m.at[2, "trafo_TR1_mw"] == 8.0
    # TR2 carries only the LV bus: the DC load
    assert m.at[4, "trafo_TR2_mw"] == 29.0


def test_each_criterion_selects_its_extreme_hour_and_reasons_are_unioned():
    hourly, pcc = tables()
    sel = select_campus_hours(campus_metrics(hourly, pcc, spec()), k=1)
    reasons = {int(r.hour): r.reasons for r in sel.itertuples()}
    # net injections by hour: -31, -18, +8, -1, -18, -26 -> hour 0 imports most
    assert "max_import_mw" in reasons[0]
    assert "max_consumption_mw" in reasons[0]                 # 31 MW
    assert "max_export_mw" in reasons[2]                      # the only export hour
    assert "max_renewable_mw" in reasons[2]                   # PV 20
    assert "max_trafo_TR2_mw" in reasons[4]                   # DC 29
    assert "max_trafo_TR1_mw" in reasons[0]
    assert set(sel["period"]) == {2030}


def test_a_criterion_that_never_happens_selects_nothing():
    hourly, pcc = tables()
    hourly.loc[hourly["unit_id"] == "PV", "p_mw"] = 0.0
    net = hourly.groupby("hour")["p_mw"].sum()
    pcc["import_mw"] = (-net).to_numpy()
    sel = select_campus_hours(campus_metrics(hourly, pcc, spec()), k=1)
    flat = [r for rs in sel["reasons"] for r in rs]
    assert "max_export_mw" not in flat and "max_renewable_mw" not in flat


def test_parallel_transformers_are_one_group_carrying_the_shared_flow():
    hourly, pcc = tables()
    m = campus_metrics(hourly, pcc, spec(parallel=True)).loc[2030]
    cols = [c for c in m.columns if c.startswith("trafo_")]
    assert "trafo_TR1+TR1B_mw" in cols and "trafo_TR1_mw" not in cols
    assert m.at[0, "trafo_TR1+TR1B_mw"] == 31.0


def test_a_meshed_transformer_gets_no_flow_estimate():
    """A second path from the PCC to MV1 (TR3 to MV2, then a cable) means
    removing TR1 cuts nothing off. Its flow depends on impedances, so it gets
    no lossless estimate. TR2 still radially feeds LV."""
    s = spec()
    s["campus"]["buses"]["MV2"] = {"vn_kv": 20.0}
    s["campus"]["transformers"]["TR3"] = dict(s["campus"]["transformers"]["TR1"], lv_bus="MV2")
    s["campus"]["cables"] = {"CB": {"from_bus": "MV2", "to_bus": "MV1", "length_km": t(1.0),
                                    "r_ohm_per_km": t(0.1), "x_ohm_per_km": t(0.1),
                                    "c_nf_per_km": t(0.0), "max_i_ka": t(1.0)}}
    hourly, pcc = tables()
    cols = [c for c in campus_metrics(hourly, pcc, s).columns if c.startswith("trafo_")]
    assert cols == ["trafo_TR2_mw"]


def test_selection_is_made_within_each_period():
    h1, p1 = tables(2030)
    h2, p2 = tables(2040, scale=2.0)
    hourly, pcc = pd.concat([h1, h2]), pd.concat([p1, p2])
    sel = select_campus_hours(campus_metrics(hourly, pcc, spec()), k=1)
    assert set(sel["period"]) == {2030, 2040}
    for period in (2030, 2040):
        hours = sel.loc[sel["period"] == period]
        assert any("max_import_mw" in rs for rs in hours["reasons"])


def test_k_larger_than_the_period_selects_every_hour():
    hourly, pcc = tables()
    sel = select_campus_hours(campus_metrics(hourly, pcc, spec()), k=50)
    assert sorted(sel["hour"]) == list(range(6))


def test_a_bad_k_is_refused():
    hourly, pcc = tables()
    with pytest.raises(ContractError, match="k"):
        select_campus_hours(campus_metrics(hourly, pcc, spec()), k=0)


def test_a_unit_missing_from_the_campus_is_refused():
    hourly, pcc = tables()
    hourly.loc[hourly["unit_id"] == "DC", "unit_id"] = "GHOST"
    with pytest.raises(ContractError, match="GHOST"):
        campus_metrics(hourly, pcc, spec())


def test_the_campus_ranking_stays_engine_free():
    """Same cage as the case39 ranking: pandas/numpy over the stage
    artifacts. Widened, as a decision, by the campus schema and ``select``,
    whose top-k with tie spreading this reuses."""
    import gridspine.ranking.campus as mod
    allowed = {"numpy", "pandas", "collections", "gridspine.schema.contracts", "gridspine.schema.campus",
               "gridspine.ranking.select"}
    tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported <= allowed, sorted(imported - allowed)
