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
import types
from pathlib import Path

import pytest

from services.profile_gen.cutout_cache import (
    CdsCredentialError,
    CutoutDownloadAborted,
    CutoutDownloadError,
    CutoutTimeoutError,
    _fetch_month_in_child,
    _key,
    _pad_bbox,
    ensure_cutout,
)
from services.profile_gen import cutout_cache
from services.profile_gen.engine import AtliteNotInstalledError, ProfileGenError
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


_SPAWN = multiprocessing.get_context("spawn")
STARTED: list = []


class _RecordedProcess(_SPAWN.Process):
    """The real spawn Process, remembered so a test can check it is dead."""

    alive_at_close = exitcode_at_close = None

    def start(self):
        STARTED.append(self)
        super().start()

    def close(self):
        # A closed Process answers neither is_alive() nor exitcode: keep both.
        self.alive_at_close, self.exitcode_at_close = self.is_alive(), self.exitcode
        super().close()


@pytest.fixture()
def recorded(monkeypatch):
    STARTED.clear()
    ctx = types.SimpleNamespace(Pipe=_SPAWN.Pipe, Process=_RecordedProcess)
    monkeypatch.setattr(
        cutout_cache,
        "multiprocessing",
        types.SimpleNamespace(get_context=lambda kind: ctx),
    )
    return STARTED


def _assert_dead(started):
    assert started, "no child was started"
    for p in started:  # reaped and closed by the parent
        assert p.alive_at_close is False and p.exitcode_at_close is not None
    _no_children_left()


def _run_child(fake, *, timeout_s=60.0, stop_event=None, target=None):
    _fetch_month_in_child(
        fake, BBOX, YEAR, 12, target or Path("unused.nc"), timeout_s, stop_event
    )


# --- the crash this fixes ----------------------------------------------------


def test_month_fetch_in_a_child_survives_saves_and_pending_writes(tmp_path):
    """The scenario fetches month 12 with a fake that writes and reads netCDF
    through xarray for 10 s, beside two network save/load threads and a
    pending writer. In-process the same scenario segfaults (rc 139; kept as
    PR evidence, not as a test: a segfault is not deterministic)."""
    env = {
        **os.environ,
        "CUTOUT_FAKE_SECONDS": "10",
        "CDSAPI_URL": "https://cds.climate.copernicus.eu/api",
        "CDSAPI_KEY": "test-token-abc123",
    }
    cache = tmp_path / "c"
    out = subprocess.run(
        [sys.executable, "-m", "tests.cutout_crash_scenario", str(cache), "child"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().endswith("OK")
    finals = list(cache.glob("era5_*.nc"))
    assert len(finals) == 1 and not _parts(cache).exists()
    import xarray as xr

    with xr.open_dataset(finals[0]) as ds:
        assert ds.sizes["time"] == 24  # 12 months x 2 hours


# --- routing: the real downloader runs in a child -----------------------------


def test_default_downloader_runs_in_a_child_and_injected_one_in_process(
    cds_env, monkeypatch
):
    report = cds_env / "report.txt"
    monkeypatch.setenv("CUTOUT_FAKE_REPORT", str(report))
    monkeypatch.setattr(
        cutout_cache, "_atlite_month_downloader", cutout_fakes.report_pid
    )

    _seed(cds_env / "a", range(1, 12))
    ensure_cutout(BBOX, YEAR, cache_dir=cds_env / "a")  # downloader=None
    assert int(report.read_text().split("\n")[0]) != os.getpid()

    _seed(cds_env / "b", range(1, 12))
    ensure_cutout(
        BBOX, YEAR, cache_dir=cds_env / "b", downloader=cutout_fakes.report_pid
    )
    assert int(report.read_text().split("\n")[0]) == os.getpid()


def test_the_child_sees_credentials_set_after_import(cds_env, monkeypatch):
    """Credentials reach the child by the environment at fetch time (the app
    sets them in-process, e.g. aliasing at bootstrap), never over the pipe."""
    report = cds_env / "report.txt"
    monkeypatch.setenv("CUTOUT_FAKE_REPORT", str(report))
    monkeypatch.setenv("CDSAPI_KEY", "set-after-import")
    monkeypatch.setenv("CUTOUT_FAKE_EXPECT_KEY", "set-after-import")
    _seed(cds_env / "c", range(1, 12))
    ensure_cutout(
        BBOX,
        YEAR,
        cache_dir=cds_env / "c",
        downloader=cutout_fakes.report_pid,
        fetch_in_subprocess=True,
    )
    pid, key = report.read_text().split("\n")
    assert int(pid) != os.getpid() and key == "match"


# --- the child's answer: exit race, typed errors -----------------------------


def test_a_child_that_answers_and_exits_at_once_reads_as_ok(recorded):
    for _ in range(20):
        _run_child(cutout_fakes.writes_nothing)  # returns at once: "ok"
    _assert_dead(recorded)


class _LatePoll:
    """The parent's receive end, made to look away at the worst moment: its
    first poll waits until the child has answered and exited, then reports
    nothing yet. The next look must still find the answer."""

    def __init__(self, conn):
        self._conn, self._first = conn, True

    def poll(self, timeout=0.0):
        if self._first:
            self._first = False
            for child in STARTED:
                child.join(30)
            return False
        return self._conn.poll(timeout)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_an_answer_sent_just_before_the_child_exits_is_not_lost(recorded, monkeypatch):
    def late_pipe(duplex=True):
        receive, send = _SPAWN.Pipe(duplex=duplex)
        return _LatePoll(receive), send

    ctx = types.SimpleNamespace(Pipe=late_pipe, Process=_RecordedProcess)
    monkeypatch.setattr(
        cutout_cache,
        "multiprocessing",
        types.SimpleNamespace(get_context=lambda kind: ctx),
    )
    _run_child(cutout_fakes.writes_nothing)  # "ok", then exits at once
    assert recorded[0].exitcode_at_close == 0
    _assert_dead(recorded)


def test_a_child_that_dies_without_answering_reports_its_exit_code(recorded):
    with pytest.raises(CutoutDownloadError, match="exited with code 3"):
        _run_child(cutout_fakes.exits_3)
    _assert_dead(recorded)


@pytest.mark.parametrize(
    "fake, cls, message",
    [
        (cutout_fakes.no_credentials, CdsCredentialError, "CDS credentials missing"),
        (
            cutout_fakes.atlite_missing,
            AtliteNotInstalledError,
            "atlite is not installed here",
        ),
        (
            cutout_fakes.typed_download_error,
            CutoutDownloadError,
            "CDS refused the request (typed)",
        ),
        (
            cutout_fakes.typed_base_error,
            ProfileGenError,
            "a profile-generation refusal",
        ),
        (cutout_fakes.typed_aborted, CutoutDownloadAborted, "aborted inside the fetch"),
        (cutout_fakes.typed_timeout, CutoutTimeoutError, "CDS queue timed out"),
    ],
)
def test_typed_child_errors_round_trip_as_their_exact_class(fake, cls, message):
    with pytest.raises(cls) as info:
        _run_child(fake)
    assert type(info.value) is cls and str(info.value) == message


def test_a_foreign_class_with_a_typed_name_takes_the_generic_path(caplog):
    with pytest.raises(CutoutDownloadError) as info:
        _run_child(cutout_fakes.foreign_same_name)
    assert str(info.value) == (
        "ERA5 download failed at month 12 (CutoutDownloadError); see the server log"
    )
    assert "cds.example" in caplog.text  # the library text stays in the server log


def test_untyped_child_error_reaches_the_caller_as_its_type_only(cds_env, caplog):
    cache = cds_env / "c"
    _seed(cache, range(1, 12))
    with pytest.raises(CutoutDownloadError) as info:
        ensure_cutout(
            BBOX,
            YEAR,
            cache_dir=cache,
            downloader=cutout_fakes.boom,
            fetch_in_subprocess=True,
        )
    assert "RuntimeError" in str(info.value) and "month 12" in str(info.value)
    assert "secret" not in str(info.value) and "cds.example" not in str(info.value)
    assert "cds.example" in caplog.text
    _no_children_left()


# --- timeout and abort: child dead, no partial month, done months kept -------


def _month_files(parts: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(parts.glob("*.nc"))}


def test_a_wedged_child_is_killed_at_the_timeout_and_its_month_refetched(
    cds_env, recorded, monkeypatch
):
    cache = cds_env / "c"
    parts = _seed(cache, range(1, 12))
    before = _month_files(parts)
    with pytest.raises(CutoutTimeoutError, match="month 12"):
        ensure_cutout(
            BBOX,
            YEAR,
            cache_dir=cache,
            downloader=cutout_fakes.hang,
            fetch_in_subprocess=True,
            timeout_s=2,
        )
    _assert_dead(recorded)
    # The half-written month never landed: only the 11 done months remain.
    assert _month_files(parts) == before

    report = cds_env / "report.txt"
    monkeypatch.setenv("CUTOUT_FAKE_REPORT", str(report))
    final = ensure_cutout(
        BBOX,
        YEAR,
        cache_dir=cache,
        downloader=cutout_fakes.report_pid,
        fetch_in_subprocess=True,
    )
    assert report.exists()  # month 12 was fetched again
    assert final.exists() and not parts.exists()


def test_stop_event_ends_the_child_and_keeps_done_months(cds_env, recorded):
    cache = cds_env / "c"
    parts = _seed(cache, range(1, 12))
    before = _month_files(parts)
    stop = threading.Event()
    timer = threading.Timer(1.5, stop.set)
    timer.start()
    try:
        with pytest.raises(CutoutDownloadAborted):
            ensure_cutout(
                BBOX,
                YEAR,
                cache_dir=cache,
                downloader=cutout_fakes.hang,
                fetch_in_subprocess=True,
                stop_event=stop,
                timeout_s=120,
            )
    finally:
        timer.cancel()
    _assert_dead(recorded)
    assert _month_files(parts) == before  # done months untouched, no month 12


# --- the resume rule holds for a fresh month too -----------------------------


def test_a_month_finished_as_the_stop_came_is_kept(cds_env):
    """Thread path: the fetch lands a valid month, then the stop arrives. The
    run aborts, and the finished month stays for resume."""
    cache = cds_env / "c"
    parts = _seed(cache, range(1, 12))
    stop = threading.Event()

    def lands_then_stops(bbox, year, month, target, stop_event):
        cutout_fakes.write_month(target, year, month)
        stop_event.set()

    with pytest.raises(CutoutDownloadAborted, match="at month 12"):
        ensure_cutout(
            BBOX, YEAR, cache_dir=cache, downloader=lands_then_stops, stop_event=stop
        )
    assert (parts / "m12.nc").exists()
    assert not list(parts.glob("*.fetch.nc"))


@pytest.mark.parametrize("in_child", [True, False])
def test_a_month_that_wrote_nothing_is_never_merged(cds_env, in_child):
    cache = cds_env / "c"
    _seed(cache, range(1, 12))
    with pytest.raises(CutoutDownloadError, match="month 12 left no readable data"):
        ensure_cutout(
            BBOX,
            YEAR,
            cache_dir=cache,
            downloader=cutout_fakes.writes_nothing,
            fetch_in_subprocess=in_child,
        )
    assert not list(cache.glob("era5_*.nc"))
    assert not (_parts(cache) / "m12.nc").exists()


def test_a_full_year_in_children_resumes_and_merges(cds_env):
    cache = cds_env / "c"
    _seed(cache, range(1, 10))
    final = ensure_cutout(
        BBOX,
        YEAR,
        cache_dir=cache,
        downloader=cutout_fakes.ok,
        fetch_in_subprocess=True,
    )
    import xarray as xr

    with xr.open_dataset(final) as ds:
        assert ds.sizes["time"] == 24
    _no_children_left()
