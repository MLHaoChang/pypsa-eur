"""RP-13: each ERA5 cutout month is fetched in a `spawn` child process.

atlite's `prepare()` writes the month through xarray/netCDF4, and HDF5 is not
thread-safe per process: an in-process fetch beside a project save or a
pending write can crash the API. In a child the fetch has its own HDF5 state.
The parent keeps the timeout (kill), the stop_event (terminate), the typed
errors (over a pipe) and the resume rule (`_valid_month`).

The fakes live in `tests/cutout_fakes.py`: a spawn child runs only an
importable function.
"""
from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from services.profile_gen.cutout_cache import (
    CdsCredentialError,
    CutoutDownloadAborted,
    CutoutDownloadError,
    CutoutTimeoutError,
    _key,
    _pad_bbox,
    ensure_cutout,
)
from tests import cutout_fakes

BACKEND = Path(__file__).resolve().parents[1]
BBOX = (13.0, 52.0, 14.0, 53.0)
YEAR = 2020


@pytest.fixture()
def cds_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CDSAPI_URL", "https://cds.climate.copernicus.eu/api")
    monkeypatch.setenv("CDSAPI_KEY", "test-token-abc123")
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _parts(cache: Path) -> Path:
    return cache / f"{_key('era5', _pad_bbox(BBOX, 0.5), YEAR)}.parts"


def _seed(cache: Path, months) -> Path:
    """Valid months on disk, as an earlier aborted run leaves them (resume)."""
    parts = _parts(cache)
    parts.mkdir(parents=True, exist_ok=True)
    for m in months:
        cutout_fakes.write_month(parts / f"m{m:02d}.nc", YEAR, m)
    return parts


def _no_children_left():
    assert multiprocessing.active_children() == []


# --- the crash this fixes ----------------------------------------------------


def _scenario(cache: Path, mode: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "CUTOUT_FAKE_SECONDS": "10",
           "CDSAPI_URL": "https://cds.climate.copernicus.eu/api",
           "CDSAPI_KEY": "test-token-abc123"}
    return subprocess.run(
        [sys.executable, "-m", "tests.cutout_crash_scenario", str(cache), mode],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=240,
    )


def test_month_fetch_in_a_child_survives_saves_and_pending_writes(tmp_path):
    out = _scenario(tmp_path / "c", "child")
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().endswith("OK")
    assert len(list(tmp_path.joinpath("c").glob("era5_*.nc"))) == 1


def test_the_same_fetch_in_process_crashes(tmp_path):
    """The red half: the scenario above, with the fetch on a thread of the
    API process, dies (segfault) or fails the fetch. If this ever passes,
    the crash test above no longer proves anything."""
    out = _scenario(tmp_path / "c", "inprocess")
    assert out.returncode != 0, out.stdout[-2000:]


# --- the parent keeps timeout, abort, typed errors and resume -----------------


def test_a_wedged_child_is_killed_at_the_timeout(cds_env):
    cache = cds_env / "c"
    parts = _seed(cache, range(1, 12))
    with pytest.raises(CutoutTimeoutError, match="month 12"):
        ensure_cutout(BBOX, YEAR, cache_dir=cache, downloader=cutout_fakes.hang,
                      fetch_in_subprocess=True, timeout_s=2)
    _no_children_left()
    # The half-written month is left for the resume check, which drops it.
    assert len(list(parts.glob("m*.nc"))) == 12
    final = ensure_cutout(BBOX, YEAR, cache_dir=cache, downloader=cutout_fakes.ok,
                          fetch_in_subprocess=True)
    assert final.exists() and not parts.exists()


def test_stop_event_terminates_the_child_and_keeps_done_months(cds_env):
    cache = cds_env / "c"
    parts = _seed(cache, range(1, 12))
    stop = threading.Event()
    timer = threading.Timer(1.5, stop.set)
    timer.start()
    try:
        with pytest.raises(CutoutDownloadAborted):
            ensure_cutout(BBOX, YEAR, cache_dir=cache, downloader=cutout_fakes.hang,
                          fetch_in_subprocess=True, stop_event=stop, timeout_s=120)
    finally:
        timer.cancel()
    _no_children_left()
    assert {p.name for p in parts.glob("m*.nc")} >= {f"m{m:02d}.nc" for m in range(1, 12)}


def test_untyped_child_error_reaches_the_caller_as_its_type_only(cds_env, caplog):
    cache = cds_env / "c"
    _seed(cache, range(1, 12))
    with pytest.raises(CutoutDownloadError) as info:
        ensure_cutout(BBOX, YEAR, cache_dir=cache, downloader=cutout_fakes.boom,
                      fetch_in_subprocess=True)
    assert "RuntimeError" in str(info.value) and "month 12" in str(info.value)
    assert "secret" not in str(info.value) and "cds.example" not in str(info.value)
    assert "cds.example" in caplog.text  # the library text stays in the server log
    _no_children_left()


def test_typed_child_error_is_rebuilt_as_its_own_type(cds_env):
    cache = cds_env / "c"
    _seed(cache, range(1, 12))
    with pytest.raises(CdsCredentialError, match="CDS credentials missing"):
        ensure_cutout(BBOX, YEAR, cache_dir=cache,
                      downloader=cutout_fakes.no_credentials, fetch_in_subprocess=True)


def test_a_month_that_wrote_nothing_is_never_merged(cds_env):
    cache = cds_env / "c"
    _seed(cache, range(1, 12))
    with pytest.raises(CutoutDownloadError, match="month 12 left no readable data"):
        ensure_cutout(BBOX, YEAR, cache_dir=cache,
                      downloader=cutout_fakes.writes_nothing, fetch_in_subprocess=True)
    assert not list(cache.glob("era5_*.nc"))


def test_a_full_year_in_children_resumes_and_merges(cds_env):
    cache = cds_env / "c"
    _seed(cache, range(1, 10))
    final = ensure_cutout(BBOX, YEAR, cache_dir=cache, downloader=cutout_fakes.ok,
                          fetch_in_subprocess=True)
    import xarray as xr
    with xr.open_dataset(final) as ds:
        assert ds.sizes["time"] == 24
    _no_children_left()


def test_the_real_downloader_runs_in_a_child_by_default(cds_env, monkeypatch):
    from services.profile_gen import cutout_cache

    seen = []
    monkeypatch.setattr(cutout_cache, "_fetch_month_in_child",
                        lambda dl, *a: seen.append(dl) or cutout_fakes.ok(*a[:4], None))
    _seed(cds_env / "c", range(1, 12))
    ensure_cutout(BBOX, YEAR, cache_dir=cds_env / "c")
    assert seen == [cutout_cache._atlite_month_downloader]
