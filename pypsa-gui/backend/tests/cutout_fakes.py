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
