"""Study-harness 40 (owner SH40-FREQ-1): the shipped microgrid templates pass
the island frequency check at their own island hours, with the island
sidecar's retuned grid-forming battery settings (2 % droop, τ_f 0.3 s; they
were 4 % and 0.1 s). ``mg.island.frequency`` stays a ``warn`` rule meanwhile.

Measured on this plan (2026-11-13), the campus run up to I6 at the Guided
default ranking:

- ``eh_microgrid``: 10 island hours, none failing, no bridge hour failing
  (before: hours 68, 141 and 165 failed E2 and E3 on the 2 Hz/s transition
  RoCoF);
- ``eh_microgrid_staged``: 35 island hours, none failing (before: 15, on E2,
  E3 and E5). Its bridge check still fails at 24 hours: an empty battery is
  asked for its droop share of the islanding step while the island rows
  reserve the bridge energy over all grid-forming batteries together. No
  sidecar setting reaches that; it is left to the bridge follow-up and is
  not pinned here.
"""
from __future__ import annotations

import json

import pytest

from services import campus_electrical_service as ce
from services import project_registry
from tests.test_campus_island_cuts_e2e import user_and_db  # noqa: F401
from tests.test_eh_staged_hubs import H, T, _solve
from tests.test_microgrid_gfm_demo import _frequency_check

MICROGRIDS = ("eh_microgrid", "eh_microgrid_staged")


@pytest.mark.parametrize("tid", MICROGRIDS)
def test_every_grid_forming_battery_ships_the_retuned_settings(tid):
    units = T.ISLAND_CONFIG[tid]["units"]
    gfm = {name: u for name, u in units.items() if u.get("control") == "gfm_droop"}
    assert gfm and all(u["kind"] == "bess" for u in gfm.values())
    for u in gfm.values():
        assert u["droop_pct"] == {"value": H.MG_GFM_DROOP_PCT, "source": "assumed"}
        assert u["tau_f_s"] == {"value": H.MG_GFM_TAU_F_S, "source": "assumed"}
    assert (H.MG_GFM_DROOP_PCT, H.MG_GFM_TAU_F_S) == (2.0, 0.3)


@pytest.mark.parametrize("tid", MICROGRIDS)
def test_the_shipped_sidecar_file_is_the_built_one(tid):
    from pathlib import Path

    shipped = Path(T.__file__).parent / tid / T.ISLAND_FILE
    assert json.loads(shipped.read_text()) == json.loads(json.dumps(T.island_config(tid)))


@pytest.mark.live_solve
@pytest.mark.slow
@pytest.mark.parametrize("tid", MICROGRIDS)
def test_the_template_passes_the_island_frequency_check(tid, user_and_db, reset_backend,  # noqa: F811
                                                        monkeypatch, tmp_path):
    n, _cfg, _state = _solve(tid, monkeypatch, tmp_path)
    db, user = user_and_db
    row = project_registry.create_root(db, user, f"SH 40 {tid}")
    d = project_registry.ensure_project_dir(row)
    n.export_to_netcdf(str(d / "network.nc"))
    T.write_sidecars(d, tid)
    ce.draft(row)
    run = _frequency_check(row)
    summary = json.loads((run / "campus_freq_summary.json").read_text())
    assert summary["island_hours"]                     # the check judged some hours
    assert summary["failing_hours"] == []
    assert summary["planned_infeasible_hours"] == []
    if tid == "eh_microgrid":
        assert summary["bridge_failing_hours"] == []
