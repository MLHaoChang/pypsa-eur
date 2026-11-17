# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""The SSTI unit-interaction-factor screen (grid-compliance GC-08, TRGC 19)."""

import math

import numpy as np
import pandas as pd
import pytest

from gridspine.ingest.campus import build_campus
from gridspine.schema.contracts import ContractError
from gridspine.static import ssti_uif as su
from gridspine.templates.grid_codes import load_grid_code, validate_profile
from tests.gridspine.test_campus import campus_spec, t


def test_two_machine_hand_calculation():
    # UIF_1 = 100/50 (1 - 800/1000)^2 = 0.08; UIF_2 = 100/200 (1 - 950/1000)^2 = 0.00125
    rows = su.screen(100.0, {"G1": (50.0, 800.0), "G2": (200.0, 950.0)}, 1000.0, 0.1)
    assert rows.set_index("machine")["uif"].to_dict() == pytest.approx(
        {"G1": 0.08, "G2": 0.00125}, abs=1e-6
    )
    assert list(rows["verdict"]) == ["clear", "clear"]
    assert set(rows["label"]) == {"screening; binding answer is the EMT study"}


def test_machine_with_no_infeed_gives_zero():
    rows = su.screen(100.0, {"G": (50.0, 1000.0)}, 1000.0, 0.1)
    assert rows["uif"].iloc[0] == 0.0
    assert rows["verdict"].iloc[0] == "clear"


def test_threshold_edge_equal_clears_above_flags():
    # 100/50 (1 - 750/1000)^2 = 0.125 exactly in binary
    at = su.screen(100.0, {"G": (50.0, 750.0)}, 1000.0, 0.125)
    below = su.screen(100.0, {"G": (50.0, 750.0)}, 1000.0, 0.12)
    assert at["uif"].iloc[0] == 0.125
    assert at["verdict"].iloc[0] == "clear"
    assert below["verdict"].iloc[0] == "flag"


def test_no_threshold_takes_default_assumed():
    rows = su.screen(100.0, {"G": (50.0, 750.0)}, 1000.0)
    assert rows["threshold"].iloc[0] == 0.1
    assert bool(rows["assumed"].iloc[0])
    assert "the grid code sets no UIF threshold; demo default" in rows["note"].iloc[0]
    assert rows["verdict"].iloc[0] == "flag"
    coded = su.screen(100.0, {"G": (50.0, 750.0)}, 1000.0, 0.2)
    assert not bool(coded["assumed"].iloc[0])
    assert su.screen(100.0, {"G": (50.0, 750.0)}, 1000.0, 0.2, assumed=True)[
        "assumed"
    ].iloc[0]


def test_sc_i_above_sc_tot_within_tolerance_clamps():
    value, clamped = su.uif(100.0, 50.0, 1000.0 * (1 + 0.5e-6), 1000.0)
    assert (value, clamped) == (0.0, True)
    rows = su.screen(100.0, {"G": (50.0, 1000.0 * (1 + 0.5e-6))}, 1000.0, 0.1)
    assert su.CLAMPED in rows["note"].iloc[0]
    assert rows["sc_without_mva"].iloc[0] == 1000.0
    assert su.uif(100.0, 50.0, 1000.0, 1000.0) == (0.0, False)


@pytest.mark.parametrize(
    ("args", "reason"),
    [
        ((100.0, 50.0, 1000.0 * (1 + 2e-6), 1000.0), "cannot raise the fault level"),
        ((0.0, 50.0, 800.0, 1000.0), "S_plant must be > 0"),
        ((100.0, 0.0, 800.0, 1000.0), "S_i must be > 0"),
        ((100.0, 50.0, 800.0, 0.0), "SC_tot must be > 0"),
        ((100.0, 50.0, -1.0, 1000.0), "SC_i must be >= 0"),
        ((math.inf, 50.0, 800.0, 1000.0), "S_plant must be finite"),
        ((100.0, math.inf, 800.0, 1000.0), "S_i must be finite"),
        ((100.0, 50.0, math.nan, 1000.0), "SC_i must be finite"),
        ((100.0, 50.0, 800.0, math.inf), "SC_tot must be finite"),
        ((100.0, "x", 800.0, 1000.0), "S_i must be a number"),
    ],
)
def test_uif_refuses(args, reason):
    with pytest.raises(ContractError, match=reason):
        su.uif(*args)


@pytest.mark.parametrize(
    ("thr", "reason"),
    [(0.0, "threshold must be > 0"), (math.inf, "threshold must be finite")],
)
def test_screen_refuses_bad_threshold(thr, reason):
    with pytest.raises(ContractError, match=reason):
        su.screen(100.0, {"G": (50.0, 800.0)}, 1000.0, thr)


@pytest.mark.parametrize(
    "key", ["ssti_uif_threshold", "subsync_torsional_clearance_hz"]
)
@pytest.mark.parametrize("value", [0.0, -1.0, math.inf])
def test_profile_keys_positive_and_finite(key, value):
    base = load_grid_code("generic_assumed", raw=True)
    with pytest.raises(ContractError, match=f"{key} value must be a positive number"):
        validate_profile(
            "x", {**base, key: {"value": value, "clause": "c", "source": "code"}}
        )


def test_threshold_from_profile():
    assert su.threshold_from_profile(None) == (None, [])
    assert su.threshold_from_profile({}) == (None, [])
    v, cited = su.threshold_from_profile(
        {"ssti_uif_threshold": {"value": 0.15, "clause": "TRGC 19", "source": "code"}}
    )
    assert v == 0.15
    assert cited == [
        {"key": "ssti_uif_threshold", "clause": "TRGC 19", "source": "code"}
    ]


# --- the campus ------------------------------------------------------------------------------


def _levels(s_mva_by_bus):
    """A ``campus_fault_levels``-shaped table from literal fault levels (MVA) per bus and case."""
    vn = {"PCC": 110.0, "MV1": 20.0, "MV2": 20.0}
    return pd.DataFrame(
        [
            {
                "bus": b,
                "vn_kv": vn[b],
                "ikss_max_ka": s["max"] / (math.sqrt(3) * vn[b]),
                "ikss_min_ka": s["min"] / (math.sqrt(3) * vn[b]),
            }
            for b, s in s_mva_by_bus.items()
        ]
    )


FULL = {
    "PCC": {"max": 3100.0, "min": 2080.0},
    "MV1": {"max": 680.0, "min": 540.0},
    "MV2": {"max": 550.0, "min": 440.0},
}
NO_GEN1 = {
    "PCC": {"max": 3050.0, "min": 2000.0},
    "MV1": {"max": 590.0, "min": 460.0},
    "MV2": {"max": 460.0, "min": 360.0},
}


def _stub(campus, installed):
    return _levels(NO_GEN1 if "GEN1" not in installed else FULL)


def test_campus_uif_hand_recompute_from_literal_fault_levels():
    campus = build_campus(campus_spec())
    study = su.campus_uif(campus, fault_levels=_stub)
    rows = study.rows.set_index(["bus", "case"])
    # S_plant: MV1 = BESS1 22 + PV1 16.5 = 38.5 MVA; MV2 = DC_LOAD 50 / 0.98; PCC = their sum. S_GEN1 = 12.5.
    s_mv2 = 50.0 / 0.98
    expect = {
        ("MV1", "max"): 38.5 / 12.5 * (1 - 590 / 680) ** 2,
        ("MV1", "min"): 38.5 / 12.5 * (1 - 460 / 540) ** 2,
        ("MV2", "max"): s_mv2 / 12.5 * (1 - 460 / 550) ** 2,
        ("MV2", "min"): s_mv2 / 12.5 * (1 - 360 / 440) ** 2,
        ("PCC", "max"): (38.5 + s_mv2) / 12.5 * (1 - 3050 / 3100) ** 2,
        ("PCC", "min"): (38.5 + s_mv2) / 12.5 * (1 - 2000 / 2080) ** 2,
    }
    for key, value in expect.items():
        assert rows.at[key, "uif"] == pytest.approx(value, abs=1e-6), key
    assert rows.at[("MV2", "min"), "s_plant_mva"] == pytest.approx(s_mv2, abs=1e-9)
    assert rows.at[("PCC", "max"), "c"] == 1.1
    assert rows.at[("PCC", "min"), "c"] == 1.0
    # the verdict is the worst converter-bus row over both cases: MV2 min, 0.1356 > 0.1
    assert study.worst_uif == pytest.approx(expect[("MV2", "min")], abs=1e-6)
    assert study.verdict == "flag"
    assert expect[("MV2", "max")] < expect[("MV2", "min")]


def test_campus_uif_pcc_row_never_sets_verdict():
    """At the PCC the grid's infeed dominates: the PCC row clears while an internal converter bus flags."""
    campus = build_campus(campus_spec())
    study = su.campus_uif(campus)
    pcc = study.rows[study.rows["role"] == "PCC (information)"]
    mv2 = study.rows[study.rows["bus"] == "MV2"]
    assert set(pcc["verdict"]) == {"clear"}
    assert "flag" in set(mv2["verdict"])
    assert study.verdict == "flag"
    assert study.worst_uif == pytest.approx(float(mv2["uif"].max()))
    # a threshold above every converter-bus UIF clears, whatever the PCC row says
    clear = su.campus_uif(campus, threshold=1.0)
    assert clear.verdict == "clear"


def test_campus_uif_plant_note_pinned():
    study = su.campus_uif(build_campus(campus_spec()), fault_levels=_stub)
    assert study.notes == [
        "S_plant is the sum at the bus of each converter-interfaced unit's s_mva and the "
        "rectifier-fed load's p_mw / pf (conservative)"
    ]
    assert all(n.startswith(su.PLANT_NOTE) for n in study.rows["note"])


def test_campus_without_genset_not_evaluated():
    spec = campus_spec()
    del spec["campus"]["units"]["GEN1"]
    study = su.campus_uif(build_campus(spec))
    assert study.verdict == "not evaluated"
    assert study.notes == [su.NO_MACHINES]
    assert study.rows.empty


def test_campus_without_converter_not_evaluated():
    spec = campus_spec()
    for uid in ("DC_LOAD", "BESS1", "PV1"):
        del spec["campus"]["units"][uid]
    study = su.campus_uif(build_campus(spec))
    assert (study.verdict, study.notes) == ("not evaluated", [su.NO_CONVERTERS])


def test_sc_i_zero_accepted_and_tolerance_edge_clamps():
    assert su.uif(100.0, 50.0, 0.0, 1000.0) == (2.0, False)
    edge = 1000.0 * (1.0 + su.SC_TOLERANCE)
    assert su.uif(100.0, 50.0, edge, 1000.0) == (0.0, True)


def test_campus_uif_fault_levels_in_mva():
    rows = su.campus_uif(
        build_campus(campus_spec()), fault_levels=_stub
    ).rows.set_index(["bus", "case"])
    assert rows.at[("MV2", "min"), "sc_tot_mva"] == pytest.approx(440.0, abs=1e-9)
    assert rows.at[("MV2", "min"), "sc_without_mva"] == pytest.approx(360.0, abs=1e-9)
    assert rows.at[("PCC", "max"), "sc_tot_mva"] == pytest.approx(3100.0, abs=1e-9)


def test_campus_uif_c_from_the_bus_own_voltage():
    """C is the evaluation bus's own: a bus at or below 1 kV takes c_min 0.95 (IEC 60909)."""

    def lv_mv2(campus, installed):
        t = _stub(campus, installed)
        t.loc[t["bus"] == "MV2", "vn_kv"] = 0.4
        return t

    rows = su.campus_uif(
        build_campus(campus_spec()), fault_levels=lv_mv2
    ).rows.set_index(["bus", "case"])
    assert rows.at[("MV2", "min"), "c"] == 0.95
    assert rows.at[("MV1", "min"), "c"] == 1.0


@pytest.mark.parametrize(
    ("pf", "reason"),
    [
        (0.0, r"must be in \(0, 1\]"),
        (-0.5, r"must be in \(0, 1\]"),
        (1.2, r"must be in \(0, 1\]"),
        (math.nan, "pf of L must be finite"),
    ],
)
def test_plant_refuses_a_bad_load_pf(pf, reason):
    units = pd.DataFrame(
        [{"kind": "load", "bus": "MV", "p_mw": 10.0, "pf": pf, "s_mva": math.nan}],
        index=["L"],
    )
    with pytest.raises(ContractError, match=reason):
        su.plant_mva_by_bus(units)


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        (
            {"kind": "bess", "s_mva": math.nan, "p_mw": 1.0, "pf": math.nan},
            "s_mva of U must be finite",
        ),
        (
            {"kind": "bess", "s_mva": -1.0, "p_mw": 1.0, "pf": math.nan},
            "rating of U must be >= 0",
        ),
        (
            {"kind": "load", "s_mva": math.nan, "p_mw": math.inf, "pf": 0.9},
            "p_mw of U must be finite",
        ),
    ],
)
def test_plant_refuses_a_bad_rating(row, reason):
    with pytest.raises(ContractError, match=reason):
        su.plant_mva_by_bus(pd.DataFrame([{**row, "bus": "MV"}], index=["U"]))


def test_fault_levels_with_the_machines_alone():
    """
    Converters are current sources in ``machine_net``: their infeed would raise SC_tot and understate
    UIF. The screen energises the machines alone, so a converter's k_sc leaves UIF unchanged.
    """
    seen = []

    def spy(campus, installed):
        seen.append(set(installed))
        return _stub(campus, installed)

    su.campus_uif(build_campus(campus_spec()), fault_levels=spy)
    assert seen == [{"GEN1"}, set()]
    base = su.campus_uif(build_campus(campus_spec())).rows["uif"]
    spec = campus_spec()
    spec["campus"]["units"]["BESS1"]["k_sc"] = t(6.0)
    stiff = su.campus_uif(build_campus(spec)).rows["uif"]
    assert np.allclose(stiff, base, rtol=1e-12, atol=0)
