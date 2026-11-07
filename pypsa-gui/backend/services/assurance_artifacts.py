"""Exact chart data/render/byte bindings, checked on delivery and download."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time

from fastapi import HTTPException

logger = logging.getLogger(__name__)


class _Integrity(ValueError):
    """A verdict: the stored chart does not match itself (S2 J1). Only this
    quarantines; a read error, a lock timeout or an outdated solve does not."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def canonical_sha256(value):
    def normalize(item):
        if isinstance(item, float) and item.is_integer():
            return int(item)
        if isinstance(item, dict):
            return {k: normalize(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(v) for v in item]
        return item

    return hashlib.sha256(
        json.dumps(
            normalize(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


# A4.1/H3: absent together = a chart drawn before them (legacy, unscoped);
# present = checked, never downgraded to legacy.
_SCOPE_KEYS = ("scope", "solve_stamp")


def solve_stamp(network):
    """The solve's recorded input fingerprints, as the result-freshness check reads them."""
    meta = getattr(network, "meta", None) or {}
    stamp = {
        "input": meta.get("assistant_solve_input_fingerprint"),
        "exact": meta.get("assistant_solve_exact_input_fingerprint"),
    }
    return stamp if all(isinstance(v, str) and v for v in stamp.values()) else None


def _resident(project_id):
    """The project's loaded context, or None when it is not loaded."""
    from services.pypsa_service import PyPSAService

    for key in PyPSAService.list_ids():
        if key == project_id or key.endswith(":" + project_id):
            ctx = PyPSAService.get_context(key)
            if ctx is not None:
                return ctx
    ctx = PyPSAService.get_request_context() or PyPSAService.get_active_context()
    if ctx is not None and str(getattr(ctx, "project_uuid", "") or "") == project_id:
        return ctx
    return None


def _scope(manifest, project_id):
    present = [key in manifest for key in _SCOPE_KEYS]
    if not any(present):
        return "legacy"
    if not all(present) or manifest["scope"] != "current":
        raise _Integrity("manifest")
    stamp = manifest["solve_stamp"]
    if manifest["source_kind"] != "captured_result":
        if stamp is not None:
            raise _Integrity("manifest")
        return "input_as_drawn"
    if (
        not isinstance(stamp, dict)
        or set(stamp) != {"input", "exact"}
        or not all(isinstance(v, str) and v for v in stamp.values())
        or stamp["input"] != manifest["validity"]["input_fingerprint"]
    ):
        raise _Integrity("manifest")
    ctx = _resident(project_id)
    if ctx is None:
        return "solve_not_rechecked"
    if solve_stamp(ctx.network) != stamp:
        raise ValueError("chart from another solve")
    from services.workflow_tools import solve_freshness

    # Same solve, but inputs edited since: the chart shows that solve's
    # results, which no longer describe the current inputs (GK B P2).
    if solve_freshness(ctx)["inputs_changed_since_solve"] is not False:
        return "solve_stale"
    return "current"


def scope_status(manifest, project_id):
    """The scope status of a manifest `verify_chart` has already verified."""
    if manifest.get("source_kind") not in ("configured_input", "captured_result"):
        return None
    return _scope(manifest, str(project_id))


def _quarantine_path(root, file_id):
    from services.upload_service import _FILE_ID_RE

    # J2: the store's own id pattern, before any path is built.
    if not isinstance(file_id, str) or not _FILE_ID_RE.fullmatch(file_id):
        raise ValueError("invalid file id")
    return root / "assistant_artifacts" / "quarantine" / (file_id + ".json")


def quarantined(root, file_id):
    """True once a file failed an integrity verdict; a marker that cannot be
    checked counts as present (J4)."""
    try:
        os.lstat(_quarantine_path(root, file_id))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True
    return True


def quarantine(root, file_id, code):
    """Record the verdict for good (H8). A failed write is logged; the access
    that found the mismatch is refused either way."""
    from harness import durable

    try:
        path = _quarantine_path(root, file_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with durable.file_lock(str(path.parent.parent / ".quarantine.lock")):
            if not path.exists():
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps({"file_id": file_id, "reason_code": code, "at": time.time()}),
                                     encoding="utf-8")
                os.replace(temporary, path)
    except (OSError, TimeoutError, ValueError) as error:
        logger.warning("chart quarantine not recorded (%s)", type(error).__name__)


def _labels(record, manifest):
    """S2: the render spec says what the dataset holds (scoped manifests only, J3)."""
    spec, data = manifest["render_spec"], manifest["dataset"]
    unit, attribute = record["unit"], record["attribute"]
    bar = len(data["index"]) == len(record["names"]) and data["columns"] == [attribute]
    if (
        spec["title"] != f"{record['component']}: {attribute}"
        or spec["y_label"] != unit
        or set(data["units"]) != set(data["columns"])
        or any(value != unit for value in data["units"].values())
        or spec["series"] != data["columns"]
        or spec["kind"] != ("bar" if bar else "line")
        or spec["x_label"] != ("Asset" if bar else "Snapshot index")
        or spec["x_values"] != (data["index"] if bar else list(range(len(data["index"]))))
        or record["rows"] != len(data["index"])
    ):
        raise _Integrity("labels")


def _verify(root, meta, data, project_id):
    path = root / "assistant_artifacts" / (meta.file_id + ".json")
    if path.stat().st_size > 2 * 1024 * 1024:
        raise _Integrity("sidecar")
    text = path.read_text(encoding="utf-8")  # an OSError here is no verdict
    try:
        record = json.loads(text)
        manifest = record["manifest"]
        if canonical_sha256(record) != meta.assurance_sha256:
            raise _Integrity("sidecar")
        if manifest["project_id"] != str(project_id):
            raise _Integrity("project")
        if (
            manifest["schema_version"] != 1
            or canonical_sha256(manifest["dataset"]) != manifest["dataset_sha256"]
            or canonical_sha256(manifest["render_spec"]) != manifest["render_spec_sha256"]
        ):
            raise _Integrity("manifest")
        if hashlib.sha256(data).hexdigest() != manifest["artifact_sha256"] or manifest["artifact_sha256"] != meta.sha256:
            raise _Integrity("bytes")
        scoped = manifest.get("source_kind") in ("configured_input", "captured_result") and any(
            key in manifest for key in _SCOPE_KEYS)
        if scoped:
            _labels(record, manifest)
    except (KeyError, TypeError, AttributeError) as error:
        raise _Integrity("sidecar") from error
    except _Integrity:
        raise
    except ValueError as error:  # malformed JSON
        raise _Integrity("sidecar") from error
    try:
        scope = scope_status(manifest, project_id)
    except (KeyError, TypeError) as error:
        raise _Integrity("manifest") from error
    if scope is not None:
        manifest = {**manifest, "scope_status": scope}
    if manifest.get("source_kind") == "table_export":
        _verify_table(manifest, data)  # seal and bytes already passed (R6)
    if manifest.get("source_kind") == "report_export":
        manifest = _verify_report(root, manifest, data)  # seal and bytes already passed (X4)
    if manifest.get("source_kind") == "checked_evidence":
        # A checked chart is still its evidence's: same source hash, same values.
        from services import checked_charts

        try:
            status = checked_charts.verify(manifest)
        except HTTPException:
            status = False
        if not status:
            raise ValueError("checked chart evidence mismatch")
        manifest = {**manifest, "evidence_status": status}
        if status in ("expired", "missing"):
            note = checked_charts.EXPIRED_NOTE if status == "expired" else checked_charts.MISSING_NOTE
            manifest["evidence_note"] = note.format(
                drawn_at=checked_charts.readable_stamp(manifest["drawn_at"]))
    return record, manifest


def verify_chart(root, meta, data, project_id, *, provenance=False):
    """The verified manifest view (scope and evidence status added), the
    stored record with provenance=True, or both with provenance="both"."""
    if meta.assurance_sha256 is None:
        return None  # Legacy/generic files have no checked chart assertion.
    try:
        if quarantined(root, meta.file_id):
            raise ValueError("quarantined")
        try:
            record, view = _verify(root, meta, data, project_id)
        except _Integrity as verdict:
            quarantine(root, meta.file_id, verdict.code)
            raise
        if provenance == "both":
            return record, view
        return record if provenance else view
    except (OSError, ValueError, TypeError, KeyError, TimeoutError):
        raise HTTPException(
            409,
            {
                "error_kind": "artifact_evidence_unavailable",
                "message": "Chart data, rendering or bytes could not be verified.",
            },
        ) from None


# Import assertions (AA-05 S3, H10, R1-R4)

_IMPORT_KEY = "assistant_import"


def stamp_import(network, file_id, sha256, fmt):
    """Mark a network the assistant just imported as an input assertion. Any
    validated fact the file itself carries is dropped and its stamp replaced
    under a fresh server-minted id (R1, R1a): validation lives in the app-data
    store only, so a pre-stamped file never reads validated."""
    import uuid

    for key in [key for key in network.meta if str(key).startswith("assistant_validated")]:
        del network.meta[key]
    network.meta[_IMPORT_KEY] = {"import_id": uuid.uuid4().hex, "file_id": file_id,
                                 "sha256": sha256, "format": fmt}


_IMPORT_ID_RE = re.compile(r"\A[0-9a-f]{32}\Z")


def _validation_path(import_id):
    """One record per import in the app-data store, outside any network or
    project (R1); the id is checked before a path is built (R1b)."""
    import app_paths

    if not isinstance(import_id, str) or not _IMPORT_ID_RE.fullmatch(import_id):
        raise ValueError("invalid import id")
    return app_paths.app_data_dir() / "assistant_import_validations" / (import_id + ".json")


def _exact(ctx):
    from services import workflow_tools

    return workflow_tools._network_input_fingerprint(ctx, exact=True)


def exact_or_none(ctx):
    try:
        return _exact(ctx)
    except Exception:  # noqa: BLE001 -- unloaded or unreadable: never validated
        return None


def record_validation(ctx, exact):
    """validate_network passed: remember it for this import and these exact
    inputs in the app-data store, outside the network (R1, R1d, R2). A failed write is logged; the status
    then stays input_assertion."""
    from harness import durable

    stamp = getattr(getattr(ctx, "network", None), "meta", {}).get(_IMPORT_KEY)
    if not isinstance(stamp, dict):
        return
    try:
        record = {"import_id": stamp["import_id"], "sha256": stamp["sha256"],
                  "exact": exact, "at": time.time()}
        path = _validation_path(stamp["import_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with durable.file_lock(str(path.parent / ".lock")):
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(record), encoding="utf-8")
            os.replace(temporary, path)
    except Exception as error:  # noqa: BLE001 -- never validated on any failure
        logger.warning("import validation not recorded (%s)", type(error).__name__)


_READ_NOW = object()


def import_status(ctx, exact=_READ_NOW):
    """"not_imported" without an assistant import stamp; "validated" only when
    the app-data record names this import and the current exact inputs;
    "input_assertion" otherwise, including on any error (R3). A caller that
    already read the exact inputs passes them; None (an unreadable read) is
    never re-read, so one call uses one fingerprint."""
    stamp = getattr(getattr(ctx, "network", None), "meta", {}).get(_IMPORT_KEY)
    if stamp is None:
        return "not_imported"
    try:
        record = json.loads(_validation_path(stamp["import_id"]).read_text(encoding="utf-8"))
        current = _exact(ctx) if exact is _READ_NOW else exact
        if current is not None and (isinstance(stamp, dict) and record["import_id"] == stamp["import_id"]
                and record["sha256"] == stamp["sha256"] and record["exact"] == current):
            return "validated"
    except Exception:  # noqa: BLE001 -- missing, unreadable or unloaded: not validated
        pass
    return "input_assertion"


# Table exports (AA-05 S3, A4.5, R5-R8)



def _xlsx_cell(value):
    """A cell as openpyxl writes it and reads it back: numbers to 16
    significant digits ("%.16g"), NaN, inf and "" as empty, datetimes as ISO."""
    import datetime as _dt
    import math

    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return float(format(value, ".16g"))
    if isinstance(value, _dt.datetime):
        return value.isoformat()
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day).isoformat()
    return value if isinstance(value, str) else str(value)


def _rectangle(rows):
    """Rows padded to the sheet's widest non-empty cell, trailing empty rows
    dropped: the shape a read-only openpyxl sheet reports."""
    width = max((i + 1 for row in rows for i, cell in enumerate(row) if cell is not None), default=0)
    rows = [list(row[:width]) + [None] * (width - len(row[:width])) for row in rows]
    while rows and all(cell is None for cell in rows[-1]):
        rows.pop()
    return rows


def table_as_written(fmt, sheets):
    """The dataset of `sheets` ([(name, rows of escaped cells)]) as the writer
    puts it in the file."""
    if fmt == "csv":
        ((_, rows),) = sheets
        return {"sheets": [{"name": None, "rows": [
            ["" if cell is None else str(cell) for cell in row] for row in rows]}]}
    return {"sheets": [{"name": name, "rows": _rectangle([[_xlsx_cell(c) for c in row] for row in rows])}
                       for name, rows in sheets]}


def parse_table(fmt, data):
    """The dataset the bytes say, read with the one parser download and
    delivery use."""
    import csv
    import io

    if fmt == "csv":
        rows = list(csv.reader(io.StringIO(data.decode("utf-8"), newline="")))
        return {"sheets": [{"name": None, "rows": rows}]}
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(data), read_only=True, data_only=False)
    try:
        return {"sheets": [{"name": sheet.title,
                            "rows": _rectangle([[_xlsx_cell(c) for c in row]
                                                for row in sheet.iter_rows(values_only=True)])}
                           for sheet in book.worksheets]}
    finally:
        book.close()


def _verify_table(manifest, data):
    """R6: runs after the seal and byte checks. A re-parse that disagrees with
    bytes whose hash still matches is parser drift, not tampering: refused and
    logged, never quarantined."""
    try:
        same = canonical_sha256(parse_table(manifest["render_spec"]["format"], data)) == manifest["dataset_sha256"]
    except Exception as error:  # noqa: BLE001 -- any parse failure refuses
        logger.warning("table re-parse failed (%s)", type(error).__name__)
        raise ValueError("table re-parse failed") from None
    if not same:
        logger.warning("table re-parse disagrees with its dataset hash")
        raise ValueError("table re-parse mismatch")


# Report exports (AA-05 S4b, GK B X1-X6)

REPORT_SOURCE_NOTES = {
    "version_missing": "The report version this file was exported from is no longer stored; "
                       "the file is as exported, but it can no longer be traced to a stored version.",
    "figure_missing": "A figure this file was exported with is no longer stored; "
                      "the file is as exported.",
    "template_missing": "The template this file was exported with is no longer stored; "
                        "the file is as exported.",
}


def report_version_changed():
    return HTTPException(409, {
        "error_kind": "report_version_changed",
        "message": "The stored report version, figure or template this file was exported from has "
                   "changed since the export; export the report again.",
    })


def _verify_report(root, manifest, data):
    """X3/X4: runs after the seal and byte checks, so nothing here quarantines.

    A source that is gone is noted (`source_status`) and the bytes are served;
    a source present with another hash refuses (`report_version_changed`); a
    Word export whose disclosures no longer read back is parser drift on
    matching bytes: refused and logged."""
    from services import upload_service
    from services.reports import store
    from services.reports.docx_reader import body_text
    from services.reports.report_job import missing_disclosures

    dataset = manifest["dataset"]
    report_id = dataset["report_id"]
    status = None
    try:
        current = store.version_sha256(root, report_id, dataset["version"])
    except store.ReportNotFound:
        status = "version_missing"
    else:
        if current != dataset["version_sha256"]:
            raise report_version_changed()
    for figure_id, sha in sorted(dataset["figures"].items()):
        try:
            figure = store.figure_path(root, report_id, figure_id).read_bytes()
        except store.ReportStoreError:
            status = status or "figure_missing"
            continue
        if hashlib.sha256(figure).hexdigest() != sha:
            raise report_version_changed()
    template = dataset["template"]
    if template is not None:
        try:
            raw = upload_service.get_upload_bytes("", template["file_id"], project_dir=root)
        except HTTPException as error:
            if error.status_code != 404:
                raise
            status = status or "template_missing"
        else:
            if hashlib.sha256(raw).hexdigest() != template["sha256"]:
                raise report_version_changed()
    if manifest["render_spec"]["format"] == "docx":
        try:
            lost = missing_disclosures(manifest["render_spec"]["disclosures"], body_text(data))
        except Exception as error:  # noqa: BLE001 -- any read-back failure refuses
            logger.warning("report export read-back failed (%s)", type(error).__name__)
            raise ValueError("report export read-back failed") from None
        if lost:
            logger.warning("report export read-back lost %d disclosure(s) on matching bytes", len(lost))
            raise ValueError("report export disclosure drift")
    if status is not None:
        manifest = {**manifest, "source_status": status, "source_note": REPORT_SOURCE_NOTES[status]}
    return manifest
