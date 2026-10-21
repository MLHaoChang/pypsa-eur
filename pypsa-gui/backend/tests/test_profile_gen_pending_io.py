"""Pending artifacts are written and read under the process-wide netCDF lock
(`PyPSAService.get_netcdf_io_lock`): netCDF4/HDF5 is not thread-safe per
process. Two previews in flight on the API's worker threads failed with a
PermissionError or crashed the process before, and a pending write must not
run beside a project save/load either."""
from __future__ import annotations

import threading
import uuid

import numpy as np
import pandas as pd
import pypsa

from services.profile_gen.job import write_pending
from services.pypsa_service import PyPSAService


def test_pending_writes_beside_each_other_and_a_network_save_load_all_land(tmp_path):
    frame = pd.DataFrame({"x": np.random.default_rng(0).random(8760)},
                         index=pd.date_range("2025-01-01", periods=8760, freq="h"))
    errors: list[str] = []
    written: list[str] = []

    def writer():
        for _ in range(8):
            job = str(uuid.uuid4())
            try:
                write_pending(tmp_path, job, frame, {"points": ["x"]})
                written.append(job)
            except Exception as exc:  # noqa: BLE001 — the test counts any failure
                errors.append(repr(exc))

    def saver():
        # A project save and load hold the same lock around their netCDF I/O.
        n = pypsa.Network()
        n.set_snapshots(frame.index)
        n.add("Bus", "b")
        n.add("Load", "l", bus="b", p_set=frame["x"])
        path = tmp_path / "network.nc"
        for _ in range(4):
            try:
                with PyPSAService.get_netcdf_io_lock():
                    PyPSAService.export_network_to_netcdf(n, path)
                with PyPSAService.get_netcdf_io_lock():
                    PyPSAService.import_network_from_netcdf(pypsa.Network(), path)
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

    threads = [threading.Thread(target=writer) for _ in range(3)] + [threading.Thread(target=saver)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(written) == 24


def test_cutout_month_merges_beside_a_network_save_load(tmp_path):
    """The cutout cache's month check and merge take the same lock (GK
    follow-up on #468): a queue worker merging ERA5 months must not run
    beside a project save/load."""
    import xarray as xr

    from services.profile_gen.cutout_cache import _merge_months, _valid_month

    months = []
    for m in range(1, 4):
        idx = pd.date_range(f"2025-{m:02d}-01", periods=24 * 28, freq="h")
        p = tmp_path / f"m{m}.nc"
        xr.Dataset({"t": ("time", np.random.default_rng(m).random(len(idx)))},
                   coords={"time": idx}).to_netcdf(p)
        months.append(p)
    errors: list[str] = []

    def merger(i):
        for k in range(6):
            final = tmp_path / f"cutout_{i}_{k}.nc"
            try:
                _merge_months(months, final)
                assert _valid_month(final)
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

    def saver():
        n = pypsa.Network()
        n.set_snapshots(pd.date_range("2025-01-01", periods=8760, freq="h"))
        n.add("Bus", "b")
        path = tmp_path / "network.nc"
        for _ in range(4):
            try:
                with PyPSAService.get_netcdf_io_lock():
                    PyPSAService.export_network_to_netcdf(n, path)
                with PyPSAService.get_netcdf_io_lock():
                    PyPSAService.import_network_from_netcdf(pypsa.Network(), path)
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

    threads = [threading.Thread(target=merger, args=(i,)) for i in range(3)]
    threads.append(threading.Thread(target=saver))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
