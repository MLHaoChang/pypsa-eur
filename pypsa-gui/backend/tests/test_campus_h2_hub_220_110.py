"""
Campus-library-gaps 01 (`.scratch/campus-library-gaps/issues/01-h2-hub-220-110kv-transformer.md`, owner Q-CL-1):
the H2 hub's PCC transformer `GRID_IMPORT` is 220/110 kV. Before the shipped campus library had a unit at that
pair, `invest_campus(method="milp")` failed ("transformer GRID_IMPORT: no candidate in the library") and the
least-cost pick left the need unresolved ("no library transformer at the group's voltages carries 104.5 MVA
intact (104.5 MVA with a unit out) ..." on `eh_h2_hub`, 27.3 MVA on `eh_h2_hub_staged`).

The library's 220/110 kV units are R10 sizes, 125, 160, 200, 250 and 315 MVA. Both methods now complete on
both hubs. Each buys one unit, the smallest that carries the need with the 20 % design margin
(`SizingCriteria.margin`):

- `eh_h2_hub_staged` buys TR_220_110_125, because 27.3 x 1.2 = 32.8 MVA.
- `eh_h2_hub` buys TR_220_110_160, because 104.5 x 1.2 = 125.4 MVA, which is more than 125.

It is one unit, not two, because the N-1 rule of `SizingCriteria` sizes the units of a *parallel group* for a
sibling out, and a group of one has none. That is the same rule every other template's PCC transformer gets.

The N-1 arm is covered by a twin case: `eh_h2_hub` with its PCC drafted as two parallel units. A survivor then
carries the whole 104.5 MVA, so each unit must be TR_220_110_160 (125.4 MVA with the margin). With N-1 off the
intact share would let one unit carry the hub.
"""

from __future__ import annotations

import copy
import pathlib
import sys

import pandas as pd
import pytest
import yaml

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

from gridspine.drivers import campus_study as cs  # noqa: E402
from gridspine.static.campus_flow import SizingCriteria  # noqa: E402

#: hub -> (the need the least-cost pick reported unresolved on master 103ef4969, intact and with a unit out,
#: the unit bought, its MVA)
HUBS = {
    "eh_h2_hub": (104.5, "TR_220_110_160", 160.0),
    "eh_h2_hub_staged": (27.3, "TR_220_110_125", 125.0),
}
SIZES = (125.0, 160.0, 200.0, 250.0, 315.0)
MARGIN = SizingCriteria().margin


def _draft(name, root):
    n = T.BUILDERS[name]()
    if not getattr(n, "objective", None):
        n.optimize(solver_name="highs")
    nc = root / "network.nc"
    n.export_to_netcdf(str(nc))
    return cs.draft_from_project(nc).spec, nc


def _invest(root, spec, nc, method, criteria=SizingCriteria()):
    run = root / method
    cs.prepare_campus(run, spec, nc)
    cs.rank_campus(run)
    cs.invest_campus(run, method=method, criteria=criteria)
    inv = pd.read_csv(run / cs.INVESTMENT_CSV)
    invested = yaml.safe_load((run / cs.INVESTED_YAML).read_text())["campus"][
        "transformers"
    ]
    comp = pd.read_csv(run / "campus_compliance_invested.csv").set_index("check")
    return inv, invested, comp


@pytest.fixture(scope="module", params=list(HUBS))
def hub(request, tmp_path_factory):
    root = tmp_path_factory.mktemp(request.param)
    return (request.param, root, *_draft(request.param, root))


@pytest.fixture(scope="module")
def twin(tmp_path_factory):
    """`eh_h2_hub` with its PCC transformer drafted as two parallel units on the same two buses."""
    root = tmp_path_factory.mktemp("twin")
    spec, nc = _draft("eh_h2_hub", root)
    spec = copy.deepcopy(spec)
    tr = spec["campus"]["transformers"]
    one = tr.pop("GRID_IMPORT")
    one.pop("pypsa_name", None)
    tr["PCC_T1"], tr["PCC_T2"] = copy.deepcopy(one), copy.deepcopy(one)
    return root, spec, nc


@pytest.mark.parametrize("method", ["least_cost", "milp"])
def test_the_hub_buys_one_cited_220_110_kv_unit(hub, method):
    name, root, spec, nc = hub
    need_mva, unit, unit_mva = HUBS[name]
    c = spec["campus"]
    t = c["transformers"]["GRID_IMPORT"]
    assert (c["pcc"]["vn_kv"], c["buses"][t["lv_bus"]]["vn_kv"]) == (220.0, 110.0)
    inv, invested, comp = _invest(root, spec, nc, method)

    tr = inv[inv["need"] == "transformer GRID_IMPORT"]
    assert len(tr) == 1 and (inv["status"] != "unresolved").all(), inv[
        ["need", "status", "reason"]
    ]
    row = tr.iloc[0]
    assert (row["library_id"], row["units"], row["status"]) == (unit, 1, "chosen")
    assert (
        invested["GRID_IMPORT"]["library_id"] == unit
        and invested["GRID_IMPORT"]["sn_mva"]["value"] == unit_mva
    )
    # the smallest R10 unit that carries the intact need with the margin
    assert (
        unit_mva
        >= need_mva * (1 + MARGIN)
        > max([s for s in SIZES if s < unit_mva], default=0.0)
    )

    loading = comp.loc["transformer_loading"]
    assert (
        loading["status_with_measures"] == "pass" and loading["worst_case"] == "intact"
    )
    # the worst intact hour is within the need the library could not meet before, and within the unit
    assert 0 < loading["value"] / 100 * unit_mva <= need_mva


@pytest.mark.parametrize("method", ["least_cost", "milp"])
def test_a_parallel_pair_is_sized_for_a_unit_out(twin, method):
    root, spec, nc = twin
    need_mva = HUBS["eh_h2_hub"][0]
    inv, invested, comp = _invest(root, spec, nc, method)
    row = inv[inv["need"] == "transformer PCC_T1+PCC_T2"].iloc[0]
    assert (row["library_id"], row["units"], row["status"]) == (
        "TR_220_110_160",
        2,
        "chosen",
    )
    assert {k: v["library_id"] for k, v in invested.items()} == {
        "PCC_T1": "TR_220_110_160",
        "PCC_T2": "TR_220_110_160",
    }
    # a survivor carries the whole need: 125 MVA would hold the intact half but not that
    assert need_mva / 2 * (1 + MARGIN) <= 125.0 < need_mva * (1 + MARGIN) <= 160.0
    assert comp.loc["transformer_loading", "status_with_measures"] == "pass"


def test_without_n_minus_1_the_pair_shrinks_to_one_unit(twin):
    root, spec, nc = twin
    inv, invested, _ = _invest(
        root / "no_n1", spec, nc, "least_cost", SizingCriteria(n_minus_1=False)
    )
    row = inv[inv["need"] == "transformer PCC_T1+PCC_T2"].iloc[0]
    assert (row["library_id"], row["units"]) == ("TR_220_110_160", 1)
    assert list(invested) == ["PCC_T1"]
