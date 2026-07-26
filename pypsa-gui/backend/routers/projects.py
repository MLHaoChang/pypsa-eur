from __future__ import annotations

import io
import json
import logging
import os
import pathlib
import pickle
import re
import shutil
import stat
import sys
import time
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession
from db.models import User
from db.session import get_db
from deps import optional_user
from models.schemas import (
    CreateScenarioRequest,
    ImportSummary,
    ProjectInfo,
    RenameProjectRequest,
)
from services import change_log_service
from services.dispatch_status import network_has_dispatch
from services.project_context import RESULT_STATE_KEYS
from services.pypsa_service import PyPSAService
from settings import get_settings
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter()


class MembersRequest(BaseModel):
    """Body for PUT /{id_or_name}/members — the desired member user-id set."""

    user_ids: list[str] = []


def _auth_enabled() -> bool:
    return get_settings().pypsa_gui_auth_enabled

PROJECTS_DIR = pathlib.Path(__file__).parent.parent / "projects"

# Files that make up a project bundle (.pypsaproj.zip).
# `layout.json` holds the blank-canvas schematic layout — the "latent
# coordinates" (node positions + edge waypoints). It is deliberately part of
# the bundle so the schematic travels with the project across machines, but
# it is NEVER part of the network model / network API: it is pure
# presentation state, decoupled from the geographic bus.x/y the map view and
# clustering consume.
_BUNDLE_FILES = ("network.nc", "user_ts.json", "solver_config.json", "metadata.json", "layout.json", "results_state.pkl")

# Per-project subdirectories that travel alongside the bundle FILES on every
# project-to-project transition. Chatbot uploads (Phase A) live here under
# `uploads/<file_id>/`. The transition table (Save-As, Save-a-Copy, scenario
# copy, snapshot create + restore) is invoked via `_copy_bundle_dirs(src, dst)`
# from each respective call site; routine re-save (loaded == target) skips it
# because the source and destination ARE the same dir. The legacy bundle-file
# loop already handles the small files; bundling dirs separately keeps it
# robust to growth (e.g. agent_export PNGs accumulating in uploads/).
_BUNDLE_DIRS = ("uploads",)

# Cap on the serialized blank-canvas layout document. Even a large network's
# schematic is a few hundred KB of coordinates; 4 MB bounds a malformed or
# abusive payload without ever clipping a legitimate one.
_MAX_LAYOUT_BYTES = 4 * 1024 * 1024

# Allowed characters in a project name. Anchored, so any path separator,
# null byte, or shell metacharacter is rejected. Length capped so a single
# huge name can't fill the FS or overflow path-length limits on Windows.
_PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9_\-. ]{1,64}$")


def _rm_onexc(func, path, _exc):  # noqa: ANN001 — shutil callback signature
    """
    shutil.rmtree error callback. On Windows a read-only attribute — set
    by OneDrive's sync engine, an AV scan, or the netcdf writer — makes
    ``os.unlink`` / ``os.rmdir`` raise ``PermissionError`` (WinError 5). Clear
    the read-only bit and retry the failing op. If it STILL fails the handle
    is genuinely locked, so re-raise and let `_force_rmtree`'s retry loop back
    off and try the whole tree again.
    """
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    func(path)  # re-raises if the path is still locked


# `onexc` superseded `onerror` in Python 3.12; the callback body is identical
# (it only uses func + path) so one function serves both — we just pass it
# under whichever keyword the running interpreter accepts.
_RMTREE_KW = (
    {"onexc": _rm_onexc} if sys.version_info >= (3, 12) else {"onerror": _rm_onexc}
)


def _force_rmtree(target: pathlib.Path) -> None:
    """
    `shutil.rmtree` hardened for Windows / OneDrive-synced paths.

    Handles the two failure modes that broke plain `rmtree` here:
      * read-only files / dirs — cleared via the `_rm_onexc` callback;
      * transient locks (OneDrive sync handle, AV scan, an open file) — the
        whole call is retried a few times with a short backoff.

    Raises the final ``OSError`` if the tree genuinely can't be removed.
    """
    last: OSError | None = None
    for attempt in range(4):
        try:
            shutil.rmtree(target, **_RMTREE_KW)
            return
        except OSError as exc:  # noqa: PERF203 — retry is the whole point
            last = exc
            if attempt < 3:
                # 0.4s → 0.8s → 1.2s — long enough for a OneDrive/AV handle
                # to clear, short enough that a real lock fails fast-ish.
                time.sleep(0.4 * (attempt + 1))
    if last is not None:
        raise last


def _copy_bundle_dirs(src: pathlib.Path, dst: pathlib.Path) -> None:
    """
    Recursively copy each `_BUNDLE_DIRS` subdir from `src` to `dst`,
    replacing any existing destination dir of the same name. No-op for
    sources that don't exist (legacy projects predate uploads/).

    Used by Save-As / Save-a-Copy / create_scenario / snapshot create.
    Snapshot restore wraps the call inside `_force_rmtree(dst / dname)`
    first so a stale upload from a post-snapshot session is dropped
    before the snapshot's bundle dirs are reinstated.

    Errors are swallowed individually per dir so a failure copying
    one dir (e.g. an in-use file in uploads/) doesn't abort the
    project save itself — a future Save will retry.
    """
    import shutil as _shutil
    for dname in _BUNDLE_DIRS:
        src_d, dst_d = src / dname, dst / dname
        if not src_d.exists():
            continue
        try:
            if dst_d.exists():
                _force_rmtree(dst_d)
            _shutil.copytree(str(src_d), str(dst_d))
        except OSError as exc:
            logger.warning(
                "bundle: copy_bundle_dir %s → %s failed: %s",
                src_d, dst_d, exc,
            )


def _safe_project_dir(name: str) -> pathlib.Path:
    r"""
    Return PROJECTS_DIR/name only if name is a safe, non-traversing token.

    Two checks: (1) the name must match `_PROJECT_NAME_RE` (no `/`, no `\\`,
    no `..`, no leading `.`); (2) the resolved path must live under
    PROJECTS_DIR. Both are belt-and-suspenders — defeating either is a P0
    security incident.
    """
    if not isinstance(name, str) or not _PROJECT_NAME_RE.match(name):
        raise HTTPException(
            400,
            f"Invalid project name: {name!r}. Allowed: letters, digits, "
            f"underscore, hyphen, dot, space (max 64 chars).",
        )
    # Reject names that start with a dot — would create hidden dirs on POSIX
    # and round-trip awkwardly through Windows Explorer.
    if name.startswith(".") or name in ("..", "."):
        raise HTTPException(400, f"Project name may not start with '.': {name!r}")
    dest = (PROJECTS_DIR / name).resolve()
    try:
        # is_relative_to was added in 3.9; this codebase targets 3.13 so it's fine.
        if not dest.is_relative_to(PROJECTS_DIR.resolve()):
            raise HTTPException(400, "Path traversal rejected.")
    except ValueError:
        raise HTTPException(400, "Path traversal rejected.")
    return dest


def _meta_path(project_dir: pathlib.Path) -> pathlib.Path:
    return project_dir / "metadata.json"


def _atomic_write_with(path: pathlib.Path, writer) -> None:
    """
    Run `writer(tmp_path)` then atomically rename to `path`.

    Survives crashes mid-write: if the process is killed before `os.replace`,
    the original `path` is untouched (intact previous-version of the file)
    and a stale `path.tmp` sibling is left behind for diagnostic / recovery.

    `os.replace` is atomic on POSIX and on NTFS (Windows) — both are
    journalled filesystems that swap the directory entry in a single op.
    """
    import os
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        writer(tmp)
        os.replace(str(tmp), str(path))
    except Exception:
        # Clean up the tmp file so the project dir doesn't accumulate junk
        # after a write failure. The original `path` is untouched either way.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def _atomic_write_text(path: pathlib.Path, text: str) -> None:
    """Atomic-replace shortcut for JSON / config writes."""
    _atomic_write_with(path, lambda p: p.write_text(text))


# Keys persisted via `results_state.pkl` — must match the save block in
# `save_project`. The canonical list now lives in services/project_context.py
# (RESULT_STATE_KEYS) as the single source of truth shared with the solver-state
# shape, so the persistence layer and the state definition can't drift. Aliased
# here under the historical name used across this module + the test harness.
_RESULTS_STATE_KEYS = RESULT_STATE_KEYS


# Schema version for the results_state.pkl payload. Bump when the shape of the
# persisted side-results changes incompatibly; readers reject unknown versions
# (skip + warn) rather than misread a newer layout. v1 = the first versioned
# envelope `{"__schema__": 1, "data": {<_RESULTS_STATE_KEYS>}}`. Bundles saved
# BEFORE versioning are a bare dict (no "__schema__") and still load via the
# legacy branch in `_unwrap_results_state`.
_RESULTS_STATE_SCHEMA = 1


# ── Restricted unpickler for results_state.pkl ────────────────────────────────
# `results_state.pkl` can arrive from an UNTRUSTED source: a shared
# `.pypsaproj.zip` imported via `import_bundle` writes the bundle's pickle to the
# project dir, and `pickle.loads` on attacker-controlled bytes is arbitrary code
# execution (a malicious `__reduce__` invokes e.g. os.system). This unpickler
# allows ONLY an EXACT, enumerated set of pure-data reconstruction callables that
# a legitimate results_state (DataFrames/Series + their Index/array internals +
# plain scalars) needs, and refuses every other global. Allowlisting by exact
# `(module, name)` — NOT by namespace prefix — is essential: a prefix like
# `pandas.*` would re-admit `pandas.read_pickle` / `pandas.eval` (which re-invoke
# the UNRESTRICTED stock unpickler / evaluate expressions) and reopen the RCE.
# The set was derived empirically across many DataFrame shapes (every freq,
# Datetime/Range/Multi index, object dtype, tz-aware, datetime/Decimal scalars).
# Applied at EVERY read site, so even a malicious pkl persisted to disk by an
# import is inert on load. A blocked/corrupt payload raises and the callers
# degrade gracefully (warn + no cached results — the netcdf still carries the LP
# dispatch, which is re-derivable). A rare un-enumerated legit class also fails
# safe (results dropped, never executed).
_SAFE_UNPICKLE_GLOBALS = frozenset({
    ("builtins", "slice"),
    ("datetime", "date"), ("datetime", "datetime"), ("datetime", "time"),
    ("datetime", "timedelta"), ("datetime", "timezone"),
    ("decimal", "Decimal"),
    ("numpy", "dtype"), ("numpy", "ndarray"),
    ("numpy._core.multiarray", "_reconstruct"),   # numpy >= 2
    ("numpy.core.multiarray", "_reconstruct"),    # numpy < 2
    ("numpy._core.multiarray", "scalar"),         # numpy scalars (np.float64, …)
    ("numpy.core.multiarray", "scalar"),
    ("pandas._libs.arrays", "__pyx_unpickle_NDArrayBacked"),
    ("pandas._libs.internals", "_unpickle_block"),
    ("pandas.core.arrays.datetimes", "DatetimeArray"),
    ("pandas.core.dtypes.dtypes", "DatetimeTZDtype"),
    ("pandas.core.frame", "DataFrame"),
    ("pandas.core.series", "Series"),
    ("pandas.core.indexes.base", "Index"),
    ("pandas.core.indexes.base", "_new_Index"),
    ("pandas.core.indexes.datetimes", "DatetimeIndex"),
    ("pandas.core.indexes.datetimes", "_new_DatetimeIndex"),
    ("pandas.core.indexes.multi", "MultiIndex"),
    ("pandas.core.indexes.range", "RangeIndex"),
    ("pandas.core.internals.managers", "BlockManager"),
    ("pandas.core.internals.managers", "SingleBlockManager"),
})
_SAFE_UNPICKLE_COPYREG = frozenset({"_reconstructor", "__newobj__", "__newobj_ex__"})
# DatetimeIndex/PeriodIndex carry a freq offset (Hour, Day, MonthEnd, …) from
# this module. Rather than enumerate every offset class, allow this module but
# ONLY for genuine DateOffset subclasses (all pure data) — so any freq works
# while any non-offset callable in the module is still refused.
_SAFE_UNPICKLE_OFFSETS_MODULE = "pandas._libs.tslibs.offsets"


class _SafeResultsUnpickler(pickle.Unpickler):
    """Unpickler that admits only an exact set of pure-data classes."""

    def find_class(self, module: str, name: str):  # type: ignore[override]
        if (module, name) in _SAFE_UNPICKLE_GLOBALS:
            return super().find_class(module, name)
        if module == "copyreg" and name in _SAFE_UNPICKLE_COPYREG:
            return super().find_class(module, name)
        if module == _SAFE_UNPICKLE_OFFSETS_MODULE:
            cls = super().find_class(module, name)  # import only; not called yet
            try:
                from pandas._libs.tslibs.offsets import BaseOffset
                if isinstance(cls, type) and issubclass(cls, BaseOffset):
                    return cls
            except Exception:
                pass
        raise pickle.UnpicklingError(
            f"Refusing to unpickle disallowed global {module}.{name} from "
            "results_state.pkl — only enumerated numpy/pandas data types are "
            "permitted (possible malicious or corrupt bundle)."
        )


def _safe_unpickle_results(data: bytes):
    """
    Deserialize a `results_state.pkl` payload with the restricted unpickler.
    Raises `pickle.UnpicklingError` on a disallowed class; every call site
    treats a load failure as "no cached results" (warn + skip).
    """
    return _SafeResultsUnpickler(io.BytesIO(data)).load()


def _unwrap_results_state(raw: object) -> dict | None:
    """
    Return the side-results dict from a loaded results_state.pkl payload,
    accepting BOTH the versioned envelope (`{"__schema__": N, "data": {...}}`)
    and the legacy bare dict (pre-versioning saves). Returns None when the
    payload isn't a dict, or carries a schema version this build doesn't
    understand (forward-incompat) — callers skip rather than misread.
    """
    if not isinstance(raw, dict):
        return None
    if "__schema__" in raw:
        try:
            ver = int(raw["__schema__"])
        except (TypeError, ValueError):
            return None
        if ver != _RESULTS_STATE_SCHEMA:
            return None  # unknown / newer version — skip, don't guess
        data = raw.get("data")
        return data if isinstance(data, dict) else None
    # Legacy bare dict (pre-versioning): the result keys ARE the top level.
    return raw


def _restore_results_state(project_dir: pathlib.Path, project_name: str) -> None:
    """
    Reset and (if present) restore `_state` side-results from
    `results_state.pkl`. Side-results are anything the netcdf can't carry:
    LP/PF deep-copy DataFrame pairs (?source= toggle), VOLL slack dispatch
    capture, AC-PF convergence map. Always resets the keys first so loading
    a project without side results doesn't inherit ghost state from a
    previously-loaded one. Pickle failures are warnings, not errors — the
    netcdf still contains every result the user can derive from the live
    network.
    """
    # Atomic clear-then-restore of every result key via `_state_update`
    # so concurrent `/results/ac_pf/status` polls don't observe a mix of
    # old and new state during the restore window.
    from routers.simulation import _state_update as _sim_state_update
    _sim_state_update(**{k: None for k in _RESULTS_STATE_KEYS})
    results_path = project_dir / "results_state.pkl"
    if not results_path.exists():
        return
    try:
        restored = _safe_unpickle_results(results_path.read_bytes())
        data = _unwrap_results_state(restored)
        if data is None:
            # Not a dict, or a schema version this build doesn't understand.
            # Warn only when it LOOKED like a versioned payload we can't read
            # (a corrupt/legacy non-dict is already covered by the except).
            if isinstance(restored, dict) and "__schema__" in restored:
                change_log_service.log(
                    "warn", "Project", project_name,
                    f"results_state.pkl uses unsupported schema "
                    f"{restored.get('__schema__')!r} (this build expects "
                    f"{_RESULTS_STATE_SCHEMA}); skipped. Re-run the solve to "
                    f"repopulate side results (cost / dispatch unaffected).",
                )
            return
        applied = {k: v for k, v in data.items() if k in _RESULTS_STATE_KEYS}
        if applied:
            _sim_state_update(**applied)
    except Exception as exc:
        change_log_service.log(
            "warn", "Project", project_name,
            f"Couldn't restore results_state.pkl ({type(exc).__name__}: {exc}). "
            f"Re-run the solve to repopulate side results (cost / dispatch "
            f"unaffected — they come from the netcdf).",
        )


def _read_meta(project_dir: pathlib.Path) -> dict:
    """
    Read a project's metadata.json. Returns {} for missing OR corrupted
    files — a malformed metadata.json must NOT 500 every endpoint that lists
    or loads the project. Callers already treat {} as "no metadata" (counts
    fall back to 0, parent_project to None).
    """
    p = _meta_path(project_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        # Corrupted / unreadable metadata. Degrade gracefully rather than
        # propagating a 500 — the project's network.nc is still usable.
        return {}


def _write_meta(project_dir: pathlib.Path, data: dict) -> None:
    _atomic_write_text(_meta_path(project_dir), json.dumps(data, indent=2))


# ── Scenario-tree helpers ────────────────────────────────────────────────────
# Scenarios are projects whose `metadata.json` carries a non-null
# `parent_project`. The tree is reconstructed by walking those pointers.
# A linear scan of `PROJECTS_DIR` is acceptable because the active project
# list is short (capped to whatever the user has on disk, typically tens),
# and these helpers run only on delete/reparent operations — not in hot
# paths.

def _find_direct_children(parent_name: str) -> list[str]:
    """
    Return names of projects whose metadata.parent_project == parent_name.

    Only direct children. Use `_find_descendants` for the transitive closure.
    """
    if not PROJECTS_DIR.exists():
        return []
    out: list[str] = []
    for d in PROJECTS_DIR.iterdir():
        if not d.is_dir() or not (d / "network.nc").exists():
            continue
        if _read_meta(d).get("parent_project") == parent_name:
            out.append(d.name)
    return out


def _find_descendants(root_name: str) -> list[str]:
    """
    All transitive children of `root_name`, BFS-ordered.

    Caller deletes in reverse so leaves go first, keeping the on-disk tree
    consistent at every step. Cycles in the parent graph (shouldn't happen
    given POST /scenarios validation but defended for paranoia) would loop
    forever — guard with a seen-set.
    """
    seen: set[str] = set()
    out: list[str] = []
    frontier = [root_name]
    while frontier:
        next_frontier: list[str] = []
        for parent in frontier:
            for child in _find_direct_children(parent):
                if child in seen:
                    continue  # cycle defense
                seen.add(child)
                out.append(child)
                next_frontier.append(child)
        frontier = next_frontier
    return out


def _walk_ancestors(name: str) -> list[str]:
    """
    Walk up the parent chain from `name` (exclusive), root-most last.

    Used by cycle detection: a candidate parent that already lists `name`
    in its own ancestry would form a cycle if set as `name`'s parent.
    """
    seen: set[str] = {name}
    chain: list[str] = []
    cursor: str | None = _read_meta(PROJECTS_DIR / name).get("parent_project") if (PROJECTS_DIR / name).is_dir() else None
    while cursor and cursor not in seen:
        seen.add(cursor)
        chain.append(cursor)
        parent_dir = PROJECTS_DIR / cursor
        if not parent_dir.is_dir():
            break  # dangling reference; treat as root
        cursor = _read_meta(parent_dir).get("parent_project")
    return chain


def _would_cycle(child_name: str, candidate_parent: str) -> bool:
    """True iff assigning `candidate_parent` to `child_name` would loop back."""
    if candidate_parent == child_name:
        return True
    # If child_name appears in candidate_parent's ancestry, setting that as
    # the new parent creates the loop: child → candidate → … → child.
    return child_name in _walk_ancestors(candidate_parent)


def _project_info(project_dir: pathlib.Path) -> ProjectInfo:
    """
    Build a ProjectInfo from a project directory. Same shape as the
    rows returned by list_projects — extracted so single-project endpoints
    (POST /scenarios) can return identical data without duplicating the
    construction logic.
    """
    meta = _read_meta(project_dir)
    nc = project_dir / "network.nc"
    has_orphan = any(
        (project_dir / f"{fname}.tmp").exists() for fname in _BUNDLE_FILES
    )
    return ProjectInfo(
        name=project_dir.name,
        created_at=meta.get(
            "created_at",
            datetime.fromtimestamp(nc.stat().st_mtime, tz=timezone.utc).isoformat()
            if nc.exists() else datetime.now(tz=timezone.utc).isoformat(),
        ),
        has_solver_config=(project_dir / "solver_config.json").exists(),
        bus_count=meta.get("bus_count", 0),
        snapshot_count=meta.get("snapshot_count", 0),
        objective=meta.get("objective"),
        has_orphan_tmp=has_orphan,
        parent_project=meta.get("parent_project"),
        scenario_description=meta.get("scenario_description"),
    )


def _project_info_db(db, project) -> ProjectInfo:
    """
    Build a ProjectInfo for a DB-registry project (auth mode). Counts come from
    the on-disk bundle at ``storage_path`` when present, but the identity
    (``id`` / ``name`` / ``parent_project`` / ``scenario_description``) is
    authoritative from the DB row — the storage dir is UUID-keyed so its own
    ``metadata.json`` name/parent pointers are only kept in sync for bundle
    export/import round-trips.
    """
    from db.models import Project as _Project

    d = pathlib.Path(project.storage_path)
    if (d / "network.nc").exists():
        info = _project_info(d)
    else:
        info = ProjectInfo(
            name=project.name,
            created_at=(
                project.created_at.isoformat()
                if project.created_at is not None
                else datetime.now(tz=timezone.utc).isoformat()
            ),
            has_solver_config=(d / "solver_config.json").exists(),
            bus_count=0,
            snapshot_count=0,
        )
    info.name = project.name
    info.id = str(project.id)
    info.scenario_description = project.scenario_description
    parent_name = None
    if project.parent_project_id is not None:
        parent = db.get(_Project, project.parent_project_id)
        parent_name = parent.name if parent is not None else None
    info.parent_project = parent_name
    return info


@router.get("/")
def list_projects(
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> list[ProjectInfo]:
    if _auth_enabled():
        from services import project_acl, project_registry

        project_registry.require_user(user)
        projects = project_acl.list_accessible_projects(db, user)
        return [_project_info_db(db, p) for p in projects]

    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    result = []
    for d in sorted(PROJECTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        if not (d / "network.nc").exists():
            continue
        result.append(_project_info(d))
    return result


@router.post("/import_bundle")
async def import_bundle(
    file: UploadFile = File(...),
    name: str | None = None,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """
    Restore a project from a .pypsaproj.zip bundle.

    Writes the bundle to ``projects/{name}/`` (defaulting to the bundle's
    metadata name or the uploaded filename) and loads it into the live network.

    NOTE: this route MUST be registered before ``POST /{name}`` so FastAPI does
    not interpret ``import_bundle`` as a value for the ``name`` path parameter
    of the save endpoint (which would silently call save_project).
    """
    from services.upload_guard import read_capped
    data = await read_capped(file)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise HTTPException(400, f"Not a valid zip bundle: {exc}") from exc

    members = set(zf.namelist())
    if "network.nc" not in members:
        raise HTTPException(400, "Bundle is missing network.nc")

    target_name = (name or "").strip()
    if not target_name and "metadata.json" in members:
        try:
            target_name = json.loads(zf.read("metadata.json").decode("utf-8")).get("name", "").strip()
        except Exception:
            target_name = ""
    if not target_name:
        base = (file.filename or "imported_project")
        for ext in (".pypsaproj.zip", ".zip"):
            if base.lower().endswith(ext):
                base = base[: -len(ext)]
                break
        target_name = base or "imported_project"

    # `target_name` may come from a zip's metadata.json — never trust it.
    # `_safe_project_dir` rejects path traversal and shell-metachar names.
    if _auth_enabled():
        from services import project_registry

        project_registry.require_user(user)
        _imported_project = project_registry.create_root(db, user, target_name)
        target_name = _imported_project.name
        dest = project_registry.project_dir(_imported_project)
    else:
        dest = _safe_project_dir(target_name)
        dest.mkdir(parents=True, exist_ok=True)
    for fname in _BUNDLE_FILES:
        if fname in members:
            (dest / fname).write_bytes(zf.read(fname))
    # Chatbot uploads (Phase A) — extract any `uploads/...` entries the
    # exporting GUI included. Each archive member's path is verified to
    # stay inside `dest` before writing (zip-slip defence) and the parent
    # subdirs are created on demand.
    for member in members:
        for dname in _BUNDLE_DIRS:
            if not member.startswith(f"{dname}/"):
                continue
            # Sanity: refuse any member that resolves outside dest.
            target_path = (dest / member).resolve()
            try:
                target_path.relative_to(dest.resolve())
            except ValueError:
                continue  # zip-slip attempt — silently drop the member
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(zf.read(member))

    nc_path = dest / "network.nc"
    from services import undo_service
    undo_service.clear()
    with PyPSAService.get_lock():
        PyPSAService.reset_network()
        n = PyPSAService.get_network()
        with PyPSAService.get_netcdf_io_lock():
            n.import_from_netcdf(str(nc_path))
        # Bind identity atomically with the swap (see load_project for the
        # rationale on why this must be inside the lock, not after).
        PyPSAService.set_loaded_project(target_name)

    cfg_path = dest / "solver_config.json"
    if cfg_path.exists():
        from routers.simulation import _state
        cfg_data = json.loads(cfg_path.read_text())
        # Shared legacy-tolerant loader (filter unknown keys + coerce removed
        # enum values) — same path load_project uses, so a bundle from an older
        # GUI version imports instead of 500-ing on an unexpected key.
        _state["solver_config"] = _solver_config_from_dict(cfg_data)

    from routers.network import (
        _ensure_snapshots_cover_user_ts,
        _reapply_user_ts_to_network,
        _restore_user_ts,
    )
    user_ts_path = dest / "user_ts.json"
    if user_ts_path.exists():
        _restore_user_ts(json.loads(user_ts_path.read_text()))
    else:
        _restore_user_ts({})
    n = PyPSAService.get_network()
    # Saved network.nc may have a smaller snapshot index than the user-uploaded
    # time series in user_ts.json. Expand n.snapshots to match the longest
    # uploaded series before re-applying so the bundle round-trip preserves
    # full-resolution data and the GUI shows the real snapshot count.
    with PyPSAService.get_lock():
        _ensure_snapshots_cover_user_ts(n)
    # Set n.name so the StatusBar / meta endpoint reflect the project name
    # rather than PyPSA's "Unnamed Network" default. The authoritative
    # loaded-project binding was already set inside the swap lock above.
    n.name = target_name
    _reapply_user_ts_to_network(n)

    # Bundle import is a load path too — restore side-results so the LP/PF
    # toggle, lost-load panel, and AC-PF badges hydrate immediately rather
    # than waiting for the user to re-run the solve.
    _restore_results_state(dest, target_name)

    # Hydrate lifecycle indicators (status/objective/solve_time) the same
    # way load_project does, so a bundle of a solved project shows up as
    # "completed" in the header instead of "idle". Gated on actually having
    # dispatch tables, matching save_project's `has_results` flag.
    has_dispatch_bundle = network_has_dispatch(n)
    meta_bundle = _read_meta(dest)
    # Atomic multi-key apply via `_state_update` so a concurrent
    # `GET /api/simulation/status` can't observe a half-applied tuple
    # (e.g. status="completed" but objective=None during the bare-dict
    # assignment window). Mirror of the F11 fix on the solver-worker
    # write path; same race vector via the project-import handler.
    from routers.simulation import _state_update as _sim_state_update
    if has_dispatch_bundle and meta_bundle.get("has_results"):
        _sim_state_update(
            status="completed",
            condition=meta_bundle.get("condition") or "optimal",
            objective=meta_bundle.get("objective"),
            solve_time=meta_bundle.get("solve_time"),
        )
    else:
        _sim_state_update(
            status="idle",
            condition=None,
            objective=None,
            solve_time=None,
        )

    change_log_service.log(
        "import", "Project", target_name,
        f"Imported project bundle '{file.filename}' as '{target_name}' "
        f"({len(n.buses)} buses, {len(n.snapshots)} snapshots)",
    )
    return {
        "imported": target_name,
        "summary": ImportSummary(
            buses=len(n.buses),
            generators=len(n.generators),
            lines=len(n.lines),
            links=len(n.links),
            storage_units=len(n.storage_units),
            stores=len(n.stores),
            loads=len(n.loads),
            transformers=len(n.transformers),
            snapshots=len(n.snapshots),
        ),
    }


# ── Project templates ────────────────────────────────────────────────────────
# Bundled starter networks live in backend/project_templates/<id>/network.nc.
# They are bare .nc files (no user_ts / solver_config) — see
# project_templates/_build.py for how they're generated. `create_from_template`
# copies the .nc into a fresh project dir and loads it, so the user ends up
# with an ordinary, fully-editable project rather than a read-only example.
_PROJECT_TEMPLATES_DIR = pathlib.Path(__file__).parent.parent / "project_templates"
_TEMPLATE_DEFAULT_NAMES = {
    "3bus": "3-Bus Tutorial",
    "ieee14": "IEEE 14-Bus",
    "belgium": "Belgium Grid",
}


def _solver_config_from_dict(data: dict):
    """
    Build a SolverConfig from a (possibly legacy/foreign) solver_config.json dict.

    Older or externally-produced bundles may carry keys that no longer exist on
    the dataclass — `SolverConfig(**data)` raises TypeError on those — or omit
    keys added since they were saved, which should fall back to the current
    default rather than error. Filter to the live field set and coerce removed
    enum values, so the project-load AND bundle-import paths accept legacy files
    identically (import_bundle previously did the raw `SolverConfig(**data)` and
    500'd on any non-current bundle).
    """
    from dataclasses import fields as _dc_fields

    from services.solver_service import SolverConfig
    valid_keys = {f.name for f in _dc_fields(SolverConfig)}
    clean = {k: v for k, v in (data or {}).items() if k in valid_keys}
    # The 'lpf' mode was removed in v1.x — coerce any legacy value to 'lopf'.
    if clean.get("mode") == "lpf":
        clean["mode"] = "lopf"
    return SolverConfig(**clean)


def _unique_project_name(base: str) -> str:
    """
    Return `base`, or `base 2` / `base 3` / … if a non-empty project of
    that name already exists. Keeps a repeat template click from silently
    overwriting the earlier import. An existing but EMPTY project dir is
    treated as reusable.
    """
    candidate = base
    suffix = 2
    while True:
        d = PROJECTS_DIR / candidate
        if not d.is_dir() or not (d / "network.nc").exists():
            return candidate
        # Reuse the dir ONLY when its metadata PARSED and explicitly reports an
        # empty network. `_read_meta` returns {} when metadata.json is missing
        # or corrupt — the old `.get("bus_count", 0) == 0` treated that as
        # "empty" and silently overwrote a real project whose metadata merely
        # failed to parse (a data-loss path). Requiring a truthy `meta` makes a
        # corrupt-metadata project fall through to a suffixed name instead.
        meta = _read_meta(d)
        if meta and meta.get("bus_count", None) == 0:
            return candidate
        candidate = f"{base} {suffix}"
        suffix += 1


@router.post("/from_template/{template_id}")
def create_from_template(
    template_id: str,
    name: str | None = None,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """
    Create a new project from a bundled starter network.

    `template_id` is one of the keys in `_TEMPLATE_DEFAULT_NAMES`. The
    template's `network.nc` is copied into a fresh `projects/<name>/` dir and
    loaded into the live network. The result is an ordinary editable project.

    MUST be registered before `POST /{name}` so FastAPI does not interpret
    `from_template` as a value for the `name` path parameter of save_project.
    """
    if template_id not in _TEMPLATE_DEFAULT_NAMES:
        raise HTTPException(
            404,
            f"Unknown template '{template_id}'. Available: "
            f"{', '.join(sorted(_TEMPLATE_DEFAULT_NAMES))}.",
        )
    src_nc = _PROJECT_TEMPLATES_DIR / template_id / "network.nc"
    if not src_nc.exists():
        raise HTTPException(
            404,
            f"Template '{template_id}' is registered but its network.nc is "
            f"missing — run project_templates/_build.py to regenerate it.",
        )

    # `name` is optional — default to the template's friendly name, then
    # uniquify so clicking the same template twice doesn't clobber the first.
    requested = (name or "").strip() or _TEMPLATE_DEFAULT_NAMES[template_id]
    if _auth_enabled():
        from services import project_registry

        project_registry.require_user(user)
        _created_project = project_registry.create_root(db, user, requested)
        target_name = _created_project.name
        dest = project_registry.project_dir(_created_project)
    else:
        _safe_project_dir(requested)  # validate the requested name (raises 400)
        target_name = _unique_project_name(requested)
        dest = _safe_project_dir(target_name)
        dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_nc, dest / "network.nc")

    # Reset + load, mirroring import_bundle / load_project.
    from services import undo_service
    undo_service.clear()
    with PyPSAService.get_lock():
        PyPSAService.reset_network()
        n = PyPSAService.get_network()
        with PyPSAService.get_netcdf_io_lock():
            n.import_from_netcdf(str(dest / "network.nc"))
        # Bind identity atomically with the swap (see load_project rationale).
        PyPSAService.set_loaded_project(target_name)

    # Templates ship without user_ts / solver_config — reset both to defaults
    # so no stale state from a previously-open project leaks into the new one.
    from routers.network import _reapply_user_ts_to_network, _restore_user_ts
    _restore_user_ts({})
    # Atomic lifecycle reset via `_state_update` (see F11 / G2 rationale).
    from services.solver_service import SolverConfig

    from routers.simulation import _state
    from routers.simulation import _state_update as _sim_state_update
    _state["solver_config"] = SolverConfig()
    _sim_state_update(status="idle", condition=None, objective=None, solve_time=None)

    n = PyPSAService.get_network()
    n.name = target_name
    # The authoritative loaded-project binding was set inside the swap lock above.
    _reapply_user_ts_to_network(n)

    _write_meta(dest, {
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "last_saved": datetime.now(tz=timezone.utc).isoformat(),
        "bus_count": len(n.buses),
        "snapshot_count": len(n.snapshots),
        "objective": None,
        "has_results": False,
        "condition": None,
        "solve_time": None,
        "user_ts_count": 0,
        "parent_project": None,
        "scenario_description": None,
    })

    change_log_service.log(
        "import", "Project", target_name,
        f"Created project '{target_name}' from template '{template_id}' "
        f"({len(n.buses)} buses, {len(n.snapshots)} snapshots)",
    )
    return {
        "imported": target_name,
        "summary": ImportSummary(
            buses=len(n.buses),
            generators=len(n.generators),
            lines=len(n.lines),
            links=len(n.links),
            storage_units=len(n.storage_units),
            stores=len(n.stores),
            loads=len(n.loads),
            transformers=len(n.transformers),
            snapshots=len(n.snapshots),
        ),
    }


@router.post("/{name}")
def save_project(
    name: str,
    objective: float | None = None,
    force: bool = False,
    clear_undo: bool = True,
    expect: str | None = None,
    rebind: bool = False,
    *,
    parent_project_override: str | None = None,
    scenario_description_override: str | None = None,
    apply_overrides: bool = False,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """
    Persist the ACTIVE project's in-memory network to ``projects/<name>/``.

    Thin wrapper over `_save_context` bound to the active ProjectContext — the
    public route + every internal caller (autosave, Ctrl+S, create_scenario)
    saves the foreground project, so this is byte-identical to the pre-B4.3
    monolithic body. `_save_context` carries the entire save logic, parameterised
    on a ctx, so the B4.3 dispatcher can save a BACKGROUND project's context
    directly without co-opting the foreground.

    `clear_undo` lives HERE (not in `_save_context`) because the undo stack is
    inherently active-scoped (`undo_service.clear()` always targets the active
    ctx). An explicit user save discards the in-memory revert history so revert
    can't roll back across the checkpoint; autosave passes clear_undo=False to
    keep it intact. A background save (dispatcher) must NOT touch the foreground's
    undo at all, which is exactly what calling `_save_context` directly achieves.
    """
    storage_dir = None
    if _auth_enabled():
        from services import project_acl, project_registry

        project_registry.require_user(user)
        project = project_registry.find_project(db, user, name)
        if project is None:
            # First save of a new project — register a root row in the DB.
            project = project_registry.create_root(
                db, user, name, scenario_description=scenario_description_override
            )
        else:
            project_acl.ensure_project_access(db, user, project)
        storage_dir = project_registry.project_dir(project)
        name = project.name
    result = _save_context(
        PyPSAService.get_active_context(),
        name,
        objective=objective,
        force=force,
        expect=expect,
        rebind=rebind,
        parent_project_override=parent_project_override,
        scenario_description_override=scenario_description_override,
        apply_overrides=apply_overrides,
        storage_dir=storage_dir,
    )
    # Saved state is the new baseline — explicit user-triggered saves discard
    # the undo stack so revert can't roll back across the checkpoint. Autosave
    # passes clear_undo=false to keep the in-memory revert history intact.
    if clear_undo:
        from services import undo_service
        undo_service.clear()
    return result


def _save_context(
    ctx,
    name: str,
    objective: float | None = None,
    force: bool = False,
    expect: str | None = None,
    rebind: bool = False,
    *,
    parent_project_override: str | None = None,
    scenario_description_override: str | None = None,
    apply_overrides: bool = False,
    persist_user_ts: bool = True,
    storage_dir: pathlib.Path | None = None,
):
    """
    Persist `ctx`'s in-memory network to ``projects/<name>/``.

    Behaviour-preserving extraction of the former `save_project` body,
    parameterised on a ProjectContext so the same logic serves both the
    foreground (active ctx, via the `save_project` wrapper) and a background
    solve's own ctx (B4.3 dispatcher). Every per-project handle is read from
    `ctx`: the in-flight gate (`_solver_in_flight_ctx(ctx)`), the mutation lock
    (`ctx.mutation_lock`), the network (`ctx.network`), the on-disk binding
    (`ctx.loaded_project`), and the solver-state reads (`ctx.solver_state`). The
    netCDF I/O lock stays GLOBAL (`PyPSAService.get_netcdf_io_lock()` — it guards
    process-global HDF5, not per-project state). Undo-clear is NOT here (it is
    active-scoped; the wrapper owns it).

    Identity guard (``expect``): a caller that is saving what it believes is
    the *active* project (autosave, explicit Ctrl+S) passes ``expect=<that
    project>``. If the backend's in-memory network is actually bound to a
    DIFFERENT project (it was swapped by a load in another browser tab, an
    external client, or any flow the caller didn't account for), the save is
    refused with 409 rather than serialising the wrong network under ``name``
    — the cross-project overwrite that destroyed a project on 2026-05-28.
    The check reads the binding under ``get_lock()``, atomically with respect
    to the network swap that loads perform. Callers that intentionally write
    under a different name (Save a Copy, first save of a new project,
    ``create_scenario``) simply omit ``expect``.

    Rebind (``rebind``): set True when this save should make ``name`` the
    backend's loaded project going forward — i.e. the caller will treat
    ``name`` as the active project after the save (Save-As to a new name,
    cloning a project, first save of a new network). The in-memory network has
    just been written to ``name``'s folder, so binding to ``name`` is always
    consistent with disk. Save-a-Copy leaves ``rebind`` False so the live
    network stays bound to the ORIGINAL project (the only valid autosave
    target); without this the post-Save-As autosave/Ctrl+S would 409 forever
    against the stale old binding until the user reloaded.

    Scenario-tree fields: when ``apply_overrides=True`` the two `*_override`
    kwargs replace the values that would otherwise round-trip from existing
    metadata. This collapses the previous two-write pattern in
    ``create_scenario`` (save then patch) into a single atomic metadata
    write — closing the brief inconsistency window when a concurrent
    GET /api/projects would observe a project with no parent_project set.
    """
    # Refuse to serialise while a solver worker is still alive — even after
    # /abort has flipped status to "aborted", HiGHS/Gurobi can stay in
    # native code for several seconds, during which PyPSA's `n.add()` calls
    # (vintage expansion, slack generators) are racing with our
    # `n.export_to_netcdf()`. The resulting netcdf is at best inconsistent
    # ("unable to infer dtype on variable 'X'"), at worst corrupted on the
    # next reload. Surfacing a clean 409 here is much friendlier than
    # letting the export fail with an opaque HDF5 error.
    from routers.simulation import _solver_in_flight_ctx
    if _solver_in_flight_ctx(ctx):
        # Structured detail so the chat agent surfaces a typed error frame
        # consistent with activate_project (Phase 4 QA: error-handling-matrix).
        raise HTTPException(
            status_code=409,
            detail={
                "error_kind": "solver_in_flight",
                "message": (
                    "Cannot save while a solver worker is still active — "
                    "the in-memory network carries transient LP transforms "
                    "(vintage rows, slack generators, dispatch fix) that "
                    "would be serialised into the project on disk. Wait for "
                    "the solver to finish its current LP iteration (the "
                    "lock-status indicator in the header polls every 0.5 s) "
                    "and retry."
                ),
            },
        )

    # `storage_dir` (auth mode) is a pre-resolved org-scoped path; legacy mode
    # falls back to the flat `PROJECTS_DIR / name`.
    dest = storage_dir if storage_dir is not None else _safe_project_dir(name)
    dest.mkdir(parents=True, exist_ok=True)
    nc_path = dest / "network.nc"
    from routers.network import (
        _backup_network_ts_to_user_ts,
        _reapply_user_ts_to_network,
        _serialize_user_ts,
    )

    # Identity guard + atomic export. The identity check, the network capture,
    # the empty-network safety check, the time-series reapply, the netcdf
    # export, and the first-save claim ALL run under ONE `get_lock()`
    # acquisition. This guarantees the bytes written under `name` are the very
    # network whose identity the guard approved: a concurrent load swaps the
    # network AND re-binds `_loaded_project` under the same lock, so it cannot
    # interleave between the guard passing and the export — closing the
    # cross-project overwrite race (2026-05-28 incident) even against a second
    # client/tab. The netcdf I/O lock nests INSIDE (pypsa_lock OUTER,
    # netcdf_io_lock INNER — the documented order) to keep the HDF5 write
    # serialised against concurrent compare-state / results-summary reads.
    with ctx.mutation_lock:
        # `expect` lets a caller assert which project it believes is active
        # (autosave, explicit Ctrl+S). Refuse only when it asserted an identity
        # AND the backend is bound to a genuinely DIFFERENT project. `loaded is
        # None` (fresh / unbound network) falls through — the claim at the end
        # of this block establishes the binding.
        loaded = ctx.loaded_project
        if expect is not None and loaded is not None and expect != loaded:
            raise HTTPException(
                409,
                f"Backend network is bound to project '{loaded}', not "
                f"'{expect}'. It was loaded/swapped out from under this client "
                f"(another tab, an external client, or a load that bypassed "
                f"this save's caller). Refusing to overwrite '{name}' with the "
                f"wrong network. Reload '{expect}' to resync, then retry.",
            )
        # Cross-project name-claim guard (chatbot integration v6 F1).
        # Refuses POST /projects/B while the active network is bound to a
        # DIFFERENT project (loaded != name) and B already has a network on
        # disk — the legacy code silently overwrote B's data with A's network
        # and left the binding on A (Save-a-Copy / create_scenario semantics).
        # For the chat agent (and any external client) this is the cross-project
        # overwrite footgun: there is no UI signal that B was just nuked. The
        # caller must explicitly opt in: ?rebind=true (Save-As — claim B as the
        # new binding, accepts overwrite as part of the claim) or ?force=true
        # (acknowledged overwrite). Same-project re-save (loaded == name) and
        # first-save of an UNBOUND network (loaded is None — the existing
        # bind/claim at the end of this lock block handles it) both fall through
        # so autosave + first-save flows are unchanged.
        #
        # Gate on `nc_path.exists()` (not `dest.exists()`): `_safe_project_dir`
        # above already `mkdir(exist_ok=True)`s the directory, so `dest.exists()`
        # is always True at this point. `nc_path.exists()` is the standard
        # "project has a network on disk" signal (matches the empty-network
        # safety check below).
        #
        # PLACEMENT INVARIANT (v6 F1 line-anchored): this guard MUST sit INSIDE
        # the `with ctx.mutation_lock:` block opened above, AFTER the
        # `loaded = ctx.loaded_project` read above. Outside the lock would risk
        # a TOCTOU race where `loaded` reads stale before another tab's load
        # swaps the binding; reading `loaded` outside the lock would observe
        # a torn value. Phase 0 QA Gate B asserts the literal line ordering.
        if (
            nc_path.exists()
            and not force
            and not rebind
            and loaded is not None
            and loaded != name
        ):
            raise HTTPException(
                409,
                f"Project '{name}' already exists on disk and the in-memory "
                f"network is bound to a DIFFERENT project ('{loaded}'). "
                f"Refusing to overwrite '{name}' with '{loaded}'s network. "
                f"Use ?rebind=true (Save-As) to claim '{name}' as the new "
                f"binding, or ?force=true to acknowledge the overwrite. "
                f"Without one of these, you would silently destroy "
                f"'{name}' while leaving the active project on '{loaded}'.",
            )
        n = ctx.network

        # Safety: never silently overwrite a project that had data with an
        # empty network. Guards autosave after a server restart, when the
        # in-memory network is blank but currentProject is still set in the
        # browser's localStorage. ?force=true bypasses (the "New Project"
        # overwrite flow).
        if not force and nc_path.exists() and n.buses.empty:
            existing_meta = _read_meta(dest)
            if existing_meta.get("bus_count", 0) > 0:
                raise HTTPException(
                    409,
                    f"Refusing to overwrite project '{name}' "
                    f"({existing_meta['bus_count']} buses) with an empty network. "
                    "Load the project first or reset it explicitly.",
                )

        # Ensure time series round-trip correctly — FOREGROUND only (gated on
        # persist_user_ts). For a BACKGROUND ctx these two would corrupt state:
        # (1) `_backup_network_ts_to_user_ts` writes THIS project's `_t` profiles
        # into the module-global `_user_ts`, which belongs to the FOREGROUND
        # project; (2) `_reapply_user_ts_to_network` overwrites THIS network's own
        # baked profiles with the foreground's `_user_ts`. The background network
        # already carries solve-ready baked profiles (from
        # `_hydrate_context_from_disk`), so export it as-is and never touch the
        # foreground's `_user_ts`. Steps when foreground:
        # 1. Backup any imported-network ts into _user_ts (skips all-NaN/existing)
        # 2. Reapply _user_ts so the .nc captures current profiles
        # 3. THEN export — the .nc now contains correct data
        if persist_user_ts:
            _backup_network_ts_to_user_ts(n)
            _reapply_user_ts_to_network(n)

        # Atomic replace so a crash mid-save leaves the previous file intact.
        with PyPSAService.get_netcdf_io_lock():
            _atomic_write_with(nc_path, lambda p: n.export_to_netcdf(str(p)))

        # Bind/claim — atomic with the export, keyed off the binding read at the
        # top of THIS lock block (it can't have changed; we hold the lock
        # throughout). Two cases bind the network to `name`:
        #   • `loaded is None` — first-save CLAIM of a fresh/unbound network.
        #   • `rebind` — the caller is making `name` the active project under a
        #     possibly-different prior binding (Save-As, clone). We just wrote
        #     the in-memory network to `name`'s folder, so binding to `name` is
        #     consistent with disk.
        # A save under `name` while ALREADY bound elsewhere WITHOUT rebind
        # (Save a Copy, create_scenario writing the base network under a new
        # scenario name) leaves the binding untouched — the live network still
        # belongs to its original project, the only valid autosave target.
        if loaded is None or rebind:
            ctx.loaded_project = name
            try:
                n.name = name
            except Exception:
                pass

    # Save solver config if present
    cfg = ctx.solver_state.get("solver_config")
    if cfg is not None:
        _atomic_write_text(dest / "solver_config.json", json.dumps(asdict(cfg), indent=2))

    # Persist solve-time _state fields that n.export_to_netcdf doesn't
    # capture. Without these, after reload:
    #   • `?source=lopf` falls back to the live network — which is the
    #     AC-PF state when both stages ran (Stage 2 mutates lines_t.p0
    #     / buses_t.v_mag_pu in-place), corrupting the LP/PF toggle.
    #   • `last_lost_load` (VOLL slack dispatch, captured before the
    #     slack generators are removed) is gone forever — the Lost Load
    #     panel reports "no data" even though the original solve shed load.
    #   • AC PF convergence list, slack-bus pick, and stripped-VOLL list
    #     reset — the LoadFlow tab's per-snapshot badges go blank.
    # Pickle is lossless for pandas DataFrames (preserves MultiIndex,
    # dtypes, _t-schema). The DataFrame dicts here are bounded by the
    # number of snapshots × component count, same order of magnitude as
    # the .nc itself, so disk overhead is acceptable.
    import pickle
    results_state = {k: ctx.solver_state.get(k) for k in _RESULTS_STATE_KEYS}
    results_path = dest / "results_state.pkl"
    if any(v is not None for v in results_state.values()):
        # Versioned envelope so a future shape change can be detected on load
        # (see `_unwrap_results_state`). Legacy bare-dict bundles still load.
        payload = {"__schema__": _RESULTS_STATE_SCHEMA, "data": results_state}
        _atomic_write_with(results_path, lambda p: p.write_bytes(pickle.dumps(payload)))
    elif results_path.exists():
        # A run that produced no result-side data (e.g. a fresh import
        # before any solve, or a save after Reset) shouldn't leave a stale
        # pickle around to confuse the next load.
        try:
            results_path.unlink()
        except OSError:
            pass

    # Save all time series (user-uploaded + captured from network) alongside the
    # network. GATED on `persist_user_ts`: `_serialize_user_ts()` reads the
    # module-global `_user_ts`, which belongs to the FOREGROUND project. For a
    # foreground save (the `save_project` wrapper, or the dispatcher solving the
    # resident foreground in-place) that's correct. For a BACKGROUND ctx save
    # (dispatcher solving a non-foreground project) it would clobber that
    # project's own `user_ts.json` with the foreground's profiles — so we skip
    # it and leave the background project's on-disk profiles intact (the netcdf
    # already carries baked, solve-ready profiles; `_user_ts` is per-project only
    # after a later phase wires it onto the context).
    # `user_ts_data` is read below by the metadata build (`user_ts_count`,
    # `ts_columns_saved`) REGARDLESS of persist_user_ts, so it must always be
    # bound — default to {} (a background save reports 0 ts columns, correct).
    user_ts_data: dict = {}
    if persist_user_ts:
        user_ts_data = _serialize_user_ts()
        user_ts_path = dest / "user_ts.json"
        if user_ts_data:
            _atomic_write_text(user_ts_path, json.dumps(user_ts_data, indent=2))
        elif user_ts_path.exists():
            user_ts_path.unlink()

    # Cache metadata. The netcdf export already captures every PyPSA `*_t`
    # result table (generators_t.p, lines_t.p0, buses_t.marginal_price, etc.) —
    # so reloading the project surfaces a previous solve's results without
    # re-running. We persist the matching simulation `_state` fields here so
    # the header status indicator + SnapshotPicker can hydrate to "completed"
    # on load instead of showing Idle while the Results panel quietly shows
    # numbers. Objective falls back to _state when the caller didn't pass one.
    obj = objective
    if obj is None:
        obj = ctx.solver_state.get("objective")
    has_dispatch = network_has_dispatch(n)
    # Preserve scenario-tree fields written at creation time. Save() runs on
    # every autosave + every explicit Ctrl+S; without this round-trip, the
    # first save after creating a scenario would clobber `parent_project` and
    # detach the child from its tree. Both fields are write-once via the
    # POST /scenarios endpoint and never set by any other code path.
    #
    # Lock the read-modify-write block: two saves racing here could see
    # the same `existing_meta` and overwrite each other's parent_project
    # (e.g. autosave fires while POST /scenarios is patching the field).
    # The PyPSA lock is the canonical mutex for any state coupled to the
    # in-memory network — save_project is one of the few mutating endpoints
    # that wasn't yet acquiring it. `last_saved` (`created_at`) carries the
    # original creation timestamp forward; only mtime conveys "last saved".
    with ctx.mutation_lock:
        existing_meta = _read_meta(dest)
        # Detect the silent-detachment hazard: a re-save of a project whose
        # network.nc exists but whose metadata.json is missing or corrupted
        # (`_read_meta` returned {}). We can't recover the old parent_project
        # from anywhere — but writing `parent_project=None` here would
        # permanently detach a scenario from its tree. We can't prevent the
        # loss, but we make it LOUD instead of silent so the user can
        # re-link the scenario via the Scenarios panel.
        if (
            not apply_overrides
            and not existing_meta
            and nc_path.exists()
            and not n.buses.empty
        ):
            change_log_service.log(
                "warn", "Project", name,
                f"metadata.json for '{name}' was missing or corrupted on save — "
                f"if this project was a scenario, its parent link was lost and "
                f"must be re-created from the Scenarios panel.",
            )
        # Scenario-tree fields: caller can override via `apply_overrides=True`,
        # otherwise they round-trip from existing metadata (the autosave path).
        # The override path lets `create_scenario` set parent_project in the
        # same atomic write as the rest of the metadata — no half-state where
        # a GET /api/projects would see the new project as a root.
        if apply_overrides:
            new_parent = parent_project_override
            new_desc = scenario_description_override
        else:
            new_parent = existing_meta.get("parent_project")
            new_desc = existing_meta.get("scenario_description")
        _write_meta(dest, {
            "created_at": existing_meta.get(
                "created_at", datetime.now(tz=timezone.utc).isoformat()
            ),
            "last_saved": datetime.now(tz=timezone.utc).isoformat(),
            "bus_count": len(n.buses),
            "snapshot_count": len(n.snapshots),
            "objective": obj,
            # Result-state fields — written when an actual solve is in memory so
            # the load path can restore the lifecycle indicators consistently.
            "has_results": bool(has_dispatch),
            "condition":   ctx.solver_state.get("condition") if has_dispatch else None,
            "solve_time":  ctx.solver_state.get("solve_time") if has_dispatch else None,
            "user_ts_count": len(user_ts_data),
            "parent_project": new_parent,
            "scenario_description": new_desc,
        })
    ts_columns_saved = len(user_ts_data) if isinstance(user_ts_data, dict) else 0
    # Flat-count the leaves of the nested {comp: {attr: {col: ...}}} structure.
    if ts_columns_saved and isinstance(user_ts_data, dict):
        ts_columns_saved = sum(
            len(cols) for attrs in user_ts_data.values() if isinstance(attrs, dict)
            for cols in attrs.values() if isinstance(cols, dict)
        )
    change_log_service.log(
        "save", "Project", name,
        f"Saved project '{name}' ({len(n.buses)} buses, {len(n.snapshots)} snapshots, "
        f"{ts_columns_saved} time-series columns)",
    )
    # NB: the undo-stack clear (the old `if clear_undo: undo_service.clear()`)
    # lives in the `save_project` wrapper, not here — it is inherently
    # active-scoped (`undo_service.clear()` targets the active ctx), so a
    # background save must NOT run it (it would wipe the FOREGROUND's undo).

    # Chatbot integration v6 Phase 4 — chat.jsonl lineage (F12).
    # `loaded` captured at line 940 BEFORE the rebind at line 1043 reflects
    # the SOURCE project; `name` is the TARGET. The rebind flag distinguishes:
    #   * rebind=True  + source != target → Save-As: MOVE chat.jsonl
    #   * rebind=False + source != target → Save-a-Copy / create_scenario: COPY
    #   * source == target OR source is None → no lineage transition needed
    # Best-effort: chat-history lineage must not fail the project save itself,
    # so `handle_save_lineage` swallows OSErrors internally.
    if loaded is not None and loaded != name:
        try:
            from services import chat_service
            mode = (
                chat_service.SAVE_LINEAGE_REBIND_MOVE
                if rebind
                else chat_service.SAVE_LINEAGE_COPY
            )
            # Pass `loaded` (the PRE-rebind project name) explicitly. By the
            # time this hook fires, `ctx.loaded_project` has already been
            # re-bound to `name` at line 1043 above — without the explicit
            # source, the lineage helper would resolve src == dst and the
            # move/copy would be a no-op (Phase 4 walkthrough bug).
            chat_service.handle_save_lineage(
                ctx, target_name=name, mode=mode, source_name=loaded,
            )
        except Exception:  # noqa: BLE001 — never abort save on lineage failure
            pass

        # Chatbot uploads (Phase A) — uploads/ travels alongside the project
        # bundle on cross-project save transitions. Unlike chat.jsonl this is
        # ALWAYS a COPY (uploads are reference materials the user may want
        # available in both projects, not a per-conversation thread). Same
        # best-effort guard — a copy failure must not abort the user's save.
        try:
            _copy_bundle_dirs(
                _safe_project_dir(loaded), _safe_project_dir(name),
            )
        except Exception:  # noqa: BLE001 — best-effort, never abort save
            logger.exception("save: _copy_bundle_dirs(%s → %s) failed", loaded, name)

    return {
        "saved": name,
        "path": str(dest),
        "ts_columns_saved": ts_columns_saved,
        "bus_count": len(n.buses),
        "snapshot_count": len(n.snapshots),
    }


def _hydrate_context_from_disk(ctx, src: pathlib.Path, name: str) -> None:
    """
    Populate a (typically OFF-TO-THE-SIDE, background) ProjectContext from the
    on-disk project at ``src`` WITHOUT touching the active pointer or the
    foreground's ``_state``.

    Used by the B4.3 dispatcher when it solves a project that is NOT the
    foreground: it builds a fresh ctx (own network + own mutation_lock + own
    solver_state) and hydrates the disk state into THAT ctx, so the subsequent
    solve + `_save_context(ctx, …)` never reach the active foreground.

    Minimal correct subset for a clean background solve + save:
      * import ``network.nc`` into ``ctx.network`` (under ``ctx.mutation_lock``
        + the GLOBAL netCDF I/O lock — HDF5 is process-global, not per-project);
      * restore solver_config from ``solver_config.json`` (legacy-tolerant via
        ``_solver_config_from_dict``), else a default ``SolverConfig()``;
      * restore the ``results_state.pkl`` side-results into ``ctx.solver_state``;
      * set ``ctx.network.name`` + ``ctx.loaded_project`` to ``name``.

    Deliberately does NOT touch the module-global ``_user_ts`` store: that store
    belongs to the FOREGROUND ctx, and ``_restore_user_ts`` REPLACES it wholesale
    — hydrating a background project's profiles into it would clobber the
    foreground's. The netcdf already carries every ``*_t`` profile (the save path
    reapplies ``_user_ts`` onto the network BEFORE export), so the imported
    network is solve-ready as-is; the only thing a ``user_ts.json`` reapply would
    add is re-expanding a series the saved netcdf truncated — a rare edge case
    not worth a cross-project clobber. (When per-ctx ``_user_ts`` lands in a later
    phase this can reapply safely; today the netcdf-baked profiles are correct.)
    """
    from services.solver_service import SolverConfig

    nc_path = src / "network.nc"
    with ctx.mutation_lock:
        with PyPSAService.get_netcdf_io_lock():
            ctx.network.import_from_netcdf(str(nc_path))
        ctx.loaded_project = name

    # Solver config (legacy-tolerant; default when absent).
    cfg_path = src / "solver_config.json"
    if cfg_path.exists():
        ctx.solver_state["solver_config"] = _solver_config_from_dict(
            json.loads(cfg_path.read_text())
        )
    else:
        ctx.solver_state["solver_config"] = SolverConfig()

    # Side-results (LP/PF DataFrame pairs, VOLL capture, AC-PF convergence) into
    # THIS ctx's solver_state. Mirrors `_restore_results_state` but writes the
    # ctx dict directly instead of the active-scoped `_state_update`. Always
    # reset the keys first so a project without a pkl can't inherit ghost state.
    for k in _RESULTS_STATE_KEYS:
        ctx.solver_state[k] = None
    results_path = src / "results_state.pkl"
    if results_path.exists():
        try:
            data = _unwrap_results_state(_safe_unpickle_results(results_path.read_bytes()))
            if isinstance(data, dict):
                for k, v in data.items():
                    if k in _RESULTS_STATE_KEYS:
                        ctx.solver_state[k] = v
        except Exception as exc:
            change_log_service.log(
                "warn", "Project", name,
                f"Couldn't restore results_state.pkl for background solve "
                f"({type(exc).__name__}: {exc}). The netcdf still carries the "
                f"dispatch; only side-results are affected.",
            )

    try:
        ctx.network.name = name
    except Exception:
        pass

    # Lifecycle indicators (status/condition/objective/solve_time) from metadata
    # when the imported network carries dispatch — so the B8 cold-activate path
    # surfaces a previously-solved project as "completed" with its objective in
    # the StatusBar, instead of a misleading "idle" beside populated result
    # tables (B8 QA C2). Mirrors load_project's hydration. Harmless for the B4.3
    # dispatcher caller: it resets the lifecycle to "running" immediately after.
    nctx = ctx.network
    has_dispatch = network_has_dispatch(nctx)
    if has_dispatch:
        meta = _read_meta(src)
        ctx.solver_state["status"] = "completed"
        ctx.solver_state["condition"] = meta.get("condition") or "optimal"
        ctx.solver_state["objective"] = meta.get("objective")
        ctx.solver_state["solve_time"] = meta.get("solve_time")
    else:
        ctx.solver_state["status"] = "idle"
        ctx.solver_state["condition"] = None
        ctx.solver_state["objective"] = None
        ctx.solver_state["solve_time"] = None


@router.post("/{project_id}/activate")
def activate_project(
    project_id: str,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> dict:
    """
    Make ``project_id`` the active context — the B8 instant in-memory switch.

    The literal ``/activate`` suffix segment disambiguates this from
    ``GET /{name}`` (a bare single-segment match), so route ordering is moot.

    Two fast paths:
      * RESIDENT (a context for this id is already in the registry — e.g. it was
        loaded once via the old foreground path, or read path-scoped under B6, or
        solved in the background): ``set_active`` does a pure pointer swap under
        ``_registry_lock``. No disk I/O — the whole point.
      * NON-resident saved project: build a fresh ctx OFF TO THE SIDE, hydrate it
        from disk, then ``activate_context(register=True)`` to publish + register
        it atomically. (Slower the first time; instant thereafter.)

    Guard: refuse (409) if a FOREGROUND solve is in flight on the CURRENTLY-active
    ctx — moving ``_active`` out from under a live foreground ``/run`` is unsafe
    (B4.1 QA). A background QUEUE solve runs on its OWN ctx and does NOT trip
    ``_solver_in_flight()`` (which keys on the active ctx), so switching away
    while the queue solves a DIFFERENT project is allowed — that is the payoff.
    """
    if _auth_enabled():
        from services import project_registry

        project_registry.require_user(user)
        project = project_registry.resolve_project(db, user, project_id)
        src = project_registry.project_dir(project)
        # Reuse the project NAME as the process-global registry key / binding
        # (names are unique within an org; the singleton is process-global).
        project_id = project.name
    else:
        src = _safe_project_dir(project_id)  # 400 on bad id
    if not (src / "network.nc").exists():
        raise HTTPException(404, f"Project '{project_id}' not found")

    # Foreground-solve guard. Check BEFORE any swap so we never half-move state.
    # A solve in flight on the active ctx is safe to switch AWAY from IFF it is a
    # QUEUE dispatcher job solving the active project in place: the dispatcher
    # writes through a CAPTURED ctx closure (ctx.network / ctx.mutation_lock /
    # ctx_state_update over ctx.solver_state) — NOT the active `_state` proxy — so
    # moving `_active` to another project leaves it solving its captured ctx in
    # the background and saving itself on completion (B4.3 isolation). The active
    # project's running queue job also keeps it protected from B9 eviction. Only a
    # LEGACY `/run` worker (which writes via the active proxy that FOLLOWS
    # `_active`) is unsafe to switch away from → 409 only in THAT case.
    from routers.simulation import _solver_in_flight
    if _solver_in_flight():
        from services.solve_queue import solve_queue
        active_id = PyPSAService.get_active_id()
        queue_solving_active = active_id is not None and any(
            j.get("project_id") == active_id and j.get("status") == "running"
            for j in solve_queue.list_jobs()
        )
        if not queue_solving_active:
            # Structured detail so the chat agent can surface this as a
            # typed error frame (v4-MAJOR-1 family — solver_in_flight).
            raise HTTPException(
                status_code=409,
                detail={
                    "error_kind": "solver_in_flight",
                    "message": (
                        "Finish or abort the running solve before switching "
                        "projects."
                    ),
                },
            )

    evicted: list[str] = []
    if PyPSAService.get_context(project_id) is not None:
        # Resident → instant pointer swap. Already in the registry, so no new
        # registration and no eviction can fire.
        PyPSAService.set_active(project_id)
    else:
        # Cold: build, hydrate from disk, then publish + register atomically.
        # activate_context(register=True) runs the B9 cap check and returns any
        # project_ids evicted to make room — the freshly-activated project is
        # protected (it's the new active id) so it's never its own victim.
        ctx = PyPSAService.build_context()
        _hydrate_context_from_disk(ctx, src, project_id)
        evicted = PyPSAService.activate_context(ctx, register=True)

    # `evicted` lets the frontend drop the evicted projects' retained React Query
    # caches (the RAM the eviction reclaimed server-side has a UI mirror).
    return {"activated": project_id, "evicted": evicted}


@router.get("/{name}")
def load_project(
    name: str,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> ImportSummary:
    if _auth_enabled():
        from services import project_registry

        project_registry.require_user(user)
        project = project_registry.resolve_project(db, user, name)
        src = project_registry.project_dir(project)
        name = project.name
    else:
        src = _safe_project_dir(name)
    nc_path = src / "network.nc"
    if not nc_path.exists():
        raise HTTPException(404, f"Project '{name}' not found")

    # Crash-recovery surface. `_atomic_write_with` renames `.tmp → final` as
    # the last step; a `.tmp` sibling means a prior save was killed mid-write.
    # The main file is still the previous good version, but warn so the user
    # can investigate (the orphan won't auto-promote — that's a deliberate
    # safety choice; users decide whether to commit the partial write).
    tmp_orphan = nc_path.with_suffix(".nc.tmp")
    if tmp_orphan.exists():
        # Don't raise — load proceeds against the known-good `network.nc`.
        # Logged via the changelog so a UI banner can surface it later.
        change_log_service.log(
            "warn", "Project", name,
            f"Orphaned partial save detected at '{tmp_orphan.name}' (last save "
            f"was interrupted). Project loaded from the previous good copy.",
        )

    # Clear undo history — snapshots from a previous project are meaningless
    # after loading a different one.
    from services import undo_service
    undo_service.clear()

    with PyPSAService.get_lock():
        PyPSAService.reset_network()
        n = PyPSAService.get_network()
        with PyPSAService.get_netcdf_io_lock():
            n.import_from_netcdf(str(nc_path))
        # Bind identity atomically with the swap — inside the SAME lock that
        # `reset_network()` just set to None. Otherwise a concurrent save could
        # observe the transient unbound state (loaded is None) and wrongly
        # claim/overwrite. `n.name` (display) is set below; this binding is the
        # authoritative signal the save-time `expect` guard enforces against.
        PyPSAService.set_loaded_project(name)

    cfg_path = src / "solver_config.json"
    if cfg_path.exists():
        from routers.simulation import _state
        data = json.loads(cfg_path.read_text())
        # Shared legacy-tolerant loader: filter to the live dataclass field set
        # (a missing key picks up the current default; unknown keys would
        # otherwise raise TypeError) and coerce removed enum values. Same path
        # import_bundle uses, so both routes accept old files identically.
        _state["solver_config"] = _solver_config_from_dict(data)

    # Hydrate simulation state from metadata when the saved network has
    # dispatch tables. Without this, even though `n.generators_t.p` etc. are
    # populated by the netcdf import, the GUI's status indicator stays "idle"
    # and the SnapshotPicker stays disabled — confusing for users who saved
    # a solved project and expect to see results immediately on reload.
    n_loaded = PyPSAService.get_network()
    has_dispatch = network_has_dispatch(n_loaded)
    meta = _read_meta(src)
    # Corruption signal: the cached metadata.json bus_count is what the project
    # LIST shows (it never opens network.nc); load reads the actual file. A
    # mismatch means the cached metadata drifted from disk — interrupted save,
    # external edit, or a partial write — so the list may have advertised a
    # count the file doesn't back. Surface it via the changelog (same channel as
    # the orphan-.tmp warning) so a UI banner can flag possible corruption
    # rather than silently trusting either number. Only compare when metadata
    # actually recorded a count (a missing/corrupt metadata.json returns {}).
    cached_bus_count = meta.get("bus_count")
    actual_bus_count = len(n_loaded.buses)
    if cached_bus_count is not None and cached_bus_count != actual_bus_count:
        change_log_service.log(
            "warn", "Project", name,
            f"Metadata mismatch: project list cached bus_count={cached_bus_count} "
            f"but network.nc has {actual_bus_count} buses. Possible interrupted "
            f"save or external edit — verify the project is intact.",
        )
    # Atomic lifecycle hydration via `_state_update` so a concurrent
    # `GET /api/simulation/status` poll can't read a half-applied tuple
    # (see F11 / G2). Without this, header could briefly display
    # "Completed €n/a" or "Idle €123M" at the load instant.
    from routers.simulation import _state_update as _sim_state_update
    # Gate on `has_dispatch` ALONE — it's computed from the actual loaded
    # network DataFrames (authoritative), whereas `meta["has_results"]` is a
    # cached flag that can drift stale (e.g. a hand-edited / older bundle, or a
    # save that didn't refresh it). The old `and meta.get("has_results")`
    # AND-gate let a stale/absent flag suppress genuinely-present dispatch,
    # leaving the GUI showing "idle" on a solved project. Metadata is still
    # used for the display-only objective/condition/solve_time values.
    if has_dispatch:
        _sim_state_update(
            status="completed",
            condition=meta.get("condition") or "optimal",
            objective=meta.get("objective"),
            solve_time=meta.get("solve_time"),
        )
    else:
        # Loading an unsolved project clears any stale state from a prior run.
        _sim_state_update(status="idle", condition=None, objective=None, solve_time=None)

    # Restore the solve-time _state side-results from results_state.pkl
    # (counterpart of the save block in save_project). Same helper used by
    # the bundle-import path so both routes restore consistently.
    _restore_results_state(src, name)

    # Restore user-uploaded time series (always reset first, then reload if saved)
    from routers.network import (
        _ensure_snapshots_cover_user_ts,
        _reapply_user_ts_to_network,
        _restore_user_ts,
    )
    user_ts_path = src / "user_ts.json"
    if user_ts_path.exists():
        user_ts_data = json.loads(user_ts_path.read_text())
        _restore_user_ts(user_ts_data)
    else:
        _restore_user_ts({})

    # If user_ts.json contains a longer time index than network.nc captured,
    # expand n.snapshots so the GUI surfaces the real time-series resolution
    # (otherwise the loaded project would silently truncate to network.nc's
    # snapshot count and the user's uploaded data would be invisible).
    n = PyPSAService.get_network()
    with PyPSAService.get_lock():
        _ensure_snapshots_cover_user_ts(n)
    # Surface the project name to the GUI's StatusBar (which reads n.name via
    # /network/meta). The authoritative loaded-project binding was already set
    # atomically inside the swap lock above.
    n.name = name
    # Re-apply restored profiles to the network's _t tables (aligned to current
    # snapshots) so that simulation picks up the correct time series.
    _reapply_user_ts_to_network(n)
    # Register the now-bound active ctx in the resident registry (B8) so a later
    # tab switch back to this project finds it resident and does an INSTANT
    # in-memory pointer swap instead of re-loading from disk. Opening a project
    # the old (foreground) way therefore makes it switchable instantly next time.
    PyPSAService.register(name, PyPSAService.get_active_context())
    change_log_service.log(
        "load", "Project", name,
        f"Loaded project '{name}' ({len(n.buses)} buses, {len(n.generators)} generators, {len(n.snapshots)} snapshots)",
    )
    return ImportSummary(
        buses=len(n.buses),
        generators=len(n.generators),
        lines=len(n.lines),
        links=len(n.links),
        storage_units=len(n.storage_units),
        stores=len(n.stores),
        loads=len(n.loads),
        transformers=len(n.transformers),
        snapshots=len(n.snapshots),
    )


def _create_scenario_db(db, user, base: str, req: CreateScenarioRequest) -> ProjectInfo:
    """
    Auth-mode scenario create. Unlike the legacy path (which serialises the
    live in-memory singleton), this copies ``base``'s on-disk bundle into the
    child's org-scoped storage dir and records ``parent_project_id`` in the DB
    — so a member can branch a project they have tree access to without first
    loading it into the process-global network.
    """
    from services import project_registry

    project_registry.require_user(user)
    base_project = project_registry.resolve_project(db, user, base)
    base_dir = project_registry.project_dir(base_project)

    if project_registry.find_project(db, user, req.name) is not None:
        raise HTTPException(409, f"Project '{req.name}' already exists")

    desc = (req.description or "").strip() or None
    child = project_registry.create_scenario(
        db, user, base_project, req.name, scenario_description=desc
    )
    child_dir = project_registry.project_dir(child)

    try:
        for fname in _BUNDLE_FILES:
            src_file = base_dir / fname
            if src_file.exists():
                (child_dir / fname).write_bytes(src_file.read_bytes())
        _copy_bundle_dirs(base_dir, child_dir)

        # Keep metadata.json's NAME pointer in sync with the DB parent id so
        # bundle export/import (which only carries the flat metadata) round-trips
        # the scenario tree. The DB parent_project_id remains authoritative.
        meta = _read_meta(child_dir)
        now = datetime.now(tz=timezone.utc).isoformat()
        meta["parent_project"] = base_project.name
        meta["scenario_description"] = desc
        meta.setdefault("created_at", now)
        meta["last_saved"] = now
        _write_meta(child_dir, meta)
    except Exception:
        try:
            project_registry.delete_project_row(db, child)
        except Exception:
            pass
        try:
            _force_rmtree(child_dir)
        except OSError:
            pass
        raise

    change_log_service.log(
        "scenario_create", "Project", req.name,
        f"Created scenario '{req.name}' from base '{base_project.name}'"
        + (f": {desc[:80]}" if desc else ""),
    )
    return _project_info_db(db, child)


@router.post("/{base}/scenarios", status_code=201)
def create_scenario(
    base: str,
    req: CreateScenarioRequest,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> ProjectInfo:
    """
    Create a scenario branched off ``base``.

    The current in-memory network is saved as a new project named ``req.name``
    with ``metadata.parent_project = base``. The base project on disk is NOT
    modified — the scenario diverges from the in-memory state at the moment
    of creation. The new scenario does not auto-activate; the caller (UI)
    decides whether to switch.

    Status: 201 on success, 400 for invalid name / self-parent, 404 if base
    is missing, 409 if the target name already exists.
    """
    if _auth_enabled():
        return _create_scenario_db(db, user, base, req)

    # 1. Validate base
    base_dir = _safe_project_dir(base)
    if not base_dir.exists() or not (base_dir / "network.nc").exists():
        raise HTTPException(404, f"Base project '{base}' not found")

    # 2. Self-parent check first (clearer error semantics than 'already
    # exists' when names match), then name validity / collision.
    if req.name == base:
        raise HTTPException(400, "Scenario name cannot match its base project name")
    new_dir = _safe_project_dir(req.name)
    if new_dir.exists():
        raise HTTPException(409, f"Project '{req.name}' already exists")

    # 3. The scenario branches FROM `base` — so the in-memory network we're
    # about to serialise MUST actually be `base`'s state. `save_project`
    # writes `PyPSAService.get_network()`, the live singleton; if the user
    # is on a *different* project, the new scenario would get `base`'s
    # parent pointer but a *foreign* topology. `load_project` stamps
    # `n.name = <project>` so the in-memory name is the reliable signal.
    # Reject the mismatch and tell the UI to load the base first.
    n_check = PyPSAService.get_network()
    if n_check.buses.empty:
        raise HTTPException(
            400,
            "Cannot create scenario from an empty in-memory network. "
            f"Load '{base}' first, then branch.",
        )
    if getattr(n_check, "name", None) != base:
        raise HTTPException(
            409,
            f"Active project is '{getattr(n_check, 'name', '?')}', not '{base}'. "
            f"Load '{base}' as the active project before branching a scenario "
            f"from it — otherwise the scenario would carry the wrong topology.",
        )

    desc = (req.description or "").strip() or None

    # 4. Save current state under the new name. By passing the scenario-tree
    # fields via the `*_override` kwargs, save_project writes them in a
    # SINGLE atomic metadata-write — no half-state where a concurrent
    # GET /api/projects would observe the new project as a root.
    # The RLock allows save_project's own `with get_lock():` to no-op
    # rather than deadlock. A `try/except → rmtree` cleans up the directory
    # on partial failure so a dangling half-create can't appear as an
    # orphan-root in list_projects.
    try:
        with PyPSAService.get_lock():
            _save_context(
                PyPSAService.get_active_context(),
                req.name,
                force=True,
                parent_project_override=base,
                scenario_description_override=desc,
                apply_overrides=True,
            )
    except Exception:
        if new_dir.exists():
            # Best-effort cleanup of a half-created project dir. `_force_rmtree`
            # handles read-only/locked files; swallow a final failure so the
            # original error (not the cleanup's) is what propagates.
            try:
                _force_rmtree(new_dir)
            except OSError:
                pass
        raise

    change_log_service.log(
        "scenario_create", "Project", req.name,
        f"Created scenario '{req.name}' from base '{base}'"
        + (f": {desc[:80]}" if desc else ""),
    )

    return _project_info(new_dir)


def _delete_project_db(db, user, name: str, cascade: bool) -> dict:
    """
    Auth-mode delete. Access is gated by ``can_delete_project`` (org admin,
    tree-root creator, or the scenario's own creator); descendants come from
    the DB parent graph and are removed leaves-first with their storage dirs.
    """
    from services import project_acl, project_registry

    project_registry.require_user(user)
    project = project_registry.resolve_project(db, user, name)
    if not project_acl.can_delete_project(db, user, project):
        raise HTTPException(403, f"You do not have permission to delete '{project.name}'")

    child_projects = project_registry.descendants(db, project)
    if child_projects and not cascade:
        names = [c.name for c in child_projects]
        preview = ", ".join(names[:5])
        ellipsis = "…" if len(names) > 5 else ""
        raise HTTPException(
            status_code=409,
            detail={
                "error_kind": "descendants_exist",
                "message": (
                    f"Cannot delete '{project.name}' — it has {len(names)} "
                    f"child scenario(s): {preview}{ellipsis}. Pass "
                    f"?cascade=true to delete recursively."
                ),
                "descendants": names,
            },
        )

    deleted: list[str] = []
    failed: list[str] = []
    # Leaves-first: reverse the BFS order so a child is always removed before
    # its parent, keeping the DB parent graph consistent at every step.
    targets = list(reversed(child_projects)) + [project]
    for target in targets:
        target_dir = pathlib.Path(target.storage_path)
        try:
            if target_dir.exists():
                _force_rmtree(target_dir)
        except OSError as exc:  # noqa: BLE001
            failed.append(target.name)
            change_log_service.log(
                "warn", "Project", target.name,
                f"Could not fully delete '{target.name}': {exc}.",
            )
            continue
        project_registry.delete_project_row(db, target)
        deleted.append(target.name)
        try:
            PyPSAService.drop(target.name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not evict resident context for '%s': %s", target.name, exc)

    change_log_service.log("delete_project", "Project", project.name, f"Deleted project '{project.name}'")
    if failed and not deleted:
        raise HTTPException(500, f"Failed to delete '{project.name}': {', '.join(failed)}.")
    return {"deleted": deleted, "failed": failed}


@router.delete("/{name}")
def delete_project(
    name: str,
    cascade: bool = False,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> dict:
    """
    Delete a project. By default, refuses if the project has children
    (scenarios pointing to it as their parent). Pass ``?cascade=true`` to
    delete recursively.

    Cascade deletion processes leaves-first (reverse-BFS) so the on-disk
    tree stays consistent at every step — a crash mid-cascade leaves a
    valid (smaller) tree, not a dangling-parent state.

    Returns ``{"deleted": [name, ...descendants]}`` (200, not 204) so the
    caller can reconcile client state — specifically, the frontend must
    clear `currentProject` if it (or one of its ancestors) was in the
    deleted set, otherwise the autosave loop would resurrect the deleted
    project directory on its next tick.
    """
    if _auth_enabled():
        return _delete_project_db(db, user, name, cascade)

    dest = _safe_project_dir(name)
    if not dest.exists():
        raise HTTPException(404, f"Project '{name}' not found")

    descendants = _find_descendants(name)
    if descendants and not cascade:
        preview = ", ".join(descendants[:5])
        ellipsis = "…" if len(descendants) > 5 else ""
        # Structured detail dict so the chat agent's _dispatch_real_tool_call
        # can extract error_kind='descendants_exist' and surface the cascade
        # confirmation flow (v4-MINOR-1). The `descendants` list is included
        # so the ChatPanel can render the full preview in the error banner.
        raise HTTPException(
            status_code=409,
            detail={
                "error_kind": "descendants_exist",
                "message": (
                    f"Cannot delete '{name}' — it has {len(descendants)} "
                    f"child scenario(s): {preview}{ellipsis}. Pass "
                    f"?cascade=true to delete recursively."
                ),
                "descendants": descendants,
            },
        )

    # `deleted` accumulates only names whose directory was ACTUALLY removed —
    # a name is appended after its `rmtree` succeeds, never before. The
    # frontend uses this list to reconcile `currentProject`; a name that
    # claims deletion but is still on disk would wrongly clear the active
    # project. rmtree is wrapped because on Windows a file lock (OneDrive
    # sync handle, AV scan, an open netcdf) can make it raise mid-tree.
    deleted: list[str] = []
    failed: list[str] = []

    def _rmtree(d: pathlib.Path, label: str) -> bool:
        # `_force_rmtree` clears read-only attributes and retries with a short
        # backoff — handles the Windows / OneDrive locks that made a plain
        # `shutil.rmtree` raise WinError 5 mid-tree.
        try:
            _force_rmtree(d)
            return True
        except OSError as exc:  # noqa: BLE001 — genuinely-locked file/dir
            failed.append(label)
            change_log_service.log(
                "warn", "Project", label,
                f"Could not fully delete '{label}': {exc}. Retry, or remove "
                f"the directory manually.",
            )
            return False

    if cascade and descendants:
        # Reverse so leaves (last appended in BFS) get deleted first — a
        # crash mid-cascade leaves a valid (smaller) tree, not orphans.
        for child in reversed(descendants):
            child_dir = PROJECTS_DIR / child
            if child_dir.is_dir():
                if _rmtree(child_dir, child):
                    deleted.append(child)
                    change_log_service.log(
                        "delete_project", "Project", child,
                        f"Cascade-deleted scenario '{child}' (descendant of '{name}')",
                    )

    if _rmtree(dest, name):
        deleted.append(name)
        summary = f"Deleted project '{name}'"
        if cascade and descendants:
            summary += f" + {len(deleted) - 1} descendant scenario(s)"
        change_log_service.log("delete_project", "Project", name, summary)

    # Evict each deleted project's RESIDENT in-memory context.
    #
    # Removing the directory is not enough. `activate_project` has a resident
    # fast path — if a ctx for that project_id is still in the registry it does
    # a pure pointer swap with NO disk I/O. So delete → recreate the same name
    # from a template → activate served the DELETED project's network: the new
    # project appeared with ghost components that were never in its template,
    # and the next save persisted them to disk.
    #
    # Reproduced end-to-end: create from 3bus (Bus 0/1/2), add 'qa_bus',
    # delete, recreate the same name from 3bus, activate → four buses
    # including 'qa_bus'.
    #
    # `drop` is a no-op for a name that was never resident, so this is safe for
    # every deleted entry. It deliberately does not touch `_active`: if the
    # deleted project was the active one the in-memory network stays put, which
    # is the pre-existing contract the frontend relies on (it clears
    # `currentProject` from the returned `deleted` list — see the docstring).
    for gone in deleted:
        try:
            PyPSAService.drop(gone)
        except Exception as exc:                       # noqa: BLE001
            logging.getLogger(__name__).warning(
                "could not evict resident context for '%s': %s", gone, exc)

    if failed and not deleted:
        # Nothing actually got removed — surface a hard error rather than a
        # misleading 200 with an empty `deleted` list.
        raise HTTPException(
            500,
            f"Failed to delete '{name}': {', '.join(failed)} could not be "
            f"removed (file lock?). Nothing was deleted.",
        )
    return {"deleted": deleted, "failed": failed}


def _rename_project_db(db, user, name: str, req: RenameProjectRequest) -> ProjectInfo:
    """
    Auth-mode rename. The storage dir is UUID-keyed so it never moves — only
    the DB ``name`` (and the metadata.json name/parent pointers used for bundle
    round-trips) change. Permission mirrors delete (org admin, tree-root
    creator, or the scenario's own creator).
    """
    from services import project_acl, project_registry

    project_registry.require_user(user)
    new_name = req.new_name.strip()
    if not new_name:
        raise HTTPException(400, "new_name is required")

    project = project_registry.resolve_project(db, user, name)
    if not project_acl.can_delete_project(db, user, project):
        raise HTTPException(403, f"You do not have permission to rename '{project.name}'")
    old_name = project.name
    if new_name == old_name:
        raise HTTPException(400, "new_name must differ from the current name")
    if project_registry.find_project(db, user, new_name) is not None:
        raise HTTPException(409, f"Project '{new_name}' already exists")

    children = project_registry.direct_children(db, project)
    project = project_registry.rename_project(db, project, new_name)

    # Sync metadata.json name pointer on the renamed project + parent pointer
    # on direct children (best-effort; DB is authoritative).
    dest = pathlib.Path(project.storage_path)
    meta = _read_meta(dest)
    if meta:
        meta["name"] = new_name
        meta["last_saved"] = datetime.now(tz=timezone.utc).isoformat()
        try:
            _write_meta(dest, meta)
        except OSError:
            pass
    for child in children:
        child_dir = pathlib.Path(child.storage_path)
        child_meta = _read_meta(child_dir)
        if child_meta.get("parent_project") == old_name:
            child_meta["parent_project"] = new_name
            try:
                _write_meta(child_dir, child_meta)
            except OSError:
                pass

    with PyPSAService.get_lock():
        if PyPSAService.get_loaded_project() == old_name:
            n = PyPSAService.get_network()
            try:
                n.name = new_name
            except Exception:
                pass
            PyPSAService.set_loaded_project(new_name)

    change_log_service.log("rename_project", "Project", new_name, f"Renamed project '{old_name}' → '{new_name}'")
    return _project_info_db(db, project)


@router.post("/{name}/rename")
def rename_project(
    name: str,
    req: RenameProjectRequest,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> ProjectInfo:
    """
    Rename a project on disk and reparent any child scenarios.

    Side effects, in order:

      1. Directory rename ``projects/{name}/`` → ``projects/{new_name}/`` —
         atomic on the same filesystem so a crash mid-rename leaves either
         the old or the new path on disk, never both.
      2. ``metadata.json`` `name` and `last_saved` fields updated at the
         new location.
      3. If the renamed project is currently the active singleton, the
         in-memory ``n.name`` is updated to match — subsequent autosaves
         then target the new directory. Protected by the PyPSA lock.
      4. Every direct-child scenario's ``parent_project`` pointer is
         rewritten to the new name. We only update direct children — the
         transitive descendants reference their immediate parent, not the
         root, so the tree stays consistent without recursion.
      5. A changelog entry is recorded summarising the rename.

    Errors:
      400 — invalid new name, or new name == old name
      404 — source project not found
      409 — target name already exists
      500 — directory rename failed (file lock, AV, OneDrive sync handle)
    """
    if _auth_enabled():
        return _rename_project_db(db, user, name, req)

    new_name = req.new_name.strip()
    if not new_name:
        raise HTTPException(400, "new_name is required")
    if new_name == name:
        raise HTTPException(400, "new_name must differ from the current name")

    src = _safe_project_dir(name)
    if not src.exists():
        raise HTTPException(404, f"Project '{name}' not found")
    # _safe_project_dir validates `new_name` (charset, length, no traversal).
    dest = _safe_project_dir(new_name)
    if dest.exists():
        raise HTTPException(409, f"Project '{new_name}' already exists")

    # Snapshot the child list BEFORE the rename — once the parent's name
    # changes, _find_direct_children couldn't find them by the old name.
    children = _find_direct_children(name)

    # 1) Directory rename. pathlib.Path.rename is atomic within the same FS
    # — the most likely failure mode on Windows is a sync handle (OneDrive)
    # or AV scan holding a file inside; we surface that as a 500.
    try:
        src.rename(dest)
    except OSError as exc:  # noqa: BLE001
        raise HTTPException(
            500,
            f"Could not rename '{name}' → '{new_name}': {exc}. "
            f"A file inside the project directory may be locked (AV / OneDrive). "
            f"Retry, or close any open netcdf reader and try again.",
        )

    # 2) Refresh metadata at the new location.
    meta_path = _meta_path(dest)
    if meta_path.exists():
        try:
            existing_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(existing_meta, dict):
                existing_meta = {}
        except Exception:
            existing_meta = {}
        existing_meta["name"] = new_name
        existing_meta["last_saved"] = datetime.now(timezone.utc).isoformat()
        try:
            _atomic_write_text(meta_path, json.dumps(existing_meta, indent=2))
        except OSError as exc:
            # The rename already succeeded — best-effort metadata fix; warn
            # but don't roll back, since the project IS at the new path.
            change_log_service.log(
                "warn", "Project", new_name,
                f"Renamed but could not refresh metadata.json ({exc}); "
                f"next save will rewrite it.",
            )

    # 3) Sync the in-memory n.name AND the authoritative loaded-project
    # identity if the renamed project is the active one. Without the n.name
    # sync, the next autosave would persist into a project DIRECTORY named
    # `new_name` but with `n.name == old_name` baked into the netcdf — an
    # inconsistency that surfaces months later when `load_project` stamps
    # `n.name = <dir_name>` and the user wonders why exports show "old_name".
    # Re-binding `_loaded_project` keeps the save-time `expect` guard accepting
    # the new name (the on-disk folder moved); without it, an autosave under
    # `new_name` would 409 against the stale `old_name` binding.
    with PyPSAService.get_lock():
        n = PyPSAService.get_network()
        # Key off the AUTHORITATIVE binding, not the mutable display `n.name`.
        # If the renamed project is the loaded one, follow both the binding and
        # the display title to the new name. Gating on `n.name == name` too
        # would wrongly rebind an UNBOUND network (e.g. a raw netcdf import)
        # whose display title happened to match the renamed folder.
        if PyPSAService.get_loaded_project() == name:
            try:
                n.name = new_name
            except Exception:
                pass
            PyPSAService.set_loaded_project(new_name)

    # 4) Reparent direct children. We only touch direct children because
    # `parent_project` is a single-level pointer — descendants reference
    # their immediate parent, not the root.
    reparented: list[str] = []
    for child in children:
        child_dir = PROJECTS_DIR / child
        child_meta_path = _meta_path(child_dir)
        if not child_meta_path.exists():
            continue
        try:
            child_meta = json.loads(child_meta_path.read_text(encoding="utf-8"))
            if not isinstance(child_meta, dict):
                continue
        except Exception:
            continue
        if child_meta.get("parent_project") == name:
            child_meta["parent_project"] = new_name
            try:
                _atomic_write_text(child_meta_path, json.dumps(child_meta, indent=2))
                reparented.append(child)
            except OSError:
                # Non-fatal — surfaces in changelog so the user can re-link
                # manually via the Scenarios panel.
                change_log_service.log(
                    "warn", "Project", child,
                    f"Could not update parent_project pointer in '{child}' "
                    f"after renaming '{name}' → '{new_name}'. "
                    f"Re-link via Scenarios panel.",
                )

    summary = f"Renamed project '{name}' → '{new_name}'"
    if reparented:
        summary += f" + reparented {len(reparented)} child scenario(s)"
    change_log_service.log("rename_project", "Project", new_name, summary)

    # Chatbot integration v6 Phase 4 — invalidate the cached chat.jsonl
    # persist_path so subsequent `chat_service.get_persist_path(ctx)` calls
    # re-resolve under the new project directory. The filesystem directory
    # has already been renamed above, so chat.jsonl follows automatically;
    # only the per-context cache needs to be cleared. Closes Phase 3 Gate
    # C-13 gap.
    try:
        active_ctx = PyPSAService.get_active_context()
        if active_ctx.loaded_project == new_name:
            from services import chat_service
            chat_service.handle_rename_lineage(active_ctx, name, new_name)
    except Exception:  # noqa: BLE001 — never abort rename on lineage failure
        pass

    return _project_info(dest)


# Component dispatch frames surfaced in the background results bundle (A6) — the
# headline per-snapshot time series a finished queued project produced, served
# WITHOUT loading it into the active slot. Each is (bundle_key, accessor, attr).
_BUNDLE_FRAMES = (
    ("generators", "generators_t", "p"),
    ("storage_soc", "storage_units_t", "state_of_charge"),
    ("line_loading", "lines_t", "p0"),
)


@router.get("/{name}/results_bundle")
def get_results_bundle(name: str, source: str = "lopf"):
    """
    Read-only per-snapshot dispatch for a SAVED (non-active) project — the
    multi-project solve queue's "view a finished job's results without loading
    it" path (Phase A / C1). Loads network.nc into a TRANSIENT network (the
    active singleton is untouched, exactly like /compare-state) and returns the
    headline dispatch time series the project's last solve produced.

    Unlike /compare-state (aggregated KPI summaries), this returns the
    per-snapshot {index, columns, data} payloads the Dispatch charts render.

    `source`:
      • "lopf" (default) — dispatch from the saved network's own _t tables.
      • "ac_pf"          — dispatch from results_state.pkl's `ac_pf_results`
                           snapshot (the AC power-flow stage), falling back
                           per-frame to the network's _t when a key is absent
                           (same fallback contract as /results/* ?source=).
    Invalid values coerce to "lopf" (fail-soft).

    204 when the project has no dispatch at all (never solved); 404 when the
    project / network.nc is missing.
    """
    import pypsa as _pypsa
    from fastapi import Response

    from services.serialization import ts_payload as _ts_payload

    src = source if source in ("lopf", "ac_pf") else "lopf"
    project_dir = _safe_project_dir(name)
    nc_path = project_dir / "network.nc"
    if not nc_path.exists():
        raise HTTPException(404, f"Project '{name}' not found")

    temp_n = _pypsa.Network()
    try:
        with PyPSAService.get_netcdf_io_lock():
            temp_n.import_from_netcdf(str(nc_path))
    except Exception as exc:  # noqa: BLE001 — surface PyPSA-side errors, path-stripped
        msg = str(exc).replace(str(PROJECTS_DIR.resolve()), "<projects>")
        raise HTTPException(500, f"Failed to load project '{name}': {msg}")

    # AC-PF dispatch (+ convergence) live ONLY in results_state.pkl — the netcdf
    # can't carry them. None when the project never ran Stage 2. Same shape as
    # _state["ac_pf_results"]: a {'<accessor>_t.<attr>': DataFrame} dict.
    ac_pf_snap: dict | None = None
    ac_pf_convergence_list = None
    results_path = project_dir / "results_state.pkl"
    if results_path.exists():
        try:
            data = _unwrap_results_state(_safe_unpickle_results(results_path.read_bytes()))
            if isinstance(data, dict):
                snap = data.get("ac_pf_results")
                ac_pf_snap = snap if isinstance(snap, dict) else None
                ac_pf_convergence_list = data.get("ac_pf_convergence_list")
        except Exception:  # noqa: BLE001 — corrupt pkl ⇒ treat AC-PF as absent
            ac_pf_snap = None

    def _frame(accessor_name: str, attr: str):
        """AC-PF snapshot first (source=ac_pf), else the network's own _t frame."""
        key = f"{accessor_name}.{attr}"
        if src == "ac_pf" and isinstance(ac_pf_snap, dict) and key in ac_pf_snap:
            return ac_pf_snap[key]
        acc = getattr(temp_n, accessor_name, None)
        return getattr(acc, attr, None) if acc is not None else None

    def _payload_or_none(df):
        if df is None or getattr(df, "empty", True):
            return None
        try:
            return _ts_payload(df)
        except Exception:  # noqa: BLE001
            return None

    frames = {key: _payload_or_none(_frame(acc, attr)) for key, acc, attr in _BUNDLE_FRAMES}

    lopf_available = bool(
        getattr(temp_n, "is_solved", False) and network_has_dispatch(temp_n)
    )
    ac_pf_available = isinstance(ac_pf_snap, dict) and len(ac_pf_snap) > 0

    if not any(v is not None for v in frames.values()) and not (lopf_available or ac_pf_available):
        return Response(status_code=204)

    # Carrier map so the client can group/colour the generation stack without a
    # second query (the saved network owns the generator→carrier mapping).
    carriers: dict[str, str] = {}
    if not temp_n.generators.empty and "carrier" in temp_n.generators.columns:
        carriers = {str(k): str(v) for k, v in temp_n.generators["carrier"].items()}

    meta = _read_meta(project_dir)
    return {
        "available": any(v is not None for v in frames.values()),
        "source": src,
        "source_available": {"lopf": lopf_available, "ac_pf": ac_pf_available},
        "objective": meta.get("objective"),
        "condition": meta.get("condition"),
        "solve_time": meta.get("solve_time"),
        "ac_pf_convergence_list": ac_pf_convergence_list if src == "ac_pf" else None,
        "carriers": carriers,
        "generators": frames.get("generators"),
        "storage_soc": frames.get("storage_soc"),
        "line_loading": frames.get("line_loading"),
    }


@router.get("/{name}/layout")
def get_layout(name: str) -> dict:
    """
    Read a project's blank-canvas layout — the "latent coordinates".

    Returns the opaque layout document the frontend canvas persists (node
    positions + edge waypoints), or ``{}`` when none has been saved yet.

    This is deliberately a project sub-resource, NOT part of the network
    model: the layout is pure presentation state, decoupled from the
    geographic ``bus.x`` / ``bus.y`` that the satellite/hybrid map view and
    the clustering algorithms consume. The backend treats the document as
    opaque — its internal shape is the frontend canvas's concern.
    """
    src = _safe_project_dir(name)
    if not src.exists():
        raise HTTPException(404, f"Project '{name}' not found")
    layout_path = src / "layout.json"
    if not layout_path.exists():
        return {}
    try:
        data = json.loads(layout_path.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        # Corrupted layout — degrade to "no layout" so the canvas falls back
        # to its layout algorithm rather than 500-ing the whole project open.
        return {}
    return data if isinstance(data, dict) else {}


@router.put("/{name}/layout")
def put_layout(name: str, layout: dict) -> dict:
    """
    Persist the blank-canvas layout for a project.

    The body is an opaque JSON object — the backend does not interpret its
    structure. It is written atomically to ``layout.json`` in the project
    directory and travels with the project bundle via ``_BUNDLE_FILES``.
    Capped at ``_MAX_LAYOUT_BYTES`` to bound a malformed/abusive payload.
    """
    dest = _safe_project_dir(name)
    if not dest.exists():
        raise HTTPException(404, f"Project '{name}' not found")
    # Compact (no indent): layout.json is a machine-written coordinate blob,
    # never hand-read — pretty-printing only inflates the on-disk size.
    serialized = json.dumps(layout, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > _MAX_LAYOUT_BYTES:
        raise HTTPException(
            413,
            f"Layout document exceeds the {_MAX_LAYOUT_BYTES // (1024 * 1024)} MB cap.",
        )
    _atomic_write_text(dest / "layout.json", serialized)
    return {"saved": name}


def _project_bundle_bytes(name: str, src: pathlib.Path | None = None) -> bytes:
    """
    Materialise a zipped project bundle (.pypsaproj.zip) as bytes.

    Bundles every project file in `_BUNDLE_FILES` (network.nc, user_ts.json,
    solver_config.json, metadata.json, layout.json) into one zip so the user
    can store it anywhere. The schematic layout travels with the bundle so the
    blank-canvas view survives a round-trip through another machine.

    Split out from `download_bundle` so the chat-tool layer can persist the
    bytes as a downloadable `agent_export` artifact (a StreamingResponse can't
    be returned as a chat-tool result). ``src`` lets an auth-mode caller pass a
    pre-resolved (ACL-gated, org-scoped) storage dir instead of the flat path.
    """
    if src is None:
        src = _safe_project_dir(name)
    nc_path = src / "network.nc"
    if not nc_path.exists():
        raise HTTPException(404, f"Project '{name}' not found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in _BUNDLE_FILES:
            p = src / fname
            if p.exists():
                zf.write(p, arcname=fname)
        # Chatbot uploads (Phase A) — locked decision row 6: uploads/ travels
        # with the bundle. Walk each _BUNDLE_DIRS recursively so an exported
        # bundle round-trips on import with all reference files intact.
        for dname in _BUNDLE_DIRS:
            d = src / dname
            if not d.is_dir():
                continue
            for root, _dirs, files in os.walk(d):
                root_p = pathlib.Path(root)
                for fname in files:
                    full = root_p / fname
                    arc = full.relative_to(src).as_posix()
                    zf.write(full, arcname=arc)
    change_log_service.log(
        "export", "Project", name, f"Downloaded project bundle '{name}.pypsaproj.zip'",
    )
    return buf.getvalue()


@router.get("/{name}/bundle")
def download_bundle(
    name: str,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Stream a zipped project bundle (.pypsaproj.zip) to the browser."""
    src = None
    if _auth_enabled():
        from services import project_registry

        project_registry.require_user(user)
        project = project_registry.resolve_project(db, user, name)
        src = project_registry.project_dir(project)
        name = project.name
    return StreamingResponse(
        io.BytesIO(_project_bundle_bytes(name, src)),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}.pypsaproj.zip"'},
    )


@router.get("/{name}/members")
def get_project_members(
    name: str,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> list[dict]:
    """
    List the members assigned to a project's tree root (auth mode only).

    Membership always attaches to the tree ROOT — the ACL grants access to
    every project in a tree via a single root membership — so this returns the
    root's assignment list regardless of which node in the tree is addressed.
    """
    if not _auth_enabled():
        raise HTTPException(404, "Project membership requires authentication to be enabled")
    from services import project_registry

    project_registry.require_user(user)
    project = project_registry.resolve_project(db, user, name)
    return project_registry.list_root_members(db, project)


@router.put("/{name}/members")
def put_project_members(
    name: str,
    body: MembersRequest,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> list[dict]:
    """
    Replace the member set on a project's tree root (auth mode only).

    Requires manage permission (org admin or the tree-root creator). Each
    supplied user id must belong to the project's organization.
    """
    if not _auth_enabled():
        raise HTTPException(404, "Project membership requires authentication to be enabled")
    import uuid as _uuid

    from services import project_acl, project_registry

    project_registry.require_user(user)
    project = project_registry.resolve_project(db, user, name)
    if not project_acl.can_manage_membership(db, user, project):
        raise HTTPException(403, "You do not have permission to manage members on this project")

    try:
        user_ids = [_uuid.UUID(str(uid)) for uid in body.user_ids]
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "user_ids must be valid UUID strings") from exc

    return project_registry.set_root_members(db, user, project, user_ids)


@router.get("/{name}/statistics")
def project_statistics(name: str):
    src = _safe_project_dir(name)
    nc_path = src / "network.nc"
    if not nc_path.exists():
        raise HTTPException(404, f"Project '{name}' not found")
    import pypsa
    # netCDF4/HDF5 share process-global, thread-unsafe state — this transient
    # read must hold the io lock or it races concurrent reads/writes (e.g. a
    # compare-state/results-summary fan-out, or a background solve once that
    # lands) and surfaces as "NetCDF: HDF error". No pypsa lock needed (we
    # never touch the live singleton here).
    with PyPSAService.get_netcdf_io_lock():
        n = pypsa.Network(str(nc_path))
    meta = _read_meta(src)
    return {
        "name": name,
        "meta": meta,
        "buses": len(n.buses),
        "generators": len(n.generators),
        "lines": len(n.lines),
        "snapshots": len(n.snapshots),
        "carriers": n.generators.carrier.unique().tolist() if not n.generators.empty else [],
    }
