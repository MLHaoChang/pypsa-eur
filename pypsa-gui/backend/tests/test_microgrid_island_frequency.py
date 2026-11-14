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
  E3 and E5). Its bridge check failed at 24 hours on I4's AC-loss step.

SH40-BRIDGE-2 (2026-11-14): both templates' subsea tie is open (p_max_pu 0),
so the solve imports nothing and there is no islanding step. The check reads
the run's own tie state (``campus_pcc.csv`` ``tie_open``): on every island
hour of both templates E1 and the bridge read "not applicable: tie open",
none failing, and the summary says they were not evaluated.
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


def test_the_single_year_ledger_states_the_shipped_battery_settings():
    """``eh_templates`` cannot import the staged module's constants (it is
    registered into by it), so its ledger text is pinned to them here."""
    row = next(
        r
        for r in T.TEMPLATE_META["eh_microgrid"]["ledger"]
        if r["item"] == "island unit data"
    )
    assert (
        f"batteries droop {H.MG_GFM_DROOP_PCT:g} %, tau_f {H.MG_GFM_TAU_F_S:g} s"
        in row["value"]
    )


@pytest.mark.parametrize("tid", MICROGRIDS)
def test_the_shipped_sidecar_file_is_the_built_one(tid):
    from pathlib import Path

    shipped = Path(T.__file__).parent / tid / T.ISLAND_FILE
    assert json.loads(shipped.read_text()) == json.loads(
        json.dumps(T.island_config(tid))
    )


def _shipped_meta_ids():
    from pathlib import Path

    root = Path(T.__file__).parent
    return sorted(t for t in T.TEMPLATE_META if (root / t / T.META_FILE).is_file())


@pytest.mark.parametrize("tid", _shipped_meta_ids())
def test_the_shipped_metadata_file_is_the_built_one(tid):
    """A ledger edit reaches new projects only through the shipped file
    (``from_template`` copies it), so it must be regenerated with the code."""
    from pathlib import Path

    shipped = Path(T.__file__).parent / tid / T.META_FILE
    assert json.loads(shipped.read_text()) == json.loads(
        json.dumps(T.template_meta(tid))
    )


@pytest.mark.live_solve
@pytest.mark.slow
@pytest.mark.parametrize("tid", MICROGRIDS)
def test_the_template_passes_the_island_frequency_check(
    tid,
    user_and_db,  # noqa: F811
    reset_backend,
    monkeypatch,
    tmp_path,
):
    n, _cfg, _state = _solve(tid, monkeypatch, tmp_path)
    db, user = user_and_db
    row = project_registry.create_root(db, user, f"SH 40 {tid}")
    d = project_registry.ensure_project_dir(row)
    n.export_to_netcdf(str(d / "network.nc"))
    T.write_sidecars(d, tid)
    ce.draft(row)
    run = _frequency_check(row)
    summary = json.loads((run / "campus_freq_summary.json").read_text())
    assert summary["island_hours"]  # the check judged some hours
    assert summary["failing_hours"] == []
    assert summary["planned_infeasible_hours"] == []
    # SH40-BRIDGE-2: the subsea tie is open (p_max_pu 0), so there is no
    # islanding step: E1 and the bridge are not judged, never failing.
    assert summary["bridge_failing_hours"] == []
    assert summary["tie_open_hours"] == summary["island_hours"]
    assert summary["e1_bridge_note"] == "not evaluated (tie open on every island hour)"
