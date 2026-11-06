"""
Campus-library-gaps 01 (`.scratch/campus-library-gaps/issues/01-h2-hub-220-110kv-transformer.md`, owner Q-CL-1):
the H2 hub's PCC transformer `GRID_IMPORT` is 220/110 kV. Before the shipped campus library had a unit at that
pair, `invest_campus(method="milp")` failed ("transformer GRID_IMPORT: no candidate in the library") and the
least-cost pick left the need unresolved ("no library transformer at the group's voltages carries 104.5 MVA
intact ..." on `eh_h2_hub`, 27.3 MVA on `eh_h2_hub_staged`).

With the cited TR_220_110_* units both methods complete on both hubs and buy one unit: the smallest that carries
the need with the 20 % design margin (`SizingCriteria.margin`), TR_220_110_150 for the hub (104.5 x 1.2 = 125.4
MVA > 120) and TR_220_110_120 for the staged hub (27.3 x 1.2 = 32.8 MVA). One unit, not two: the N-1
rule of `SizingCriteria` sizes the units of a parallel group for a sibling out, and a group of one has none
(the same rule as every other template's PCC transformer).
"""
from __future__ import annotations

import pathlib
import sys

import pandas as pd
import pytest
import yaml

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

from gridspine.drivers import campus_study as cs  # noqa: E402

#: hub -> (the need the least-cost pick reported unresolved on master 103ef4969, intact and with a unit out,
#: the unit bought, its MVA)
HUBS = {"eh_h2_hub": (104.5, "TR_220_110_150", 150.0), "eh_h2_hub_staged": (27.3, "TR_220_110_120", 120.0)}
MARGIN = 0.2


@pytest.fixture(scope="module", params=list(HUBS))
def hub(request, tmp_path_factory):
    root = tmp_path_factory.mktemp(request.param)
    n = T.BUILDERS[request.param]()
    if not getattr(n, "objective", None):
        n.optimize(solver_name="highs")
    nc = root / "network.nc"
    n.export_to_netcdf(str(nc))
    draft = cs.draft_from_project(nc)
    return request.param, root, draft.spec, nc


@pytest.mark.parametrize("method", ["least_cost", "milp"])
def test_the_hub_buys_one_cited_220_110_kv_unit(hub, method):
    name, root, spec, nc = hub
    need_mva, unit, unit_mva = HUBS[name]
    c = spec["campus"]
    t = c["transformers"]["GRID_IMPORT"]
    assert (c["pcc"]["vn_kv"], c["buses"][t["lv_bus"]]["vn_kv"]) == (220.0, 110.0)
    run = root / method
    cs.prepare_campus(run, spec, nc)
    cs.rank_campus(run)
    cs.invest_campus(run, method=method)

    inv = pd.read_csv(run / cs.INVESTMENT_CSV)
    tr = inv[inv["need"] == "transformer GRID_IMPORT"]
    assert len(tr) == 1 and (inv["status"] != "unresolved").all(), inv[["need", "status", "reason"]]
    row = tr.iloc[0]
    assert (row["library_id"], row["units"], row["status"]) == (unit, 1, "chosen")

    invested = yaml.safe_load((run / cs.INVESTED_YAML).read_text())["campus"]["transformers"]["GRID_IMPORT"]
    assert invested["library_id"] == unit and invested["sn_mva"]["value"] == unit_mva
    # the smallest cited unit that carries the need with the margin
    smaller = [s for s in (120.0, 150.0, 200.0, 250.0, 315.0) if s < unit_mva]
    assert unit_mva >= need_mva * (1 + MARGIN) > max(smaller, default=0.0)

    comp = pd.read_csv(run / "campus_compliance_invested.csv").set_index("check")
    loading = comp.loc["transformer_loading"]
    assert loading["status_with_measures"] == "pass" and loading["worst_case"] == "intact"
    # the worst intact hour is within the need the library could not meet before, and within the unit
    assert 0 < loading["value"] / 100 * unit_mva <= need_mva
