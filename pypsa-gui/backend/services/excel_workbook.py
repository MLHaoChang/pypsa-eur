"""The app side of the Excel input workbook (IMPORTS-1, excel-input-workbook issue 02).

`gridspine.ingest.excel_export.write_workbook` and `excel_import.read_excel` are pure:
they take the plain names, units and allowed custom columns from the caller. This
module is that caller. It builds the catalogue from the asset forms' fields
(`asset_schema.fields.fields_for`) and the app's own columns (investment parts and
the eh_* tags), and it is the one seam every Excel upload goes through (`prepare`):
the reader, the name checks the other imports make, the eh_* typing and the
investment parts' owned costs. A range check that only flags (expert-catalogue
01a-3 C9) plugs in here too.

The workbook routes (`routers/workbook.py`) never touch the open network: a
preview writes nothing, and an import writes a new project to disk and leaves it
closed (the UI opens it with /activate).
"""

from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import shutil
import tempfile
from dataclasses import asdict, dataclass

from fastapi import HTTPException

logger = logging.getLogger(__name__)

MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
#: The solver settings the workbook carries (`excel_layout.HORIZON_SETTINGS`).
SOLVER_KEYS = ("discount_rate", "multi_investment_periods", "investment_periods")


def _gs():
    """The workbook's layout, reader and writer; 503 in a build without gridspine, as `gridspine_service` answers."""
    try:
        from gridspine.ingest import excel_export, excel_import, excel_layout
    except ImportError as exc:  # pragma: no cover - build-shape branch
        raise HTTPException(
            503, "The Excel input workbook is not available in this build."
        ) from exc
    return excel_layout, excel_import, excel_export


def not_carried() -> list[str]:
    """What the workbook does not carry; the About sheet lists the same items."""
    return list(_gs()[0].NOT_CARRIED)


def catalogue() -> dict:
    """Per model class, the asset form's input fields as `excel_layout.Column`s (derived ones left out)."""
    from services.asset_schema.fields import fields_for

    L = _gs()[0]
    out: dict[str, list[L.Column]] = {}
    for cls, _, _ in L.SHEETS:
        try:
            specs = fields_for(cls)
        except KeyError:
            continue
        out[cls] = [
            L.Column(
                f.key, f.label, f.unit_display, float(f.scale), f.kind, tuple(f.options)
            )
            for f in specs
            if not f.derived
        ]
    return out


def custom_columns() -> dict[str, list[str]]:
    """Per model class, the non-PyPSA columns a project may hold: investment parts, eh_* tags and the outage flag."""
    from services.adequacy.eh_columns import eh_columns_for
    from services.adequacy.occurrence import FLAG_COL
    from services.asset_schema.schema import part_columns

    L = _gs()[0]
    out: dict[str, list[str]] = {}
    for cls, _, _ in L.SHEETS:
        flag = [FLAG_COL] if cls == "Generator" else []
        cols = list(dict.fromkeys([*part_columns(cls), *eh_columns_for(cls), *flag]))
        if cols:
            out[cls] = cols
    return out


def _write(n, sidecars: dict | None, *, template: bool) -> bytes:
    return _gs()[2].write_workbook(
        n,
        columns=catalogue(),
        sidecars=sidecars,
        template=template,
        custom_columns=custom_columns(),
    )


def template_bytes() -> bytes:
    """The blank template. It reads no project, so it is the same whichever one is open."""
    import pypsa

    return _write(pypsa.Network(), None, template=True)


def _saved_solver_config(directory: pathlib.Path):
    """The project's saved solver settings (never the live ones); the defaults when it saved none."""
    from routers.projects import _solver_config_from_dict
    from services.solver_service import SolverConfig

    path = directory / "solver_config.json"
    if not path.exists():
        return SolverConfig()
    try:
        return _solver_config_from_dict(json.loads(path.read_text("utf-8")))
    except (OSError, TypeError, ValueError) as exc:
        raise HTTPException(
            422,
            "The project's saved solver settings cannot be read: fix or re-save them.",
        ) from exc


def export_bytes(directory: pathlib.Path, name: str) -> bytes:
    """A project as last saved, as the input workbook: unsaved edits in an open project are not in it."""
    from routers.compare import _load_project_network

    L = _gs()[0]
    n = _load_project_network(directory)
    n.name = name
    user_ts = {}
    path = directory / "user_ts.json"
    if path.exists():
        try:
            user_ts = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(
                422, "The project's saved time series (user_ts.json) cannot be read."
            ) from exc
    cfg = _saved_solver_config(directory)
    solver = {k: getattr(cfg, k) for k in SOLVER_KEYS}
    return _write(
        n, {L.SIDECAR_USER_TS: user_ts, L.SIDECAR_SOLVER: solver}, template=False
    )


@dataclass
class Prepared:
    """An upload read and checked, not yet anyone's network."""

    network: object
    sidecars: dict
    report: object
    rederived: list[str]
    sha256: str
    input_workbook: bool
    filename: str

    @property
    def suggested_name(self) -> str:
        """The upload's file name: an exported workbook is named after its project."""
        return pathlib.PurePath(self.filename).stem.strip() or "Imported workbook"


def _unreadable(message: str) -> HTTPException:
    return HTTPException(422, {"error_kind": "import_unreadable", "message": message})


def workbook_rate(sidecars: dict) -> float:
    """The rate a new project's priced rows are derived at: the workbook's, else the default (never the open project's)."""
    from services.solver.periodized_costs import discount_rate_problem
    from services.solver_service import SolverConfig

    rate = (sidecars.get(_gs()[0].SIDECAR_SOLVER) or {}).get("discount_rate")
    if rate is None:
        return float(SolverConfig().discount_rate)
    if problem := discount_rate_problem(rate):
        raise _unreadable(
            f"The workbook's discount rate cannot be used ({problem}); nothing was imported."
        )
    return float(rate)


def prepare(data: bytes, filename: str | None, *, new_project: bool) -> Prepared:
    """
    Read an uploaded workbook and run every check an import makes, writing nothing.

    `new_project`: the workbook routes; priced rows are derived at the workbook's
    rate (`workbook_rate`). Otherwise (/api/io/import/excel, which replaces the open
    network) the input workbook is refused (IMPORT-FLOW-1: preview first, as a new
    project) and the open project's rate applies, as for the other network imports.
    """
    from routers.io import _refuse_invalid_names, _refuse_reserved_buses
    from services.adequacy.eh_columns import normalise_eh_columns
    from services.network_crud import rederive_imported_parts

    L, reader, _ = _gs()
    filename = pathlib.PurePath(filename or "workbook.xlsx").name or "workbook.xlsx"
    digest = hashlib.sha256(data).hexdigest()
    options = {"custom_columns": custom_columns(), "columns": catalogue()}
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "upload.xlsx"
        path.write_bytes(data)
        try:
            res = reader.read_excel(path, options)
        except L.ImportLimitError as exc:
            raise HTTPException(
                413, {"error_kind": "import_expansion_too_large", "message": str(exc)}
            ) from exc
        except L.ImportFormatError as exc:
            # The reader names the file it opened: the user's name, not the temporary one.
            message = str(exc).replace(path.name, filename)
            raise _unreadable(f"{message} The open network is unchanged.") from exc
        except Exception as exc:  # noqa: BLE001 - any reader failure is one refusal
            logger.warning("Excel workbook refused as unreadable", exc_info=True)
            raise _unreadable(
                "The file is not a readable Excel workbook; the open network is unchanged."
            ) from exc
    res.report.source_files = [filename]
    input_workbook = L.SIDECAR_SOLVER in res.sidecars
    if input_workbook and not new_project:
        raise HTTPException(
            422,
            {
                "error_kind": "import_as_new_project",
                "message": "This is a PyPSA Studio input workbook: import it as a new project, which shows what "
                "it reads before anything is created. The open network is unchanged.",
            },
        )
    n = res.network
    if n.buses.empty:
        raise _unreadable(
            "The workbook has no nodes, so there is nothing to import; the open network is unchanged."
        )
    _refuse_reserved_buses(n)
    _refuse_invalid_names(n)
    rate = workbook_rate(res.sidecars) if new_project else None
    rederived = rederive_imported_parts(n, global_rate=rate)
    # P14: typed eh_* tags (a sheet's TRUE/1/blank → bool; netCDF-safe).
    normalise_eh_columns(n)
    return Prepared(
        n, dict(res.sidecars), res.report, rederived, digest, input_workbook, filename
    )


def payload(prep: Prepared) -> dict:
    """What a preview and an import both return about the file."""
    from routers.io import _build_summary

    return {
        "summary": _build_summary(prep.network).model_dump(),
        "report": prep.report.to_dict(),
        "rederived": prep.rederived,
        "not_carried": not_carried(),
    }


def _write_files(dest: pathlib.Path, prep: Prepared, name: str) -> None:
    from datetime import UTC, datetime

    from routers.projects import _solver_config_from_dict, _write_meta
    from services.atomic_io import atomic_write_text, atomic_write_with
    from services.pypsa_service import PyPSAService

    L = _gs()[0]
    n = prep.network
    n.name = name
    with PyPSAService.get_netcdf_io_lock():
        atomic_write_with(
            dest / "network.nc", lambda p: PyPSAService.export_network_to_netcdf(n, p)
        )
    cfg = _solver_config_from_dict(prep.sidecars.get(L.SIDECAR_SOLVER) or {})
    atomic_write_text(dest / "solver_config.json", json.dumps(asdict(cfg), indent=2))
    user_ts = prep.sidecars.get(L.SIDECAR_USER_TS) or {}
    if user_ts:
        atomic_write_text(dest / "user_ts.json", json.dumps(user_ts))
    now = datetime.now(tz=UTC).isoformat()
    _write_meta(
        dest,
        {
            "created_at": now,
            "last_saved": now,
            "bus_count": len(n.buses),
            "snapshot_count": len(n.snapshots),
            "objective": None,
            "has_results": False,
            "condition": None,
            "solve_time": None,
            "user_ts_count": sum(
                len(a)
                for k, v in user_ts.items()
                if not k.startswith("$")
                for a in v.values()
            ),
            "network_revision": 0,
            "parent_project": None,
            "scenario_description": None,
        },
    )


def create_project(db, user, name: str, prep: Prepared):
    """
    A new project row and folder holding the prepared network, all or nothing.

    Every check has run (`prepare`) before the row exists. `create_root` refuses a
    taken name (409) before any folder is made, and a fresh folder is never an
    existing one, so a collision overwrites nothing. A failure after the row exists
    removes the row and the folder this call made.
    """
    from services import project_registry

    project = project_registry.create_root(db, user, name)
    dest = project_registry.project_dir(project)
    made = False
    try:
        dest.mkdir(parents=True, exist_ok=False)
        made = True
        _write_files(dest, prep, project.name)
    except BaseException as exc:
        if made:
            shutil.rmtree(dest, ignore_errors=True)
        project_registry.delete_project_row(db, project)
        if isinstance(exc, Exception) and not isinstance(exc, HTTPException):
            logger.warning(
                "Excel workbook import could not write the project", exc_info=True
            )
            raise HTTPException(
                500,
                {
                    "error_kind": "import_write_failed",
                    "message": f"The project could not be written ({type(exc).__name__}); nothing was created.",
                },
            ) from exc
        raise
    return project
