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
Each monthly call runs on a daemon thread with a hard timeout: CDS requests
can wedge, and a stuck daemon thread must not block the queue — the job
fails cleanly instead (spec §3.2).

CDS credentials (spec D2): env `CDSAPI_URL` + `CDSAPI_KEY`, or a new-format
`~/.cdsapirc` (`url:` + bare token `key:`). The legacy `UID:KEY` form 404s
against the current CDS API and is refused up front. The key is never
logged, never echoed in errors, never stored per project.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import threading
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app_paths import app_data_dir
from services.profile_gen.engine import ProfileGenError

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
    import atlite  # lazy: importing atlite is expensive and unneeded offline

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

        with xr.open_dataset(path) as ds:
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
        raise CutoutDownloadError(
            f"ERA5 download failed at month {month}: {type(exc).__name__}: {exc}"
        ) from exc


def _merge_months(parts: list[Path], final: Path) -> None:
    """Concat monthly files along time and atomically replace `final`.

    Eager concat on purpose: profile cutouts are point-scale (a padded bbox of
    a degree or two), so the whole year fits comfortably in memory, and this
    avoids depending on dask being importable in minimal environments.
    """
    import xarray as xr

    tmp = Path(str(final) + ".part")
    opened = []
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
) -> Path:
    """Return the cached cutout for `bbox`/`year`, downloading if needed.

    `bbox` is (min_lon, min_lat, max_lon, max_lat) in WGS-84 degrees; it is
    padded by `pad` degrees before keying so nearest-cell lookups at the edge
    still land inside the grid. Only the complete `<key>.nc` is ever
    returned — `.part`/`.parts` are internal staging.
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
                _call_with_timeout(
                    lambda m=month, p=mpath: dl(padded, int(year), m, p, stop_event),
                    timeout_s,
                    month,
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
