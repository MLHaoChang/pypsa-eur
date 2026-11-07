"""Exact chart data/render/byte bindings, checked on delivery and download."""

from __future__ import annotations

import hashlib
import json
import logging
import os
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
