"""
Study-owned project forks (guided investment study MVP-1, S4 M1/M2).

Plan: docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md (S4
"Mutation boundary"); gate notes S1 (SB-4) and S2.

A decision study never solves a user project. Each option solves on a FORK: a
child project of the study's own base project (M0), named deterministically
``<base>-opt-<option_id>``, created here and removed here.

* **Creation** is ``routers/projects.py::_create_scenario_db`` lifted to a
  service that takes ``db`` and a ``user_id`` (the user is re-fetched: a
  worker thread has no request), with ``scenario_type="scenario"``. The copy
  is its OWN walk over the bundle, which SKIPS ``studies/`` (a fork must not
  carry the study that owns it) and ``results_state.pkl`` (a fork starts
  unsolved); ``_copy_bundle_dirs`` is not reused unchanged (review v2 BC-4).
  The fork's ``network.nc`` and ``solver_config.json`` are then replaced by
  the option's pack network and its explicit ``SolverConfig``.
* **Ownership is verified server-side** (gate S1 SB-4), never read off the
  study's ``option_projects`` list, which Save-As, scenarios, snapshots and
  bundle import all copy: a fork is study-owned only when its
  ``metadata.json`` names this study (``owner_study_id``) and this base
  project (``owner_base_project``) AND its DATABASE row's parent is that base
  project. The metadata survives the fork's own saves
  (``_save_context`` round-trips the owner keys) but not a copy into another
  project, whose row has another parent.
* **Replace on re-run**: an owned fork of the same name is deleted and
  created afresh; a project of that name the study does not own is refused
  (``ForkError``, 409), never overwritten.
* **Deletion** (abort, study delete, M2 throw-away forks) refuses while the
  solve queue holds an active job for the fork, and refuses a fork a user
  has branched a scenario from, rather than cascading into the user's copy.
* **The startup sweep** (:func:`sweep_leftover_forks`, called by
  ``main.lifespan``; plan S9, gate S6 [N7]) removes what a crash left behind:
  throw-away variant forks, and option forks whose study record is gone.
"""
from __future__ import annotations

import json
import logging
import pathlib
import re
import shutil
import uuid
from dataclasses import asdict
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

__all__ = [
    "FORK_SEPARATOR", "ForkError", "OWNER_KEYS", "VARIANT_SEPARATOR",
    "create_option_fork", "create_variant_fork", "delete_fork", "fork_name",
    "is_study_owned", "owned_forks", "sweep_leftover_forks", "variant_fork_name",
]

FORK_SEPARATOR = "-opt-"
# S6 (M2): the tornado's throw-away forks.
VARIANT_SEPARATOR = "-var-"
OWNER_KEYS = ("owner_study_id", "owner_base_project", "owner_option_id",
              "owner_variant_id", "throwaway")
# What a fork's copy walk leaves behind (plan S4 M1, review v2 BC-4).
SKIPPED = frozenset({"studies", "results_state.pkl"})
_OPTION_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,31}$")


class ForkError(RuntimeError):
    """A fork operation refused; `code` is stable, `status` the HTTP answer."""

    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status


def fork_name(base_name: str, option_id: str) -> str:
    if not _OPTION_RE.fullmatch(option_id or ""):
        raise ForkError("option_id_invalid", f"option id {option_id!r} is not a slug", 422)
    return f"{base_name}{FORK_SEPARATOR}{option_id}"


def _user(db, user_id):
    from db.models import User
    from services import project_registry

    uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    return project_registry.require_user(db.get(User, uid))


def _meta(project_dir: pathlib.Path) -> dict:
    from routers.projects import _read_meta

    return _read_meta(project_dir)


def is_study_owned(fork_row, *, study_id: str, base_uuid: str) -> bool:
    """
    True only when BOTH the fork's metadata names this study and base, and
    its database row is a direct child of that base. Either alone can be
    copied or forged; together they cannot be by any copy path.
    """
    from services import project_registry

    if fork_row is None or str(fork_row.parent_project_id or "") != str(base_uuid):
        return False
    meta = _meta(project_registry.project_dir(fork_row))
    return (meta.get("owner_study_id") == study_id
            and meta.get("owner_base_project") == str(base_uuid))


def owned_forks(db, base_row, study_id: str) -> list:
    """Every direct child of the base that this study verifiably owns."""
    from services import project_registry

    return [c for c in project_registry.direct_children(db, base_row)
            if is_study_owned(c, study_id=study_id, base_uuid=str(base_row.id))]


def _copy_walk(src: pathlib.Path, dst: pathlib.Path) -> None:
    """The bundle, minus what a fork must not inherit (`SKIPPED`)."""
    from routers.projects import _BUNDLE_DIRS, _BUNDLE_FILES
    from services.atomic_io import atomic_write_bytes

    for fname in _BUNDLE_FILES:
        if fname in SKIPPED:
            continue
        f = src / fname
        if f.is_file():
            atomic_write_bytes(dst / fname, f.read_bytes())
    for dname in _BUNDLE_DIRS:
        if dname in SKIPPED:
            continue
        d = src / dname
        if d.is_dir() and not d.is_symlink():
            shutil.copytree(str(d), str(dst / dname), dirs_exist_ok=True)


def _write_network(dst: pathlib.Path, network) -> None:
    from routers.projects import _atomic_write_with
    from services.pypsa_service import PyPSAService

    with PyPSAService.get_netcdf_io_lock():
        _atomic_write_with(dst / "network.nc",
                           lambda p: PyPSAService.export_network_to_netcdf(network, p))


def delete_fork(db, fork_row, *, study_id: str, base_uuid: str) -> bool:
    """
    Remove one study-owned fork: its directory, its row, its resident
    context. Returns False (and deletes nothing) when ownership does not
    verify. Raises `ForkError` while the queue holds an active job for it or
    when a user has branched a project from it.
    """
    from routers.projects import _force_rmtree
    from services import project_registry
    from services.pypsa_service import PyPSAService
    from services.solve_queue import solve_queue

    if not is_study_owned(fork_row, study_id=study_id, base_uuid=base_uuid):
        return False
    key = project_registry.registry_key(fork_row)
    if any(j.get("project_key") == key and j.get("status") in ("queued", "running")
           for j in solve_queue.list_jobs()):
        raise ForkError("fork_solving",
                        f"'{fork_row.name}' has an active solve-queue job; abort it first")
    if project_registry.direct_children(db, fork_row):
        raise ForkError(
            "fork_has_children",
            f"a project was branched from the study fork '{fork_row.name}'; it "
            "is left in place rather than deleted with the fork")
    target = project_registry.project_dir(fork_row)
    if target.exists():
        _force_rmtree(target)
    project_registry.delete_project_row(db, fork_row)
    try:
        PyPSAService.drop(key)
    except Exception:  # noqa: BLE001 — the row and files are gone either way
        logger.warning("forks: could not drop resident context %s", key)
    return True


def create_option_fork(db, user_id, *, base_row, study_id: str, option_id: str,
                       network, solver_config):
    """
    Create (or replace) the fork ``<base>-opt-<option_id>`` holding
    ``network`` and ``solver_config``, owned by ``study_id``. Returns the
    fork's `Project` row.
    """
    return _create_fork(
        db, user_id, base_row=base_row, study_id=study_id,
        name=fork_name(base_row.name, option_id),
        description=f"Decision study option {option_id} (study-owned)",
        owner={"owner_option_id": option_id},
        network=network, solver_config=solver_config)


def variant_fork_name(base_name: str, variant_id: str) -> str:
    if not _OPTION_RE.fullmatch(variant_id or ""):
        raise ForkError("variant_id_invalid", f"variant id {variant_id!r} is not a slug", 422)
    return f"{base_name}{VARIANT_SEPARATOR}{variant_id}"


def create_variant_fork(db, user_id, *, base_row, study_id: str, variant_id: str,
                        network, solver_config):
    """
    A THROW-AWAY fork for one tornado re-dispatch (plan S4 M2, S6):
    ``<base>-var-<variant_id>``, study-owned exactly like an option fork
    (metadata owner keys AND a database row whose parent is the base), marked
    ``throwaway``. The caller deletes it (:func:`delete_fork`) once the
    variant's NPV is read, on abort and on failure too. Option forks are
    never re-solved: a variant is always a fork of its own.
    """
    return _create_fork(
        db, user_id, base_row=base_row, study_id=study_id,
        name=variant_fork_name(base_row.name, variant_id),
        description=f"Decision study tornado variant {variant_id} (study-owned, throw-away)",
        owner={"owner_variant_id": variant_id, "throwaway": True},
        network=network, solver_config=solver_config)


def _create_fork(db, user_id, *, base_row, study_id: str, name: str, description: str,
                 owner: dict, network, solver_config):
    from routers.projects import _force_rmtree, _write_meta
    from services import project_registry
    from services.atomic_io import atomic_write_text

    user = _user(db, user_id)
    existing = project_registry.find_project(db, user, name)
    if existing is not None:
        if not delete_fork(db, existing, study_id=study_id, base_uuid=str(base_row.id)):
            raise ForkError(
                "fork_name_taken",
                f"a project named '{name}' exists and is not owned by this "
                "study; rename it before running the study")
    base_dir = project_registry.project_dir(base_row)
    child = project_registry.create_scenario(
        db, user, base_row, name,
        scenario_description=description,
        scenario_type="scenario",
    )
    child_dir = project_registry.ensure_project_dir(child)
    try:
        _copy_walk(base_dir, child_dir)
        _write_network(child_dir, network)
        atomic_write_text(child_dir / "solver_config.json",
                          json.dumps(asdict(solver_config), indent=2))
        now = datetime.now(tz=UTC).isoformat()
        _write_meta(child_dir, {
            "created_at": now, "last_saved": now,
            "bus_count": len(network.buses), "snapshot_count": len(network.snapshots),
            "objective": None, "has_results": False, "condition": None,
            "solve_time": None, "user_ts_count": 0,
            "parent_project": base_row.name,
            "scenario_description": description,
            "scenario_type": "scenario",
            "owner_study_id": study_id,
            "owner_base_project": str(base_row.id),
            **owner,
        })
    except Exception:
        try:
            project_registry.delete_project_row(db, child)
        except Exception:  # noqa: BLE001
            pass
        try:
            _force_rmtree(child_dir)
        except OSError:
            pass
        raise
    return child


def sweep_leftover_forks(db) -> list[str]:
    """
    Delete the study forks a crash left behind; return their names.

    Run once at startup (``main.lifespan``), before the solve queue is
    reconciled, when no run or tornado can be live. Two kinds, and only
    what is PROVABLY study-owned by the ownership rule (M2,
    :func:`is_study_owned`: the fork's metadata names the study and the base
    AND its database row is a direct child of that base):

    * a THROW-AWAY variant fork (``throwaway`` and ``owner_variant_id`` in
      its metadata, ``-var-`` in its name): a tornado deletes each one after
      reading it, on every path, so one that exists at startup was left by a
      crash (gate S6 [N7]);
    * an OPTION fork whose study record (``studies/<study_id>.json`` in the
      base's directory) no longer exists. A record that exists but cannot be
      read is not absent: nothing is provable, nothing is deleted. A base
      whose directory is gone is left to ``storage_reconcile``.

    Every deletion goes through :func:`delete_fork`, which verifies ownership
    again and refuses a fork with an active queue job or a child project; a
    refusal is logged and the fork kept. Never raises for one fork.
    """
    from sqlalchemy import select

    from db.models import Project
    from services import project_registry
    from services.study import store

    swept: list[str] = []
    children = list(db.scalars(
        select(Project).where(Project.parent_project_id.is_not(None))).all())
    for row in children:
        try:
            meta = _meta(project_registry.project_dir(row))
            study_id = meta.get("owner_study_id")
            base_uuid = meta.get("owner_base_project")
            if not isinstance(study_id, str) or not isinstance(base_uuid, str):
                continue
            if not is_study_owned(row, study_id=study_id, base_uuid=base_uuid):
                continue
            if meta.get("throwaway") is True:
                if not meta.get("owner_variant_id") or VARIANT_SEPARATOR not in row.name:
                    continue
            elif meta.get("owner_option_id"):
                base = db.get(Project, row.parent_project_id)
                if base is None:
                    continue
                base_dir = project_registry.project_dir(base)
                if not base_dir.is_dir():
                    continue
                try:
                    record = store._path(base_dir, study_id)
                except ValueError:
                    continue
                if record.exists():
                    continue
            else:
                continue
            name = row.name
            if delete_fork(db, row, study_id=study_id, base_uuid=base_uuid):
                swept.append(name)
                logger.info("forks: swept leftover study fork %s (study %s)",
                            name, study_id)
        except ForkError as exc:
            logger.warning("forks: leftover study fork %s kept (%s)",
                           getattr(row, "name", "?"), exc.code)
        except Exception:  # noqa: BLE001 — one fork never stops the sweep
            logger.exception("forks: could not sweep %s", getattr(row, "name", "?"))
    return swept
