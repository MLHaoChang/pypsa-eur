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


# ── the islanded case (GC-08b) ────────────────────────────────────────────────


def _island_spec(**gens):
    """campus_spec with its PCC tie normally open, DC_LOAD 81 MW at pf 0.98 on MV2 and ``gens`` (name:
    (s_mva, xd_pp)) as the gensets on MV2, each with R/X 0.07."""
    spec = campus_spec()
    c = spec["campus"]
    c["pcc"].update(tie_normally_open=True, p_connection_mw=t(20.0))
    c["units"]["DC_LOAD"]["p_mw"] = t(81.0)
    del c["units"]["GEN1"]
    for name, (s, xd) in gens.items():
        c["units"][name] = {"kind": "genset", "bus": "MV2", "p_mw": t(0.8 * s), "s_mva": t(s),
                            "xd_pp": t(xd), "rx_sc": t(0.07)}
    return spec


def _genset_sc(s_mva, xd, c):
    """IEC 60909 S''k (MVA) of a genset at its own terminals, rated at the bus voltage: c Un^2 / |K_G Z_G|,
    K_G = c_max / (1 + x''d sin phi) with cos phi 0.8, |Z_G| = x''d (1 + (R/X)^2)^0.5 Un^2 / S_r."""
    k_g = 1.1 / (1.0 + xd * 0.6)
    return c * s_mva / (k_g * xd * math.sqrt(1.0 + 0.07**2))


#: S''k (MVA) of a genset with x''d 0.15, R/X 0.07 at its own 20 kV bus, by ``_genset_sc``: {S_r: (max, min)}.
GENSET_SC = {
    10.0: (72.48928494562487, 65.89934995056807),
    12.5: (90.61160618203111, 82.37418743821009),
    8.0: (57.991427956499905, 52.71947996045446),
}
ISLAND = {"island max": ("max", 1.1, 0), "island min": ("min", 1.0, 1)}


def _close(a, b):
    return a == pytest.approx(b, rel=1e-9, abs=1e-6)


def test_genset_sc_literals_are_the_hand_formula():
    for s, (smax, smin) in GENSET_SC.items():
        assert _close(_genset_sc(s, 0.15, 1.1), smax) and _close(_genset_sc(s, 0.15, 1.0), smin)


def test_island_one_genset_is_its_own_fault_level():
    """Tie open, one 10 MVA genset: SC_i = 0 and SC_tot is the genset's own, so UIF = S_plant / S_i ~ 8.27."""
    study = su.campus_uif(build_campus(_island_spec(GEN1=(10.0, 0.15))))
    rows = study.rows.set_index(["bus", "case"])
    for label, (_, c, k) in ISLAND.items():
        row = rows.loc[("MV2", label)]
        assert row["sc_without_mva"] == 0.0
        assert _close(row["sc_tot_mva"], GENSET_SC[10.0][k])
        assert _close(row["uif"], 8.265306122448980)  # (81 / 0.98) / 10
        assert row["c"] == c and row["role"] == su.ROLE_ISLAND and row["verdict"] == "flag"
        assert _close(rows.at[("MV1", label), "uif"], 3.85)  # 38.5 / 10: SC_i = 0 there too
    assert study.island_verdict == "flag"
    assert _close(study.island_worst_uif, 8.265306122448980)


def test_island_two_gensets_hand_computed():
    """Two gensets on MV2 with the same x''d and R/X, so SC_tot = SC_1 + SC_2 and SC_i is the other
    machine's alone."""
    rows = su.campus_uif(build_campus(_island_spec(GEN1=(12.5, 0.15), GEN2=(8.0, 0.15)))).rows
    rows = rows.set_index(["bus", "case", "machine"])
    s_plant = 81.0 / 0.98
    for label, (_, _, k) in ISLAND.items():
        sc = {"GEN1": GENSET_SC[12.5][k], "GEN2": GENSET_SC[8.0][k]}
        tot = sc["GEN1"] + sc["GEN2"]
        for uid, s_i, other in (("GEN1", 12.5, "GEN2"), ("GEN2", 8.0, "GEN1")):
            row = rows.loc[("MV2", label, uid)]
            assert _close(row["sc_tot_mva"], tot)
            assert _close(row["sc_without_mva"], sc[other])
            assert _close(row["uif"], s_plant / s_i * (1 - sc[other] / tot) ** 2)
    # the same, as literals: SC scales with S_r at equal x''d, so (S_plant / S_i)(S_i / 20.5)^2
    for label in ISLAND:
        assert _close(rows.at[("MV2", label, "GEN1"), "uif"], 2.4584491738396728)
        assert _close(rows.at[("MV2", label, "GEN2"), "uif"], 1.5734074712573907)


#: The grid-case UIF rows of the islanding fixtures, recorded from master's ``campus_uif`` (GC-08), which
#: GC-08b must leave as they were (GK A2 U4): {(bus, case, machine): UIF}, and the meta verdict/worst.
MASTER_ONE = {
    ("MV1", "max", "GEN1"): 0.04946584244527219, ("MV2", "max", "GEN1"): 0.17598066611394375,
    ("PCC", "max", "GEN1"): 0.006270726812655763, ("MV1", "min", "GEN1"): 0.055935427407156714,
    ("MV2", "min", "GEN1"): 0.19422748113521945, ("PCC", "min", "GEN1"): 0.01145306613658685,
}
MASTER_TWO = {
    ("MV1", "max", "GEN1"): 0.04360592101632999, ("MV1", "max", "GEN2"): 0.026962383092670093,
    ("MV2", "max", "GEN1"): 0.16576847246883214, ("MV2", "max", "GEN2"): 0.10614685151638045,
    ("PCC", "max", "GEN1"): 0.004736321163903126, ("PCC", "max", "GEN2"): 0.002706262592840422,
    ("MV1", "min", "GEN1"): 0.0485374484353147, ("MV1", "min", "GEN2"): 0.029978246678375297,
    ("MV2", "min", "GEN1"): 0.18079756680761608, ("MV2", "min", "GEN2"): 0.1158121289720601,
    ("PCC", "min", "GEN1"): 0.008532808183664682, ("PCC", "min", "GEN2"): 0.004873432669216666,
}


@pytest.mark.parametrize("gens, master", [({"GEN1": (10.0, 0.15)}, MASTER_ONE),
                                          ({"GEN1": (12.5, 0.15), "GEN2": (8.0, 0.15)}, MASTER_TWO)])
def test_island_leaves_the_grid_cases_as_master(gens, master):
    study = su.campus_uif(build_campus(_island_spec(**gens)))
    grid = study.rows[study.rows["case"].isin(su.CASES)].set_index(["bus", "case", "machine"])
    assert sorted(grid.index) == sorted(master)
    for key, value in master.items():
        assert _close(grid.at[key, "uif"], value), key
    assert set(grid["role"]) == {su.ROLE_CONVERTER, su.ROLE_PCC}
    # the verdict and worst the GC-08 readers use are the grid cases' alone
    assert study.verdict == "flag" and _close(study.worst_uif, max(v for (b, _, _), v in master.items()
                                                                   if b != "PCC"))
    island = study.rows[~study.rows["case"].isin(su.CASES)]
    assert set(island["case"]) == set(ISLAND) and set(island["role"]) == {su.ROLE_ISLAND}
    assert set(island["bus"]) == {"MV1", "MV2"}  # no PCC information row islanded


def _no_source_at_mv1(campus, installed, island=False):
    """Real fault levels, except that islanded nothing reaches MV1 (SC 0 there)."""
    from gridspine.static.campus_sc import campus_fault_levels

    out = campus_fault_levels(campus, installed, island=island)
    if island:
        out.loc[out["bus"] == "MV1", ["ikss_max_ka", "ikss_min_ka"]] = 0.0
    return out


def test_island_bus_without_a_source_is_not_evaluated():
    """SC_tot 0 islanded: the bus's rows read not evaluated with the reason, never inf, NaN-as-clear or a
    refusal, and the island verdict cannot clear while one reads so."""
    campus = build_campus(_island_spec(GEN1=(10.0, 0.15)))
    study = su.campus_uif(campus, threshold=100.0, fault_levels=_no_source_at_mv1)
    mv1 = study.rows[(study.rows["bus"] == "MV1") & (study.rows["role"] == su.ROLE_ISLAND)]
    assert len(mv1) == 2 and set(mv1["verdict"]) == {"not evaluated"}
    assert mv1["uif"].isna().all() and (mv1["sc_tot_mva"] == 0.0).all()
    assert all(su.NO_ISLAND_SOURCE in n for n in mv1["note"])
    mv2 = study.rows[(study.rows["bus"] == "MV2") & (study.rows["role"] == su.ROLE_ISLAND)]
    assert set(mv2["verdict"]) == {"clear"}  # 8.27 under the threshold 100
    assert study.island_verdict == "not evaluated"
    assert _close(study.island_worst_uif, 8.265306122448980)
    assert study.verdict == "clear"  # the grid cases are untouched
    # with a flag elsewhere in the island, the island verdict flags
    assert su.campus_uif(campus, fault_levels=_no_source_at_mv1).island_verdict == "flag"


def test_converter_only_campus_with_island_data_is_not_evaluated():
    spec = _island_spec()
    study = su.campus_uif(build_campus(spec))
    assert study.verdict == "not evaluated" and study.island_verdict == "not evaluated"
    assert study.notes == [su.NO_MACHINES]


def test_island_case_selection_both_ways():
    """A closed tie without island data: no island call and the GC-08 rows. An open tie, or a closed tie
    with island requirements: the grid-out calls, one with every machine (a lone machine's SC_i is 0)."""
    seen = []

    def spy(campus, installed, island=False):
        seen.append((set(installed), island))
        return _stub(campus, installed)

    closed = build_campus(campus_spec())
    assert not su.islands(closed)
    study = su.campus_uif(closed, fault_levels=spy)
    assert seen == [({"GEN1"}, False), (set(), False)]
    assert set(study.rows["case"]) == set(su.CASES) and study.island_verdict == "not evaluated"
    assert math.isnan(study.island_worst_uif)
    spec = campus_spec()
    spec["campus"]["pcc"].update(tie_normally_open=True, p_connection_mw=t(20.0))
    declared = build_campus(campus_spec())
    declared.island["requirements"] = {"values": {}, "sources": {}}  # island operation declared
    for campus in (build_campus(spec), declared):
        assert su.islands(campus)
        seen.clear()
        study = su.campus_uif(campus, fault_levels=spy)
        assert seen == [({"GEN1"}, False), (set(), False), ({"GEN1"}, True)]
        assert set(study.rows["case"]) == {*su.CASES, *ISLAND}


@pytest.mark.parametrize("sc", [0.0, math.nan, math.inf])
@pytest.mark.parametrize("threshold, assumed, limit, flagged",
                         [(None, False, su.DEFAULT_THRESHOLD, True), (0.5, False, 0.5, False),
                          (0.5, True, 0.5, True)])
def test_unevaluated_island_rows_keep_the_threshold_and_its_tag(sc, threshold, assumed, limit, flagged):
    def levels(campus, installed, island=False):
        out = _no_source_at_mv1(campus, installed, island)
        if island:
            out.loc[out["bus"] == "MV1", ["ikss_max_ka", "ikss_min_ka"]] = sc
        return out

    study = su.campus_uif(build_campus(_island_spec(GEN1=(10.0, 0.15))), threshold, assumed=assumed,
                          fault_levels=levels)
    mv1 = study.rows[(study.rows["bus"] == "MV1") & (study.rows["role"] == su.ROLE_ISLAND)]
    assert set(mv1["verdict"]) == {"not evaluated"} and (mv1["threshold"] == limit).all()
    assert set(mv1["assumed"]) == {flagged}
    assert all((su.NO_THRESHOLD in n) == (threshold is None) for n in mv1["note"])
    assert list(mv1["case"]) == list(ISLAND) and list(mv1["c"]) == [1.1, 1.0]


def test_island_all_clear_clears():
    study = su.campus_uif(build_campus(_island_spec(GEN1=(10.0, 0.15))), threshold=100.0)
    assert study.island_verdict == "clear" and study.verdict == "clear"


def test_grid_case_without_fault_level_still_refused():
    """The grid cases keep GC-08's refusal of SC_tot 0; only an island bus reads not evaluated."""

    def levels(campus, installed, island=False):
        out = _no_source_at_mv1(campus, installed, island)
        if not island:
            out.loc[out["bus"] == "MV1", ["ikss_max_ka", "ikss_min_ka"]] = 0.0
        return out

    with pytest.raises(ContractError, match="SC_tot must be > 0"):
        su.campus_uif(build_campus(_island_spec(GEN1=(10.0, 0.15))), fault_levels=levels)
