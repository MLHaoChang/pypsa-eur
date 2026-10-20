"""Tests for the ERA5 cutout cache + downloader (profile_gen Task 2).

Plan: docs/superpowers/plans/2026-10-17-renewable-profile-generator.md, Task 2.
Spec: docs/superpowers/specs/2026-10-17-renewable-profile-generator-spec.md
(D2 credentials, §3.2 cache semantics).

Everything here runs OFFLINE: the downloader is stubbed and writes tiny real
netCDF files, so the merge/atomicity/resume logic runs for real. CDS is never
touched.

Run with the pixi env python:
    pixi run python -m pytest pypsa-gui/backend/tests/test_profile_gen_cutout_cache.py
"""
from __future__ import annotations

import threading
import time

import numpy as np
import pytest
import xarray as xr

from services.profile_gen.cutout_cache import (
    CdsCredentialError,
    CutoutDownloadAborted,
    CutoutDownloadError,
    CutoutTimeoutError,
    ensure_cutout,
)

BBOX = (13.0, 52.0, 14.0, 53.0)  # (min_lon, min_lat, max_lon, max_lat)
YEAR = 2020  # leap on purpose: length assertions must not assume 365 days


def _write_month(path, year, month, hours=2):
    """A tiny but VALID monthly netCDF, as the real downloader would leave."""
    idx = xr.date_range(f"{year}-{month:02d}-01", periods=hours, freq="h")
    ds = xr.Dataset(
        {"wspd": ("time", np.full(hours, float(month)))},
        coords={"time": idx},
    )
    ds.to_netcdf(path)


def _stub_downloader(calls, set_stop_at=None, sleep_s=None, fail_at=None):
    def dl(bbox, year, month, target, stop_event):
        calls.append(month)
        if fail_at is not None and month == fail_at:
            raise RuntimeError(f"boom month {month}")
        if sleep_s:
            time.sleep(sleep_s)
        if set_stop_at is not None and month >= set_stop_at:
            stop_event.set()
            return  # stopped mid-flight: this month never lands on disk
        _write_month(target, year, month)
    return dl


@pytest.fixture()
def cds_env(monkeypatch, tmp_path):
    """Valid CDS credentials via env; HOME redirected so no real ~/.cdsapirc."""
    monkeypatch.setenv("CDSAPI_URL", "https://cds.climate.copernicus.eu/api")
    monkeypatch.setenv("CDSAPI_KEY", "test-token-abc123")
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


# --- happy paths ------------------------------------------------------------


def test_cold_download_merges_months_and_cleans_up(cds_env, tmp_path):
    calls = []
    progress = []
    out = ensure_cutout(
        BBOX, YEAR, cache_dir=tmp_path / "cutouts",
        downloader=_stub_downloader(calls),
        progress=lambda done, total, note: progress.append((done, total)),
    )
    assert calls == list(range(1, 13))
    assert out.exists()
    # `.part` / `.parts` must never survive a successful run
    assert not list(out.parent.glob("*.part"))
    assert not (out.parent / (out.stem + ".parts")).exists()
    assert progress[-1] == (12, 12)
    merged = xr.open_dataset(out)
    assert merged.sizes["time"] == 24  # 12 months x 2 stub hours
    assert float(merged["wspd"].isel(time=0)) == 1.0
    assert float(merged["wspd"].isel(time=-1)) == 12.0


def test_cache_hit_never_calls_downloader(cds_env, tmp_path):
    calls = []
    dl = _stub_downloader(calls)
    first = ensure_cutout(BBOX, YEAR, cache_dir=tmp_path / "c", downloader=dl)
    assert len(calls) == 12

    def forbidden(*a, **k):
        raise AssertionError("downloader called on a cache hit")

    second = ensure_cutout(BBOX, YEAR, cache_dir=tmp_path / "c",
                           downloader=forbidden)
    assert second == first


def test_final_path_is_never_a_part_file(cds_env, tmp_path):
    # Mutation target: returning the .part path turns this red.
    out = ensure_cutout(BBOX, YEAR, cache_dir=tmp_path / "c",
                        downloader=_stub_downloader([]))
    assert out.parent == tmp_path / "c"
    assert out.name.endswith(".nc")
    assert ".part" not in out.name
    assert out.name.startswith(f"era5_") and out.name.endswith(f"_{YEAR}.nc")


def test_bbox_changes_cache_key(cds_env, tmp_path):
    a = ensure_cutout(BBOX, YEAR, cache_dir=tmp_path / "c",
                      downloader=_stub_downloader([]))
    b = ensure_cutout((10.0, 50.0, 11.0, 51.0), YEAR, cache_dir=tmp_path / "c",
                      downloader=_stub_downloader([]))
    assert a.name != b.name


# --- resume / stale files ---------------------------------------------------


def test_resume_keeps_valid_months_and_replaces_corrupt(cds_env, tmp_path):
    cache = tmp_path / "c"
    # First run aborts after month 2: months 1-2 stay on disk for resume.
    stop = threading.Event()
    calls1 = []
    dl1 = _stub_downloader(calls1, set_stop_at=3)
    with pytest.raises(CutoutDownloadAborted):
        ensure_cutout(BBOX, YEAR, cache_dir=cache, downloader=dl1,
                      stop_event=stop)
    assert calls1 == [1, 2, 3]

    # Corrupt month 2 by hand; only 2..12 may be downloaded again.
    parts = next(cache.glob("*.parts"))
    (parts / "m02.nc").write_text("garbage")
    calls2 = []
    out = ensure_cutout(BBOX, YEAR, cache_dir=cache,
                        downloader=_stub_downloader(calls2))
    assert calls2 == list(range(2, 13))
    assert out.exists()


def test_stray_part_file_is_deleted_and_never_served(cds_env, tmp_path):
    cache = tmp_path / "c"
    calls = []
    out = ensure_cutout(BBOX, YEAR, cache_dir=cache,
                        downloader=_stub_downloader(calls))
    # Hand-craft a stray final-stage leftover, then re-run on a cold cache.
    stray = out.with_suffix(".nc.part")
    out.rename(stray)
    # sabotage: if the stray were served, the downloader would not run
    calls.clear()
    out2 = ensure_cutout(BBOX, YEAR, cache_dir=cache,
                         downloader=_stub_downloader(calls))
    assert calls == list(range(1, 13)), "a .part file must never be served"
    assert not stray.exists()
    assert out2.exists()


# --- abort / timeout / failure ----------------------------------------------


def test_abort_between_months_leaves_no_final(cds_env, tmp_path):
    stop = threading.Event()
    with pytest.raises(CutoutDownloadAborted):
        ensure_cutout(BBOX, YEAR, cache_dir=tmp_path / "c",
                      downloader=_stub_downloader([], set_stop_at=5),
                      stop_event=stop)
    assert not list((tmp_path / "c").glob("*.nc"))


def test_timeout_is_a_typed_error(cds_env, tmp_path):
    with pytest.raises(CutoutTimeoutError, match="month 1"):
        ensure_cutout(BBOX, YEAR, cache_dir=tmp_path / "c",
                      downloader=_stub_downloader([], sleep_s=5.0),
                      timeout_s=0.2)


def test_downloader_failure_is_wrapped_with_month_context(cds_env, tmp_path):
    with pytest.raises(CutoutDownloadError, match="month 4"):
        ensure_cutout(BBOX, YEAR, cache_dir=tmp_path / "c",
                      downloader=_stub_downloader([], fail_at=4))


# --- CDS credential preflight (spec D2) --------------------------------------


def test_missing_credentials_refused_with_instructions(monkeypatch, tmp_path):
    monkeypatch.delenv("CDSAPI_URL", raising=False)
    monkeypatch.delenv("CDSAPI_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.cdsapirc in there
    with pytest.raises(CdsCredentialError, match="cds.climate.copernicus.eu"):
        ensure_cutout(BBOX, YEAR, cache_dir=tmp_path / "c",
                      downloader=_stub_downloader([]))


def test_legacy_uid_colon_key_refused_without_leaking_it(monkeypatch, tmp_path):
    monkeypatch.setenv("CDSAPI_URL", "https://cds.climate.copernicus.eu/api")
    monkeypatch.setenv("CDSAPI_KEY", "12345:secret-uuid-value")
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(CdsCredentialError) as excinfo:
        ensure_cutout(BBOX, YEAR, cache_dir=tmp_path / "c",
                      downloader=_stub_downloader([]))
    msg = str(excinfo.value)
    assert "legacy" in msg.lower() or "UID" in msg
    assert "secret-uuid-value" not in msg, "the key must never be echoed"


def test_cdsapirc_new_format_accepted(monkeypatch, tmp_path):
    monkeypatch.delenv("CDSAPI_URL", raising=False)
    monkeypatch.delenv("CDSAPI_KEY", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".cdsapirc").write_text(
        "url: https://cds.climate.copernicus.eu/api\n"
        "key: rc-token-xyz\n"
    )
    monkeypatch.setenv("HOME", str(home))
    calls = []
    out = ensure_cutout(BBOX, YEAR, cache_dir=tmp_path / "c",
                        downloader=_stub_downloader(calls))
    assert calls == list(range(1, 13))
    assert out.exists()
