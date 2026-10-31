# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""AS-04: the GPU envelope's residual step and reactive swing feed the flicker screen."""
from __future__ import annotations

import json
import math

import pytest

from services import dc_load_profile as P
from services.adequacy import flicker_inputs as F
from services.adequacy import grid_readiness as G
from tests.test_grid_readiness import PROFILE, _project, _rows


def test_residuals_after_smoothing_with_a_reactive_swing():
    p_res, q_res = F.residual_swing(30.0, 10.0, 39.9, tan_phi_swing=0.1)
    assert p_res == pytest.approx(0.1)
    assert q_res == pytest.approx(0.1 * 40.0)  # 4.0 Mvar before any STATCOM


def test_residuals_never_go_negative_when_smoothing_exceeds_the_swing():
    assert F.residual_swing(30.0, 10.0, 80.0, tan_phi_swing=0.1) == pytest.approx((0.0, 4.0))
    assert F.residual_swing(30.0, 10.0, 80.0, tan_phi_swing=0.1, q_dyn_mvar=9.0) == pytest.approx((0.0, 0.0))


def test_voltage_form_on_a_250_mva_grid_with_xr_4():
    psi = math.atan(4.0)
    v = F.voltage_form_mva(0.1, 4.0, xr=4.0)
    assert v == pytest.approx(math.cos(psi) * 0.1 + math.sin(psi) * 4.0)
    assert v == pytest.approx(3.9049, abs=1e-3)
    assert v / 250.0 * 100.0 == pytest.approx(1.562, abs=1e-3)  # % of S_sc


def test_without_a_reactive_swing_the_result_is_the_p_only_form():
    p_res, q_res = F.residual_swing(30.0, 10.0, 39.9, tan_phi_swing=0.0)
    assert q_res == 0.0
    assert F.voltage_form_mva(p_res, q_res, xr=4.0) == pytest.approx(math.cos(math.atan(4.0)) * p_res)


def test_the_envelope_carries_an_assumed_reactive_swing(tmp_path):
    d, _ = _project(tmp_path, "eh_datacenter", dc_load={})
    env = json.loads((d / "eh_template.json").read_text())["dc_load"]["envelope"]
    assert env["phases"][0]["tan_phi_swing"] == pytest.approx(0.1)
    assert env["max"]["tan_phi_swing"] == pytest.approx(0.1)
    assert "tan_phi_swing" in P.SOURCES and P.SOURCES["tan_phi_swing"].startswith("assumed")


def test_flicker_row_reports_pst_from_the_envelope(tmp_path):
    d, n = _project(tmp_path, "eh_datacenter", dc_load={})
    row = _rows(G.grid_readiness(d, n, PROFILE))["flicker"]
    assert row["project_value"] is not None and row["verdict"] in ("pass", "warn", "fail")
    assert "GPU envelope" in row["note"] and row["assumed"] is True
    assert row["flicker"]["envelope"]["unit"] == F.ENVELOPE_UNIT


def _mk(tmp_path, tan):
    p = tmp_path / str(tan)
    p.mkdir()
    return p


def test_a_larger_reactive_swing_raises_the_envelope_pst(tmp_path):
    def pst(tan):
        d, n = _project(_mk(tmp_path, tan), "eh_datacenter", dc_load={})
        meta = json.loads((d / "eh_template.json").read_text())
        meta["dc_load"]["envelope"]["max"]["tan_phi_swing"] = tan
        (d / "eh_template.json").write_text(json.dumps(meta))
        return _rows(G.grid_readiness(d, n, PROFILE))["flicker"]["flicker"]["pst"]

    assert pst(0.3) > pst(0.0)


def test_the_envelope_row_pins_step_rate_and_pst_by_hand(tmp_path):
    from gridspine.static import flicker as fl

    d, n = _project(tmp_path, "eh_datacenter", dc_load={})
    mx = json.loads((d / "eh_template.json").read_text())["dc_load"]["envelope"]["max"]
    row = _rows(G.grid_readiness(d, n, PROFILE))["flicker"]["flicker"]
    env = row["envelope"]
    swing = mx["swing_mw"] + mx["oscillation_amplitude_mw"]
    p_res = max(0.0, swing - env["p_sm_mw"])
    q_res = mx["tan_phi_swing"] * swing
    psi = math.atan(F.DEFAULT_XR)
    assert env["step_mva"] == pytest.approx(math.cos(psi) * p_res + math.sin(psi) * q_res)
    # r counts voltage changes: two per cycle, 120 / T, not 60 / T.
    assert env["changes_per_min"] == pytest.approx(120.0 / mx["oscillation_period_s"])
    d_pct = env["step_mva"] / row["sk_mva"] * 100.0
    # Pst of the lumped envelope alone, then the whole row (other units may add).
    assert fl.pst(d_pct, env["changes_per_min"]) > fl.pst(d_pct, 60.0 / mx["oscillation_period_s"])
    assert row["pst"] >= fl.pst(d_pct, env["changes_per_min"]) - 1e-9


def test_a_process_unit_only_row_has_no_envelope_part(tmp_path):
    from tests.test_grid_readiness import _flicker_row_of, _flicker_site

    row = _flicker_row_of(tmp_path, _flicker_site())
    assert "envelope" not in row["flicker"] and row["project_value"] == pytest.approx(0.8963, abs=1e-4)
