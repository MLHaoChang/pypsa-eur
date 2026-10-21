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
        if manifest.get("source_kind") == "checked_evidence":
            # A checked chart is still its evidence's: same source hash, same values.
            from services import checked_charts

            try:
                current = checked_charts.verify(manifest)
            except HTTPException:
                current = False
            if not current:
                raise ValueError("checked chart evidence mismatch")
        return record if provenance else manifest
    except (OSError, ValueError, TypeError, KeyError):
        raise HTTPException(
            409,
            {
                "error_kind": "artifact_evidence_unavailable",
                "message": "Chart data, rendering or bytes could not be verified.",
            },
        ) from None
