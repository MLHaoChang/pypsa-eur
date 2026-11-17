"""Module-level fake month downloaders for the cutout subprocess tests (RP-13).

A spawn child can only run a picklable, importable function, so the fakes
live here rather than as closures in the test. Each takes the real
downloader's signature `(bbox, year, month, target, stop_event)`; the child
always passes `stop_event=None`.
"""

from __future__ import annotations

import os
import time
from pathlib import Path


def write_month(target: Path, year: int, month: int, hours: int = 2) -> None:
    """A tiny but VALID monthly netCDF, as the real downloader would leave."""
    import numpy as np
    import xarray as xr

    idx = xr.date_range(f"{year}-{month:02d}-01", periods=hours, freq="h")
    xr.Dataset(
        {"wspd": ("time", np.full(hours, float(month)))}, coords={"time": idx}
    ).to_netcdf(target)


def ok(bbox, year, month, target, stop_event):
    write_month(target, year, month)


def hang(bbox, year, month, target, stop_event):
    """A wedged CDS request: never returns on its own."""
    Path(target).write_bytes(b"partial")  # a half-written month
    time.sleep(600)


def boom(bbox, year, month, target, stop_event):
    raise RuntimeError("https://cds.example/request?key=secret")


def no_credentials(bbox, year, month, target, stop_event):
    from services.profile_gen.cutout_cache import CdsCredentialError

    raise CdsCredentialError("CDS credentials missing")


def writes_nothing(bbox, year, month, target, stop_event):
    return None


def writes_garbage(bbox, year, month, target, stop_event):
    """Answers "ok" but leaves a file no netCDF reader can open."""
    Path(target).write_bytes(b"partial")


def exits_3(bbox, year, month, target, stop_event):
    """Dies without answering, as a crash in the fetch would."""
    os._exit(3)


def atlite_missing(bbox, year, month, target, stop_event):
    from services.profile_gen.engine import AtliteNotInstalledError

    raise AtliteNotInstalledError("atlite is not installed here")


def typed_download_error(bbox, year, month, target, stop_event):
    from services.profile_gen.cutout_cache import CutoutDownloadError

    raise CutoutDownloadError("CDS refused the request (typed)")


def typed_base_error(bbox, year, month, target, stop_event):
    from services.profile_gen.engine import ProfileGenError

    raise ProfileGenError("a profile-generation refusal")


def typed_aborted(bbox, year, month, target, stop_event):
    from services.profile_gen.cutout_cache import CutoutDownloadAborted

    raise CutoutDownloadAborted("aborted inside the fetch")


def typed_timeout(bbox, year, month, target, stop_event):
    from services.profile_gen.cutout_cache import CutoutTimeoutError

    raise CutoutTimeoutError("CDS queue timed out")


class CutoutDownloadError(Exception):
    """A foreign class that shares the typed error's bare name."""


def foreign_same_name(bbox, year, month, target, stop_event):
    raise CutoutDownloadError("https://cds.example/foreign?key=secret")


def report_pid(bbox, year, month, target, stop_event):
    """Write the fetching process's pid, and whether the CDS key it sees
    equals `CUTOUT_FAKE_EXPECT_KEY` (never the key itself), to the file named
    by `CUTOUT_FAKE_REPORT`, then leave a valid month."""
    seen = os.environ.get("CDSAPI_KEY") == os.environ.get("CUTOUT_FAKE_EXPECT_KEY")
    Path(os.environ["CUTOUT_FAKE_REPORT"]).write_text(
        f"{os.getpid()}\n{'match' if seen else 'mismatch'}"
    )
    write_month(target, year, month)


def hammer_netcdf(bbox, year, month, target, stop_event):
    """Write and read netCDF through xarray for `CUTOUT_FAKE_SECONDS`, the way
    atlite's prepare() does, then leave a valid month at `target`."""
    import numpy as np
    import pandas as pd
    import xarray as xr

    seconds = float(os.environ.get("CUTOUT_FAKE_SECONDS", "0"))
    idx = pd.date_range(f"{year}-{month:02d}-01", periods=2000, freq="h")
    scratch = Path(target).with_suffix(".scratch.nc")
    stop = time.monotonic() + seconds
    while time.monotonic() < stop:
        ds = xr.Dataset(
            {"w": (("time", "x"), np.random.rand(len(idx), 20))},
            coords={"time": idx},
        )
        ds.to_netcdf(scratch)
        with xr.open_dataset(scratch) as back:
            back.load()
    scratch.unlink(missing_ok=True)
    write_month(target, year, month)


def log_fetch(bbox, year, month, target, stop_event):
    """Append the month to the file named by `CUTOUT_FAKE_LOG` (one line per
    fetch, from whichever process runs it), then leave a valid month (RP-21)."""
    with open(os.environ["CUTOUT_FAKE_LOG"], "a") as log:
        log.write(f"{month}\n")
    time.sleep(0.05)  # long enough that a racing second fetch would overlap
    write_month(target, year, month)
