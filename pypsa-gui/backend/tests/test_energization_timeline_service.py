# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""DC template review 06: the on-site firm rule, shortfall and run overlay of
`services.dc_energization_timeline`, on hand-built networks."""

from __future__ import annotations

# ── the on-site firm rule and the run overlay, on a hand-built network ──────


def _net():
    import pandas as pd
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "b")
    n.add("Load", "l", bus="b", p_set=[10.0, 30.0, 20.0, 10.0])
    return n


def test_onsite_firm_counts_only_committed_fuel_units_and_batteries():
    from services import dc_energization_timeline as T

    n = _net()
    n.add("Generator", "gas1", bus="b", carrier="gas", p_nom=8.0)
    n.add("Generator", "gas2", bus="b", carrier="gas", p_nom=4.0)
    n.add(
        "Generator", "gas_new", bus="b", carrier="gas", p_nom=5.0, p_nom_extendable=True
    )
    n.add("Generator", "pv", bus="b", carrier="solar", p_nom=9.0)
    n.add("Generator", "grid", bus="b", carrier="grid", p_nom=50.0)
    n.add("Generator", "zero", bus="b", carrier="gas", p_nom=0.0)
    n.add("Generator", "__voll_l", bus="b", carrier="load_shedding", p_nom=99.0)
    n.add("Generator", "late", bus="b", carrier="gas", p_nom=7.0, build_year=2040)
    n.add("Generator", "off", bus="b", carrier="gas", p_nom=6.0, active=False)
    n.add("StorageUnit", "bess", bus="b", carrier="battery", p_nom=3.0, max_hours=2)
    n.add(
        "StorageUnit",
        "bess_new",
        bus="b",
        carrier="battery",
        p_nom=1.0,
        p_nom_extendable=True,
    )
    out = {k["kind"]: k for k in T.onsite_firm(n, 2030)}
    assert out["genset"]["mw"] == 12.0 and sorted(out["genset"]["units"]) == [
        "gas1",
        "gas2",
    ]
    assert out["battery"]["mw"] == 3.0 and out["battery"]["units"] == ["bess"]
    assert {k["kind"] for k in T.onsite_firm(_net(), 2030)} == set()


def test_timeline_shortfall_and_period_peak_on_a_built_network():
    from services import dc_energization_timeline as T

    n = _net()
    n.add("Generator", "gas1", bus="b", carrier="gas", p_nom=8.0)
    out = T.timeline(n, {})
    (p,) = out["periods"]
    assert p["demand_mw"] == 30.0 and p["onsite_mw"] == 8.0
    assert out["basis"] == "inputs" and out["run_state"] is None
    assert "run" not in p


def test_run_overlay_reports_shed_and_curtailment_in_mwh():
    import numpy as np
    import pandas as pd

    from services import dc_energization_timeline as T

    n = _net()
    n.add("Generator", "gas1", bus="b", carrier="gas", p_nom=25.0)
    n.add("Generator", "pv", bus="b", carrier="solar", p_nom=5.0)
    n.add("Generator", "__voll_l", bus="b", carrier="load_shedding", p_nom=99.0)
    n.generators["p_nom_opt"] = n.generators["p_nom"]
    idx = n.snapshots
    n.generators_t.p = pd.DataFrame(
        {
            "gas1": [10.0, 25.0, 20.0, 10.0],
            "pv": [0.0, 0.0, 0.0, 0.0],
            "__voll_l": [0.0, 5.0, 0.0, 0.0],
        },
        index=idx,
    )
    (row,) = T._overlay(n, T._period_masks(n))
    assert row["shed_mwh"] == 5.0
    assert row["served_mwh"] == 70.0 - 5.0
    assert row["curtailed_mwh"] == 20.0  # pv could give 5 MW in 4 h and gave 0
    assert np.isclose(row["served_mwh"] + row["shed_mwh"], 70.0)


def test_a_nan_in_the_dispatch_never_reaches_the_answer():
    import json

    import pandas as pd

    from services import dc_energization_timeline as T

    n = _net()
    n.add("Generator", "pv", bus="b", carrier="solar", p_nom=5.0)
    n.add("Generator", "__voll_l", bus="b", carrier="load_shedding", p_nom=99.0)
    n.generators["p_nom_opt"] = n.generators["p_nom"]
    n.generators_t.p = pd.DataFrame(
        {"pv": [float("nan")] * 4, "__voll_l": [float("nan"), 1.0, 0.0, 0.0]},
        index=n.snapshots,
    )
    out = T.timeline(n, {})
    json.dumps(out, allow_nan=False)  # a NaN would be a 500 on the wire
    assert out["periods"][0]["run"]["shed_mwh"] == 1.0
    assert out["periods"][0]["run"]["curtailed_mwh"] == 20.0


def test_grid_mw_is_the_period_minimum_not_its_maximum(monkeypatch):
    from services import dc_energization_timeline as T

    stage = {
        "period": "all",
        "start": "2030-01-01",
        "end": "2031-01-01",
        "energized_mw": 5.0,
        "max_mw": 9.0,
        "set": True,
    }
    monkeypatch.setattr(T.T, "import_stages", lambda n, meta: [stage])
    (p,) = T.timeline(_net(), {})["periods"]
    assert p["grid_mw"] == 5.0
    assert p["shortfall_mw"] == 25.0  # peak 30 against 5 grid, no on-site
