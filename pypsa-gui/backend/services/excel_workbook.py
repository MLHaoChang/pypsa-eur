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
            503,
            {
                "error_kind": "excel_workbook_unavailable",
                "message": "The Excel input workbook is not available in this build.",
            },
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


# Asset-form inputs the single edit never writes as plain values, so a workbook does not either (issue 07):
# a Link's extra ports follow the port rule (`network_crud._link_port_extras`), and a transformer's
# winding voltages are read from its buses (`update_transformer` excludes them from the write).
_NOT_WRITTEN = {
    "Link": frozenset(
        {"bus2", "bus3", "bus4", "efficiency2", "efficiency3", "efficiency4"}
    ),
    "Transformer": frozenset({"v_nom_0", "v_nom_1"}),
}


def _own_custom_columns(cls: str) -> list[str]:
    """The custom columns with their own reading: investment parts, eh_* tags and the outage flag."""
    from services.adequacy.eh_columns import eh_columns_for
    from services.adequacy.occurrence import FLAG_COL
    from services.asset_schema.schema import part_columns

    flag = [FLAG_COL] if cls == "Generator" else []
    return list(dict.fromkeys([*part_columns(cls), *eh_columns_for(cls), *flag]))


def form_columns() -> dict[str, list[str]]:
    """Per model class, the asset form's inputs beyond PyPSA's attributes that a workbook carries (issue 07).

    A field joins when it is not derived (`catalogue` leaves those out), not a PyPSA attribute, declared by
    the class's Create model (so the single-edit validators exist for it, `refuse_bad_form_values`), not a
    bus or carrier reference, not one the single edit never writes (`_NOT_WRITTEN`) and not already one of
    the custom columns with their own reading (`_own_custom_columns`).
    """
    import pypsa

    from services.network_crud import _declared_attributes

    blank = pypsa.Network()
    out: dict[str, list[str]] = {}
    for cls, cols in catalogue().items():
        skip = {*blank.components[cls].defaults.index, *_own_custom_columns(cls)}
        skip |= _NOT_WRITTEN.get(cls, frozenset())
        keys = [
            c.key
            for c in cols
            if c.key not in skip
            and c.key in _declared_attributes(cls)
            and c.kind not in ("bus", "carrier")
        ]
        if keys:
            out[cls] = keys
    return out


def custom_columns() -> dict[str, list[str]]:
    """Per model class, the non-PyPSA columns a project may hold: the ones with their own reading and the
    asset form's other inputs (`form_columns`), so the reader accepts what the writer emits."""
    L = _gs()[0]
    forms = form_columns()
    out: dict[str, list[str]] = {}
    for cls, _, _ in L.SHEETS:
        cols = [*_own_custom_columns(cls), *forms.get(cls, [])]
        if cols:
            out[cls] = cols
    return out


def _form_validator(cls: str, key: str):
    """The single-edit path's validator for one Create-model field (#738: the same checks on every write)."""
    from typing import Annotated

    import models.schemas as schemas
    from pydantic import TypeAdapter

    from services.network_crud import _CREATE_MODEL_NAMES

    field = getattr(schemas, _CREATE_MODEL_NAMES[cls]).model_fields[key]
    return TypeAdapter(
        Annotated[field.annotation, *field.metadata]
        if field.metadata
        else field.annotation
    )


def refuse_bad_form_values(n, unchanged: str = "") -> None:
    """A form column's value the single edit would refuse refuses the whole import, before anything is written.

    `unchanged`: the open-network import's ending; a new-project import says nothing was imported."""
    import pandas as pd
    from pydantic import ValidationError

    L = _gs()[0]
    labels = {cls: {c.key: c.label for c in cols} for cls, cols in catalogue().items()}
    for cls, keys in form_columns().items():
        frame = n.static(cls)
        for key in keys:
            if key not in frame.columns:
                continue
            check = _form_validator(cls, key)
            for name, v in frame[key].items():
                if pd.isna(v) or (isinstance(v, str) and not v.strip()):
                    continue
                try:
                    check.validate_python(v.item() if hasattr(v, "item") else v)
                except ValidationError:
                    raise HTTPException(
                        422,
                        {
                            "error_kind": "invalid_edit_value",
                            "message": f"{L.SHEET_OF[cls]} · {labels[cls][key]}: the value for “{name}” "
                            f"is not one the asset form accepts.{unchanged or ' Nothing was imported.'}",
                        },
                    ) from None


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
            {
                "error_kind": "saved_solver_config_unreadable",
                "message": "The project's saved solver settings cannot be read: fix or re-save them.",
            },
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
                422,
                {
                    "error_kind": "saved_time_series_unreadable",
                    "message": "The project's saved time series (user_ts.json) cannot be read.",
                },
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
    #: A new project's solver settings and saved time series, built and checked by `prepare`.
    solver_config: object = None
    user_ts_json: str = ""
    user_ts_count: int = 0

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


def _new_project_sidecars(sidecars: dict) -> tuple[object, str, int]:
    """A new project's solver settings, saved time series (as user_ts.json's text) and their count; a malformed sidecar is 422 `import_unreadable`."""
    from routers.projects import _solver_config_from_dict

    L = _gs()[0]
    try:
        cfg = _solver_config_from_dict(sidecars.get(L.SIDECAR_SOLVER) or {})
    except (AttributeError, TypeError, ValueError) as exc:
        raise _unreadable(
            "The workbook's solver settings cannot be read; nothing was imported."
        ) from exc
    user_ts = sidecars.get(L.SIDECAR_USER_TS) or {}
    try:
        count = sum(
            len(a)
            for k, v in user_ts.items()
            if not k.startswith("$")
            for a in v.values()
        )
        text = json.dumps(user_ts) if user_ts else ""
    except (AttributeError, TypeError, ValueError) as exc:
        raise _unreadable(
            "The workbook's uploaded time series cannot be read; nothing was imported."
        ) from exc
    return cfg, text, count


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
    # A new-project upload never touches the open network, so only the open-network
    # import says it is unchanged.
    unchanged = "" if new_project else " The open network is unchanged."
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
            raise _unreadable(f"{message}{unchanged}") from exc
        except Exception as exc:  # noqa: BLE001 - any reader failure is one refusal
            logger.warning("Excel workbook refused as unreadable", exc_info=True)
            raise _unreadable(
                f"The file is not a readable Excel workbook.{unchanged}"
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
            f"The workbook has no nodes, so there is nothing to import.{unchanged}"
        )
    _refuse_reserved_buses(n)
    _refuse_invalid_names(n)
    refuse_bad_form_values(n, unchanged)
    # A new project's sidecars are checked here, before any row exists (all or nothing).
    checked = _new_project_sidecars(res.sidecars) if new_project else (None, "", 0)
    rate = workbook_rate(res.sidecars) if new_project else None
    rederived = rederive_imported_parts(n, global_rate=rate, new_project=new_project)
    # P14: typed eh_* tags (a sheet's TRUE/1/blank → bool; netCDF-safe).
    normalise_eh_columns(n)
    return Prepared(
        n,
        dict(res.sidecars),
        res.report,
        rederived,
        digest,
        input_workbook,
        filename,
        *checked,
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

    from routers.projects import _write_meta
    from services.atomic_io import atomic_write_text, atomic_write_with
    from services.pypsa_service import PyPSAService

    n = prep.network
    n.name = name
    with PyPSAService.get_netcdf_io_lock():
        atomic_write_with(
            dest / "network.nc", lambda p: PyPSAService.export_network_to_netcdf(n, p)
        )
    atomic_write_text(
        dest / "solver_config.json", json.dumps(asdict(prep.solver_config), indent=2)
    )
    if prep.user_ts_json:
        atomic_write_text(dest / "user_ts.json", prep.user_ts_json)
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
            "user_ts_count": prep.user_ts_count,
            "network_revision": 0,
            "parent_project": None,
            "scenario_description": None,
        },
    )


def create_project(db, user, name: str, prep: Prepared):
    """
    A new project row and folder holding the prepared network, all or nothing.

    Every check has run (`prepare`, the sidecars included) before the row exists.
    `create_root` refuses a taken name (409) before any folder is made, and a fresh
    folder is never an existing one, so a collision overwrites nothing. A failure after the row exists
    removes the row and the folder this call made.
    """
    from services import project_registry

    project = project_registry.create_root(db, user, name)
    made = False
    try:
        dest = project_registry.project_dir(project)
        dest.mkdir(parents=True, exist_ok=False)
        made = True
        _write_files(dest, prep, project.name)
    except BaseException as exc:
        if made:
            shutil.rmtree(dest, ignore_errors=True)
        # A failed flush or statement leaves the session unusable until rolled back.
        db.rollback()
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
