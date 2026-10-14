"""
I3a of the campus island plan: the campus run writes the island hourly
tables beside ``campus_hourly.csv`` when the project has an island sidecar
(owner option A, 2026-10-13: gridspine's contracts, pypsa-gui's values).
"""
import json

import pandas as pd
import pytest
from fastapi import HTTPException

from gridspine.schema.island import (
    ISLAND_HOURLY_CSV,
    ISLAND_UNIT_HOURLY_CSV,
    validate_island_hourly,
    validate_island_unit_hourly,
)
from services import campus_electrical_service as ce
from services import project_registry
from tests import test_campus_electrical_service as _ces
from tests.test_island_rows import _BESS, _a

H = _ces.H
hub, user_and_db = _ces.hub, _ces.user_and_db          # the campus service's fixtures


def _sidecar(project, **req):
    cfg = {"units": {"bess": dict(_BESS), "dc_load": {"kind": "load", "critical": True}},
           "requirements": {"bridge_s": _a(1.0), "sustained_h": _a(1.0),
                            "rocof_window_ms": _a(500.0), "gfm_margin": _a(0.0),
                            "scenarios": ["seamless"], **req,
                            "frequency_limits": {
                                s: {"rocof_hz_per_s": _a(1.0), "f_min_hz": _a(47.5),
                                    "f_max_hz": _a(51.5), "qss_band_hz": _a(1.0)}
                                for s in ("transition", "steady_island")}}}
    (project_registry.project_dir(project) / "island_config.json").write_text(json.dumps(cfg))


def _run(project):
    ce.draft(project)
    ce.run(project, {"k": 1, "invest": False})
    return ce.campus_dir(project) / "run"


def test_a_run_writes_the_island_tables_beside_the_campus_hourly(hub):
    _sidecar(hub)
    run = _run(hub)
    unit = validate_island_unit_hourly(pd.read_csv(run / ISLAND_UNIT_HOURLY_CSV))
    hourly = validate_island_hourly(pd.read_csv(run / ISLAND_HOURLY_CSV))
    assert len(hourly) == H and set(hourly["scenario"]) == {"seamless"}
    assert hourly["critical_load_mw"].tolist() == pytest.approx([40.0] * H)
    campus = pd.read_csv(run / "campus_hourly.csv", dtype={"unit_id": str})
    # The unit is keyed by its campus id, mapped by pypsa_name.
    assert set(unit["unit_id"]) <= set(campus["unit_id"])
    assert set(unit["hour"]) == set(campus["hour"])


def test_a_project_without_a_sidecar_gets_no_island_tables_and_loses_stale_ones(hub):
    _sidecar(hub)
    run = _run(hub)
    (project_registry.project_dir(hub) / "island_config.json").unlink()
    ce.run(hub, {"k": 1, "invest": False})
    assert not (run / ISLAND_UNIT_HOURLY_CSV).exists()
    assert not (run / ISLAND_HOURLY_CSV).exists()


def test_a_sidecar_that_does_not_validate_is_refused_with_its_reason(hub):
    _sidecar(hub, scenarios=["ups_bridged"])          # ups_bridged needs pickup_blocks
    with pytest.raises(HTTPException) as exc:
        _run(hub)
    assert exc.value.status_code == 422 and "pickup_blocks" in exc.value.detail
