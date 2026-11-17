"""Run one cold cutout fetch beside network save/load and pending writers.

`python -m tests.cutout_crash_scenario <cache_dir> <child|inprocess>` from the
backend directory. Months 1-11 are seeded valid (resume), so only month 12 is
fetched, by `cutout_fakes.hammer_netcdf`. Prints OK and exits 0 when the fetch
and every writer finished; a netCDF collision crashes the process (HDF5 is not
thread-safe) or fails the fetch. Used by test_profile_gen_cutout_subprocess.
"""
from __future__ import annotations

import sys
import threading
import time
import uuid
from pathlib import Path


def main(cache: Path, mode: str) -> int:
    import numpy as np
    import pandas as pd
    import pypsa

    from services.profile_gen.cutout_cache import (
        _key, _pad_bbox, ensure_cutout,
    )
    from services.profile_gen.job import write_pending
    from services.pypsa_service import PyPSAService
    from tests import cutout_fakes

    bbox, year = (13.0, 52.0, 14.0, 53.0), 2020
    parts = cache / f"{_key('era5', _pad_bbox(bbox, 0.5), year)}.parts"
    parts.mkdir(parents=True)
    for m in range(1, 12):
        cutout_fakes.write_month(parts / f"m{m:02d}.nc", year, m)

    idx = pd.date_range("2025-01-01", periods=2000, freq="h")
    frame = pd.DataFrame({"x": np.random.default_rng(0).random(len(idx))}, index=idx)
    done = threading.Event()
    errors: list[str] = []

    def saver(k: int) -> None:
        n = pypsa.Network()
        n.set_snapshots(idx)
        n.add("Bus", "b")
        n.add("Load", "l", bus="b", p_set=frame["x"])
        path = cache / f"network{k}.nc"
        while not done.is_set():
            with PyPSAService.get_netcdf_io_lock():  # as a project save/load
                n.export_to_netcdf(path)
                pypsa.Network(path)
            time.sleep(0.005)  # threading.Lock is unfair: let the fetch's checks in

    def pending() -> None:
        while not done.is_set():
            try:
                write_pending(cache / "pending", str(uuid.uuid4()), frame, {"points": ["x"]})
            except Exception as exc:  # noqa: BLE001 — any failure counts
                errors.append(repr(exc))
            time.sleep(0.005)

    workers = [threading.Thread(target=saver, args=(k,)) for k in range(2)]
    workers.append(threading.Thread(target=pending))
    for t in workers:
        t.start()
    try:
        final = ensure_cutout(
            bbox, year, cache_dir=cache, downloader=cutout_fakes.hammer_netcdf,
            fetch_in_subprocess=(mode == "child"), timeout_s=300,
        )
    finally:
        done.set()
        for t in workers:
            t.join()
    if errors or not final.exists():
        print("FAILED", errors[:3])
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), sys.argv[2]))
