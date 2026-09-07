"""The gridspine action layer: one function per study operation.

Increment 4, task 3, and the spec's parity rule made structural — "every study
operation is a backend function first (`create_study`, `run_pipeline`,
`list_ranked_snapshots`, `edit_template_param`, `get_assumption_ledger`,
`export_handoff_bundle`, …); UI endpoints and chat tools are both thin
wrappers". Everything here takes plain arguments and returns JSON-able data:
no `Request`, no `Depends`, no HTTP. A router that wants to do more than
resolve auth and call one of these is writing a second implementation.

`drivers/` IS THE ONLY SURFACE. This module imports `gridspine.drivers`,
`gridspine.schema` and `gridspine.templates` and nothing else — no
`static/`, no `producers/`, no `handoff/` internals — so the engine cage the
spec draws around gridspine holds from the backend side too, and swapping a
solver stays a gridspine-internal change. `tests/test_gridspine_service.py`
asserts it with an AST scan.

A gridspine study is a PROJECT of kind `planning_dynamics` (CONTEXT.md: the
vocabulary is Project, never "study" as an entity), with its artifacts under
`<project dir>/gridspine/`:

    gridspine/config.json             the StudyConfig this project runs
    gridspine/templates_overlay.json  per-project template edits, with provenance
    gridspine/run/                    the study directory drivers/ writes

Nothing here is derived from a stored path: the run directory is always
`<project dir>/gridspine/run`, so a project that is copied, restored from a
bundle or moved by a rename keeps working.
"""
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from services import project_registry

# --------------------------------------------------------------------------
# `gridspine` lives at the REPO ROOT, beside `pypsa-gui/`, and this repository
# packages nothing — `pixi run gridspine-tests` works only because it runs from
# the root, and the backend runs from `pypsa-gui/backend`. The same explicit
# insert `tests/conftest.py` already does for the backend directory, in the one
# module that needs it, rather than a hidden PYTHONPATH the desktop build would
# have to reproduce by accident.
#
# THIS IS A STOPGAP, and it is the increment's open decision D5: the real fix
# is to give gridspine a pyproject and install it, which touches pixi.toml and
# the lockfile and should be done deliberately, not as a side effect of wiring
# a service.
# --------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from gridspine.drivers.status import ledger_entries as _ledger_entries
    from gridspine.drivers.status import ranked_snapshots as _ranked_snapshots
    from gridspine.drivers.status import stage_status as _stage_status
    from gridspine.drivers.study import StudyConfig, run_study
    from gridspine.schema.contracts import ContractError
    from gridspine.templates.unit_params import load_unit_templates, provenance_counts

    GRIDSPINE_AVAILABLE = True
    GRIDSPINE_IMPORT_ERROR = None
except ImportError as exc:                      # pragma: no cover - build-shape branch
    # The guard is not defensive noise: the frozen desktop app is built from a
    # pip venv (`gui-requirements.txt`) that ships neither `gridspine` nor its
    # engines (pandapower, lightsim2grid), so on that build this import really
    # does fail. Every action then answers 503 with this message instead of
    # 500ing on an AttributeError three frames down.
    GRIDSPINE_AVAILABLE = False
    GRIDSPINE_IMPORT_ERROR = str(exc)

    class ContractError(Exception):
        """Stand-in so the `except ContractError` clauses below still parse."""

    StudyConfig = run_study = None
    _stage_status = _ranked_snapshots = _ledger_entries = None
    load_unit_templates = provenance_counts = None


def require_gridspine() -> None:
    """503 rather than 500 when this build does not carry gridspine."""
    if not GRIDSPINE_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail=(
                "The planning → dynamics pipeline is not available in this build: "
                f"{GRIDSPINE_IMPORT_ERROR}. gridspine and its engines (pandapower, "
                "lightsim2grid) are not part of the packaged app yet."
            ),
        )


#: Project kinds. NULL in the database means the default — every project that
#: existed before this column is a capacity-expansion project.
CAPACITY_EXPANSION = "capacity_expansion"
PLANNING_DYNAMICS = "planning_dynamics"
CONNECTION = "connection"
PROJECT_KINDS = (CAPACITY_EXPANSION, PLANNING_DYNAMICS, CONNECTION)

#: Who made a template edit. The spec asks for exactly this distinction so the
#: ledger audits both interfaces: chat edits carry `chat`, UI edits `user`.
EDITORS = ("user", "chat")


# --------------------------------------------------------------------------
# paths and kinds
# --------------------------------------------------------------------------

def kind_of(project) -> str:
    return getattr(project, "project_kind", None) or CAPACITY_EXPANSION


def require_planning(project):
    """Every action starts here. A capacity-expansion project reaching a
    gridspine function is a caller bug, and it must not leave a `gridspine/`
    directory behind in an unrelated project."""
    require_gridspine()
    if kind_of(project) != PLANNING_DYNAMICS:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Project '{project.name}' is a {kind_of(project)} project; "
                f"this operation needs a planning → dynamics project."
            ),
        )
    return project


def gridspine_dir(project) -> Path:
    """`<project dir>/gridspine`, created. Callers here are always about to write."""
    path = project_registry.ensure_project_dir(project) / "gridspine"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_dir(project) -> Path:
    return gridspine_dir(project) / "run"


def overlay_path(project) -> Path:
    return gridspine_dir(project) / "templates_overlay.json"


def config_path(project) -> Path:
    return gridspine_dir(project) / "config.json"


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

def _config_from(project, data: dict) -> StudyConfig:
    """Build the config with the project's OWN outdir, whatever the file says.

    A stored absolute path survives a copy, a restore and a rename as a lie;
    the run directory is derived from the project every time.
    """
    data = dict(data or {})
    data["outdir"] = str(run_dir(project))
    # The overlay is this project's, always — a stored path from a copied or
    # restored project would apply another study's edits to this one.
    overlay = overlay_path(project)
    data["templates_overlay"] = str(overlay) if overlay.is_file() else None
    try:
        return StudyConfig.from_json(data)
    except ContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def read_config(project) -> StudyConfig:
    path = config_path(project)
    stored = json.loads(path.read_text()) if path.is_file() else {}
    return _config_from(project, stored)


def _write_config(project, config: StudyConfig) -> dict:
    data = config.to_json()
    config_path(project).write_text(json.dumps(data, indent=2))
    return data


# --------------------------------------------------------------------------
# actions
# --------------------------------------------------------------------------

def create_study(db, user, name: str, config: dict = None, kind: str = PLANNING_DYNAMICS) -> dict:
    """A new project of `kind`, with its gridspine config written.

    The config is validated BEFORE the row is committed: a study nobody can run
    should not exist, and a half-created project is worse than an error.
    """
    require_gridspine()
    if kind != PLANNING_DYNAMICS:
        raise HTTPException(status_code=422, detail=f"create_study makes {PLANNING_DYNAMICS} projects, not {kind!r}")
    data = dict(config or {})
    data["outdir"] = "."          # replaced by the project's own directory below
    try:
        StudyConfig.from_json(data)
    except ContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    project = project_registry.create_root(db, user, name)
    project.project_kind = kind
    db.commit()
    db.refresh(project)
    written = _write_config(project, _config_from(project, data))
    return {
        "id": str(project.id),
        "name": project.name,
        "kind": kind_of(project),
        "config": written,
        "status": get_stage_status(project),
    }


def set_dispatch_source(db, project, source) -> dict:
    """`"generate"` to solve the unit commitment, or `{"from_dispatch": dir}`
    to study a finished run's dispatch (drivers F3)."""
    require_planning(project)
    config = read_config(project)
    if source == "generate" or source is None:
        updated = StudyConfig.from_json({**config.to_json(), "from_dispatch": None})
    elif isinstance(source, dict) and "from_dispatch" in source:
        src = Path(source["from_dispatch"])
        missing = [n for n in ("dispatch.csv", "loads.csv") if not (src / n).is_file()]
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"{src} is not a finished study directory: missing {', '.join(missing)}",
            )
        updated = StudyConfig.from_json({**config.to_json(), "from_dispatch": str(src)})
    else:
        raise HTTPException(
            status_code=422,
            detail='dispatch source must be "generate" or {"from_dispatch": "<directory>"}',
        )
    return _write_config(project, updated)


def run_pipeline(db, project, progress=None, stop_event=None) -> dict:
    """Run the study to completion, here on this thread.

    Task 4 moves the call into the solve queue; the signature already carries
    what the queue gives a job, so that change is a runner, not a rewrite.
    """
    require_planning(project)
    config = read_config(project)
    try:
        run_study(config, progress=progress, stop_event=stop_event)
    except ContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return get_stage_status(project)


def get_stage_status(project) -> dict:
    """Per-stage state read from the artifacts — valid after a restart."""
    require_planning(project)
    return _stage_status(run_dir(project))


def list_ranked_snapshots(project) -> list:
    """The selected hours with their reasons and every ranking metric."""
    require_planning(project)
    try:
        table = _ranked_snapshots(run_dir(project))
    except ContractError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    rows = json.loads(table.to_json(orient="records"))
    return rows


def get_assumption_ledger(project) -> dict:
    """The ledger AS DATA, plus this project's own template edits.

    A study that has not run yet still has a ledger worth showing — the
    provenance of the templates it WILL use, including anything already edited.
    Answering "what am I assuming?" before the first run is exactly when the
    question is cheapest to act on, so this falls back to the template library
    rather than refusing.
    """
    require_planning(project)
    try:
        ledger = _ledger_entries(run_dir(project))
    except ContractError:
        overlay = overlay_path(project)
        templates = load_unit_templates(overlay=overlay if overlay.is_file() else None)
        counts = provenance_counts(templates)
        ledger = {
            "entries": [],
            "provenance_counts": {k: int(counts.get(k, 0)) for k in ("measured", "datasheet", "assumed")},
            "measurements": {},
            "hour": None,
            "from_run": False,
        }
    else:
        ledger["from_run"] = True
    ledger["edits"] = _edits(project)
    return ledger


def edit_template_param(project, unit_id: str, param: str, value, source: str, edited_by: str) -> dict:
    """Record one template edit in this project's overlay, with provenance.

    The shipped `templates/data/*.yaml` is never written: two projects may
    disagree about a machine and each keeps its own value and its own audit
    trail. `edited_by` is `user` or `chat` — the spec's ledger provenance,
    which is what makes the audit cover both interfaces for free.
    """
    require_planning(project)
    if edited_by not in EDITORS:
        raise HTTPException(status_code=422, detail=f"edited_by must be one of {list(EDITORS)}")
    overlay = _read_overlay(project)
    candidate = json.loads(json.dumps(overlay))          # deep copy, JSON-shaped
    candidate.setdefault(unit_id, {})[param] = {"value": value, "source": source}
    if unit_id not in _unit_ids():
        raise HTTPException(status_code=404, detail=f"no template for unit {unit_id!r}")
    try:
        # Validated by LOADING the edited library, so an edit that breaks the
        # physics checks fails here rather than in a .dyr three stages later.
        load_unit_templates(overlay=candidate)
    except ContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    entry = {
        "value": float(value),
        "source": source,
        "edited_by": edited_by,
        "at": datetime.now(tz=timezone.utc).isoformat(),
    }
    overlay.setdefault(unit_id, {})[param] = entry
    overlay_path(project).write_text(json.dumps(overlay, indent=2))
    return {"unit_id": unit_id, "param": param, **entry}


def export_handoff_bundle(project, hour: int) -> Path:
    """Zip one selected hour's bundle. Returns the path; the download is the
    router's business."""
    require_planning(project)
    hour = int(hour)
    bundle = run_dir(project) / f"bundle_h{hour}"
    if not (bundle / "manifest.json").is_file():
        raise HTTPException(
            status_code=404,
            detail=f"no handoff bundle for hour {hour} in this study",
        )
    target = gridspine_dir(project) / f"bundle_h{hour}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=str(Path(bundle.name) / path.relative_to(bundle)))
    return target


def fetch_result_figure(project, name: str) -> dict:
    """Read-back figures are spec phase 4. Typed, so the tool surface is
    complete and the answer is a fact rather than a missing endpoint."""
    require_planning(project)
    return {
        "available": False,
        "name": name,
        "reason": (
            "PowerFactory read-back is not implemented yet (spec phase 4): result "
            "figures come from exported PowerFactory CSVs, which are uploaded per "
            "study once the read-back stage exists."
        ),
    }


# --------------------------------------------------------------------------
# internals
# --------------------------------------------------------------------------

def _read_overlay(project) -> dict:
    path = overlay_path(project)
    return json.loads(path.read_text()) if path.is_file() else {}


def _edits(project) -> list:
    out = []
    for unit_id, params in sorted(_read_overlay(project).items()):
        for param, entry in sorted(params.items()):
            out.append({"unit_id": unit_id, "param": param, **entry})
    return out


def _unit_ids() -> set:
    return set(load_unit_templates().units.index)
