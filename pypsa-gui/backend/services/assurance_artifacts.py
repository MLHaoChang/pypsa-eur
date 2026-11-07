"""Exact chart data/render/byte bindings, checked on delivery and download."""

from __future__ import annotations

import hashlib
import json

from fastapi import HTTPException


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
        raise ValueError("scope mismatch")
    stamp = manifest["solve_stamp"]
    if manifest["source_kind"] != "captured_result":
        if stamp is not None:
            raise ValueError("input chart with a solve stamp")
        return "input_as_drawn"
    if (
        not isinstance(stamp, dict)
        or set(stamp) != {"input", "exact"}
        or not all(isinstance(v, str) and v for v in stamp.values())
        or stamp["input"] != manifest["validity"]["input_fingerprint"]
    ):
        raise ValueError("solve stamp mismatch")
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


def verify_chart(root, meta, data, project_id, *, provenance=False):
    marker = meta.assurance_sha256
    if marker is None:
        return None  # Legacy/generic files have no checked chart assertion.
    try:
        path = root / "assistant_artifacts" / (meta.file_id + ".json")
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("oversized manifest")
        record = json.loads(path.read_text(encoding="utf-8"))
        manifest = record["manifest"]
        if (
            canonical_sha256(record) != marker
            or manifest["project_id"] != str(project_id)
            or manifest["schema_version"] != 1
            or canonical_sha256(manifest["dataset"]) != manifest["dataset_sha256"]
            or canonical_sha256(manifest["render_spec"])
            != manifest["render_spec_sha256"]
            or hashlib.sha256(data).hexdigest() != manifest["artifact_sha256"]
            or manifest["artifact_sha256"] != meta.sha256
        ):
            raise ValueError("manifest mismatch")
        scope = scope_status(manifest, project_id)
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
        return record if provenance else manifest
    except (OSError, ValueError, TypeError, KeyError):
        raise HTTPException(
            409,
            {
                "error_kind": "artifact_evidence_unavailable",
                "message": "Chart data, rendering or bytes could not be verified.",
            },
        ) from None
