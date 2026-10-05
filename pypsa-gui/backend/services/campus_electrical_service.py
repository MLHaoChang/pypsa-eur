"""Campus electrical study for a capacity-expansion (hub) project (plan C6).

A solved, saved hub project is taken to its electrical design by the
gridspine campus engine (``gridspine.drivers.campus_study``). This service
keeps everything under ``<project dir>/campus_electrical/``:

``campus_input.yaml``
    The campus description the user edits. ``draft`` writes it from the
    project's network. Every value is tagged; see
    ``gridspine.ingest.campus``.
``draft_notes.json``
    What the draft skipped, and why.
``settings.json``
    The last run's settings: k, power factor, profile, margin, N-1.
``run/``
    The engine's run directory.
``grid_codes/``
    The project's own grid codes: uploaded documents, drafts and published
    profiles (``services/campus_grid_code_service.py``, plan C10). A
    published profile is offered next to the shipped ones and accepted by
    ``run``.

The functions:

* ``get_state``: the campus file, the profiles a study can use, the last
  settings, and the results with a ``stale`` flag. Results are stale when
  the campus file or the project's network has changed since the run;
  the engine's manifest hashes are compared.
* ``draft``: writes the campus file from the network. It refuses with 409
  rather than overwrite an existing file unless asked.
* ``save_campus``: validates by building the campus, then writes it.
* ``run``: prepare, rank and size, in one call.

Every function refuses a project of another kind with 409, the way the
planning study refuses a capacity-expansion project. A missing network, an
unsolved network, a description that does not build, and bad settings are
422s, each naming what to fix. A pypsa NetCDF read happens under
``PyPSAService.get_netcdf_io_lock()``, and the run under a per-project lock.
"""
import hashlib
import json
import math
import threading
from pathlib import Path

import pandas as pd
import yaml
from fastapi import HTTPException

from services import project_registry
from services.gridspine_service import CAPACITY_EXPANSION, kind_of

# Guarded like services/gridspine_service.py, for the same reason: the frozen
# desktop app is built from a pip venv that ships neither gridspine nor its
# engines, so there this import really fails. Every action then answers 503
# instead of 500ing three frames down.
try:
    from gridspine.drivers import campus_study as cs
    from gridspine.drivers.campus_study import SizingCriteria
    from gridspine.schema.contracts import ContractError

    CAMPUS_AVAILABLE = True
    CAMPUS_IMPORT_ERROR = None
except ImportError as exc:                      # pragma: no cover - build-shape branch
    CAMPUS_AVAILABLE = False
    CAMPUS_IMPORT_ERROR = str(exc)
    cs = SizingCriteria = None

    class ContractError(Exception):
        """Stand-in so the `except ContractError` clauses below still parse."""

SUBDIR = "campus_electrical"
CAMPUS_FILE = "campus_input.yaml"
NOTES_FILE = "draft_notes.json"
SETTINGS_FILE = "settings.json"
NETWORK_FILE = "network.nc"
GRID_CODES_SUBDIR = "grid_codes"
MAX_CAMPUS_BYTES = 1_000_000
DEFAULTS = {"k": 3, "pf": None, "profile": "eu_rfg_dcc_ce", "margin": 0.2, "n_minus_1": True}

_LOCKS: dict = {}
_LOCKS_GUARD = threading.Lock()


def _lock(path: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(path), threading.Lock())


def require_engine() -> None:
    """503 rather than 500 when this build does not carry gridspine."""
    if not CAMPUS_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail=f"The campus electrical study is not available in this build: {CAMPUS_IMPORT_ERROR}",
        )


def require_capacity_expansion(project) -> None:
    require_engine()
    kind = kind_of(project)
    if kind != CAPACITY_EXPANSION:
        raise HTTPException(
            status_code=409,
            detail=f"Project '{project.name}' is a {kind} project; the campus electrical study is for "
                   "capacity-expansion (hub) projects",
        )


def campus_dir(project) -> Path:
    path = project_registry.ensure_project_dir(project) / SUBDIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def grid_codes_dir(project) -> Path:
    """Where the project's grid codes live; its ``*.yaml`` are the published
    profiles the engine loads next to the shipped ones."""
    return campus_dir(project) / GRID_CODES_SUBDIR


def profiles(project) -> dict:
    """``{profile: title}`` a study of this project can be held to: the
    shipped profiles and the project's published ones."""
    try:
        return cs.grid_code_profiles(extra_dirs=(grid_codes_dir(project),))
    except ContractError as exc:
        raise _unprocessable(exc)


def _network(project) -> Path:
    nc = project_registry.project_dir(project) / NETWORK_FILE
    if not nc.is_file():
        raise HTTPException(status_code=422, detail=f"Project '{project.name}' has no saved network yet — save it first")
    return nc


def _netcdf_lock():
    from services.pypsa_service import PyPSAService
    return PyPSAService.get_netcdf_io_lock()


def _unprocessable(exc: ContractError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


def _records(df: pd.DataFrame) -> list:
    """JSON-ready rows: NaN and NaT leave as None."""
    out = []
    for row in df.to_dict(orient="records"):
        out.append({k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in row.items()})
    return out


def _read_csv(path: Path):
    return _records(pd.read_csv(path)) if path.is_file() else None


def _results(run: Path):
    if not (run / cs.COMPLIANCE_CSV).is_file():
        return None
    selection = pd.read_csv(run / cs.SELECTED_CSV)
    selection["reasons"] = selection["reasons"].str.split(";")
    return {
        "selection": _records(selection),
        "transformers": _read_csv(run / cs.SIZING_TRAFO_CSV),
        "compensation": _read_csv(run / cs.SIZING_COMP_CSV),
        "short_circuit": _read_csv(run / cs.SHORT_CIRCUIT_CSV),
        "compliance": _read_csv(run / cs.COMPLIANCE_CSV),
        "reactive": _read_csv(run / cs.REACTIVE_CSV),
        "requirement": json.loads((run / cs.REQUIREMENT_JSON).read_text()),
    }


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _stale(project, d: Path) -> bool:
    manifest = d / "run" / cs.CAMPUS_MANIFEST
    campus = d / CAMPUS_FILE
    nc = project_registry.project_dir(project) / NETWORK_FILE
    if not manifest.is_file() or not campus.is_file() or not nc.is_file():
        return False
    m = json.loads(manifest.read_text())
    text = yaml.safe_dump(yaml.safe_load(campus.read_text()), sort_keys=False)
    return (hashlib.sha256(text.encode()).hexdigest() != m.get("campus_sha256")
            or _sha256_file(nc) != m.get("network_sha256"))


def get_state(project) -> dict:
    require_capacity_expansion(project)
    d = campus_dir(project)
    campus = d / CAMPUS_FILE
    notes = d / NOTES_FILE
    settings = d / SETTINGS_FILE
    results = _results(d / "run")
    return {
        "campus_yaml": campus.read_text() if campus.is_file() else None,
        "skipped": json.loads(notes.read_text()) if notes.is_file() else [],
        "profiles": profiles(project),
        "settings": json.loads(settings.read_text()) if settings.is_file() else None,
        "results": results,
        "stale": bool(results) and _stale(project, d),
    }


def draft(project, overwrite: bool = False) -> dict:
    """Write the campus file from the project's solved network."""
    require_capacity_expansion(project)
    nc = _network(project)
    d = campus_dir(project)
    if (d / CAMPUS_FILE).is_file() and not overwrite:
        raise HTTPException(status_code=409, detail="a campus file already exists; draft again with overwrite to replace your edits")
    with _netcdf_lock():
        try:
            result = cs.draft_from_project(nc)
        except ContractError as exc:
            raise _unprocessable(exc)
    text = yaml.safe_dump(result.spec, sort_keys=False)
    (d / CAMPUS_FILE).write_text(text)
    (d / NOTES_FILE).write_text(json.dumps(result.skipped))
    return {"campus_yaml": text, "skipped": result.skipped}


def save_campus(project, text: str) -> dict:
    """Validate the description by building it, then keep it."""
    require_capacity_expansion(project)
    if len(text.encode()) > MAX_CAMPUS_BYTES:
        raise HTTPException(status_code=413, detail=f"the campus file is larger than {MAX_CAMPUS_BYTES} bytes")
    try:
        spec = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=422, detail=f"the campus file is not valid YAML: {exc}")
    try:
        cs.check_campus(spec)
    except ContractError as exc:
        raise _unprocessable(exc)
    (campus_dir(project) / CAMPUS_FILE).write_text(text)
    return {"campus_yaml": text}


def _settings(raw: dict, known: dict) -> dict:
    s = {**DEFAULTS, **{k: v for k, v in (raw or {}).items() if v is not None or k == "pf"}}
    unknown = sorted(set(s) - set(DEFAULTS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown setting(s) {unknown}")
    k = s["k"]
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise HTTPException(status_code=422, detail=f"k must be a positive integer, got {k!r}")
    if s["pf"] is not None and not (isinstance(s["pf"], (int, float)) and 0 < s["pf"] <= 1):
        raise HTTPException(status_code=422, detail=f"pf must be in (0, 1], got {s['pf']!r}")
    if not isinstance(s["margin"], (int, float)) or not 0 <= s["margin"] <= 2:
        raise HTTPException(status_code=422, detail=f"margin must be between 0 and 2, got {s['margin']!r}")
    if s["profile"] not in known:
        raise HTTPException(status_code=422, detail=f"unknown grid-code profile {s['profile']!r}; known {sorted(known)}")
    s["n_minus_1"] = bool(s["n_minus_1"])
    return s


def run(project, settings: dict) -> dict:
    """Prepare, rank and size the campus; returns ``get_state``."""
    require_capacity_expansion(project)
    d = campus_dir(project)
    campus = d / CAMPUS_FILE
    if not campus.is_file():
        raise HTTPException(status_code=422, detail="there is no campus file yet; draft one from the project or save your own")
    s = _settings(settings, profiles(project))
    nc = _network(project)
    spec = yaml.safe_load(campus.read_text())
    run_dir = d / "run"
    with _lock(run_dir):
        try:
            with _netcdf_lock():
                cs.prepare_campus(run_dir, spec, nc)
            cs.rank_campus(run_dir, k=s["k"])
            cs.size_campus(run_dir, SizingCriteria(margin=float(s["margin"]), n_minus_1=s["n_minus_1"]),
                           profile=s["profile"], pf=s["pf"], profile_dirs=(grid_codes_dir(project),))
        except ContractError as exc:
            raise _unprocessable(exc)
        (d / SETTINGS_FILE).write_text(json.dumps(s))
    return get_state(project)
