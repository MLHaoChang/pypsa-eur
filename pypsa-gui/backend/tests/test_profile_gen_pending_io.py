"""Pending artifacts are written and read under one lock: netCDF4/HDF5 is
not thread-safe, and two previews in flight on the API's worker threads
failed with a PermissionError or crashed the process before it."""
from __future__ import annotations

import threading
import uuid

import numpy as np
import pandas as pd

from services.profile_gen.job import write_pending


def test_concurrent_pending_writes_all_land(tmp_path):
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

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(written) == 32
