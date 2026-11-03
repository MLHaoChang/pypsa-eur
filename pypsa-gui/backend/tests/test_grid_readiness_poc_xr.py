"""
AS-17 (owner decision FILL-10-2; follow-up of AS11B-RVC-1): the project's point-of-connection X/R.

The RVC row, its after-battery value and the flicker row's GPU-envelope unit take the X/R the
project enters (`settings.poc_xr` in metadata.json). With it entered, the X/R no longer marks the
RVC row assumed and the X/R-free bound guard falls away, since that guard holds only while the
X/R is assumed. With no entry, the scorecard is what it was.

Hand figures at 250 MVA, the 3 % limit and the template's tan φ_swing 0.1:
  X/R 4   (assumed): cos ψ = 0.242536, sin ψ = 0.970143 → d = S·0.339550/2.5
  X/R 1.5 (entered): cos ψ = 1/√3.25 = 0.554700, sin ψ = 1.5/√3.25 = 0.832050
                     → d = S·(0.554700 + 0.1·0.832050)/2.5 = S·0.637905/2.5
  X/R-free bound     √(1 + 0.1²)·S/2.5 = S·1.004988/2.5, whatever the X/R.
"""
from __future__ import annotations

import json
import math

import pytest

from services.adequacy import grid_readiness as G
from tests.test_grid_readiness import PROFILE, _project, _rows, _with_envelope


def _enter_xr(d, xr) -> None:
    """The project's metadata.json settings table, as `PATCH .../settings` writes it."""
    path = d / "metadata.json"
    meta = json.loads(path.read_text()) if path.is_file() else {}
    meta["settings"] = {**(meta.get("settings") or {}), "poc_xr": xr}
    path.write_text(json.dumps(meta))


def _rvc(d, n, **kw):
    return _rows(G.grid_readiness(d, n, PROFILE, **kw))["rapid_voltage_change"]


def test_an_entered_xr_lifts_the_guard_and_the_voltage_form_decides(tmp_path):
    """10 MW. At the assumed X/R 4: d = 10·0.339550/2.5 = 1.3582 % passes, the bound
    10·1.004988/2.5 = 4.0200 % does not → warn at 4.0200 %. At the entered X/R 1.5:
    d = 10·0.637905/2.5 = 2.5516 % passes on its own → pass at 2.5516 %."""
    d, n = _with_envelope(tmp_path, swing=10.0)
    before = _rvc(d, n)
    assert before["verdict"] == "warn" and before["project_value"] == pytest.approx(4.0200, rel=1e-4)
    assert before["xr_assumed"] is True                       # AS-18: the scorecard links to the field
    assert "enter the network operator's X/R in Project info, Settings" in before["note"]
    _enter_xr(d, 1.5)
    rvc = _rvc(d, n)
    assert "xr_assumed" not in rvc
    assert rvc["verdict"] == "pass" and rvc["project_value"] == pytest.approx(2.5516, rel=1e-4)
    assert rvc["voltage_form_pct"] == pytest.approx(2.5516, rel=1e-4)
    assert rvc["xr_free_bound_pct"] == before["xr_free_bound_pct"] == pytest.approx(4.0200, rel=1e-4)
    assert G._judge(rvc["project_value"], rvc["limit"], rvc["rule"]) == "pass"   # the stored value re-judges
    assert "X/R 1.5 (entered for the project)" in rvc["note"] and "does not" not in rvc["note"]
    assert "assumed X/R" not in rvc["note"]


def test_an_entered_xr_that_fails_on_the_voltage_form_warns_on_it(tmp_path):
    """20 MW at the entered X/R 1.5: d = 20·0.637905/2.5 = 5.1032 % warns, and that is the value."""
    d, n = _with_envelope(tmp_path, swing=20.0)
    _enter_xr(d, 1.5)
    rvc = _rvc(d, n)
    assert rvc["verdict"] == "warn" and rvc["project_value"] == pytest.approx(5.1032, rel=1e-4)
    assert rvc["xr_free_bound_pct"] == pytest.approx(8.0399, rel=1e-4)


def test_the_guard_needs_the_xr_to_be_entered_not_merely_four(tmp_path):
    """Entering 4 (the default's figure) still lifts the guard: 20 MW, d = 2.7164 % passes, where the
    assumed 4 warns at the bound 8.0399 %."""
    d, n = _with_envelope(tmp_path, swing=20.0)
    assert _rvc(d, n)["verdict"] == "warn"
    _enter_xr(d, 4)
    rvc = _rvc(d, n)
    assert rvc["verdict"] == "pass" and rvc["project_value"] == pytest.approx(2.7164, rel=1e-4)


def test_with_tan_phi_assumed_an_entered_xr_drops_the_guard_but_the_row_stays_assumed(tmp_path):
    """GK A condition 2: no tan φ in the envelope → the assumed 0.1, same figures; the guard goes
    with the entered X/R, the row stays assumed for tan φ."""
    d, n = _with_envelope(tmp_path, swing=10.0)
    meta = json.loads((d / "eh_template.json").read_text())
    del meta["dc_load"]["envelope"]["max"]["tan_phi_swing"]
    (d / "eh_template.json").write_text(json.dumps(meta))
    _enter_xr(d, 1.5)
    rvc = _rvc(d, n)
    assert rvc["verdict"] == "pass" and rvc["project_value"] == pytest.approx(2.5516, rel=1e-4)
    assert rvc["assumed"] and "assumed tan φ_swing 0.1" in rvc["note"]


def _all_but_xr_from_code(tmp_path, d, monkeypatch):
    """A profile whose RVC limit is from the code, an envelope and a fault level from the code."""
    from gridspine.templates.grid_codes import load_grid_code

    p = load_grid_code(PROFILE, raw=True)
    p["rvc_limit_pct"] = {**p["rvc_limit_pct"], "source": "code"}
    codes = tmp_path / "codes"
    codes.mkdir()
    (codes / "rvc_code.yaml").write_text(json.dumps(p))
    meta = json.loads((d / "eh_template.json").read_text())
    meta["dc_load"]["envelope"]["source"] = "code"
    (d / "eh_template.json").write_text(json.dumps(meta))
    monkeypatch.setattr(G, "_marking_assumed", lambda row: False)
    return "rvc_code", (codes,)


def test_the_xr_alone_marks_the_row_assumed(tmp_path, monkeypatch):
    """With every other input from the code, the row is assumed only while the X/R is."""
    d, n = _with_envelope(tmp_path, swing=10.0)
    name, dirs = _all_but_xr_from_code(tmp_path, d, monkeypatch)
    rvc = _rows(G.grid_readiness(d, n, name, profile_dirs=dirs))["rapid_voltage_change"]
    assert rvc["assumed"] is True
    _enter_xr(d, 1.5)
    rvc = _rows(G.grid_readiness(d, n, name, profile_dirs=dirs))["rapid_voltage_change"]
    assert rvc["assumed"] is False and rvc["verdict"] == "pass"


def test_the_note_names_the_poc_and_the_governing_one_of_two(tmp_path):
    """GK A condition 1: one X/R for the project, applied at the PoC whose S_k'' governs."""
    d, n = _with_envelope(tmp_path, swing=10.0)
    _enter_xr(d, 1.5)
    poc = next(b for b in n.buses.index if str(n.buses.at[b, "eh_poc"]).lower() in ("true", "1"))
    rvc = _rvc(d, n)
    assert f"X/R 1.5 (entered for the project), at PoC {poc}" in rvc["note"]
    assert "governing" not in rvc["note"]
    n.add("Bus", "poc_b", v_nom=float(n.buses.at[poc, "v_nom"]))
    n.buses.at["poc_b", "eh_poc"] = True
    n.buses.at["poc_b", "eh_sk_mva"] = 500.0                      # stronger: the 250 MVA PoC governs
    rvc = _rvc(d, n)
    assert f"at PoC {poc}, the governing (lowest S_k'') of 2 PoCs" in rvc["note"]
    assert rvc["project_value"] == pytest.approx(2.5516, rel=1e-4)


def test_a_tie_between_two_pocs_names_the_first_in_bus_index_order(tmp_path):
    """AS-17b (d): equal S_k'' at two PoCs. The first of them in the bus index governs, not the first by name:
    "zz_poc" is added before "aa_poc", and both are weaker than the template's PoC."""
    d, n = _with_envelope(tmp_path, swing=10.0)
    _enter_xr(d, 1.5)
    v_nom = float(n.buses.v_nom.iloc[0])
    for bus in ("zz_poc", "aa_poc"):
        n.add("Bus", bus, v_nom=v_nom)
        n.buses.at[bus, "eh_poc"] = True
        n.buses.at[bus, "eh_sk_mva"] = 100.0
    assert list(n.buses.index).index("zz_poc") < list(n.buses.index).index("aa_poc")
    assert G._governing_poc(n)[0] == "zz_poc"
    assert "at PoC zz_poc, the governing (lowest S_k'') of 3 PoCs" in _rvc(d, n)["note"]
    n.buses = n.buses.loc[["aa_poc", *[b for b in n.buses.index if b != "aa_poc"]]]   # reorder: aa first
    assert G._governing_poc(n)[0] == "aa_poc"


def test_the_after_battery_voltage_change_takes_the_entered_xr(tmp_path):
    """The small-UPS case (13.4 MW uncovered, ΔQ 2.34 Mvar): at the assumed X/R 4 the bound
    √(13.4² + 2.34²)/2.5 = 5.4411 % decides; at the entered X/R 1.5,
    d = (0.554700·13.4 + 0.832050·2.34)/2.5 = (7.43298 + 1.94700)/2.5 = 3.7520 %."""
    d, n = _project(tmp_path, "eh_datacenter", dc_load={})
    n.storage_units.at["ups_battery", "p_nom"] = 10.0
    n.storage_units.at["ups_battery", "max_hours"] = 2.0
    assert _rvc(d, n)["after_battery"]["value"] == pytest.approx(5.4411, rel=1e-4)
    _enter_xr(d, 1.5)
    after = _rvc(d, n)["after_battery"]
    assert after["value"] == pytest.approx(3.7520, rel=1e-4)
    assert "assumed X/R" not in after["note"]


def test_the_flicker_envelope_takes_the_entered_xr_and_its_pst_follows_the_step(tmp_path):
    """The template's 40 MW smoothing leaves no real swing (P_res 0) and the 2.7 Mvar reactive one
    (tan φ 0.1 · 27 MW), so the step is sin ψ · 2.7: 0.970143 · 2.7 = 2.6194 MVA at the assumed X/R 4,
    0.832050 · 2.7 = 2.2465 MVA at the entered 1.5. A purely reactive step shrinks as X/R falls, so
    Pst falls with it; the note names the entered X/R."""
    d, n = _project(tmp_path, "eh_datacenter", dc_load={})
    before = _rows(G.grid_readiness(d, n, PROFILE))["flicker"]
    env0 = before["flicker"]["envelope"]
    assert env0["xr"] == 4.0 and "X/R 4 (assumed" in before["note"]
    assert env0["p_res_mw"] == 0.0 and env0["step_mva"] == pytest.approx(2.6194, rel=1e-4)
    _enter_xr(d, 1.5)
    after = _rows(G.grid_readiness(d, n, PROFILE))["flicker"]
    env = after["flicker"]["envelope"]
    assert env["xr"] == 1.5
    assert env["step_mva"] == pytest.approx(2.7 * 1.5 / math.sqrt(3.25), rel=1e-9) == pytest.approx(2.2465, rel=1e-4)
    assert after["flicker"]["pst"] < before["flicker"]["pst"]
    assert "X/R 1.5 (entered for the project), at PoC" in after["note"] and after["assumed"]


def test_an_entered_xr_changes_only_the_rvc_and_flicker_rows(tmp_path):
    """The infrequent RVC tier (a ratio screen) and every other row read no X/R."""
    d, n = _project(tmp_path, "eh_datacenter", dc_load={})
    before = _rows(G.grid_readiness(d, n, PROFILE))
    _enter_xr(d, 1.5)
    after = _rows(G.grid_readiness(d, n, PROFILE))
    changed = {k for k in before if before[k] != after[k]}
    assert changed == {"rapid_voltage_change", "flicker"}


@pytest.mark.parametrize("stored", [0, -1.5, 100.0001, "1.5", True, None, [1.5], float("nan"), float("inf")])
def test_a_bad_stored_xr_reads_as_none_and_the_scorecard_is_todays(tmp_path, stored):
    d, n = _with_envelope(tmp_path, swing=10.0)
    base = G.grid_readiness(d, n, PROFILE)
    _enter_xr(d, stored)
    assert G.grid_readiness(d, n, PROFILE) == base
