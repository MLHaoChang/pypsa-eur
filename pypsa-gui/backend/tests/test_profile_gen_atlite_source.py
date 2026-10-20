"""Tests for the atlite source (profile_gen Task 3).

Plan: docs/superpowers/plans/2026-10-17-renewable-profile-generator.md, Task 3.
Spec: docs/superpowers/specs/2026-10-17-renewable-profile-generator-spec.md §3.1.

Everything runs OFFLINE. The tiny cutout (3x3 cells x 48h) is BINARY and
therefore not committed; the session fixture below builds it deterministically
from tools/make_profile_gen_fixture.py into a tmp dir (closed forms, no RNG,
no network). The COMMITTED golden.json — recorded from the real atlite 0.6.1
conversion chain — is what values are asserted against. The asserted length
is the FIXTURE's length (48h), not 8760 — spec §6.

Run with the pixi env python:
    pixi run python -m pytest pypsa-gui/backend/tests/test_profile_gen_atlite_source.py
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import numpy as np
import pytest

from services.profile_gen import generate_profile
from services.profile_gen.engine import InvalidCoordinatesError, ProfileGenError

GOLDEN = json.loads(
    (pathlib.Path(__file__).resolve().parent
     / "fixtures" / "profile_gen" / "golden.json").read_text()
)

# Embedded configs — deliberately DICTS, never atlite library names: some
# named turbines trigger a runtime OEDB download (spec §3.3, review fix 6).
TURBINE = {"V": [0, 3, 12, 25, 26], "POW": [0, 0.1, 2.0, 3.0, 0.0],
           "hub_height": 100, "P": 3.0}
PANEL = {"model": "huld", "efficiency": 0.1, "c_temp_amb": 1,
         "c_temp_irrad": 0.035, "r_tamb": 293, "r_tmod": 298,
         "r_irradiance": 1000, "k_1": -0.017162, "k_2": -0.040289,
         "k_3": -0.004681, "k_4": 0.000148, "k_5": 0.000169,
         "k_6": 0.000005, "inverter_efficiency": 0.9}
ORIENTATION = {"slope": 35.0, "azimuth": 180.0}


@pytest.fixture(scope="session")
def fixture_cutout(tmp_path_factory):
    """Build the deterministic fixture cutout into a tmp dir (offline)."""
    from tools.make_profile_gen_fixture import build

    return build(tmp_path_factory.mktemp("profile_gen") / "cutout_tiny.nc")


def _gen(points, technology, params, year=2020):
    return generate_profile(
        points=points, technology=technology, params=params,
        source="atlite", year=year,
        now="2026-10-18T12:00:00+00:00",
    )


def _wind_params(cutout_path):
    return {"cutout_path": str(cutout_path), "turbine": TURBINE}


def _pv_params(cutout_path):
    return {"cutout_path": str(cutout_path), "panel": PANEL,
            "orientation": ORIENTATION}


# --- golden + length contract -------------------------------------------------


def test_wind_matches_recorded_golden_at_cell_center(fixture_cutout):
    res = _gen([("center", 13.25, 52.25)], "wind", _wind_params(fixture_cutout))
    assert len(res.series) == 48, "fixture length contract — NOT 8760"
    assert res.series.index[0] == np.datetime64("2020-01-01T00:00")
    np.testing.assert_allclose(res.series["center"].to_numpy(),
                               GOLDEN["center"]["wind"], atol=1e-6)


def test_pv_matches_recorded_golden_at_cell_center(fixture_cutout):
    res = _gen([("center", 13.25, 52.25)], "solar", _pv_params(fixture_cutout))
    np.testing.assert_allclose(res.series["center"].to_numpy(),
                               GOLDEN["center"]["pv"], atol=1e-6)


# --- nearest-cell mapping (mutation target: +1-shifted selection turns these red) ---


def test_nearest_cell_mapping_between_centers(fixture_cutout):
    # (13.24, 52.24) sits 0.01 deg from the center cell (13.25, 52.25):
    # nearest-cell selection must land exactly on the center cell's golden.
    res = _gen([("near", 13.24, 52.24)], "wind", _wind_params(fixture_cutout))
    np.testing.assert_allclose(res.series["near"].to_numpy(),
                               GOLDEN["center"]["wind"], atol=1e-6)


def test_corner_cell_maps_to_corner_golden_not_center(fixture_cutout):
    res = _gen([("corner", 13.0, 52.0), ("center", 13.25, 52.25)], "wind",
               _wind_params(fixture_cutout))
    np.testing.assert_allclose(res.series["corner"].to_numpy(),
                               GOLDEN["corner"]["wind"], atol=1e-6)
    assert not np.allclose(res.series["corner"].to_numpy(),
                           res.series["center"].to_numpy())


# --- guards --------------------------------------------------------------------


def test_missing_cutout_path_refused():
    with pytest.raises(ProfileGenError, match="cutout_path"):
        _gen([("a", 13.25, 52.25)], "wind", {"turbine": TURBINE})


def test_missing_technology_params_refused(fixture_cutout):
    with pytest.raises(ProfileGenError, match="turbine"):
        _gen([("a", 13.25, 52.25)], "wind", {"cutout_path": str(fixture_cutout)})
    with pytest.raises(ProfileGenError, match="panel"):
        _gen([("a", 13.25, 52.25)], "solar",
             {"cutout_path": str(fixture_cutout), "orientation": ORIENTATION})


def test_point_outside_cutout_refused(fixture_cutout):
    with pytest.raises(InvalidCoordinatesError, match="outside the cutout"):
        _gen([("far", 0.0, 0.0)], "wind", _wind_params(fixture_cutout))


def test_year_mismatch_refused(fixture_cutout):
    with pytest.raises(ProfileGenError, match="weather year"):
        _gen([("a", 13.25, 52.25)], "wind", _wind_params(fixture_cutout),
             year=2019)


# --- engine integration ----------------------------------------------------------


def test_engine_meta_records_atlite_version(fixture_cutout):
    import atlite

    res = _gen([("a", 13.25, 52.25)], "wind", _wind_params(fixture_cutout))
    assert res.meta["source"] == "atlite"
    assert res.meta["atlite_version"] == atlite.__version__


# --- import cage: atlite must stay lazily inside atlite_source ------------------


def test_atlite_import_is_caged():
    """Importing every profile_gen module must NOT import atlite."""
    code = (
        "import sys;"
        "import services.profile_gen, services.profile_gen.engine,"
        " services.profile_gen.synthetic, services.profile_gen.cutout_cache,"
        " services.profile_gen.atlite_source;"
        "offenders=[m for m in sys.modules if m == 'atlite' or m.startswith('atlite.')];"
        "assert not offenders, f'atlite leaked at import time: {offenders}'"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True,
        cwd=str(pathlib.Path(__file__).resolve().parent.parent),
    )
    assert proc.returncode == 0, proc.stderr
