# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""Heat-pump COP against the served band's sink temperature, and part-load on the Link (grid-to-process 16).

Acceptance first: the Carnot COP at a 90 degC sink; two pumps on two bands get two series; a heat pump with
a minimum load is a committable Link that is off or at least at minimum; the part_load kind keeps its refusal."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.process_heat import served_sink_c
from services.profile_gen.engine import generate_profile
from services.profile_gen.series import PartLoadNotASeriesError

_GROUND = {"source": "ground", "source_c": 10.0, "eta": 0.45, "cop_max": 7.0}
NOW = "2026-01-01T00:00:00+00:00"


def _gen(params, ids=("X",)):
    return generate_profile(
        points=list(ids),
        technology="cop",
        params=params,
        year=2023,
        now=NOW,
        family="series",
    )


def test_cop_at_45_is_todays_and_at_90_follows_the_carnot_formula():
    base = _gen({**_GROUND, "sink_c": 45.0}).series["X"]
    assert base.iloc[0] == pytest.approx(0.45 * 318.15 / 35.0, abs=1e-9)
    hot = _gen({**_GROUND, "sink_c": 90.0}).series["X"]
    assert hot.iloc[0] == pytest.approx(0.45 * 363.15 / 80.0, abs=1e-9)
    assert hot.iloc[0] < base.iloc[0]
    # the default sink is still 45 degC, so a series without a band is unchanged
    assert _gen(_GROUND).series["X"].equals(base)


def test_two_heat_pumps_on_two_bands_get_two_series():
    res = _gen(
        {**_GROUND, "sink_c_by_id": {"hp_lo": 60.0, "hp_hi": 120.0}},
        ids=("hp_lo", "hp_hi", "hp_none"),
    )
    s = res.series
    assert s["hp_lo"].iloc[0] == pytest.approx(0.45 * 333.15 / 50.0, abs=1e-9)
    assert s["hp_hi"].iloc[0] == pytest.approx(0.45 * 393.15 / 110.0, abs=1e-9)
    assert s["hp_none"].iloc[0] == pytest.approx(
        0.45 * 318.15 / 35.0, abs=1e-9
    )  # no band: sink_c fallback
    assert res.meta["sink_c_by_id"] == {"hp_lo": 60.0, "hp_hi": 120.0}


def test_served_sink_reads_the_band_bus_and_none_off_a_band():
    n = pypsa.Network()
    n.add("Bus", "e", carrier="AC")
    n.add("Bus", "h", carrier="heat")
    n.add("Bus", "plain", carrier="heat")
    n.buses["temperature_c"] = np.nan
    n.buses.loc["h", "temperature_c"] = 90.0
    n.add("Link", "hp", bus0="e", bus1="h")
    n.add("Link", "hp2", bus0="e", bus1="plain")
    assert served_sink_c(n, "hp") == 90.0
    assert served_sink_c(n, "hp2") is None
    assert served_sink_c(n, "missing") is None


def test_part_load_stays_a_refusal_that_points_at_the_link_fields():
    with pytest.raises(PartLoadNotASeriesError, match="p_min_pu"):
        generate_profile(
            points=["X"],
            technology="part_load",
            params={},
            year=2023,
            now=NOW,
            family="series",
        )


def test_a_committable_heat_pump_is_off_or_at_its_minimum():
    cop = 0.45 * 363.15 / 80.0
    snaps = pd.date_range("2023-01-01", periods=6, freq="h")
    n = pypsa.Network()
    n.set_snapshots(snaps)
    n.add("Bus", "e", carrier="AC")
    n.add("Bus", "h", carrier="heat")
    n.add("Generator", "grid", bus="e", p_nom=100.0, marginal_cost=10.0)
    n.add("Generator", "shed", bus="h", p_nom=100.0, marginal_cost=1000.0)
    n.add(
        "Link",
        "hp",
        bus0="e",
        bus1="h",
        p_nom=10.0,
        efficiency=cop,
        committable=True,
        p_min_pu=0.3,
    )
    n.add("Load", "d", bus="h", p_set=[1.0, 20.0, 1.0, 20.0, 1.0, 20.0])
    status, cond = n.optimize(
        solver_name="highs", solver_options={"output_flag": False}
    )
    assert status == "ok", cond
    p0 = n.links_t.p0["hp"].to_numpy()
    on = p0 > 1e-6
    assert (p0[on] >= 0.3 * 10.0 - 1e-6).all()  # never below the minimum load
    assert on.tolist() == [
        False,
        True,
        False,
        True,
        False,
        True,
    ]  # 1 MW of demand sits under the minimum
    assert n.generators_t.p["shed"].iloc[0] == pytest.approx(1.0)
