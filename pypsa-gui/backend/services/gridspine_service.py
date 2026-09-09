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
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from db.models import Project
from services import project_acl
from services import project_registry

# `gridspine` is the repository's own package, installed EDITABLE into every
# pixi environment from the root `pyproject.toml` (increment-4 decision D5,
# taken in increment 5). It imports like any other package from any working
# directory; the `sys.path` insert that used to bridge `pypsa-gui/backend` to
# the repo root is gone, and `tests/test_gridspine_service.py` fails if it
# comes back.
try:
    from gridspine.drivers.readback import ingest_powerfactory_results as _ingest_readback
    from gridspine.drivers.readback import readback_status as _readback_status
    from gridspine.drivers.readback import result_figure as _result_figure
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

#: The per-project subdirectory holding everything gridspine. Named here
#: because the queue's dispatcher reaches it from an authorized `storage_dir`
#: with no Project row in hand.
GRIDSPINE_SUBDIR = "gridspine"

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
    path = project_registry.ensure_project_dir(project) / GRIDSPINE_SUBDIR
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

def config_for_dir(gs_dir, data: dict = None) -> StudyConfig:
    """The config for the study in `gs_dir`, from its own `config.json` unless
    `data` overrides it.

    Path-only: the queue's dispatcher has an authorized directory and no
    Project row, so this is the seam it runs through. `outdir` and
    `templates_overlay` are always derived from `gs_dir` — a stored absolute
    path survives a copy, a restore or a rename as a lie, and an overlay path
    from another project would apply that project's edits to this one.
    """
    gs_dir = Path(gs_dir)
    if data is None:
        stored = gs_dir / "config.json"
        data = json.loads(stored.read_text()) if stored.is_file() else {}
    data = dict(data)
    data["outdir"] = str(gs_dir / "run")
    overlay = gs_dir / "templates_overlay.json"
    data["templates_overlay"] = str(overlay) if overlay.is_file() else None
    try:
        return StudyConfig.from_json(data)
    except ContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def run_study_dir(gs_dir, progress=None, stop_event=None) -> dict:
    """Run the study in `gs_dir` to completion. The queue runner's entry point.

    Raises whatever the driver raises — `StudyAborted` on a stop, a
    `ContractError` on a config the run cannot honour — because the caller is
    a job runner that has to record which of those happened.
    """
    require_gridspine()
    gs_dir = Path(gs_dir)
    config = config_for_dir(gs_dir)
    run_study(config, progress=progress, stop_event=stop_event)
    return _stage_status(gs_dir / "run")


def _config_from(project, data: dict) -> StudyConfig:
    """Build the config with the project's OWN outdir, whatever the file says.

    A stored absolute path survives a copy, a restore and a rename as a lie;
    the run directory is derived from the project every time.
    """
    return config_for_dir(gridspine_dir(project), dict(data or {}))


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
    if data.get("from_network") is not None:
        raise HTTPException(
            status_code=422,
            detail="from_network is not set directly; choose a source project with "
                   "set_dispatch_source({'from_project': name}) after creation",
        )
    if data.get("from_dispatch") is not None:
        # There is no project yet to authorize the path against, and the row is
        # committed before the config is written — so a raw path here would be
        # stored unchecked. Same answer `from_network` gives.
        raise HTTPException(
            status_code=422,
            detail="from_dispatch is not set at creation; pick the source with "
                   "set_dispatch_source({'from_dispatch': dir}) afterwards",
        )
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


#: The config fields a caller may change after creation. `outdir` and
#: `templates_overlay` are derived from the project and never accepted.
EDITABLE_CONFIG_FIELDS = frozenset({
    "hours", "k", "window", "overlap", "screen", "n2_prune_threshold_pct", "from_dispatch",
})


def get_config(project, db=None) -> dict:
    """The study config as the next run will use it (project-derived paths
    included). With `db`, also `from_project`: the NAME of the project whose
    network `from_network` is, so the UI and the copilot can say which project
    rather than which path."""
    require_planning(project)
    data = read_config(project).to_json()
    data["from_project"] = _project_for_network(db, project, data.get("from_network"))
    return data


def _active_job_for(project):
    from services import solve_queue as sq

    return next(
        (j for j in sq.solve_queue.list_jobs()
         if j.get("project_id") == project.name and j.get("status") in ("queued", "running")),
        None,
    )


def _normalized(value) -> str:
    """An absolute, `..`-free spelling of `value` — WITHOUT touching the disk.

    `os.path.abspath` only joins the process CWD; `normpath` is string
    arithmetic. Neither reads the named path, which is the point: this runs on
    caller-supplied input before anything has authorized it. A value that is
    not a path at all (the chat tool takes `**patch`, so a model can put a
    list or an object there) stringifies into something that matches no
    project, which is the refusal we want.
    """
    return os.path.normpath(os.path.abspath(str(value)))


def _authorized_dispatch_dir(db, user, project, raw) -> Path:
    """The finished study directory `from_dispatch` names — resolved back to a
    PROJECT THE CALLER MAY READ, and returned as the path derived from that
    project's row rather than as the string the caller sent.

    Same reasoning `set_dispatch_source` states for `from_project`, applied to
    the older field: a raw path would let a caller read any directory the
    server can, and the contents come back to them through the ranked metrics
    and the handoff bundle. Every real study directory is
    `<project dir>/gridspine/run`, so that — for a project the ACL admits — is
    the only shape accepted. CodeQL `py/path-injection` flagged the two reads
    this replaces, and it was right.

    The refusal is one message for "no such directory", "not a project's" and
    "not yours": the same reason `resolve_project` answers 404 for all three,
    so a study cannot be used to probe what else is on the disk.

    SPELLINGS ARE COMPARED, NOT RESOLVED TARGETS. `Path(raw).resolve()` walks
    the caller's path and reads its symlinks — a filesystem access on input
    that has not been authorized yet, which is what CodeQL flagged the second
    time round. `_normalized` is pure string arithmetic, so the raw value
    never reaches a path expression at all. The cost is that a SYMLINK to a
    run directory the caller may read is refused too; that is the right
    trade, and `tests/test_gridspine_service.py` pins both halves of it.
    """
    if db is None or user is None:
        raise HTTPException(status_code=422, detail="from_dispatch needs an acting user")
    refused = HTTPException(
        status_code=422,
        detail=f"'{raw}' is not the study directory of a project you can read; a dispatch "
               f"source is the `gridspine/run` directory of one of your studies",
    )
    target = _normalized(raw)
    for row in db.query(Project).filter(Project.org_id == project.org_id):
        candidate = project_registry.project_dir(row) / GRIDSPINE_SUBDIR / "run"
        if _normalized(candidate) == target and project_acl.can_access_project(db, user, row):
            return candidate
    raise refused


def _finished_dispatch_dir(db, user, project, raw) -> Path:
    """`_authorized_dispatch_dir`, plus the two files a resumed study reads.
    Authorization first: a caller must not learn what a directory contains by
    reading the difference between the two refusals."""
    src = _authorized_dispatch_dir(db, user, project, raw)
    missing = [n for n in ("dispatch.csv", "loads.csv") if not (src / n).is_file()]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"{src} is not a finished study directory: missing {', '.join(missing)}",
        )
    return src


def update_config(project, patch: dict, *, db=None, user=None) -> dict:
    """Change some of the study config after creation. Validated as a whole
    through StudyConfig (422 on a bad value, nothing written), refused while a
    job for this project is queued or running (409): the queue snapshotted the
    directory, not the config, so an edit mid-run would change what the running
    job reads at its next stage.

    `db` and `user` are needed only for `from_dispatch`, which is
    authorization-bearing (`_authorized_dispatch_dir`); a patch without it is
    the plain-argument call the parity rule asks for.
    """
    require_planning(project)
    if not isinstance(patch, dict):
        raise HTTPException(status_code=422, detail="config patch must be an object")
    unknown = sorted(set(patch) - EDITABLE_CONFIG_FIELDS)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"config field(s) not editable: {unknown}; editable: {sorted(EDITABLE_CONFIG_FIELDS)}",
        )
    if _active_job_for(project) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Project '{project.name}' has a queued or running study; abort it before changing the config.",
        )
    current = read_config(project).to_json()
    merged = {**current, **patch}
    if patch.get("from_dispatch"):
        src = _finished_dispatch_dir(db, user, project, patch["from_dispatch"])
        merged["from_dispatch"] = str(src)       # the resolved path, never the caller's string
        merged["from_network"] = None            # one dispatch source at a time
    config = _config_from(project, merged)          # validates; 422 on a bad value
    return _write_config(project, config)


NETWORK_FILE = "network.nc"


def _source_project(db, user, project, name: str):
    """The capacity-expansion project whose saved network a study will read,
    resolved under the ACTING USER — the same 404 for unknown and foreign that
    every other lookup gives, so the study cannot be used to probe names."""
    if user is None:
        raise HTTPException(status_code=422, detail="from_project needs an acting user")
    row = project_registry.resolve_project(db, user, name)
    if row.id == project.id:
        raise HTTPException(status_code=422, detail="a study cannot be its own dispatch source")
    if kind_of(row) != CAPACITY_EXPANSION:
        raise HTTPException(
            status_code=422,
            detail=f"Project '{row.name}' is a {kind_of(row)} project; the dispatch source "
                   f"must be a capacity-expansion project with a solved, saved network",
        )
    return row


def _require_solved_network(nc: Path, row) -> None:
    """A saved network with a dispatch in it. The identity map itself is the
    driver's check (`producers.tables_from_network`, in the dispatch stage);
    the two failures a user is most likely to make — never saved, never
    solved — are answered here, before anything is written."""
    if not nc.is_file():
        raise HTTPException(
            status_code=422,
            detail=f"Project '{row.name}' has no saved network yet — save it first",
        )
    import pypsa
    from services.pypsa_service import PyPSAService

    with PyPSAService.get_netcdf_io_lock():
        n = pypsa.Network(str(nc))
    if n.generators_t.p.empty:
        raise HTTPException(
            status_code=422,
            detail=f"Project '{row.name}' is not solved — solve it and save, then pick it as the source",
        )


def _project_for_network(db, project, path):
    """The org project whose saved network is `path`, by name; None when the
    path is not one of them (a config written by hand, or a deleted project)."""
    if db is None or not path:
        return None
    target = Path(path)
    for row in db.query(Project).filter(Project.org_id == project.org_id):
        if project_registry.project_dir(row) / NETWORK_FILE == target:
            return row.name
    return None


def set_dispatch_source(db, project, source, user=None) -> dict:
    """`"generate"` to solve the unit commitment; `{"from_dispatch": dir}` to
    study a finished run's dispatch (drivers F3); `{"from_project": name}` to
    study the SOLVED NETWORK of one of the caller's capacity-expansion projects
    (increment 5, D3). One source at a time: setting any clears the others.

    `from_project` is the only way to point a study at a network. A raw path
    would let a caller read any file the server can; the project route goes
    through the ACL and the kind check. `from_dispatch` IS a path, for
    historical reasons, and is held to the same line by
    `_authorized_dispatch_dir`: it is accepted only when it resolves to the
    run directory of a project the caller may read.
    """
    require_planning(project)
    base = {**read_config(project).to_json(), "from_dispatch": None, "from_network": None}
    if source == "generate" or source is None:
        updated = StudyConfig.from_json(base)
    elif isinstance(source, dict) and "from_dispatch" in source:
        src = _finished_dispatch_dir(db, user, project, source["from_dispatch"])
        updated = StudyConfig.from_json({**base, "from_dispatch": str(src)})
    elif isinstance(source, dict) and "from_project" in source:
        row = _source_project(db, user, project, str(source["from_project"] or ""))
        nc = project_registry.project_dir(row) / NETWORK_FILE
        _require_solved_network(nc, row)
        updated = StudyConfig.from_json({**base, "from_network": str(nc)})
    else:
        raise HTTPException(
            status_code=422,
            detail='dispatch source must be "generate", {"from_dispatch": "<directory>"} '
                   'or {"from_project": "<project name>"}',
        )
    return _write_config(project, updated)


def run_pipeline(db, project, user=None, *, queued: bool = True,
                 progress=None, stop_event=None) -> dict:
    """Run the study — through the SOLVE QUEUE by default.

    The spec asks for exactly one job system ("chat-triggered runs go through
    the same solve queue — status, abort included"), so a run is a job of kind
    `gridspine`: one at a time, abortable, its log on the existing SSE stream,
    its status surviving a restart. Returns the job's public view.

    `queued=False` runs it here and returns the stage status instead. That is
    for callers that want the answer rather than a job — the CLI, and tests
    that would otherwise have to poll a background thread.

    The job carries the authorized `storage_dir` resolved HERE, where the
    caller's ACL has been checked; the dispatcher never derives a path from a
    project name.
    """
    require_planning(project)
    if not queued:
        config = read_config(project)
        try:
            run_study(config, progress=progress, stop_event=stop_event)
        except ContractError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return get_stage_status(project)

    from services import solve_queue as sq

    # Validate before queueing: a job that cannot start is worse than an
    # error, because it fails on a worker thread minutes later.
    read_config(project)
    job, _created = sq.solve_queue.enqueue_unique(
        project.name,
        project_key=project_registry.registry_key(project),
        storage_dir=str(project_registry.ensure_project_dir(project)),
        kind=sq.KIND_GRIDSPINE,
    )
    if _created:
        try:
            from services import solve_job_store

            solve_job_store.record_enqueued(
                job,
                enqueued_by_user_id=getattr(user, "id", None),
                solver_config_json=None,
            )
        except Exception:  # noqa: BLE001 - durability is an upgrade, not a precondition
            pass
    # `get_job` already answers with the public view AND the live queue
    # position; recomputing it here would be a second implementation of
    # `_position_locked` that could disagree with the listing endpoint.
    return sq.solve_queue.get_job(job.id) or job.to_public(None)


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


def uploads_dir(project, hour: int) -> Path:
    """`<project dir>/gridspine/uploads/h<hour>`, created: where the engineer's
    PowerFactory exports land before the driver reads them into the bundle."""
    path = gridspine_dir(project) / "uploads" / f"h{int(hour)}"
    path.mkdir(parents=True, exist_ok=True)
    return path


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(name, fallback: str) -> str:
    """The client's basename with anything path-like removed; the fallback when
    nothing usable is left. The summary records it as the upload's name."""
    base = Path(str(name or "")).name
    cleaned = _SAFE_NAME.sub("_", base).strip("._")
    return cleaned if cleaned and cleaned.lower().endswith(".csv") else fallback


def upload_readback(project, hour: int, bus_csv: bytes, bus_name=None,
                    branch_csv: bytes = None, branch_name=None) -> dict:
    """Spec stage 6: read a PowerFactory export back against hour `hour`'s
    handoff bundle. The bus CSV is required (the phase-1 gate), the branch CSV
    optional; both are kept byte-for-byte under the study's uploads and in the
    bundle. The comparison's refusals (no bundle for the hour, a load flow that
    did not converge, a wrong header, a bus or branch set that disagrees) are
    422 with the driver's message — they are the engineer's to fix."""
    require_planning(project)
    hour = int(hour)
    target = uploads_dir(project, hour)
    bus_path = target / _safe_filename(bus_name, "pf_bus.csv")
    bus_path.write_bytes(bus_csv)
    branch_path = None
    if branch_csv is not None:
        branch_path = target / _safe_filename(branch_name, "pf_branches.csv")
        if branch_path == bus_path:
            branch_path = target / "pf_branches.csv"
        branch_path.write_bytes(branch_csv)
    try:
        return _ingest_readback(run_dir(project), hour, bus_path, branch_path)
    except ContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def get_readback(project) -> dict:
    """{hour: summary} for every bundle hour with a read-back — keys are
    strings, as the status payload's are."""
    require_planning(project)
    return {str(h): s for h, s in _readback_status(run_dir(project)).items()}


def fetch_result_figure(project, name: str, hour: int) -> dict:
    """One read-back comparison as data (`vm`, `va`, `branch_p`, `branch_q`)
    for hour `hour`; `available: False` with the reason when nothing has been
    uploaded for that hour yet. An unknown figure is 422, an hour with no
    bundle 404."""
    require_planning(project)
    try:
        return _result_figure(run_dir(project), int(hour), name)
    except ContractError as exc:
        status = 404 if "no handoff bundle" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc


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
