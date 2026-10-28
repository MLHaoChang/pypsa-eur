"""The P–V/Q–V margin at a campus run's PCC (study harness issue 18): the
grid-side screening net on S_k''min, the planned load per period, the probe's
power factor and source EMF, and the analytic nose on R = 0. Generic demo
figures only."""
import math

import pandas as pd
import pytest

from gridspine.drivers.campus_study import (
    LF_BUS_CSV,
    LF_PCC_CSV,
    NO_SK_MIN,
    VOLTAGE_STABILITY_CSV,
    VS_NO_OPERATING_POINT,
    _vs_net,
    rank_campus,
    size_campus,
    voltage_stability_campus,
)
from gridspine.schema.contracts import ContractError
from gridspine.static.island_sc_seq import c_factor
from gridspine.static.voltage_stability import (
    SCREENING_BASIS,
    VoltageStabilitySettings,
    voltage_stability,
)
from tests.gridspine.test_harmonics_campus import _hub, _run

VN = 110.0
SK = 600.0           # a weak demo grid: the nose sits within reach of the probe's steps


def _pcc(**over):
    def edit(spec):
        pcc = spec["campus"]["pcc"]
        pcc["sk_min_mva"] = {"value": SK, "source": "assumed"}
        for k, v in over.items():
            pcc[k] = {"value": v, "source": "assumed"}
    return edit


def _lf(run, hours, period=None):
    """Write the sized run's PCC and bus tables by hand: ``hours`` is
    ``[(hour, converged, p_mw, q_mvar, vm_pu)]`` for every period."""
    periods = sorted(pd.read_csv(run / "campus_pcc.csv")["period"].unique())
    pcc, bus = [], []
    for period in periods:
        for hour, ok, p, q, vm in hours:
            pcc.append({"period": period, "hour": hour, "case": "intact", "converged": ok, "p_mw": p,
                        "q_mvar": q, "losses_mw": 0.0})
            # A campus-internal outage case is ignored: it does not move the grid-side nose.
            pcc.append({"period": period, "hour": hour, "case": "N-1:T1", "converged": ok, "p_mw": 10 * p,
                        "q_mvar": q, "losses_mw": 0.0})
            bus.append({"period": period, "hour": hour, "case": "intact", "bus": "GRID", "vm_pu": vm})
            bus.append({"period": period, "hour": hour, "case": "intact", "bus": "MV", "vm_pu": 0.9})
    pd.DataFrame(pcc).to_csv(run / LF_PCC_CSV, index=False)
    pd.DataFrame(bus).to_csv(run / LF_BUS_CSV, index=False)
    return periods


def _prepared(tmp_path, hours, **over):
    run, _ = _run(tmp_path, _hub(), _pcc(**over))
    _lf(run, hours)
    return run


def _analytic_nose(e_kv, x_ohm, pf):
    phi = math.acos(pf)
    return e_kv**2 * math.cos(phi) / (2 * x_ohm * (1 + math.sin(phi)))


def _one(run):
    rows = pd.read_csv(run / VOLTAGE_STABILITY_CSV)
    return rows.iloc[0], rows


# ── the equivalent and the nose ─────────────────────────────────────────────

def test_on_r0_the_nose_matches_the_analytic_one_within_one_percent(tmp_path):
    run = _prepared(tmp_path, [(0, True, 40.0, 10.0, 1.0)], rx_min=0.0)
    summary = voltage_stability_campus(run)
    r, rows = _one(run)
    assert len(rows) == rows["period"].nunique() and r["verdict"] == "solved"
    c = c_factor(VN, "min")
    assert c == 1.0 and summary["c"] == c                # c_min above 1 kV, the standing rule
    pf = 40.0 / math.hypot(40.0, 10.0)
    assert r["pf"] == pytest.approx(pf)
    assert r["nose_mw"] == pytest.approx(_analytic_nose(VN, c * VN**2 / SK, pf), rel=0.01)
    assert not r["nose_is_lower_bound"] and r["basis"] == SCREENING_BASIS


def test_with_rx_the_nose_is_finite_and_below_the_r0_one(tmp_path):
    run = _prepared(tmp_path, [(0, True, 40.0, 10.0, 1.0)], rx_min=0.12)
    voltage_stability_campus(run)
    r, _ = _one(run)
    r0 = _analytic_nose(VN, VN**2 / SK, 40.0 / math.hypot(40.0, 10.0))
    assert math.isfinite(r["nose_mw"]) and r["nose_mw"] < r0
    assert not r["nose_is_lower_bound"]


def test_c_min_and_the_impedance_are_in_the_ledger_and_notes(tmp_path):
    run = _prepared(tmp_path, [(0, True, 40.0, 10.0, 1.0)])
    summary = voltage_stability_campus(run)
    assert any("c_min" in line for line in summary["ledger"])
    assert any("source EMF" in line for line in summary["ledger"])
    assert summary["notes"][0] == (f"S_k''min {SK:g} MVA, R/X 0.1, c_min 1 at 110 kV: "
                                   f"Z = c·Un²/S_k''min = {VN**2 / SK:.4g} Ω")
    # Each period's operating point is named: the planned load and what bound it, the power factor and E.
    periods = pd.read_csv(run / VOLTAGE_STABILITY_CSV)["period"]
    assert [n.split(":")[0] for n in summary["notes"][1:]] == [f"period {p}" for p in periods]
    assert all("binds" in n and "power factor" in n and "E = " in n for n in summary["notes"][1:])


# ── the operating point per period ──────────────────────────────────────────

def test_the_emf_is_the_lowest_converged_pcc_voltage_when_below_one(tmp_path):
    run = _prepared(tmp_path, [(0, True, 40.0, 10.0, 0.99), (1, True, 20.0, 5.0, 0.97),
                               (2, False, 30.0, 5.0, 0.90)], rx_min=0.0)
    voltage_stability_campus(run)
    r, _ = _one(run)
    assert r["e_pu"] == pytest.approx(0.97)              # the unconverged hour's 0.90 is not read
    assert "E = 0.97 pu, the lowest converged PCC voltage" in r["note"]
    pf = 40.0 / math.hypot(40.0, 10.0)
    assert r["nose_mw"] == pytest.approx(_analytic_nose(0.97 * VN, VN**2 / SK, pf), rel=0.01)


def test_a_pcc_above_one_pu_keeps_the_emf_at_one(tmp_path):
    run = _prepared(tmp_path, [(0, True, 40.0, 10.0, 1.02)])
    voltage_stability_campus(run)
    r, _ = _one(run)
    assert r["e_pu"] == 1.0 and "E = 1.0 pu" in r["note"]


def test_the_planned_load_is_the_peak_converged_import_and_says_so(tmp_path):
    run = _prepared(tmp_path, [(0, True, 40.0, 10.0, 1.0), (1, False, 90.0, 10.0, 1.0)])
    voltage_stability_campus(run)
    r, _ = _one(run)
    assert r["planned_mw"] == 40.0 and r["hour"] == 0    # an unconverged hour never sets it
    assert "the largest converged PCC import (hour 0) binds" in r["note"]


def test_a_larger_rated_import_binds_with_the_max_import_hours_power_factor(tmp_path):
    run = _prepared(tmp_path, [(0, True, 40.0, 10.0, 1.0)], p_connection_mw=60.0)
    voltage_stability_campus(run)
    r, _ = _one(run)
    assert r["planned_mw"] == 60.0 and "the rated import (the connection capacity) binds" in r["note"]
    assert r["pf"] == pytest.approx(40.0 / math.hypot(40.0, 10.0))


def test_a_smaller_rated_import_leaves_the_peak_binding(tmp_path):
    run = _prepared(tmp_path, [(0, True, 40.0, 10.0, 1.0)], p_connection_mw=30.0)
    voltage_stability_campus(run)
    r, _ = _one(run)
    assert r["planned_mw"] == 40.0 and "largest converged PCC import" in r["note"]


@pytest.mark.parametrize("q", [0.0, -5.0])
def test_a_leading_or_zero_q_hour_falls_back_to_the_default_power_factor(tmp_path, q):
    run = _prepared(tmp_path, [(0, True, 40.0, q, 1.0)])
    voltage_stability_campus(run)
    r, _ = _one(run)
    assert r["pf"] == VoltageStabilitySettings().pf
    assert "draws no lagging reactive power" in r["note"] and "the default" in r["note"]


def test_no_converged_hour_and_no_rated_import_has_no_operating_point(tmp_path):
    run = _prepared(tmp_path, [(0, False, 40.0, 10.0, 1.0)])
    voltage_stability_campus(run)
    r, _ = _one(run)
    assert r["verdict"] == VS_NO_OPERATING_POINT
    assert r["note"] == "no converged hour and no rated import (the connection capacity)"
    assert math.isnan(r["nose_mw"])


def test_no_converged_hour_with_a_rated_import_probes_the_rated_import(tmp_path):
    run = _prepared(tmp_path, [(0, False, 40.0, 10.0, 1.0)], p_connection_mw=50.0)
    voltage_stability_campus(run)
    r, _ = _one(run)
    assert r["verdict"] == "solved" and r["planned_mw"] == 50.0
    assert r["pf"] == VoltageStabilitySettings().pf and "no converged hour" in r["note"]


# ── refusals ────────────────────────────────────────────────────────────────

def test_the_drafts_typical_fault_level_is_no_sk_min_entered(tmp_path):
    n = _hub()
    n.buses = n.buses.drop(columns="eh_sk_mva")
    run, _ = _run(tmp_path, n)                            # the draft falls back to the typical level
    _lf(run, [(0, True, 40.0, 10.0, 1.0)])
    with pytest.raises(ContractError, match=NO_SK_MIN):
        voltage_stability_campus(run)
    assert not (run / VOLTAGE_STABILITY_CSV).exists()


def test_a_normally_open_tie_has_no_grid_to_collapse_against(tmp_path):
    run = _prepared(tmp_path, [(0, True, 40.0, 10.0, 1.0)], p_connection_mw=50.0)
    import yaml
    spec = yaml.safe_load((run / "campus.yaml").read_text())
    spec["campus"]["pcc"]["tie_normally_open"] = True
    (run / "campus.yaml").write_text(yaml.safe_dump(spec))
    with pytest.raises(ContractError, match="normally open"):
        voltage_stability_campus(run)


# ── the Q–V sign on the equivalent (GK A, plan review) ──────────────────────

def test_the_qv_margin_is_positive_for_a_light_probe_and_falls_as_the_load_rises():
    net = _vs_net(VN, 1.0, SK, 0.0, 1.0, 60.0)
    x = VN**2 / SK
    margins = []
    for planned in (0.5, 50.0, 150.0):
        rows, _ = voltage_stability(net, "PCC", planned, None, VoltageStabilitySettings(pf=0.95, max_cases=0))
        margins.append(float(rows.iloc[0]["q_margin_mvar"]))
    assert margins[0] > 0
    assert margins[0] > margins[1] > margins[2]
    # At P -> 0 the Q–V minimum is -E²/(4X): the margin is E²/(4X).
    assert margins[0] == pytest.approx(VN**2 / (4 * x), rel=0.01)


# ── a real sized run ────────────────────────────────────────────────────────

def test_a_sized_run_writes_one_row_per_period(tmp_path):
    run, _ = _run(tmp_path, _hub(), _pcc())
    rank_campus(run)
    size_campus(run)
    summary = voltage_stability_campus(run)
    rows = pd.read_csv(run / VOLTAGE_STABILITY_CSV)
    assert list(rows["period"]) == sorted(pd.read_csv(run / "campus_pcc.csv")["period"].unique())
    assert set(rows["verdict"]) == {"solved"}
    assert (rows["nose_mw"] > rows["planned_mw"]).all() and (rows["q_margin_mvar"] > 0).all()
    assert summary["basis"] == SCREENING_BASIS


# ── the profile keys (optional, numeric; no profile ships values: D3-KEEP) ──

def _raw():
    from gridspine.templates.grid_codes import load_grid_code
    return load_grid_code("generic_assumed", raw=True)


def test_the_margin_keys_are_optional_and_no_shipped_profile_sets_them():
    from gridspine.templates.grid_codes import (
        PROFILE_KEYS,
        list_grid_codes,
        load_grid_code,
    )

    assert {"pv_margin_min_pct", "qv_margin_min_mvar"} <= PROFILE_KEYS
    for name in list_grid_codes():
        p = load_grid_code(name)
        assert "pv_margin_min_pct" not in p and "qv_margin_min_mvar" not in p, name


def test_the_margin_keys_validate_like_the_other_single_limits():
    from gridspine.templates.grid_codes import validate_profile

    p = _raw()
    p["pv_margin_min_pct"] = {"value": 5.0, "clause": "demo", "source": "assumed"}
    p["qv_margin_min_mvar"] = {"value": 0.0, "clause": "demo", "source": "assumed"}   # zero is a valid margin
    out = validate_profile("p", p)
    assert out["pv_margin_min_pct"]["value"] == 5.0 and out["qv_margin_min_mvar"]["value"] == 0.0


@pytest.mark.parametrize("key, value, match", [
    ("pv_margin_min_pct", 0.0, "pv_margin_min_pct value must be a positive number"),
    ("pv_margin_min_pct", -1.0, "pv_margin_min_pct value must be a positive number"),
    ("pv_margin_min_pct", "5", "pv_margin_min_pct value must be a positive number"),
    ("qv_margin_min_mvar", -0.1, "qv_margin_min_mvar value must be a number >= 0"),
    ("qv_margin_min_mvar", True, "qv_margin_min_mvar value must be a number >= 0"),
])
def test_a_margin_key_out_of_range_is_refused(key, value, match):
    from gridspine.templates.grid_codes import validate_profile

    p = _raw()
    p[key] = {"value": value, "clause": "demo", "source": "assumed"}
    with pytest.raises(ContractError, match=match):
        validate_profile("p", p)


@pytest.mark.parametrize("key", ["pv_margin_min_pct", "qv_margin_min_mvar"])
def test_a_margin_key_without_a_clause_is_refused(key):
    from gridspine.templates.grid_codes import validate_profile

    p = _raw()
    p[key] = {"value": 5.0, "source": "assumed"}
    with pytest.raises(ContractError, match=f"{key} has no clause"):
        validate_profile("p", p)


def test_an_unknown_key_still_warns(caplog):
    from gridspine.templates.grid_codes import validate_profile

    p = _raw()
    p["pv_margin_pct"] = {"value": 5.0, "clause": "demo", "source": "assumed"}
    with caplog.at_level("WARNING"):
        validate_profile("p", p)
    assert "pv_margin_pct" in caplog.text
