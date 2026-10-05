"""The campus study as the backend sees it (plan C2).

A campus study is separate from the case39 year study. It runs no unit
commitment, no N-2 and no PSS/E handoff. It takes two inputs:

* a **solved capacity-expansion project** (``network.nc``), the assets and
  their hourly dispatch per investment period;
* a **campus description** (``gridspine.ingest.campus``), the electrical
  plant between those assets and the grid.

From these it prepares a run directory that the ranking (C3) and the sizing
(C4) read:

``campus.yaml``
    The campus exactly as used.
``campus_hourly.csv``, ``campus_pcc.csv``
    The hourly tables (``gridspine.schema.campus``).
``campus_manifest.json``
    Both inputs by sha256, the periods and the unit count. A later stage can
    then recognise a run whose project or campus has changed since.

``draft_from_project`` is the "generate" step. It drafts a campus from the
saved project, for the user to edit before ``prepare_campus``.

Everything is validated before anything is written: the campus is built,
the tables are made and checked, and only then do the files land. Each file
is replaced atomically, so a refused campus leaves the run as it was.
"""
import hashlib
import json
import os
import tempfile
from pathlib import Path

import pandas as pd
import yaml

from gridspine.ingest.campus import Campus, build_campus
from gridspine.producers.campus import CampusDraft, campus_hourly, draft_campus
from gridspine.producers.pypsa_nodal import load_solved_network
from gridspine.schema.campus import HOURLY_CSV, PCC_CSV, validate_hourly, validate_pcc
from gridspine.schema.contracts import ContractError

CAMPUS_YAML = "campus.yaml"
CAMPUS_MANIFEST = "campus_manifest.json"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_text_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def draft_from_project(network_path) -> CampusDraft:
    """A campus draft from the saved, solved project at ``network_path``."""
    return draft_campus(load_solved_network(network_path))


def prepare_campus(run_dir, campus: dict, network_path) -> dict:
    """Validate ``campus`` against the project at ``network_path``, then write
    the run (see the module docstring). Returns a summary: ``periods``,
    ``units``, ``hours_per_period``."""
    network_path = Path(network_path)
    build_campus(campus)                                   # refuse before writing
    n = load_solved_network(network_path)
    hourly, pcc = campus_hourly(n, campus)
    periods = sorted(int(p) for p in pcc["period"].unique())
    campus_text = yaml.safe_dump(campus, sort_keys=False)
    manifest = {
        "campus_sha256": hashlib.sha256(campus_text.encode()).hexdigest(),
        "network_path": str(network_path),
        "network_sha256": _sha256_file(network_path),
        "periods": periods,
        "units": int(hourly["unit_id"].nunique()),
        "hours_per_period": int(pcc.groupby("period").size().max()),
    }
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(run_dir / CAMPUS_YAML, campus_text)
    _write_text_atomic(run_dir / HOURLY_CSV, hourly.to_csv(index=False))
    _write_text_atomic(run_dir / PCC_CSV, pcc.to_csv(index=False))
    _write_text_atomic(run_dir / CAMPUS_MANIFEST, json.dumps(manifest, indent=2))
    return {"periods": periods, "units": manifest["units"], "hours_per_period": manifest["hours_per_period"]}


def _require(run_dir: Path, name: str) -> Path:
    path = run_dir / name
    if not path.is_file():
        raise ContractError(f"{path} is missing; prepare the campus study first")
    return path


def campus_tables(run_dir):
    """``(hourly, pcc)`` of a prepared run, validated."""
    run_dir = Path(run_dir)
    hourly = validate_hourly(pd.read_csv(_require(run_dir, HOURLY_CSV), dtype={"unit_id": str}))
    pcc = validate_pcc(pd.read_csv(_require(run_dir, PCC_CSV)))
    return hourly, pcc


def load_run_campus(run_dir) -> Campus:
    """The campus of a prepared run, built from its stored ``campus.yaml``."""
    return build_campus(yaml.safe_load(_require(Path(run_dir), CAMPUS_YAML).read_text()))
