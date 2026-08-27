"""
Project-level snapshot router.

A snapshot is a frozen copy of a project's bundle files (`network.nc`,
`user_ts.json`, `solver_config.json`, `metadata.json`, `layout.json` — the
full `_BUNDLE_FILES` set) plus a small `snapshot.json` describing it.
Snapshots live under
``projects/<name>/snapshots/<snapshot_id>/`` where ``snapshot_id`` is
``<iso-ts>-<slug>`` so they list newest-first on lexicographic sort.

Operations:

  POST   /api/projects/{name}/snapshots                  create
  GET    /api/projects/{name}/snapshots                  list (newest first)
  POST   /api/projects/{name}/snapshots/{sid}/restore    restore + load
  DELETE /api/projects/{name}/snapshots/{sid}            delete one

Cap is :data:`_MAX_SNAPSHOTS_PER_PROJECT`. When the cap is reached, the
oldest snapshot is pruned before creating the new one. This is the same
"FIFO history" model VS Code and Figma use; users always get the most
recent N restore points without the dir growing unbounded.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pypsa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession
from db.models import Session as SessionRow, User
from db.session import get_db
from deps import current_session, optional_user
from services import active_project, change_log_service, project_registry
from services.dispatch_status import (
    dispatch_status as _classify_dispatch,
    network_has_dispatch,
)
from routers.deps import AuthorizedProject, ProjectAccessDep
from services.pypsa_service import PyPSAService

# Reuse the same path-validation pattern as projects.py — snapshots inherit the
# project-name safety check by going through `_safe_project_dir`.
from services.atomic_io import (
    atomic_write_text as _atomic_write_text,
    atomic_write_with as _atomic_write_with,
)
from routers.projects import (
    _BUNDLE_FILES,
    _enforce_project_lock,
    _force_rmtree,
    _read_meta,
    _restore_results_state,
    _safe_project_dir,
    _solver_config_from_dict,
)

router = APIRouter()


def _lock_target(project: AuthorizedProject) -> SimpleNamespace:
    """
    Adapt an `AuthorizedProject` (an id/name/directory view built for ACL, not
    an ORM row) into the shape `_enforce_project_lock` needs: `.id` as the
    `uuid.UUID` the lock table keys on (matches `Project.id`), and `.name` for
    the error message. `AuthorizedProject.uuid` carries the same value as a
    plain string.
    """
    return SimpleNamespace(id=uuid.UUID(project.uuid), name=project.name)

_MAX_SNAPSHOTS_PER_PROJECT = 50

# Same-shape regex as project names but allows the iso-timestamp prefix
# (`2026-05-13T14-30-00-mylabel`). Snapshot ids are generated server-side from
# a sanitized timestamp + label so the only attack surface is the path-traversal
# check below, but we still validate the form for defence in depth. The `T`
# separator and `-` digit-group dividers are the only non-alphanumerics that
# leak out of strftime + slugify.
_SNAPSHOT_ID_RE = re.compile(r"^[A-Za-z0-9_\-.T]{1,128}$")
_LABEL_RE = re.compile(r"[^A-Za-z0-9_\-]+")


class CreateSnapshotRequest(BaseModel):
    label: str = ""
    message: str | None = ""


class SnapshotInfo(BaseModel):
    id: str
    label: str
    message: str
    created_at: str
    # Compact summary of the project state at snapshot time. Lets the UI render
    # "14 buses · 168 snapshots · Obj €1.234B" without re-opening the .nc.
    bus_count: int
    snapshot_count: int
    objective: float | None = None
    has_results: bool = False
    # Internal consistency of the snapshot's own dispatch tables. 'fresh' =
    # dispatch column-set matches the snapshot's topology (true SOLVED).
    # 'stale' = the snapshot was saved after a topology edit without a re-run
    # — dispatch is for a different generator/bus set than the snapshot
    # contains. 'none' = no dispatch in the snapshot at all (truly unsolved).
    # See services/dispatch_status.py for the classification rules.
    dispatch_status: str = "none"


def _snapshots_dir(project_dir: pathlib.Path) -> pathlib.Path:
    return project_dir / "snapshots"


def _safe_snapshot_dir(project_dir: pathlib.Path, snapshot_id: str) -> pathlib.Path:
    """
    Validate snapshot_id and return its directory under the project.

    Mirrors :func:`_safe_project_dir`'s defence-in-depth pattern: regex on the
    id plus a `is_relative_to` check on the resolved path.
    """
    if not isinstance(snapshot_id, str) or not _SNAPSHOT_ID_RE.match(snapshot_id):
        raise HTTPException(400, f"Invalid snapshot id: {snapshot_id!r}")
    if snapshot_id.startswith(".") or snapshot_id in ("..", "."):
        raise HTTPException(400, "Snapshot id may not start with '.'")
    root = _snapshots_dir(project_dir).resolve()
    dest = (_snapshots_dir(project_dir) / snapshot_id).resolve()
    try:
        if not dest.is_relative_to(root):
            raise HTTPException(400, "Path traversal rejected.")
    except ValueError:
        raise HTTPException(400, "Path traversal rejected.")
    return dest


def _slugify_label(label: str) -> str:
    """
    Convert a free-form label to a filename-safe slug.

    Empty / whitespace-only labels collapse to ``"snapshot"`` so the id always
    has a meaningful suffix. Anything outside ``[A-Za-z0-9_-]`` is replaced
    with ``-`` and leading/trailing dashes are trimmed. Capped to 32 chars so
    the full ``<iso>-<slug>`` id stays under the Windows 260-char path limit.
    """
    slug = _LABEL_RE.sub("-", (label or "").strip())[:32].strip("-_.")
    return slug or "snapshot"


def _read_snapshot_meta(snap_dir: pathlib.Path) -> dict:
    p = snap_dir / "snapshot.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        # Mirror _read_meta in projects.py — a binary-garbage snapshot.json
        # must degrade to {} rather than 500-ing list_snapshots.
        return {}


def _list_snapshot_dirs(project_dir: pathlib.Path) -> list[pathlib.Path]:
    """
    Return existing snapshot subdirs, sorted by id descending (newest first).

    Lexicographic descending order on the ISO-prefixed id IS newest-first since
    the prefix is sortable.
    """
    snaps = _snapshots_dir(project_dir)
    if not snaps.exists():
        return []
    return sorted(
        (d for d in snaps.iterdir() if d.is_dir()),
        key=lambda d: d.name, reverse=True,
    )


def _to_info(snap_dir: pathlib.Path) -> SnapshotInfo:
    meta = _read_snapshot_meta(snap_dir)
    # Backwards compat: older snapshots (created before Phase 4) don't have
    # `dispatch_status`. Derive from `has_results`: True → 'fresh' (best
    # guess; the staleness check wasn't possible then), False → 'none'.
    # Honest defaults — we don't re-open the .nc on every list call because
    # that would scan every snapshot's network.nc on each /snapshots GET.
    if "dispatch_status" in meta:
        status = str(meta["dispatch_status"])
    else:
        status = "fresh" if meta.get("has_results") else "none"
    return SnapshotInfo(
        id=snap_dir.name,
        label=meta.get("label", ""),
        message=meta.get("message", ""),
        created_at=meta.get("created_at", ""),
        bus_count=int(meta.get("bus_count", 0)),
        snapshot_count=int(meta.get("snapshot_count", 0)),
        objective=meta.get("objective"),
        has_results=bool(meta.get("has_results", False)),
        dispatch_status=status,
    )


def _prune_oldest(
    project_dir: pathlib.Path,
    protect: frozenset[str] | None = None,
) -> int:
    """
    Delete oldest snapshots beyond the cap. Returns number pruned.

    ``protect`` is a set of snapshot ids that must never be pruned, even if
    they are the oldest. This is used by ``restore_snapshot`` to exempt the
    source snapshot it's about to read from — otherwise restoring an
    at-cap project's oldest snapshot would trigger a pre-restore prune that
    deletes the very dir we're about to copy from, resulting in a silent
    no-op restore.

    Called before each create so the cap is enforced just-in-time without a
    background sweeper. Lexicographic-ascending order on the id gives oldest
    first; we keep the newest ``_MAX_SNAPSHOTS_PER_PROJECT - 1`` so the about-
    to-be-created snapshot fits under the cap.
    """
    protect = protect or frozenset()
    snaps = _list_snapshot_dirs(project_dir)
    # snaps is newest-first; reverse to get oldest-first for pruning
    oldest_first = [d for d in reversed(snaps) if d.name not in protect]
    # Cap calculation accounts for protected entries — they count toward the
    # cap but are not eligible for deletion. If protection blocks pruning
    # enough to keep us over the limit, we just stay over until the next
    # create with a different protect set; better than corrupting a restore.
    keep_n = _MAX_SNAPSHOTS_PER_PROJECT - 1
    to_delete = max(0, len(snaps) - keep_n)
    if to_delete <= 0 or not oldest_first:
        return 0
    pruned = 0
    for d in oldest_first[:to_delete]:
        try:
            _force_rmtree(d)
            pruned += 1
        except OSError:
            pass
    return pruned


def _create_snapshot_internal(
    name: str, label: str, message: str,
    protect: frozenset[str] | None = None,
    project_dir: pathlib.Path | None = None,
) -> SnapshotInfo:
    """
    Shared snapshot-creation core used by both the public endpoint and the
    restore safety net. Splits out so callers can pass a ``protect`` set for
    the at-cap-restore edge case.

    ``project_dir`` is the AUTHORIZED org-scoped directory resolved by the
    route's `ProjectAccessDep`. It is passed explicitly rather than re-derived
    from ``name`` because `_safe_project_dir` is a path-traversal guard, not an
    authorization check — it resolves any org's project under the shared root.
    """
    if project_dir is None:
        project_dir = _safe_project_dir(name)
    if not (project_dir / "network.nc").exists():
        raise HTTPException(404, f"Project '{name}' not found")

    snaps = _snapshots_dir(project_dir)
    snaps.mkdir(parents=True, exist_ok=True)

    _prune_oldest(project_dir, protect=protect)

    # Snapshot id: filesystem-safe ISO timestamp (no colons — Windows refuses
    # them in file names — and no `+00:00` tz suffix; we're always UTC) plus a
    # slugified label. Lexicographic order on this id is newest-first when
    # reversed, which the list/prune helpers rely on.
    iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    sid = f"{iso}-{_slugify_label(label)}"
    snap_dir = _safe_snapshot_dir(project_dir, sid)

    if snap_dir.exists():
        # Extremely unlikely collision on second-resolution timestamps, but
        # the id must be unique — append a 4-char nonce.
        from secrets import token_hex
        sid = f"{sid}-{token_hex(2)}"
        snap_dir = _safe_snapshot_dir(project_dir, sid)
    try:
        snap_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        # TOCTOU: another concurrent POST won the race for this sid between
        # our `.exists()` check and `mkdir`. Surface a 409 (conflict) rather
        # than letting a raw FileExistsError surface as a 500.
        raise HTTPException(
            409, f"Snapshot id '{sid}' already exists — retry to generate a fresh id."
        ) from exc

    # Copy each bundle file if present, then classify dispatch freshness by
    # reading the snapshot's own network.nc. Both happen INSIDE the PyPSA
    # lock so:
    #   1. A concurrent save's atomic-write can't produce a snapshot with
    #      files from two different save points (network.nc from save N +
    #      user_ts from save N+1).
    #   2. The freshness read can't race with a concurrent project-level
    #      delete or another snapshot create that prunes this same dir
    #      (the snapshot dir is reachable from outside this function via
    #      shutil.rmtree paths).
    # The freshness read itself is judged against the snapshot's OWN files
    # (not the live in-memory state), because the user may have edited the
    # network since the last save — those edits are NOT in this snapshot.
    # Cost: one pypsa.Network() construction per snapshot create (~100 ms
    # on typical networks; the user is already paying for a multi-file copy).
    snap_dispatch_status = "none"
    with PyPSAService.get_lock():
        for fname in _BUNDLE_FILES:
            src = project_dir / fname
            if src.exists():
                shutil.copy2(src, snap_dir / fname)

        # Chatbot uploads (Phase A) — uploads/ travels into the snapshot
        # bundle. Same best-effort posture as chat.jsonl on Save-As: a
        # copy failure must not abort the snapshot create.
        try:
            from routers.projects import _copy_bundle_dirs
            _copy_bundle_dirs(project_dir, snap_dir)
        except Exception:  # noqa: BLE001 — best-effort
            pass

        snap_nc = snap_dir / "network.nc"
        if snap_nc.exists():
            try:
                # netCDF/HDF5 is process-global thread-unsafe — take the io
                # lock for the read (INNER to the pypsa lock already held
                # above, per the documented ordering). `_classify_dispatch`
                # runs on the in-memory result, so it stays outside the lock.
                with PyPSAService.get_netcdf_io_lock():
                    snap_n = pypsa.Network(str(snap_nc))
                snap_dispatch_status = _classify_dispatch(snap_n)
            except Exception:
                # Broad except is intentional: PyPSA / xarray / pandas can
                # raise OSError, ValueError, AttributeError, KeyError,
                # xarray.MergeError, or PyPSA-specific exceptions on
                # malformed netcdfs. The narrow `(OSError, ValueError)`
                # catch leaked some of these as 500s. Falling through to
                # 'none' here means the user sees a non-SOLVED badge and
                # can investigate; the snapshot file is still on disk.
                snap_dispatch_status = "none"

    # Compact summary from the project's metadata.json. Cached values are fine
    # — they describe what was on disk at copy time, which is exactly what
    # we're snapshotting.
    proj_meta = _read_meta(project_dir)
    snap_meta = {
        "id": sid,
        "label": (label or "").strip()[:80],
        "message": (message or "").strip()[:2000],
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "bus_count": proj_meta.get("bus_count", 0),
        "snapshot_count": proj_meta.get("snapshot_count", 0),
        "objective": proj_meta.get("objective"),
        # has_results stays for backward compatibility — true when the
        # snapshot contains any dispatch tables, regardless of staleness.
        "has_results": snap_dispatch_status != "none",
        "dispatch_status": snap_dispatch_status,
    }
    _atomic_write_text(snap_dir / "snapshot.json", json.dumps(snap_meta, indent=2))

    # Chatbot integration v6 Phase 4 — include chat.jsonl + chat.jsonl.1 in
    # the snapshot bundle (C2). Best-effort: snapshot create must not fail
    # because chat history could not be included. Operates on the ACTIVE
    # ctx's chat.jsonl when the snapshot is being taken of the same project
    # as the active binding; for snapshots of non-active projects we read
    # the file directly from the project directory.
    try:
        from services import chat_service
        active_ctx = PyPSAService.get_active_context()
        if active_ctx.loaded_project == name:
            chat_service.handle_snapshot_lineage(active_ctx, snap_dir, mode="create")
        else:
            # Non-active snapshot: copy chat.jsonl from the project dir
            # directly (no active ctx to lock against, so a torn-write race
            # is possible but acceptable for a checkpoint of a stale project).
            src_chat = project_dir / chat_service.CHAT_FILENAME
            if src_chat.exists():
                shutil.copy2(str(src_chat), str(snap_dir / chat_service.CHAT_FILENAME))
            src_backup = project_dir / (chat_service.CHAT_FILENAME + ".1")
            if src_backup.exists():
                shutil.copy2(str(src_backup), str(snap_dir / (chat_service.CHAT_FILENAME + ".1")))
    except Exception:  # noqa: BLE001 — never abort snapshot on chat lineage
        pass

    change_log_service.log(
        "snapshot", "Project", name,
        f"Created snapshot '{snap_meta['label'] or sid}' of '{name}' "
        f"({snap_meta['bus_count']} buses, {snap_meta['snapshot_count']} snapshots)",
    )
    return _to_info(snap_dir)


@router.post("/{name}/snapshots")
def create_snapshot(
    req: CreateSnapshotRequest,
    project: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """
    Snapshot the current on-disk project bundle.

    Operates on the FILES, not the in-memory network — so the user is
    snapshotting whatever the last Save persisted, not their unsaved edits.
    This is the same semantics as git: you snapshot what's committed.
    Callers that want to capture in-memory state should Save first.
    """
    _enforce_project_lock(db, _lock_target(project), user)
    return _create_snapshot_internal(
        project.name, req.label, req.message or "", project_dir=project.directory
    )


@router.get("/{name}/snapshots")
def list_snapshots(
    project: AuthorizedProject = ProjectAccessDep,
) -> list[SnapshotInfo]:
    project_dir = project.directory
    if not (project_dir / "network.nc").exists():
        raise HTTPException(404, f"Project '{project.name}' not found")
    return [_to_info(d) for d in _list_snapshot_dirs(project_dir)]


@router.post("/{name}/snapshots/{snapshot_id}/restore")
def restore_snapshot(
    snapshot_id: str,
    project: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
    session: SessionRow | None = Depends(current_session),
):
    """
    Restore a snapshot: overwrite the project files + reload in-memory.

    Order of operations matters:
    1. Validate the snapshot exists and is complete (network.nc present)
    2. Create a "pre-restore" auto-snapshot so the user can undo the restore
    3. Copy snapshot's bundle files over the project's (atomic per-file)
    4. Reset + reload the in-memory PyPSA network from the restored files
    5. Clear the undo stack (the in-memory state is now the new baseline)

    Step 2 is the safety net: a careless restore can't lose work because the
    pre-restore state is itself saved as a snapshot named "before-restore-...".
    """
    name = project.name
    project_dir = project.directory
    if not (project_dir / "network.nc").exists():
        raise HTTPException(404, f"Project '{name}' not found")

    snap_dir = _safe_snapshot_dir(project_dir, snapshot_id)
    if not snap_dir.exists() or not (snap_dir / "network.nc").exists():
        raise HTTPException(404, f"Snapshot '{snapshot_id}' not found (or incomplete)")

    _enforce_project_lock(db, _lock_target(project), user)

    # Safety net: create an auto-snapshot of the current state before we
    # overwrite it. Pass `protect={snapshot_id}` so the cap-driven prune that
    # runs inside the safety-snapshot create cannot delete the source we're
    # about to restore from — restoring an at-cap project's oldest snapshot
    # would otherwise silently no-op (prune deletes source, copy loop skips
    # missing files, reload re-imports the unchanged-because-overwritten-by-
    # auto-snapshot project, user sees nothing change).
    try:
        _create_snapshot_internal(
            name,
            project_dir=project_dir,
            label="before-restore",
            message=f"Auto-saved before restoring snapshot '{snapshot_id}'",
            protect=frozenset({snapshot_id}),
        )
    except HTTPException as exc:
        # If the pre-restore snapshot fails, refuse the restore — losing work
        # is worse than a confusing error.
        raise HTTPException(
            500, f"Could not auto-snapshot before restore: {exc.detail}",
        ) from exc

    # Re-verify the source snapshot still has network.nc after the pre-restore
    # safety snap ran (in case _prune_oldest somehow took it despite protect=).
    if not (snap_dir / "network.nc").exists():
        raise HTTPException(
            500,
            f"Source snapshot '{snapshot_id}' was lost during pre-restore. "
            f"The auto-saved 'before-restore' snapshot is your previous state.",
        )

    # Hold the PyPSA lock for the entire restore — copy phase AND reload —
    # so a concurrent autosave can't race with our `network.nc` rewrite.
    # `_atomic_write_with` (write to .tmp + rename) makes each individual
    # file safe against crashes, but a concurrent writer to the same path
    # (e.g. autosave triggering during restore) could lose updates without
    # the lock.
    from services import dirty_state, undo_service
    undo_service.clear()
    dirty_state.clear()  # memory and disk now agree
    with PyPSAService.get_lock():
        # Copy snapshot's files over the project's. Use atomic-write per file
        # so a crash mid-restore leaves either the pre-restore file or the
        # snapshot file in place — never a half-written one.
        for fname in _BUNDLE_FILES:
            src = snap_dir / fname
            if not src.exists():
                continue
            _atomic_write_with(project_dir / fname, lambda p, src=src: shutil.copy2(src, p))

        # Chatbot uploads (Phase A) — snapshot restore overwrites the live
        # uploads dir with the snapshot bundle's copy. `_copy_bundle_dirs`
        # already rmtree's the destination before copying, so a post-
        # snapshot session's uploads are dropped (the pre-restore safety
        # snapshot the route handler created earlier preserves them if the
        # user wants to undo). Best-effort: a copy failure here must not
        # abort restore.
        try:
            from routers.projects import _copy_bundle_dirs
            _copy_bundle_dirs(snap_dir, project_dir)
        except Exception:  # noqa: BLE001 — best-effort
            pass

        # Reload in-memory network from the restored files. Lock is already
        # held — `reset_network()` and `import_from_netcdf()` are safe to
        # call inside the same `with` block.
        PyPSAService.reset_network()
        n = PyPSAService.get_network()
        with PyPSAService.get_netcdf_io_lock():
            n.import_from_netcdf(str(project_dir / "network.nc"))
        # Bind identity atomically with the swap — inside the SAME lock that
        # reset it to None — so a concurrent save can't observe the transient
        # unbound state and wrongly claim/overwrite (see load_project). Binds
        # the TENANT identity too, from the already-authorized `project`.
        PyPSAService.bind_project(
            name,
            org_id=project.org_id,
            project_uuid=project.uuid,
            storage_dir=str(project_dir),
        )

    cfg_path = project_dir / "solver_config.json"
    if cfg_path.exists():
        from routers.simulation import _state
        # Shared legacy-tolerant loader — same path load_project / import_bundle
        # use, so a snapshot taken by an older GUI version restores instead of
        # 500-ing on an unknown solver_config key.
        _state["solver_config"] = _solver_config_from_dict(json.loads(cfg_path.read_text()))

    # Restoring a saved snapshot rebinds this session's active context to that
    # Project, so the pointer follows — same rule as load_project. AuthorizedProject
    # carries the identity but not the ORM row, and set_active_project needs the row.
    if session is not None:
        project_row = project_registry.find_project(
            db, project_registry.require_user(user), project.name
        )
        if project_row is not None:
            active_project.set_active_project(db, session, project_row)

    # Hydrate simulation state from the restored project's metadata. Without
    # this, restoring a previously-solved snapshot leaves the header status
    # indicator showing "Idle" while the Results panel quietly serves dispatch
    # numbers from the imported netcdf — the exact inconsistency that
    # `load_project` was extended to fix. Mirror the same logic here.
    n_loaded = PyPSAService.get_network()
    has_dispatch = network_has_dispatch(n_loaded)
    proj_meta = _read_meta(project_dir)
    # Atomic lifecycle hydration via `_state_update` so a concurrent status
    # poll can't observe a half-applied tuple (status="completed" while
    # objective is still stale `None`). See F11 / G2 rationale.
    from routers.simulation import _state_update as _sim_state_update
    # Gate on `has_dispatch` ALONE (computed from the loaded network), not the
    # cached `meta["has_results"]` flag which can drift stale — same fix as
    # load_project. A stale/absent flag must not hide genuinely-present dispatch.
    if has_dispatch:
        _sim_state_update(
            status="completed",
            condition=proj_meta.get("condition") or "optimal",
            objective=proj_meta.get("objective"),
            solve_time=proj_meta.get("solve_time"),
        )
    else:
        _sim_state_update(status="idle", condition=None, objective=None, solve_time=None)

    # Restore the solve-time _state side-results (lopf_results / ac_pf_results /
    # last_lost_load) from the snapshot's results_state.pkl — same as
    # load_project / import_bundle. Without this, restoring a solved snapshot
    # left the LP/PF result-source toggle and the Lost-Load panel blank even
    # though the netcdf dispatch loaded.
    _restore_results_state(project_dir, name)

    # Chatbot integration v6 Phase 4 — restore chat.jsonl from the snapshot
    # bundle (C2). Overwrite the active project's chat.jsonl with the snapshot
    # copy and invalidate the persist_path cache. Best-effort: snapshot restore
    # must not fail because chat history could not be restored.
    try:
        from services import chat_service
        active_ctx = PyPSAService.get_active_context()
        if active_ctx.loaded_project == name:
            chat_service.handle_snapshot_lineage(active_ctx, snap_dir, mode="restore")
    except Exception:  # noqa: BLE001
        pass

    from routers.network import (
        _ensure_snapshots_cover_user_ts,
        _reapply_user_ts_to_network,
        _restore_user_ts,
    )
    user_ts_path = project_dir / "user_ts.json"
    if user_ts_path.exists():
        _restore_user_ts(json.loads(user_ts_path.read_text()))
    else:
        _restore_user_ts({})
    n = PyPSAService.get_network()
    with PyPSAService.get_lock():
        _ensure_snapshots_cover_user_ts(n)
    # The authoritative loaded-project binding was set inside the swap lock above.
    n.name = name
    _reapply_user_ts_to_network(n)

    snap_meta = _read_snapshot_meta(snap_dir)
    change_log_service.log(
        "restore", "Project", name,
        f"Restored snapshot '{snap_meta.get('label') or snapshot_id}' of '{name}' "
        f"({len(n.buses)} buses, {len(n.snapshots)} snapshots)",
    )
    return {
        "restored": snapshot_id,
        "label": snap_meta.get("label", ""),
        "bus_count": len(n.buses),
        "snapshot_count": len(n.snapshots),
    }


@router.delete("/{name}/snapshots/{snapshot_id}", status_code=204)
def delete_snapshot(
    snapshot_id: str,
    project: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    name = project.name
    project_dir = project.directory
    if not (project_dir / "network.nc").exists():
        raise HTTPException(404, f"Project '{name}' not found")
    snap_dir = _safe_snapshot_dir(project_dir, snapshot_id)
    if not snap_dir.exists():
        raise HTTPException(404, f"Snapshot '{snapshot_id}' not found")
    _enforce_project_lock(db, _lock_target(project), user)
    label = _read_snapshot_meta(snap_dir).get("label", snapshot_id)
    # `_force_rmtree` clears read-only attributes and retries with a backoff —
    # a plain `shutil.rmtree` raises WinError 5 on OneDrive-synced paths.
    try:
        _force_rmtree(snap_dir)
    except OSError as exc:
        raise HTTPException(
            500,
            f"Could not delete snapshot '{label}': {exc}. The folder is "
            f"locked (OneDrive sync / AV scan?) — retry, or remove it manually.",
        ) from exc
    change_log_service.log(
        "delete_snapshot", "Project", name,
        f"Deleted snapshot '{label}' of '{name}'",
    )
