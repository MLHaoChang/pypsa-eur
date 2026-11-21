"""What a weather cutout is, for the project's record (project-io 03, owner Q4).

A project keeps the series it generated, not the cutout. It records the
cutout's own extent (`bbox` = west, south, east, north of its grid), its
weather `year` and the `sha256` of its bytes, so the profiles' source can be
named and checked later. The extent is read from the file, not from the points
that asked for it: a larger cached cutout records what it really covers (GK B3
W1).

The hash is computed per job, with no cache beside the cutout (GK B3). The
bytes hashed are those of the file the profile is generated from: the hash
reads one open file, and the record is refused when the path no longer names
that file once the extent has been read (W2). Generation opens the path again
afterwards. The cache (`cutout_cache.py`) returns an existing file untouched
and places a new one only when the path is free, with `os.replace`, never by
writing in place; only two cold fetches of the same key racing could swap the
file in that window, and the second holds the same request's data.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

_CHUNK = 1 << 20


class CutoutChanged(OSError):
    """The cutout's path named another file while it was being recorded."""


def _same_file(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino, a.st_size, a.st_mtime_ns) == (
        b.st_dev,
        b.st_ino,
        b.st_size,
        b.st_mtime_ns,
    )


def cutout_record(path: Path | str) -> dict:
    """{module, bbox, year, sha256} of the cutout at `path`. Raises OSError
    (the file is unreadable or changed underneath) or ValueError (it is not a
    cutout with x/y/time coordinates)."""
    import xarray as xr

    path = Path(path)
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        before = os.fstat(fh.fileno())
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
        try:
            with xr.open_dataset(path) as ds:
                xs, ys, times = ds["x"].values, ds["y"].values, ds["time"].values
        except KeyError as exc:
            raise ValueError(f"not a cutout: no {exc} coordinate") from exc
        if not _same_file(before, os.stat(path)):
            raise CutoutChanged("the cutout was replaced while it was recorded")
    if not (len(xs) and len(ys) and len(times)):
        raise ValueError("not a cutout: an empty grid or time axis")
    import pandas as pd

    years = pd.DatetimeIndex(times).year
    return {
        "module": path.name.split("_", 1)[0],
        "bbox": [float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())],
        "year": int(years.min()),
        "sha256": digest.hexdigest(),
    }
