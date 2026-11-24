import math

import pandas as pd
import pytest

from gridspine.static.campus_ratings import (
    CableAssumptions,
    SwitchgearAssumptions,
    assumptions_table,
    cable_checks,
    choose_cable,
    choose_switchgear,
    dc_factor_m,
    switchgear_duty,
)
from gridspine.templates.campus_assets import load_asset_library


def _v(x):
    return {"value": x, "source": "assumed"}


LIB = {
    "switchgear": [
        {
            "id": f"SG_33_{r}",
            "vn_kv": _v(33.0),
            "ik_rated_ka": _v(r),
            "capex_eur": _v(1000 * r),
        }
        for r in (40.0, 25.0, 31.5)  # unsorted on purpose
    ],
    "cables": [
        {
            "id": "A95",
            "vn_kv": _v(33.0),
            "cross_section_mm2": _v(95.0),
            "r_ohm_per_km": _v(0.32),
            "x_ohm_per_km": _v(0.134),
            "max_i_ka": _v(0.22),
            "capex_eur_per_km": _v(100.0),
        },
        {
            "id": "A240",
            "vn_kv": _v(33.0),
            "cross_section_mm2": _v(240.0),
            "r_ohm_per_km": _v(0.125),
            "x_ohm_per_km": _v(0.115),
            "max_i_ka": _v(0.35),
            "capex_eur_per_km": _v(150.0),
        },
    ],
}


def _sc(*rows):
    return pd.DataFrame(
        [
            {
                "bus": b,
                "vn_kv": kv,
                "ikss_max_ka": ik,
                "ip_max_ka": ip,
                "rated_ka": rated,
                "period": p,
            }
            for b, kv, ik, ip, rated, p in rows
        ]
    )


def _one(rows, f_hz=50, margin=0.1, a=SwitchgearAssumptions(), library=LIB):
    return choose_switchgear(_sc(*rows), library, f_hz, margin, a).iloc[0]


NO_DC = 1.4  # ip / Ik'' below sqrt(2): kappa < 1, so m = 0 and Ith = Ik''


def test_dc_factor_m_hand_values():
    # m = (exp(4 f Tk ln(k - 1)) - 1) / (2 f Tk ln(k - 1)); kappa 1.8: ln 0.8 = -0.2231436
    assert dc_factor_m(1.8, 50, 1.0) == pytest.approx(
        0.0448142, rel=1e-5
    )  # 1 / (100 x 0.2231436)
    assert dc_factor_m(1.8, 60, 1.0) == pytest.approx(
        0.0373452, rel=1e-5
    )  # 1 / (120 x 0.2231436)
    assert dc_factor_m(1.8, 50, 0.1) == pytest.approx(
        0.4429753, rel=1e-5
    )  # (1 - e^-4.462871) / 2.231436
    assert dc_factor_m(1.0, 50, 1.0) == 0.0 and dc_factor_m(0.9, 50, 1.0) == 0.0
    assert dc_factor_m(2.0, 50, 1.0) == 2.0


def test_duty_with_computed_m():
    ip = math.sqrt(2) * 20.0 * 1.8
    d = switchgear_duty(20.0, ip, 50, SwitchgearAssumptions(mu=0.9))
    assert d["Ik''"] == 20.0 and d["ip"] == ip
    assert d["Ib"] == pytest.approx(18.0)
    assert d["Ith"] == pytest.approx(
        20.4432307, rel=1e-6
    )  # 20 x sqrt(m 0.0448142 + n 1)
    assert switchgear_duty(20.0, 28.0, 50)["Ith"] == 20.0  # no DC component


def test_mu_and_n_above_one_are_refused():
    for kw in ({"mu": 1.2}, {"n": 1.1}, {"mu": 0.0}, {"n": 0.0}, {"tk_s": 0.0}):
        with pytest.raises(ValueError):
            SwitchgearAssumptions(**kw)


def test_ik_governs_with_no_dc_and_the_margin_boundary_is_exact():
    r = _one([("B1", 33.0, 20.0, 28.0, math.nan, 1)], margin=0.25)  # need exactly 25.0
    assert (r.chosen_id, r.chosen_ka, r.chosen_ip_ka, r.capex_eur) == (
        "SG_33_25.0",
        25.0,
        62.5,
        25000.0,
    )
    assert r.governing == "Ik'' 20.00 kA, period 1"
    assert r.utilisation == pytest.approx(0.8)
    assert r.ib_ka == 20.0 and r.ith_ka == 20.0
    assert pd.isna(r.flag)  # undeclared: nothing to flag
    assert (
        _one([("B1", 33.0, 20.0001, 28.0, math.nan, 1)], margin=0.25).chosen_ka == 31.5
    )


def test_ith_governs_with_its_dc_heat():
    ip = (
        math.sqrt(2) * 20.0 * 1.8
    )  # 50.91 kA: 50.91 / 62.5 = 0.8146 < Ith 20.443 / 25 = 0.8177
    r = _one([("B1", 33.0, 20.0, ip, math.nan, 1)])
    assert r.chosen_ka == 25.0
    assert r.governing == "Ith 20.44 kA, period 1"
    assert r.utilisation == pytest.approx(20.4432307 / 25, rel=1e-6)


def test_ib_is_reported_but_never_above_ik():
    r = _one(
        [("B1", 33.0, 20.0, 28.0, math.nan, 1)], a=SwitchgearAssumptions(mu=0.8, n=0.9)
    )
    assert r.ib_ka == pytest.approx(16.0) and r.ith_ka == pytest.approx(
        20.0 * math.sqrt(0.9)
    )
    assert r.governing.startswith("Ik'' 20.00 kA")


def test_peak_governs():
    ip = (
        math.sqrt(2) * 20.0 * 1.9
    )  # 53.74 kA; Ith 20.93 kA x 1.2 = 25.11 > 25 -> 31.5 kA
    r = _one([("B1", 33.0, 20.0, ip, math.nan, 1)], margin=0.2)
    assert r.chosen_ka == 31.5
    assert r.governing == "ip 53.74 kA, period 1"
    assert r.utilisation == pytest.approx(53.7401154 / 78.75, rel=1e-6)


def test_peak_boundary_at_60_hz_and_50_hz():
    a = SwitchgearAssumptions(
        tk_s=3.0, tkr_s=3.0
    )  # gear rated for 3 s: Ith 24.37 kA stays under 25
    assert (
        _one(
            [("B1", 33.0, 24.0, 65.0, math.nan, 1)], f_hz=60, margin=0.0, a=a
        ).chosen_ka
        == 25.0
    )  # 2.6 x 25
    r = _one([("B1", 33.0, 24.0, 65.0, math.nan, 1)], f_hz=60, margin=0.0, a=a)
    assert r.chosen_ip_ka == 65.0 and r.governing == "ip 65.00 kA, period 1"
    assert (
        _one(
            [("B1", 33.0, 24.0, 65.01, math.nan, 1)], f_hz=60, margin=0.0, a=a
        ).chosen_ka
        == 31.5
    )
    assert (
        _one(
            [("B1", 33.0, 24.0, 65.0, math.nan, 1)], f_hz=50, margin=0.0, a=a
        ).chosen_ka
        == 31.5
    )  # > 62.5


def test_worst_period_named():
    r = _one(
        [
            ("B1", 33.0, 12.0, 12.0 * NO_DC, math.nan, 1),
            ("B1", 33.0, 21.0, 21.0 * NO_DC, math.nan, 3),
            ("B1", 33.0, 18.0, 18.0 * NO_DC, math.nan, 2),
        ]
    )
    assert r.ikss_ka == 21.0 and r.governing == "Ik'' 21.00 kA, period 3"
    assert r.chosen_ka == 25.0


def test_declared_below_chosen_is_flagged_not_overwritten():
    r = _one([("B1", 33.0, 20.0, 28.0, 20.0, 1)])  # need 22 -> 25
    assert (r.declared_ka, r.chosen_ka, r.flag) == (20.0, 25.0, "declared_below_chosen")
    equal = _one([("B1", 33.0, 20.0, 28.0, 25.0, 1)])
    assert equal.declared_ka == 25.0 and pd.isna(equal.flag)
    above = _one([("B1", 33.0, 20.0, 28.0, 40.0, 1)])
    assert above.declared_ka == 40.0 and above.chosen_ka == 25.0 and pd.isna(above.flag)


def test_beyond_ladder():
    r = _one([("B1", 33.0, 40.0, 50.0, math.nan, 1)])  # 44 kA > 40
    assert r.chosen_id is None and math.isnan(r.chosen_ka) and r.flag == "beyond_ladder"
    assert r.governing == (
        "Ik'' 44.00 kA with the 10% margin, above the largest rated short-time current 40 kA at 33 kV"
    )


def test_beyond_ladder_names_the_peak_when_the_peak_fails():
    # Ik'' 37, ip 101: Ith 39.47 kA fits 40, the peak 101 > 2.5 x 40 does not
    r = _one([("B1", 33.0, 37.0, 101.0, math.nan, 1)], margin=0.0)
    assert r.flag == "beyond_ladder"
    assert (
        r.governing
        == "ip 101.00 kA with the 0% margin, above the largest rated peak 100 kA at 33 kV"
    )


def test_beyond_ladder_never_prints_a_need_equal_to_the_limit():
    r = _one([("B1", 33.0, 40.001, 40.001 * NO_DC, math.nan, 1)], margin=0.0)
    assert r.flag == "beyond_ladder"
    assert r.governing.startswith("Ik'' 40.001 kA with the 0% margin")


def test_iec_ladder_when_library_has_no_voltage():
    r = _one([("B11", 11.0, 17.0, 20.0, math.nan, 1)])  # 18.7 -> 20
    assert (r.chosen_id, r.chosen_ka) == ("IEC 62271-1 20 kA", 20.0)
    assert math.isnan(r.capex_eur)
    assert (
        _one([("B11", 11.0, 17.0, 20.0, math.nan, 1)], library=None).chosen_ka == 20.0
    )


def test_one_row_per_bus():
    out = choose_switchgear(
        _sc(
            ("A", 33.0, 10.0, 14.0, math.nan, 1),
            ("B", 33.0, 30.0, 42.0, math.nan, 1),
            ("A", 33.0, 11.0, 15.0, math.nan, 2),
        ),
        LIB,
        50,
        0.1,
    )
    assert list(out["bus"]) == ["A", "B"]
    assert list(out["chosen_ka"]) == [25.0, 40.0]


def _cable(
    i_ka,
    length_km=0.5,
    ith_ka=5.0,
    margin=0.0,
    a=CableAssumptions(),
    library=LIB,
    vn_kv=33.0,
):
    return choose_cable(
        library,
        vn_kv=vn_kv,
        length_km=length_km,
        i_ka=i_ka,
        ith_ka=ith_ka,
        margin=margin,
        a=a,
    )


def test_cable_ampacity_governs():
    c = _cable(0.15)  # 0.15 / (0.22 x 0.8) = 0.852
    assert (c["chosen_id"], c["runs"], c["section_mm2"]) == ("A95", 1, 95.0)
    assert c["governing"] == "ampacity"
    assert c["utilisation"] == pytest.approx(0.15 / 0.176)
    assert c["capex_eur"] == pytest.approx(50.0)
    assert c["chosen_i_ka"] == pytest.approx(0.22)


def test_cable_cheapest_passing_section_or_runs():
    c = _cable(0.2)  # A95 x1 fails; A95 x2 costs 100, A240 x1 costs 75
    assert (c["chosen_id"], c["runs"]) == ("A240", 1)
    assert (
        _cable(0.2, a=CableAssumptions(derating=1.0))["chosen_id"] == "A95"
    )  # 0.2 <= 0.22


def test_cable_margin_on_current():
    assert _cable(0.17, margin=0.0)["chosen_id"] == "A95"
    assert _cable(0.17, margin=0.1)["chosen_id"] == "A240"  # 0.187 > 0.176


def test_cable_short_circuit_withstand_governs():
    c = _cable(0.1, ith_ka=10.0)  # S_min = 10000 / 94 = 106.4 > 95, not divided by runs
    assert c["chosen_id"] == "A240" and c["runs"] == 1
    assert c["governing"] == "short-circuit withstand"
    assert c["s_min_mm2"] == pytest.approx(1e4 / 94)
    assert c["utilisation"] == pytest.approx(1e4 / 94 / 240)
    t = _cable(0.1, ith_ka=10.0, a=CableAssumptions(t_clear_s=0.25))  # 53.2 mm2
    assert t["chosen_id"] == "A95"


def test_cable_voltage_drop_governs():
    c = _cable(
        0.1, length_km=20.0
    )  # A95 x1 3.63 %; A240 x1 1.62 % at 3000 vs A95 x2 at 4000
    sin = math.sqrt(1 - 0.95**2)
    vd = 100 * math.sqrt(3) * 0.1 * 20.0 * (0.125 * 0.95 + 0.115 * sin) / 33.0
    assert (c["chosen_id"], c["runs"]) == ("A240", 1)
    assert c["governing"] == "voltage drop"
    assert c["vd_pct"] == pytest.approx(vd)
    assert c["utilisation"] == pytest.approx(vd / 3.0)
    assert (
        _cable(0.1, length_km=20.0, a=CableAssumptions(vd_limit_pct=5.0))["chosen_id"]
        == "A95"
    )


def test_cable_runs_reduce_voltage_drop():
    only95 = {"cables": [LIB["cables"][0]]}
    c = _cable(0.1, length_km=20.0, library=only95)
    assert (c["chosen_id"], c["runs"]) == ("A95", 2)


BOUNDARY = {
    "id": "B",
    "vn_kv": _v(10.0),
    "cross_section_mm2": _v(95.0),
    "r_ohm_per_km": _v(0.3),
    "x_ohm_per_km": _v(0.0),
    "max_i_ka": _v(0.2),
    "capex_eur_per_km": _v(1.0),
}
UNITY = CableAssumptions(derating=1.0, pf=1.0)


def _checks(i_ka, ith_ka=1.0, n=1, a=UNITY, margin=0.0):
    return cable_checks(BOUNDARY, n, i_ka, 1.0, 10.0, ith_ka, margin, a)


def test_voltage_drop_units_literal():
    # 100 A on 1 km of 0.3 ohm/km at 10 kV line to line, pf 1:
    # dU = sqrt(3) x 100 A x 0.3 ohm = 51.96 V = 0.5196 % of 10 kV
    c = _checks(0.1)
    assert c["_vd_pct"] == pytest.approx(0.519615, rel=1e-6)
    assert _checks(0.2, n=2)["_vd_pct"] == pytest.approx(
        0.519615, rel=1e-6
    )  # per run: 100 A each
    lib = {"cables": [BOUNDARY]}
    pick = lambda limit: choose_cable(  # noqa: E731
        lib,
        vn_kv=10.0,
        length_km=1.0,
        i_ka=0.1,
        ith_ka=1.0,
        margin=0.0,
        a=CableAssumptions(derating=1.0, pf=1.0, vd_limit_pct=limit),
    )
    assert pick(0.52)["runs"] == 1
    assert pick(0.519)["runs"] == 2  # just over the limit in one run


def test_ampacity_boundary_literal():
    assert _checks(0.2)["ampacity"] == pytest.approx(1.0)  # exactly at 200 A passes
    assert _checks(0.2001)["ampacity"] > 1.0
    assert _checks(0.16, a=CableAssumptions(derating=0.8, pf=1.0))[
        "ampacity"
    ] == pytest.approx(1.0)
    assert _checks(0.1, margin=1.0)["ampacity"] == pytest.approx(1.0)  # 100 A x 2


def test_short_circuit_withstand_boundary_literal():
    # S >= Ik'' sqrt(t) / k: 95 mm2 x 94 = 8.93 kA for 1 s (Al); copper 143 carries 13.585 kA
    assert _checks(0.01, ith_ka=8.93)["short-circuit withstand"] == pytest.approx(1.0)
    assert _checks(0.01, ith_ka=8.94)["short-circuit withstand"] > 1.0
    cu = CableAssumptions(derating=1.0, pf=1.0, conductor="Cu")
    assert cu.k == 143.0 and CableAssumptions().k == 94.0
    assert _checks(0.01, ith_ka=13.585, a=cu)[
        "short-circuit withstand"
    ] == pytest.approx(1.0)
    assert _checks(0.01, ith_ka=8.93, n=3)["short-circuit withstand"] == pytest.approx(
        1.0
    )  # not split


def test_the_conductor_is_shown_per_cable():
    assert _cable(0.15)["conductor"] == "Al"
    assert _cable(0.15)["k"] == 94.0
    assert _cable(0.15, a=CableAssumptions(conductor="Cu"))["k"] == 143.0
    assert _cable(5.0)["conductor"] == "Al"


def test_cable_none_passes():
    c = _cable(5.0)
    assert c["chosen_id"] is None and c["runs"] == 0
    assert "no library cable at 33 kV" in c["governing"]
    assert _cable(0.1, vn_kv=66.0)["chosen_id"] is None


def test_shipped_library_picks():
    lib = load_asset_library()
    sg = choose_switchgear(_sc(("B", 33.0, 20.0, 50.0, math.nan, 1)), lib, 50, 0.2)
    assert sg.iloc[0].chosen_id == "SG_33_25p0"  # 24 kA, peak 60 <= 62.5
    c = choose_cable(lib, vn_kv=33.0, length_km=1.0, i_ka=0.2, ith_ka=10.0, margin=0.2)
    assert c["chosen_id"] is not None and c["section_mm2"] >= 1e4 / 94


def test_assumptions_table_all_assumed():
    rows = assumptions_table(SwitchgearAssumptions(), CableAssumptions())
    assert {r["source"] for r in rows} == {"assumed"}
    assert {r["name"] for r in rows} == {
        "mu",
        "n",
        "conductor",
        "tk_s",
        "tkr_s",
        "derating",
        "vd_limit_pct",
        "pf",
        "t_clear_s",
        "k",
    }
    assert next(r for r in rows if r["name"] == "derating")["value"] == 0.8


# --------------------------------------------------------------------------
# the driver: rate_campus reads what size_campus wrote
# --------------------------------------------------------------------------


def _run(tmp_path, declared_i_ka=1.2):
    import yaml

    from gridspine.drivers.campus_study import (
        CAMPUS_YAML,
        LF_LINE_CSV,
        SHORT_CIRCUIT_CSV,
    )
    from tests.gridspine.test_campus import campus_spec, t

    spec = campus_spec()
    spec["campus"]["cables"]["CB1"]["max_i_ka"] = t(declared_i_ka)
    spec["campus"]["buses"]["MV1"]["ik_rated_ka"] = t(20.0)
    run = tmp_path / "run"
    run.mkdir(parents=True)
    (run / CAMPUS_YAML).write_text(yaml.safe_dump(spec))
    _sc(
        ("PCC", 110.0, 15.0, 37.0, math.nan, 2030),
        ("MV1", 20.0, 18.0, 45.0, 20.0, 2030),
        ("MV2", 20.0, 9.0, 20.0, math.nan, 2030),
        ("MV1", 20.0, 21.0, 52.0, 20.0, 2040),
        ("MV2", 20.0, 10.0, 22.0, math.nan, 2040),
    ).drop(columns="rated_ka").assign(
        rated_ka=[math.nan, 20.0, math.nan, 20.0, math.nan]
    ).to_csv(run / SHORT_CIRCUIT_CSV, index=False)
    pd.DataFrame(
        [
            (2030, 7, "intact", "CB1", 0.20),
            (2030, 7, "N-1:TR1", "CB1", 0.31),
            (2040, 19, "intact", "CB1", 0.25),
            (2040, 19, "N-1:TR2", "CB1", float("nan")),
        ],
        columns=["period", "hour", "case", "cable", "i_ka"],
    ).to_csv(run / LF_LINE_CSV, index=False)
    return run


def test_rate_campus_names_the_n_minus_1_case_and_writes_its_tables(tmp_path):
    from gridspine.drivers.campus_study import (
        CABLE_RATING_CSV,
        RATING_ASSUMPTIONS_JSON,
        SWITCHGEAR_RATING_CSV,
        rate_campus,
    )
    from gridspine.static.campus_flow import SizingCriteria

    run = _run(tmp_path)
    out = rate_campus(run, criteria=SizingCriteria(margin=0.2))
    sg = out["switchgear"].set_index("bus")
    assert list(sg.index) == ["PCC", "MV1", "MV2"]
    assert (
        sg.at["MV1", "governing"] == "Ith 21.36 kA, period 2040"
    )  # Ith 21.36 x 1.2 = 25.6 kA -> 31.5
    assert (sg.at["MV1", "chosen_ka"], sg.at["MV1", "declared_ka"]) == (31.5, 20.0)
    assert sg.at["MV1", "flag"] == "declared_below_chosen"
    cb = out["cables"].iloc[0]
    assert (cb.cable, cb.from_bus, cb.to_bus, cb.vn_kv, cb.length_km) == (
        "CB1",
        "MV1",
        "MV2",
        20.0,
        2.0,
    )
    assert cb.design_i_ka == pytest.approx(0.31)
    assert cb.design_case == "N-1:TR1, period 2030 hour 7"
    assert cb.ith_ka == pytest.approx(
        21.363422451751465
    )  # Ith over 1 s at the larger end, worst period
    assert cb.chosen_id.startswith("CB_20_")
    assert cb.chosen_i_ka * 0.8 >= 0.31 * 1.2
    assert cb.section_mm2 >= 21.363422451751465e3 / 94
    assert cb.flag is None or (
        isinstance(cb.flag, float) and math.isnan(cb.flag)
    )  # 1.2 kA declared covers it
    assert (run / SWITCHGEAR_RATING_CSV).is_file() and (
        run / CABLE_RATING_CSV
    ).is_file()
    names = {
        a["name"]: a
        for a in __import__("json").loads((run / RATING_ASSUMPTIONS_JSON).read_text())
    }
    assert names["margin"]["value"] == 0.2 and names["margin"]["source"] == "declared"
    assert names["derating"]["source"] == "assumed"


def test_rate_campus_flags_a_declared_cable_below_the_chosen_one_and_keeps_it(tmp_path):
    from gridspine.drivers.campus_study import load_run_campus, rate_campus

    run = _run(tmp_path, declared_i_ka=0.1)
    cb = rate_campus(run)["cables"].iloc[0]
    assert cb.flag == "declared_below_chosen"
    assert cb.declared_i_ka == pytest.approx(0.1)
    assert float(load_run_campus(run).net.line.at[0, "max_i_ka"]) == pytest.approx(
        0.1
    )  # not overwritten


def test_rate_campus_without_a_flow_on_a_cable_says_so(tmp_path):
    from gridspine.drivers.campus_study import LF_LINE_CSV, rate_campus

    run = _run(tmp_path)
    pd.DataFrame(columns=["period", "hour", "case", "cable", "i_ka"]).to_csv(
        run / LF_LINE_CSV, index=False
    )
    cb = rate_campus(run)["cables"].iloc[0]
    assert cb.flag == "not_rated" and cb.chosen_id is None
    assert cb.governing == "no load flow reached this cable"


def _rewrite_sc(run, *rows):
    from gridspine.drivers.campus_study import SHORT_CIRCUIT_CSV

    _sc(*rows).to_csv(run / SHORT_CIRCUIT_CSV, index=False)


def test_rate_campus_a_cable_with_only_nan_currents_is_not_rated(tmp_path):
    from gridspine.drivers.campus_study import LF_LINE_CSV, rate_campus

    run = _run(tmp_path)
    pd.DataFrame(
        [(2030, 7, "intact", "CB1", math.nan), (2040, 19, "N-1:TR2", "CB1", math.nan)],
        columns=["period", "hour", "case", "cable", "i_ka"],
    ).to_csv(run / LF_LINE_CSV, index=False)
    cb = rate_campus(run)["cables"].iloc[0]
    assert cb.flag == "not_rated" and cb.chosen_id is None and math.isnan(cb.ith_ka)


def test_rate_campus_the_larger_end_governs_when_it_is_the_to_bus(tmp_path):
    from gridspine.drivers.campus_study import rate_campus
    from gridspine.static.campus_ratings import thermal_current

    run = _run(tmp_path)
    cb = rate_campus(run)["cables"].iloc[0]
    assert (cb.from_bus, cb.to_bus) == ("MV1", "MV2")
    _rewrite_sc(
        run,
        ("MV1", 20.0, 9.0, 20.0, math.nan, 2030),
        ("MV2", 20.0, 21.0, 52.0, math.nan, 2040),
        ("MV2", 20.0, 18.0, 45.0, math.nan, 2030),
        ("PCC", 110.0, 15.0, 37.0, math.nan, 2030),
    )
    cb = rate_campus(run)["cables"].iloc[0]
    assert (
        cb.ith_ka
        == pytest.approx(thermal_current(21.0, 52.0, 50, 1.0))
        == pytest.approx(21.363422451751465)
    )
    assert cb.withstand_evaluated


@pytest.mark.parametrize("mv2", [None, (math.nan, 20.0), (10.0, math.nan)])
def test_rate_campus_an_unknown_end_leaves_the_withstand_not_evaluated(tmp_path, mv2):
    from gridspine.drivers.campus_study import rate_campus

    run = _run(tmp_path)
    rows = [
        ("MV1", 20.0, 21.0, 52.0, math.nan, 2040),
        ("PCC", 110.0, 15.0, 37.0, math.nan, 2030),
    ]
    if mv2 is not None:
        rows.append(("MV2", 20.0, *mv2, math.nan, 2040))
    _rewrite_sc(run, *rows)
    cb = rate_campus(run)["cables"].iloc[0]
    assert math.isnan(cb.ith_ka) and math.isnan(cb.s_min_mm2)
    assert not cb.withstand_evaluated and cb.flag == "withstand_not_evaluated"
    assert (
        cb.chosen_id is not None
    )  # chosen on ampacity and voltage drop, the withstand shown as unknown


def test_rate_campus_a_declared_cable_equal_to_the_chosen_one_is_not_flagged(tmp_path):
    from gridspine.drivers.campus_study import rate_campus

    first = rate_campus(_run(tmp_path / "a"))["cables"].iloc[0]
    assert first.declared_runs == 1
    cb = rate_campus(_run(tmp_path / "b", declared_i_ka=float(first.chosen_i_ka)))[
        "cables"
    ].iloc[0]
    assert cb.declared_i_ka == cb.chosen_i_ka and pd.isna(cb.flag)


def test_a_zero_fault_level_is_rated_without_dividing_by_it():
    from gridspine.static.campus_ratings import thermal_current

    assert thermal_current(0.0, 0.0, 50, 1.0) == 0.0
    r = _one([("B1", 33.0, 0.0, 0.0, math.nan, 1)])
    assert r.chosen_ka == 25.0 and r.ith_ka == 0.0


def test_size_then_rate_a_cabled_campus_reads_what_sizing_wrote(tmp_path):
    """
    Writer -> reader: ``size_campus`` writes campus_lf_line.csv,
    ``rate_campus`` reads it and the short-circuit table.
    """
    from gridspine.drivers.campus_study import (
        CABLE_DESIGN_BASIS,
        LF_LINE_CSV,
        draft_from_project,
        prepare_campus,
        rank_campus,
        rate_campus,
        selected_hours,
        size_campus,
    )
    from tests.gridspine.test_campus_hourly import solved_hub

    nc = tmp_path / "n.nc"
    solved_hub(periods=[2030, 2040]).export_to_netcdf(str(nc))
    spec = draft_from_project(nc).spec
    c = spec["campus"]
    c["buses"]["MV2"] = {"vn_kv": 20.0}
    c["cables"] = {
        "CB1": {
            "from_bus": "MV",
            "to_bus": "MV2",
            "length_km": _v(1.5),
            "r_ohm_per_km": _v(0.125),
            "x_ohm_per_km": _v(0.11),
            "c_nf_per_km": _v(300.0),
            "max_i_ka": _v(0.4),
            "parallel": 3,
        }
    }
    c["units"]["DC_LOAD"]["bus"] = "MV2"
    run = tmp_path / "run"
    prepare_campus(run, spec, nc)
    rank_campus(run, k=1)
    size_campus(run)
    lines = pd.read_csv(run / LF_LINE_CSV)
    assert list(lines.columns) == ["period", "hour", "case", "cable", "i_ka"]
    intact = lines[lines["case"] == "intact"]
    assert sorted(map(tuple, intact[["period", "hour"]].values.tolist())) == sorted(
        map(tuple, selected_hours(run)[["period", "hour"]].values.tolist())
    )
    assert (lines["cable"] == "CB1").all() and (lines["i_ka"] > 0).all()
    cb = rate_campus(run)["cables"].iloc[0]
    top = lines.loc[lines["i_ka"].idxmax()]
    assert cb.design_i_ka == pytest.approx(float(top.i_ka))
    assert (
        cb.design_case == f"{top.case}, period {int(top.period)} hour {int(top.hour)}"
    )
    assert cb.design_basis == CABLE_DESIGN_BASIS
    assert (cb.declared_runs, cb.declared_i_ka) == (3, pytest.approx(1.2))
    # about 1.2 kA: more than three runs of the shipped library's largest 20 kV cable carry
    assert cb.chosen_id is None and cb.flag == "no_library_cable"


def test_the_margin_applies_to_the_peak_too():
    # Ik'' 19, ip 50 (kappa 1.861): Ith 19.62 x 1.25 = 24.53 <= 25, and ip 50 x 1.25 = 62.5 = 2.5 x 25 exactly
    r = _one([("B1", 33.0, 19.0, 50.0, math.nan, 1)], margin=0.25)
    assert r.chosen_ka == 25.0 and r.governing == "ip 50.00 kA, period 1"
    above = _one([("B1", 33.0, 19.0, 50.0001, math.nan, 1)], margin=0.25)
    assert above.chosen_ka == 31.5


def test_the_ith_margin_boundary_is_exact():
    # m = 0 and Tk 4 s on Tkr 1 s: Ith@Tkr = 2 Ik'' exactly; 2 x 10 x 1.25 = 25
    a = SwitchgearAssumptions(tk_s=4.0)
    r = _one([("B1", 33.0, 10.0, 10.0 * NO_DC, math.nan, 1)], margin=0.25, a=a)
    assert r.chosen_ka == 25.0 and r.ith_tkr_ka == 20.0
    assert r.governing == "Ith over 4 s referred to 1 s 20.00 kA, period 1"
    assert (
        _one(
            [("B1", 33.0, 10.0001, 10.0001 * NO_DC, math.nan, 1)], margin=0.25, a=a
        ).chosen_ka
        == 31.5
    )


def test_a_fault_longer_than_the_rated_duration_needs_more():
    # IEC 62271-1: Ith sqrt(Tk) <= Ithr sqrt(Tkr). Ik'' 15 with no DC over 3 s on 1 s gear: 15 sqrt(3) = 25.98 kA
    longer = _one(
        [("B1", 33.0, 15.0, 15.0 * NO_DC, math.nan, 1)],
        margin=0.0,
        a=SwitchgearAssumptions(tk_s=3.0),
    )
    assert longer.ith_ka == 15.0 and longer.ith_tkr_ka == pytest.approx(
        25.980762113533157
    )
    assert longer.chosen_ka == 31.5
    rated = _one(
        [("B1", 33.0, 15.0, 15.0 * NO_DC, math.nan, 1)],
        margin=0.0,
        a=SwitchgearAssumptions(tk_s=3.0, tkr_s=3.0),
    )
    assert rated.chosen_ka == 25.0


def test_a_three_second_fault_on_one_second_gear_is_beyond_the_ladder():
    # the probe: Ith(3 s) 24.37 kA, I2t 1782 kA2s; referred to 1 s that is 42.21 kA > 40
    r = _one(
        [("B1", 33.0, 24.0, 65.0, math.nan, 1)],
        f_hz=60,
        margin=0.0,
        a=SwitchgearAssumptions(tk_s=3.0),
    )
    assert r.chosen_id is None and r.flag == "beyond_ladder"
    assert r.governing == (
        "Ith over 3 s referred to 1 s 42.21 kA with the 0% margin, above the largest rated short-time current 40 kA at 33 kV"
    )


def test_a_shorter_fault_is_not_credited_below_the_rated_current():
    r = _one(
        [("B1", 33.0, 24.0, 24.0 * NO_DC, math.nan, 1)],
        margin=0.0,
        a=SwitchgearAssumptions(tk_s=0.2),
    )
    assert r.ith_tkr_ka == r.ith_ka == 24.0 and r.chosen_ka == 25.0


NAN_FIRST = [
    ("B1", 33.0, math.nan, 10.0, math.nan, 1),
    ("B1", 33.0, 35.0, 49.0, math.nan, 2),
]


@pytest.mark.parametrize("rows", [NAN_FIRST, NAN_FIRST[::-1]])
def test_a_nan_period_is_skipped_in_either_row_order(rows):
    r = _one(rows, margin=0.1)  # 35 x 1.1 = 38.5 -> 40
    assert (
        r.chosen_ka == 40.0
        and r.ikss_ka == 35.0
        and r.governing == "Ik'' 35.00 kA, period 2"
    )
    assert pd.isna(r.flag)


def test_a_bus_with_no_finite_fault_level_is_not_evaluated():
    r = _one(
        [("B1", 33.0, math.nan, 10.0, 25.0, 1), ("B1", 33.0, 12.0, math.nan, 25.0, 2)]
    )
    assert r.chosen_id is None and math.isnan(r.chosen_ka) and math.isnan(r.utilisation)
    assert (
        r.flag == "not_evaluated"
        and r.governing == "no fault level was computed at this bus"
    )


@pytest.mark.parametrize(
    "kw",
    [
        dict(mu=math.nan),
        dict(n=math.nan),
        dict(tk_s=0.0),
        dict(tk_s=-1.0),
        dict(tk_s=math.inf),
        dict(tkr_s=0.0),
        dict(tkr_s=math.nan),
    ],
)
def test_switchgear_assumptions_are_validated(kw):
    with pytest.raises(ValueError, match=next(iter(kw))):
        SwitchgearAssumptions(**kw)


@pytest.mark.parametrize(
    "kw",
    [
        dict(derating=0.0),
        dict(derating=math.nan),
        dict(vd_limit_pct=0.0),
        dict(pf=1.5),
        dict(pf=math.nan),
        dict(t_clear_s=-0.1),
        dict(t_clear_s=math.inf),
        dict(conductor="Fe"),
    ],
)
def test_cable_assumptions_are_validated(kw):
    with pytest.raises(ValueError, match=next(iter(kw))):
        CableAssumptions(**kw)


@pytest.mark.parametrize("margin", [math.nan, -0.5, math.inf])
def test_a_bad_margin_is_refused(margin):
    with pytest.raises(ValueError, match="margin"):
        choose_switchgear(_sc(("B1", 33.0, 20.0, 28.0, math.nan, 1)), LIB, 50, margin)
    with pytest.raises(ValueError, match="margin"):
        choose_cable(
            LIB, vn_kv=33.0, length_km=1.0, i_ka=0.1, ith_ka=1.0, margin=margin
        )


def test_a_zero_margin_is_allowed():
    assert (
        _one([("B1", 33.0, 25.0, 25.0 * NO_DC, math.nan, 1)], margin=0.0).chosen_ka
        == 25.0
    )


def test_the_row_carries_the_voltage_and_duration():
    r = _one([("B1", 33.0, 20.0, 28.0, math.nan, 1)], a=SwitchgearAssumptions(tk_s=3.0))
    assert (r.vn_kv, r.tk_s) == (33.0, 3.0)


def test_a_cable_exactly_at_its_ampacity_passes_in_one_run():
    c = choose_cable(
        {"cables": [BOUNDARY]},
        vn_kv=10.0,
        length_km=1.0,
        i_ka=0.2,
        ith_ka=1.0,
        margin=0.0,
        a=UNITY,
    )
    assert (c["chosen_id"], c["runs"]) == ("B", 1)
    assert c["utilisation"] == pytest.approx(1.0)


# --------------------------------------------------------------------------
# mutant kills (scripts/coord/mutants.py survivors on the GK A fix)
# --------------------------------------------------------------------------


def test_a_boolean_assumption_is_refused():
    with pytest.raises(ValueError, match="tk_s"):
        SwitchgearAssumptions(tk_s=True)
    with pytest.raises(ValueError, match="pf"):
        CableAssumptions(pf=True)


def test_a_fault_level_that_is_not_a_number_reads_not_evaluated():
    r = _one([("B1", 33.0, "n/a", 10.0, math.nan, 1)])
    assert r.flag == "not_evaluated" and r.chosen_id is None


def test_a_tie_between_periods_names_the_earliest():
    rows = [
        ("B1", 33.0, 20.0, 20.0 * NO_DC, math.nan, 2),
        ("B1", 33.0, 20.0, 20.0 * NO_DC, math.nan, 1),
    ]
    assert _one(rows).governing == "Ik'' 20.00 kA, period 1"


def test_a_cable_tie_keeps_the_first_in_library_order():
    twin = {**LIB["cables"][0], "id": "A95_TWIN"}
    lib = {"cables": [LIB["cables"][0], twin]}
    c = choose_cable(lib, vn_kv=33.0, length_km=1.0, i_ka=0.1, ith_ka=1.0, margin=0.0)
    assert c["chosen_id"] == "A95"


SWITCHGEAR_COLUMNS = [
    "bus",
    "vn_kv",
    "ikss_ka",
    "ib_ka",
    "ith_ka",
    "ith_tkr_ka",
    "ip_ka",
    "tk_s",
    "tkr_s",
    "declared_ka",
    "chosen_id",
    "chosen_ka",
    "chosen_ip_ka",
    "capex_eur",
    "utilisation",
    "governing",
    "flag",
]


@pytest.mark.parametrize(
    "row",
    [
        ("B1", 33.0, 20.0, 28.0, math.nan, 1),  # chosen
        ("B1", 33.0, math.nan, 28.0, math.nan, 1),  # not evaluated
        ("B1", 33.0, 45.0, 60.0, math.nan, 1),  # beyond the ladder
    ],
)
def test_every_switchgear_row_has_every_column(row):
    out = choose_switchgear(_sc(row), LIB, 50, 0.1)
    assert sorted(out.columns) == sorted(SWITCHGEAR_COLUMNS)
