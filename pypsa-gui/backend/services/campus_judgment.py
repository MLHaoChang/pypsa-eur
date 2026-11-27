"""The pack read-back judgment of a campus run (grid-compliance GC-22).

Each PSS/E bundle of a campus run carries the EMT pack at
``run/psse/<bundle>/emt/``; its ``battery.json`` lists the cases (written when
the campus holds the PCC's zero sequence). The engineer runs those cases in an
EMT tool and brings the results back. This module puts them in the pack and
runs GC-07's read-back (``gridspine.drivers.readback.judge_pack_results``)
against the run, which writes ``run/compliance/judgment.json`` and
``judgment.csv``. It serves that judgment as written: nothing is re-judged on
the way out, so no status is upgraded and an unknown case stays unknown.

The judgment lives in the run directory, so a new campus run drops it, and a
second read-back replaces it as GC-07 replaces its files. A sidecar,
``judgment_source.json``, names the bundle judged and when.

* ``add_results``: replace one bundle's pack results with the uploaded set, then judge.
* ``judge``: judge the results the pack holds again.
* ``get_judgment``: the stored judgment, with its status counts and whether it is stale.
* ``stored``: the same for GC-09's tracker (the ``GC-07`` source).

Every write (the results swap, the judgment, the sidecar) runs under the run's
lock, the one a campus study holds while it publishes ``run``; a study in
progress refuses the read-back. A read-back that fails puts the previous
results and judgment back exactly, so a judgment is never served for results
it was not made from.

The pack is judged on the grid-code profile the run was held to. A profile
published again with other content since the run is refused: the judgment
would no longer be the run's.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, UTC
from pathlib import Path

import yaml
from fastapi import HTTPException

from services import campus_electrical_service as ce

try:  # gridspine is optional in a packaged app; without it there is no pack to judge
    from gridspine.drivers import readback as _rb
    from gridspine.handoff import test_battery as _tb
    from gridspine.readback import pack as _gp
except ImportError:  # pragma: no cover - the packaging tests cover the guard
    _rb = _tb = _gp = None

ContractError = ce.ContractError
from services.atomic_io import atomic_write_bytes

log = logging.getLogger(__name__)

#: Next to gridspine's ``compliance/`` folder in the run: which bundle was judged, and when.
JUDGMENT_SOURCE_FILE = "judgment_source.json"
#: The most result files one upload may carry. A battery has a few dozen cases, each up to a few files.
MAX_RESULT_FILES = 400
#: The most bytes one result file may hold.
MAX_RESULT_FILE_BYTES = 64 * 1024 * 1024
#: The most bytes all the result files of one upload may hold together.
MAX_RESULT_BYTES = 256 * 1024 * 1024
#: The case statuses GC-07 writes; any other reads unknown in the counts and tables.
STATUSES = ("pass", "fail", "unknown")

RESULTS_DIR = "results"
#: In the run, beside ``psse/``: per-call stage and aside folders of an upload.
STAGING_DIR = ".emt_results"


def _refuse(status: int, kind: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status, detail={"error_kind": kind, "message": message}
    )


def _run_dir(project) -> Path:
    return ce.campus_dir(project) / "run"


def _pack_dir(project, bundle) -> Path:
    """The EMT pack of bundle ``bundle`` of the latest run. The folder is picked
    from the bundle listing by its name, never built from ``bundle``."""
    listed = {b["name"] for b in ce.psse_bundles(project)["bundles"]}
    root = _run_dir(project) / ce.cs.PSSE_DIR
    found = next(
        (
            b
            for b in ce._psse_bundle_dirs(root)
            if b.name == bundle and bundle in listed
        ),
        None,
    )
    if found is None:
        raise _refuse(
            404,
            "campus_bundle_not_found",
            "no such PSS/E bundle in the latest campus run",
        )
    pack = found / "emt"
    if pack.is_symlink() or not pack.is_dir() or not (pack / "manifest.json").is_file():
        raise _refuse(404, "campus_pack_not_found", f"bundle {bundle} has no EMT pack")
    return pack


def _profile(project, run: Path) -> dict:
    """The grid-code profile the run was held to, as long as it still reads the same."""
    used = ce._profile_used(run)
    if used is None:
        raise _refuse(
            409,
            "campus_judgment_no_profile",
            "this campus run does not record the grid-code profile it was held to; run the study again",
        )
    try:
        profile = ce.cs.load_grid_code(
            used["profile"], extra_dirs=(ce.grid_codes_dir(project),)
        )
    except Exception as exc:  # noqa: BLE001 — any load failure means the run's profile is gone
        raise _refuse(
            409,
            "campus_judgment_profile_changed",
            f"grid-code profile {used['profile']} no longer loads; run the study again",
        ) from exc
    if ce._fingerprint(profile) != used["sha256"]:
        raise _refuse(
            409,
            "campus_judgment_profile_changed",
            f"grid-code profile {used['profile']} has changed since the campus run; run the study again",
        )
    return profile


def _check_files(pack: Path, files: list[tuple[str, bytes]]) -> None:
    """Every file is a plain name one battery case claims, once each, within the caps."""
    if not files:
        raise _refuse(422, "campus_results_empty", "no result files were uploaded")
    if len(files) > MAX_RESULT_FILES:
        raise _refuse(
            413,
            "campus_results_too_many",
            f"at most {MAX_RESULT_FILES} result files per upload",
        )
    if sum(len(data) for _, data in files) > MAX_RESULT_BYTES:
        raise _refuse(
            413,
            "campus_results_too_large",
            f"the result files exceed {MAX_RESULT_BYTES // (1024 * 1024)} MB together",
        )
    battery = pack / _gp.BATTERY_JSON
    if battery.is_symlink() or not battery.is_file():
        raise _refuse(
            409,
            "campus_pack_no_battery",
            "this pack has no test battery: the campus file needs the PCC's zero sequence "
            "(x0x_max, r0x0_max); add it and run the study again",
        )
    try:
        ids = [
            c.case_id for c in _tb.cases_from_json(battery.read_text(encoding="utf-8"))
        ]
    except (ContractError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise _refuse(
            422,
            "campus_pack_battery_unreadable",
            "this pack's test battery cannot be read; run the study again to write it afresh",
        ) from exc
    seen = set()
    for name, data in files:
        if (
            not name
            or name != os.path.basename(name)
            or "\\" in name
            or ".." in name
            or name.startswith(".")
            or not name.isascii()
            or name != name.strip()
        ):
            raise _refuse(
                422, "campus_result_name", f'"{name}" is not a plain file name'
            )
        if not any(_gp._claims(name, i) for i in ids):
            raise _refuse(
                422,
                "campus_result_name",
                f"{name} is not a result of any case of this pack (<case id>.csv, .inf, .out, _NN.out with two digits, or .status.txt)",
            )
        if name in seen:
            raise _refuse(422, "campus_result_name", f"{name} is uploaded twice")
        if len(data) > MAX_RESULT_FILE_BYTES:
            raise _refuse(
                413,
                "campus_result_too_large",
                f"{name} exceeds {MAX_RESULT_FILE_BYTES // (1024 * 1024)} MB",
            )
        seen.add(name)


def _swap_in(pack: Path, files: list[tuple[str, bytes]]) -> Path | None:
    """Replace ``pack/results`` with ``files``: written to a stage of this call's
    own, then the old folder renamed aside and the stage renamed in. Returns the
    aside folder (None when there was no earlier results folder)."""
    token = uuid.uuid4().hex
    staging = _staging(pack)
    staging.mkdir(exist_ok=True)
    stage = staging / f"stage.{token}"
    stage.mkdir()
    try:
        for name, data in files:
            (stage / name).write_bytes(data)
    except BaseException:
        ce._remove_tree(stage)
        raise
    results = pack / RESULTS_DIR
    aside = None
    if results.exists() or results.is_symlink():
        aside = staging / f"aside.{token}"
        results.rename(aside)
    try:
        stage.rename(results)
    except BaseException:
        if aside is not None:
            aside.rename(results)
        ce._remove_tree(stage)
        raise
    return aside


def _staging(pack: Path) -> Path:
    """Where an upload is staged and the old results wait: in the run
    (``run/psse/<bundle>/emt`` → ``run/.emt_results``), so on the same disk
    for the renames, and never inside a bundle the download zips."""
    return pack.parents[2] / STAGING_DIR


def _ratings(run: Path):
    """The run's ratings from its campus file; a campus file that no longer reads is a plain 422."""
    try:
        return _rb.run_ratings(run)
    except yaml.YAMLError as exc:
        raise _refuse(
            422,
            "campus_run_unreadable",
            "this run's campus file cannot be read; run the study again",
        ) from exc


def _swap_back(pack: Path, aside: Path | None) -> None:
    ce._remove_tree(pack / RESULTS_DIR)
    if aside is not None:
        aside.rename(pack / RESULTS_DIR)


def _judgment_files(run: Path) -> list[Path]:
    return [
        run / _rb.COMPLIANCE_DIR / _rb.JUDGMENT_JSON,
        run / _rb.COMPLIANCE_DIR / _rb.JUDGMENT_CSV,
        run / JUDGMENT_SOURCE_FILE,
    ]


def _snapshot(run: Path) -> dict:
    return {p: (p.read_bytes() if p.is_file() else None) for p in _judgment_files(run)}


def _restore(snapshot: dict) -> None:
    # Try every file: one that cannot be put back must not leave the others
    # unrestored, nor hide the error that made the read-back fail.
    for path, data in snapshot.items():
        try:
            if data is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write_bytes(path, data)
        except OSError:
            log.error(
                "could not restore %s after a failed read-back", path, exc_info=True
            )


def _read_back(project, bundle, files: list[tuple[str, bytes]] | None) -> dict:
    ce.require_capacity_expansion(project)
    if _rb is None:
        raise _refuse(
            503,
            "campus_judgment_unavailable",
            "the pack read-back is not installed in this app",
        )
    run = _run_dir(project)
    lock = ce._lock(run)
    if not lock.acquire(blocking=False):
        raise _refuse(
            409,
            "campus_run_in_progress",
            "a campus study is running; judge the pack once it has finished",
        )
    try:
        pack = _pack_dir(project, bundle)
        if (pack / RESULTS_DIR).is_symlink():
            raise _refuse(
                409,
                "campus_pack_results_link",
                "the pack's results folder is a link; it is not read",
            )
        if files is not None:
            _check_files(pack, files)
        profile = _profile(project, run)
        before = _snapshot(run)
        swapped, aside = False, None
        try:
            (run / JUDGMENT_SOURCE_FILE).unlink(missing_ok=True)
            if files is not None:
                aside = _swap_in(pack, files)
                swapped = True
            # A pack written with its own ratings is judged on those; else on the run's campus file.
            ratings = None if (pack / _gp.RATINGS_JSON).exists() else _ratings(run)
            _rb.judge_pack_results(run, pack, profile, ratings)
            source = {"bundle": bundle, "judged_at": datetime.now(UTC).isoformat()}
            atomic_write_bytes(
                run / JUDGMENT_SOURCE_FILE, json.dumps(source, sort_keys=True).encode()
            )
        except BaseException as exc:
            try:
                if swapped:
                    _swap_back(pack, aside)
            finally:
                _restore(before)
            if isinstance(exc, ContractError):
                raise ce._unprocessable(exc) from exc
            raise
        if aside is not None:
            try:
                ce._remove_tree(aside)
            except (
                OSError
            ):  # the judgment is stored; a leftover hidden folder is not read
                log.warning(
                    "could not remove %s after a read-back", aside, exc_info=True
                )
        # read under the lock, so the answer is this read-back's, not a study published since
        return get_judgment(project)
    finally:
        lock.release()


def add_results(project, bundle: str, files: list[tuple[str, bytes]]) -> dict:
    """Replace bundle ``bundle``'s EMT pack results with ``files``
    (``(name, bytes)``), the whole set, never merged with an earlier upload,
    and judge the pack."""
    return _read_back(project, bundle, files)


def judge(project, bundle: str) -> dict:
    """Judge bundle ``bundle``'s EMT pack again on the results it holds."""
    return _read_back(project, bundle, None)


def _is_stale(project, run: Path, judgment: dict, bundle) -> bool:
    """True when the judgment no longer matches the pack it names: the bundle is
    gone, or its manifest's ``data_hash`` or contract version (or, for a rated
    pack, the run's campus data hash) differ from the judgment's."""
    header = judgment.get("header")
    if not isinstance(bundle, str) or not isinstance(header, dict):
        return True
    try:
        manifest = _gp.read_manifest(_pack_dir(project, bundle))
        if (manifest["data_hash"], manifest["contract_version"]) != (
            header.get("data_hash"),
            header.get("contract_version"),
        ):
            return True
        if "campus_data_hash" in header and header[
            "campus_data_hash"
        ] != _rb.run_data_hash(run):
            return True
    except (HTTPException, ContractError, OSError, ValueError, KeyError, TypeError):
        return True
    return False


def counts(cases: list) -> dict:
    """Gridspine's ``status_counts`` over ``cases``; a status it does not know counts as unknown."""
    return _gp.status_counts(
        {
            "cases": [
                {
                    "status": c.get("status")
                    if c.get("status") in STATUSES
                    else "unknown"
                }
                for c in cases
            ]
        }
    )


def stored(project) -> dict | None:
    """``{judgment, source, stale}`` as stored for the latest run; None when it
    has none. Raises ``ValueError`` when the stored judgment cannot be read."""
    if _rb is None:
        return None
    run = _run_dir(project)
    path = _judgment_files(run)[0]
    if not (run / ce.cs.CAMPUS_MANIFEST).is_file() or not path.is_file():
        return None
    judgment = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(judgment, dict)
        or not isinstance(judgment.get("cases"), list)
        or not all(isinstance(c, dict) for c in judgment["cases"])
    ):
        raise ValueError("judgment.json is not a judgment")
    try:
        source = json.loads((run / JUDGMENT_SOURCE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        source = {}
    source = source if isinstance(source, dict) else {}
    return {
        "judgment": judgment,
        "source": source,
        "stale": _is_stale(project, run, judgment, source.get("bundle")),
    }


def get_judgment(project) -> dict:
    """The latest run's stored judgment exactly as GC-07 wrote it (``header``
    and ``cases``), its ``status_counts``, the bundle judged and when,
    ``stale`` (it no longer matches its pack) and ``run_stale`` (the campus
    was edited since the run)."""
    ce.require_capacity_expansion(project)
    try:
        got = stored(project)
    except (OSError, ValueError) as exc:
        raise _refuse(
            409,
            "campus_judgment_unreadable",
            f"the stored judgment cannot be read: {exc}",
        ) from exc
    if got is None:
        return {"has_judgment": False}
    judgment, source = got["judgment"], got["source"]
    return {
        "has_judgment": True,
        "bundle": source.get("bundle"),
        "judged_at": source.get("judged_at"),
        "stale": got["stale"],
        "run_stale": ce._stale(project, ce.campus_dir(project)),
        "header": judgment.get("header"),
        "cases": judgment["cases"],
        "status_counts": counts(judgment["cases"]),
    }
