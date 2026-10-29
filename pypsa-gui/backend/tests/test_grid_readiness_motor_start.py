"""The motor-start rows of the grid-readiness scorecard (grid-to-process issue 18): one row per process unit, the
voltage change its motor's start causes at the PoC (``gridspine.static.motor_start``), judged on the profile's
rapid-voltage-change limit. Hand values in each test."""
from __future__ import annotations

import json
import math

import pypsa
import pytest

from services.adequacy import grid_readiness as G
from tests.test_grid_readiness import PROFILE, _project, _rows

# 2000 kW at 6 × rated, eta · pf_rated 0.85, start pf 0.30: Q_start = 6 · 2.352941 · 0.953939 = 13.467372 Mvar.
Q_DOL = 13.467372


def _site(kw=2000.0, method="dol", sk=150.0):
    """The fixture: one PoC at 150 MVA (110 kV) feeding one process unit, a mill motor."""
    n = pypsa.Network()
    n.add("Bus", "grid", v_nom=110.0)
    n.add("Bus", "mv", v_nom=20.0)
    n.add("Bus", "ore", carrier="ore")
    n.add("Link", "grid_import", bus0="grid", bus1="mv", p_nom=60.0)
    n.add("Link", "mill", bus0="mv", bus1="ore", p_nom=5.0)
    n.buses["eh_poc"] = [True, False, False]
    n.buses["eh_sk_mva"] = [sk, math.nan, math.nan]
    n.links["eh_process"] = [False, True]
    n.links["motor_kw"] = [math.nan, kw]
    n.links["start_method"] = ["", method]
    return n


def _row(d, n, unit="mill"):
    return _rows(G.grid_readiness(d, n, PROFILE))[f"motor_start:{unit}"]


def test_a_dol_start_warns_and_a_vfd_start_passes_by_hand(tmp_path):
    dol = _row(tmp_path, _site(method="dol"))
    # 13.467372 / (150 / 1.1) · 100 = 9.876076 % > the demo 3 %.
    assert dol["project_value"] == pytest.approx(9.8761, abs=1e-4) and dol["limit"] == 3.0
    assert dol["verdict"] == "warn" and dol["unit"] == "% of nominal" and dol["section"] == "assets"
    assert dol["motor_start"]["sk_case"] == "entered" and dol["motor_start"]["c"] == 1.1
    assert "planning level, not an emission limit allocated to this installation" in dol["source"]
    assert "Q_gfm 0 (conservative)" in dol["note"] and "entered S_k'' 150 MVA / c_max 1.1" in dol["note"]
    assert dol["target"] == {**dol["target"], "component": "Link", "entity": "mill", "field": "start_method"}
    assert dol["rule"]["comparator"] == "<=" and dol["assumed"] is True
    # 2.352941 · 0.312250 / 136.3636 · 100 = 0.538784 %.
    vfd = _row(tmp_path, _site(method="vfd"))
    assert vfd["project_value"] == pytest.approx(0.5388, abs=1e-4) and vfd["verdict"] == "pass"


def test_the_campus_minimum_case_is_used_when_the_poc_has_one(tmp_path):
    (tmp_path / "campus_electrical").mkdir()
    (tmp_path / "campus_electrical" / "campus_input.yaml").write_text(json.dumps(
        {"campus": {"pcc": {"bus": "PCC", "pypsa_name": "grid", "vn_kv": 110.0, "sk_min_mva": 120.0,
                            "sk_max_mva": 150.0}}}))
    row = _row(tmp_path, _site())
    # 13.467372 / (120 / 1.0) · 100 = 11.222810 %.
    assert row["motor_start"]["sk_case"] == "min" and row["motor_start"]["c"] == 1.0
    assert row["project_value"] == pytest.approx(11.2228, abs=1e-4)
    assert "minimum case S_k''min 120 MVA" in row["note"] and "entered" not in row["note"]


def test_a_campus_minimum_copied_from_the_bus_is_not_a_minimum_case(tmp_path):
    (tmp_path / "campus_electrical").mkdir()
    (tmp_path / "campus_electrical" / "campus_input.yaml").write_text(json.dumps(
        {"campus": {"pcc": {"bus": "PCC", "pypsa_name": "grid", "vn_kv": 110.0, "sk_min_mva": 150.0}}}))
    assert _row(tmp_path, _site())["motor_start"]["sk_case"] == "entered"


@pytest.mark.parametrize("kw, method, field, note", [
    (math.nan, "", "motor_kw", "no input: Motor rating and Start method not set"),
    (2000.0, "", "start_method", "no input: Start method not set"),
    (math.nan, "vfd", "motor_kw", "no input: Motor rating not set")])
def test_a_unit_with_no_motor_fields_is_unknown_not_pass(tmp_path, kw, method, field, note):
    row = _row(tmp_path, _site(kw=kw, method=method))
    assert row["verdict"] == "unknown" and row["project_value"] is None and row["note"] == note
    assert row["target"]["entity"] == "mill" and row["target"]["field"] == field


def test_a_unit_without_the_motor_columns_is_unknown(tmp_path):
    n = _site()
    n.links = n.links.drop(columns=["motor_kw", "start_method"])
    assert _row(tmp_path, n)["verdict"] == "unknown"


def test_no_fault_level_is_unknown(tmp_path):
    row = _row(tmp_path, _site(sk=math.nan))
    assert row["verdict"] == "unknown" and row["note"] == "No short-circuit level at the point of connection"
    assert row["target"]["field"] == "eh_sk_mva"


def test_a_malformed_stored_start_method_reads_unknown_on_its_row(tmp_path):
    row = _row(tmp_path, _site(method="star-delta"))
    assert row["verdict"] == "unknown" and "star-delta" in row["note"]


def test_a_start_current_multiple_of_zero_reads_unknown_never_pass(tmp_path):
    n = _site(method="dol")
    n.links["i_start_pu"] = [math.nan, 0.0]
    row = _row(tmp_path, n)
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert "Start current multiple must be above 0" in row["note"]


def test_the_gui_and_gridspine_start_methods_are_equal():
    from gridspine.static.motor_start import START_METHODS
    from models.process_unit import START_METHODS as GUI_START_METHODS

    assert GUI_START_METHODS == START_METHODS


def test_one_row_per_process_unit(tmp_path):
    n = _site()
    n.add("Link", "kiln", bus0="mv", bus1="ore", p_nom=1.0)
    n.links.loc["kiln", ["eh_process", "motor_kw", "start_method"]] = [True, 500.0, "soft"]
    ids = [r["id"] for r in G.grid_readiness(tmp_path, n, PROFILE)["rows"] if r["id"].startswith("motor_start")]
    assert ids == ["motor_start:mill", "motor_start:kiln"]


def test_no_process_unit_is_one_unknown_row(tmp_path):
    d, n = _project(tmp_path, "eh_datacenter")
    rows = [r for r in G.grid_readiness(d, n, PROFILE)["rows"] if r["id"].startswith("motor_start")]
    assert [r["id"] for r in rows] == ["motor_start"] and rows[0]["verdict"] == "unknown"
    assert rows[0]["note"].startswith("No process unit on this project")


def test_the_grid_forming_battery_supplies_q_gfm(tmp_path):
    d, n = _project(tmp_path, "eh_datacenter")
    sk = G._poc_sk_mva(n)
    n.add("Bus", "ore", carrier="ore")
    bus0 = n.links.at[n.links.index[0], "bus1"]
    n.add("Link", "mill", bus0=bus0, bus1="ore", p_nom=5.0)
    n.links["eh_process"] = [name == "mill" for name in n.links.index]
    n.links["motor_kw"] = [2000.0 if name == "mill" else math.nan for name in n.links.index]
    n.links["start_method"] = ["dol" if name == "mill" else "" for name in n.links.index]
    # bess_new (gfm_droop) is extendable and unsized: it cannot be stated, so it counts zero and says so.
    row = _row(d, n)
    assert row["motor_start"]["q_gfm_mvar"] == 0.0 and "bess_new counted zero" in row["note"]
    assert row["project_value"] == pytest.approx(Q_DOL / (sk / 1.1) * 100, abs=1e-4)
    n.storage_units.at["bess_new", "p_nom_extendable"] = False
    n.storage_units.at["bess_new", "p_nom"] = 10.0
    row = _row(d, n)
    q = 10.0 * math.tan(math.acos(0.95))                  # 3.286841 Mvar at the battery's rated pf
    assert row["motor_start"]["q_gfm_mvar"] == pytest.approx(q, rel=1e-9)
    assert row["project_value"] == pytest.approx((Q_DOL - q) / (sk / 1.1) * 100, abs=1e-4)
    assert f"Q_gfm {q:.4g} Mvar (bess_new" in row["note"] and "conservative" not in row["note"]


def test_without_a_limit_the_row_is_unknown(tmp_path):
    from tests.test_grid_readiness import _profile_without

    name, dirs = _profile_without(tmp_path, "rvc_limit_pct")
    rows = _rows(G.grid_readiness(tmp_path, _site(), name, profile_dirs=dirs))
    row = rows["motor_start:mill"]
    assert row["verdict"] == "unknown" and row["limit"] is None


def test_a_computed_motor_start_row_is_checked_by_its_declared_rule(tmp_path):
    from harness import grid_readiness_calculations as R

    assert R.disposition(_row(tmp_path, _site(method="dol"))) == ("checked", "warn")
    assert R.disposition(_row(tmp_path, _site(method="vfd"))) == ("checked", "pass")
    assert R.disposition(_row(tmp_path, _site(method="")))[0] == "unknown"
