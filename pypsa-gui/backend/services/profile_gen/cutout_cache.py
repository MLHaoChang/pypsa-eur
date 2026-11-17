"""ERA5 cutout cache + downloader (profile_gen Task 2).

Spec: docs/superpowers/specs/2026-10-17-renewable-profile-generator-spec.md
(D2 credentials, §3.2 cache). Plan Task 2.

Layout under `<app data>/cutouts/` (or the `cache_dir` override):

    era5_<bbox_hash>_<year>.nc        complete cutout — the ONLY servable file
    era5_<bbox_hash>_<year>.nc.part   merge-stage temp: deleted on sight,
                                      NEVER served
    era5_<bbox_hash>_<year>.parts/    per-month downloads m01.nc..m12.nc;
                                      valid months survive an abort and are
                                      resumed, corrupt ones are re-fetched

Downloads run MONTH BY MONTH (atlite's era5 backend requests per month when
asked to; running one month per call gives the queue runner 12 natural
progress steps a year, a stop check between months, and per-month resume).
Each month is fetched in a `spawn` child process (RP-13): atlite's
`prepare()` writes the month through xarray/netCDF4, and HDF5 is not
thread-safe per process, so an in-process fetch could crash the API beside a
project save. The child has its own HDF5 state and needs no lock. The parent
keeps the hard timeout (kills the child: CDS requests can wedge, and the job
fails cleanly instead, spec §3.2), the caller's stop_event (terminates it),
the typed errors (sent back over a pipe), the resume rule (`_valid_month`)
and the merge under the process netCDF lock. An injected test downloader runs
on a daemon thread instead, unless `fetch_in_subprocess=True`.

CDS credentials (spec D2): env `CDSAPI_URL` + `CDSAPI_KEY`, or a new-format
`~/.cdsapirc` (`url:` + bare token `key:`). The legacy `UID:KEY` form 404s
against the current CDS API and is refused up front. The key is never
logged, never echoed in errors, never stored per project.
"""
from __future__ import annotations

import hashlib
import logging
import multiprocessing
import os
import shutil
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app_paths import app_data_dir
from services.profile_gen.engine import AtliteNotInstalledError, ProfileGenError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class CdsCredentialError(ProfileGenError):
    """CDS credentials missing or in the legacy UID:KEY format."""


class CutoutDownloadAborted(ProfileGenError):
    """The caller's stop_event fired; partial months stay on disk for resume."""


class CutoutTimeoutError(ProfileGenError):
    """A monthly CDS request exceeded the server-side timeout."""


class CutoutDownloadError(ProfileGenError):
    """The downloader failed; message carries the month, never secrets."""


_CDS_SETUP_HINT = (
    "ERA5 downloads need a (free) CDS API key: create an account at "
    "https://cds.climate.copernicus.eu/, accept the dataset licence, then set "
    "CDSAPI_URL=https://cds.climate.copernicus.eu/api and CDSAPI_KEY=<token> "
    "in the environment, or write `url:`/`key:` lines to ~/.cdsapirc. "
    "The old UID:KEY format is no longer accepted by CDS."
)

MONTHS_PER_YEAR = 12

Downloader = Callable[[tuple[float, float, float, float], int, int, Path, Any], None]
ProgressFn = Callable[[int, int, str], None]


# ---------------------------------------------------------------------------
# Paths / keys
# ---------------------------------------------------------------------------


def default_cutout_cache_dir() -> Path:
    """`<app data>/cutouts/` — shared across projects, LRU-evictable by hand."""
    return app_data_dir() / "cutouts"


def _pad_bbox(bbox: Sequence[float], pad: float) -> tuple[float, float, float, float]:
    w, s, e, n = (float(v) for v in bbox)
    return (
        round(max(-180.0, w - pad), 4),
        round(max(-90.0, s - pad), 4),
        round(min(180.0, e + pad), 4),
        round(min(90.0, n + pad), 4),
    )


def _key(module: str, bbox: Sequence[float], year: int) -> str:
    digest = hashlib.sha1(
        f"{module}|{bbox[0]:.4f},{bbox[1]:.4f},{bbox[2]:.4f},{bbox[3]:.4f}".encode()
    ).hexdigest()[:12]
    return f"{module}_{digest}_{year}"


# ---------------------------------------------------------------------------
# CDS credential preflight — before ANY download, never on a cache hit
# ---------------------------------------------------------------------------


def _read_cdsapirc() -> tuple[str | None, str | None]:
    rc = Path.home() / ".cdsapirc"
    if not rc.exists():
        return None, None
    url = key = None
    for line in rc.read_text().splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if k == "url":
            url = v
        elif k == "key":
            key = v
    return url, key


def cds_preflight(env: Mapping[str, str] | None = None) -> None:
    """Typed refusal unless usable CDS credentials are visible. Never logs
    or echoes the key."""
    env = os.environ if env is None else env
    url = env.get("CDSAPI_URL")
    key = env.get("CDSAPI_KEY")
    if not key:
        rc_url, rc_key = _read_cdsapirc()
        url = url or rc_url
        key = key or rc_key
    if not key:
        raise CdsCredentialError("no CDS API key configured. " + _CDS_SETUP_HINT)
    if ":" in key.strip():
        raise CdsCredentialError(
            "CDS API key is in the legacy UID:KEY format, which the current "
            "CDS API rejects (404). " + _CDS_SETUP_HINT
        )


# ---------------------------------------------------------------------------
# Real downloader (lazy atlite import — the only network code in the module)
# ---------------------------------------------------------------------------


def _atlite_month_downloader(
    bbox: tuple[float, float, float, float],
    year: int,
    month: int,
    target: Path,
    stop_event: Any,
) -> None:
    try:
        import atlite  # lazy: importing atlite is expensive and unneeded offline
    except ImportError as exc:
        raise AtliteNotInstalledError(
            "cutout download needs the optional 'atlite' package, which is "
            "not installed in this environment — use the synthetic source or "
            "install atlite"
        ) from exc

    w, s, e, n = bbox
    cutout = atlite.Cutout(
        path=str(target),
        module="era5",
        x=slice(w, e),
        y=slice(s, n),  # atlite Cutout convention: y slice is (south, north)
        time=f"{year}-{month:02d}",
    )
    cutout.prepare()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_month(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        import xarray as xr

        from services.profile_gen.job import pending_io_lock

        with pending_io_lock(), xr.open_dataset(path) as ds:
            return ds.sizes.get("time", 0) > 0
    except Exception:
        return False


def _call_with_timeout(fn: Callable[[], None], timeout_s: float, month: int) -> None:
    """Run fn() on a daemon thread; a wedged CDS request fails the job
    instead of parking a queue worker forever. The daemon thread may linger
    until the process exits — it holds no locks and touches only its own
    month file, which the next run re-validates before resuming."""
    error: list[BaseException] = []

    def run() -> None:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 — re-raised below
            error.append(exc)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise CutoutTimeoutError(
            f"ERA5 download timed out after {timeout_s:.0f}s (month {month}); "
            "the CDS request wedged — retrying usually resumes where it left off"
        )
    if error:
        exc = error[0]
        if isinstance(exc, ProfileGenError):
            raise exc
        # The library's own text stays in the server log: it can carry the
        # request URL, and a caller sees only the type (`refusals`).
        logger.error("ERA5 download failed at month %s", month, exc_info=exc)
        raise CutoutDownloadError(
            f"ERA5 download failed at month {month} ({type(exc).__name__}); "
            "see the server log"
        ) from exc


# The typed errors a child may send back, rebuilt by class name in the parent.
_CHILD_ERRORS: dict[str, type[ProfileGenError]] = {
    cls.__name__: cls
    for cls in (
        ProfileGenError,
        AtliteNotInstalledError,
        CdsCredentialError,
        CutoutDownloadAborted,
        CutoutTimeoutError,
        CutoutDownloadError,
    )
}

_CHILD_POLL_S = 0.25


def _month_child(
    conn: Any,
    downloader: Downloader,
    bbox: tuple[float, float, float, float],
    year: int,
    month: int,
    target: str,
) -> None:
    """Child entry point: fetch one month, answer ("ok",) or ("err", class
    name, message, traceback, is_typed) down `conn`. The parent's stop_event
    cannot cross the process boundary, so the downloader gets None; the
    parent terminates the child instead."""
    try:
        downloader(bbox, year, month, Path(target), None)
        reply: tuple = ("ok",)
    except BaseException as exc:  # noqa: BLE001 — sent to the parent
        reply = (
            "err",
            type(exc).__name__,
            str(exc),
            traceback.format_exc(),
            isinstance(exc, ProfileGenError),
        )
    try:
        conn.send(reply)
    finally:
        conn.close()


def _fetch_month_in_child(
    downloader: Downloader,
    bbox: tuple[float, float, float, float],
    year: int,
    month: int,
    target: Path,
    timeout_s: float,
    stop_event: Any | None,
) -> None:
    """Run one month's fetch in a `spawn` child (own HDF5 state, so no lock).

    Same pattern as `services.document_values._parse`: a frozen desktop build
    hands spawn children to `multiprocessing.freeze_support()`
    (`desktop/gui.py`). The parent polls the pipe: the caller's stop_event
    terminates the child, the timeout kills it, and a child that dies without
    answering fails the month. A killed child may leave a partial month file;
    the next run's `_valid_month` check deletes it before re-fetching."""
    ctx = multiprocessing.get_context("spawn")
    receive, send = ctx.Pipe(duplex=False)
    child = ctx.Process(
        target=_month_child,
        args=(send, downloader, bbox, year, month, str(target)),
        daemon=True,
        name=f"cutout-m{month:02d}",
    )
    child.start()
    send.close()
    reply: tuple | None = None
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            if stop_event is not None and stop_event.is_set():
                child.terminate()
                raise CutoutDownloadAborted(
                    f"cutout download aborted during month {month}"
                )
            left = deadline - time.monotonic()
            if left <= 0:
                child.kill()
                raise CutoutTimeoutError(
                    f"ERA5 download timed out after {timeout_s:.0f}s (month {month}); "
                    "the CDS request wedged — retrying usually resumes where it left off"
                )
            if receive.poll(min(_CHILD_POLL_S, left)):
                try:
                    reply = receive.recv()
                except (EOFError, OSError):  # died before it answered
                    reply = None
                break
    finally:
        receive.close()
        if child.is_alive():
            child.kill()
        child.join(5)

    if reply is None:
        logger.error(
            "ERA5 download child for month %s died without answering (exit %s)",
            month, child.exitcode,
        )
        raise CutoutDownloadError(
            f"ERA5 download failed at month {month} (the fetch process exited "
            f"with code {child.exitcode}); see the server log"
        )
    if reply[0] == "ok":
        return
    _, name, message, tb, typed = reply
    cls = _CHILD_ERRORS.get(name) if typed else None
    if cls is not None:
        raise cls(message)
    # Today's rule for untyped errors: the library's text can carry the
    # request URL, so it stays in the server log and the caller sees the type.
    logger.error("ERA5 download failed at month %s:\n%s", month, tb)
    raise CutoutDownloadError(
        f"ERA5 download failed at month {month} ({name}); see the server log"
    )


def _merge_months(parts: list[Path], final: Path) -> None:
    """Concat monthly files along time and atomically replace `final`.

    Eager concat on purpose: profile cutouts are point-scale (a padded bbox of
    a degree or two), so the whole year fits comfortably in memory, and this
    avoids depending on dask being importable in minimal environments.
    """
    import xarray as xr

    from services.profile_gen.job import pending_io_lock

    tmp = Path(str(final) + ".part")
    opened = []
    # The process netCDF lock (HDF5 is not thread-safe): a merge on a queue
    # worker must not run beside a project save/load or a pending write.
    with pending_io_lock():
        try:
            for p in parts:
                ds = xr.open_dataset(str(p))
                ds.load()
                opened.append(ds)
            xr.concat(opened, dim="time").to_netcdf(tmp)
            os.replace(tmp, final)
        finally:
            for ds in opened:
                ds.close()
            tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def ensure_cutout(
    bbox: Sequence[float],
    year: int,
    *,
    module: str = "era5",
    cache_dir: str | Path | None = None,
    downloader: Downloader | None = None,
    progress: ProgressFn | None = None,
    stop_event: Any | None = None,
    timeout_s: float = 900.0,
    pad: float = 0.5,
    fetch_in_subprocess: bool | None = None,
) -> Path:
    """Return the cached cutout for `bbox`/`year`, downloading if needed.

    `bbox` is (min_lon, min_lat, max_lon, max_lat) in WGS-84 degrees; it is
    padded by `pad` degrees before keying so nearest-cell lookups at the edge
    still land inside the grid. Only the complete `<key>.nc` is ever
    returned — `.part`/`.parts` are internal staging.

    `fetch_in_subprocess` (default: True for the real atlite downloader,
    False for an injected one) runs each month in a spawn child; an injected
    downloader must then be a module-level (picklable) function.
    """
    cache = Path(cache_dir) if cache_dir is not None else default_cutout_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    padded = _pad_bbox(bbox, pad)
    key = _key(module, padded, int(year))
    final = cache / f"{key}.nc"
    parts_dir = cache / f"{key}.parts"

    if final.exists():
        return final  # cache hit: no credentials needed, no network

    # Cold path: credentials first (spec D2), then stale-merge cleanup.
    cds_preflight()
    Path(str(final) + ".part").unlink(missing_ok=True)  # never serve a .part

    dl = downloader or _atlite_month_downloader
    in_child = downloader is None if fetch_in_subprocess is None else fetch_in_subprocess

    def stopped() -> bool:
        return bool(stop_event is not None and stop_event.is_set())

    parts_dir.mkdir(exist_ok=True)
    written: list[Path] = []
    try:
        for month in range(1, MONTHS_PER_YEAR + 1):
            if stopped():
                raise CutoutDownloadAborted(
                    f"cutout download aborted before month {month}"
                )
            mpath = parts_dir / f"m{month:02d}.nc"
            if _valid_month(mpath):
                written.append(mpath)  # resume: keep the valid month
            else:
                mpath.unlink(missing_ok=True)  # corrupt partial: never resume
                # Not under the netCDF lock: a month can take minutes, and the
                # lock would stall every project save. The child has its own
                # HDF5 state, so its xarray/netCDF4 writes cannot collide with
                # this process's (RP-13).
                if in_child:
                    _fetch_month_in_child(
                        dl, padded, int(year), month, mpath, timeout_s, stop_event
                    )
                else:
                    _call_with_timeout(
                        lambda m=month, p=mpath: dl(padded, int(year), m, p, stop_event),
                        timeout_s,
                        month,
                    )
                if not stopped() and not _valid_month(mpath):
                    # The resume rule holds for a fresh month too: only a
                    # readable month is ever merged.
                    raise CutoutDownloadError(
                        f"ERA5 download for month {month} left no readable data; "
                        "retrying re-fetches it"
                    )
                written.append(mpath)
            if stopped():
                raise CutoutDownloadAborted(
                    f"cutout download aborted at month {month}"
                )
            if progress:
                progress(month, MONTHS_PER_YEAR, f"{module} {year}-{month:02d}")

        _merge_months(written, final)
    finally:
        # On success the dir is empty after the merge; on abort/timeout the
        # completed months stay for resume — either way stale empties go.
        if final.exists():
            shutil.rmtree(parts_dir, ignore_errors=True)

    return final
