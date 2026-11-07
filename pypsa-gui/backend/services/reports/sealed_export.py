# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""AA-05 S4b: the sealed manifest of an assistant report export.

`routers/reports.export_report` calls `publish_sealed_report` on its strict
path only, after the render, the PDF conversion and the disclosure check
(GK B X1); `services.assurance_artifacts._verify_report` re-checks what it
seals on every download and delivery.
"""

from __future__ import annotations

import hashlib
from typing import Any

from models.report import ReportDocument
from services.reports import store
from services.reports.formatting import is_withheld_block
from services.reports.report_job import disclosure_texts


def publish_sealed_report(
    project: Any,
    doc: ReportDocument,
    fmt: str,
    data: bytes,
    filename: str,
    mime: str,
    *,
    figure_bytes: dict[str, bytes],
    template: bytes | None,
    plan: dict | None,
) -> dict:
    """AA-05 S4b: an assistant export with its sealed manifest (B1, X1, X5).

    The manifest names the stored version by the hash of its file as written
    (X2), each figure and the template by their bytes, the disclosures the
    export carries and the placeholders it shows. The mapping plan is recorded
    but not re-checked on download: the template routes may change it later
    without changing what this file was rendered from."""
    from services import chat_tools
    from services import workflow_tools as workflow
    from services.assurance_artifacts import canonical_sha256, quarantined

    byte_hash = hashlib.sha256(data).hexdigest()
    if quarantined(project.directory, byte_hash[:16]):
        workflow._refuse(
            "artifact_quarantined",
            "This exact file failed an integrity check earlier and is quarantined; it cannot be "
            "published again. Tell the user it is unavailable and do not retry this call.",
            409,
        )
    dataset = {
        "report_id": doc.report_id,
        "version": doc.version,
        "version_sha256": store.version_sha256(
            project.directory, doc.report_id, doc.version
        ),
        "version_hash_basis": "stored_file",
        "figures": {
            fid: hashlib.sha256(raw).hexdigest()
            for fid, raw in sorted(figure_bytes.items())
        },
        "template": (
            None
            if template is None
            else {
                "file_id": doc.template_file_id,
                "sha256": hashlib.sha256(template).hexdigest(),
            }
        ),
        "mapping_plan_sha256": None if plan is None else canonical_sha256(plan),
    }
    # AA-05 Y5: a declared summary names the sections its numbers may come from.
    summaries = {s.section_id: list(s.summarises) for s in doc.sections if s.summarises}
    if summaries:
        dataset["summaries"] = summaries
    withheld = [
        {"section_id": s.section_id, "placeholders": n}
        for s in doc.sections
        if (n := sum(1 for b in s.blocks if is_withheld_block(b)))
    ]
    render_spec = {
        "format": fmt,
        "disclosures": disclosure_texts(doc),
        "withheld": withheld,
    }
    manifest = {
        "schema_version": 1,
        "project_id": str(project.uuid),
        "source_kind": "report_export",
        "dataset": dataset,
        "render_spec": render_spec,
        "dataset_sha256": canonical_sha256(dataset),
        "render_spec_sha256": canonical_sha256(render_spec),
        "artifact_sha256": byte_hash,
        "validity": {
            "coverage": "stored_version",
            "limitations": [
                "Withheld numbers are listed in Numbers to check; this does not certify "
                "engineering validity."
            ],
        },
    }
    record = {
        "filename": filename,
        "report_id": doc.report_id,
        "version": doc.version,
        "manifest": manifest,
    }
    return chat_tools._publish_sealed_export(
        project.directory, record, data, filename, mime
    )[0]
