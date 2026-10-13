"""Exports use the same validated answer, coverage and evidence as chat/TTS."""

import csv
import hashlib
import io
import json
import zipfile

from services import assurance_records as records
from harness.durable import network_guard


def _cell(value):
    # Component identifiers are untrusted text, including in spreadsheets.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def export(owner, answer_id, format):
    if format not in {"json", "zip"}:
        raise ValueError("unsupported_checked_export")
    with network_guard():
        answer = records.answer(owner, answer_id)
        evidence = [
            records.public(records.evidence(owner, eid))
            for eid in answer["receipt"]["evidence_ids"]
        ]
        envelope = {
            "schema_version": 1,
            "answer": records.public(answer),
            "evidence": evidence,
            "limitations": answer["receipt"]["limitations"],
        }
        if answer["receipt"].get("operation_ids"):
            from harness.effects import operation

            envelope["operations"] = [
                records.public(operation(owner, oid))
                for oid in answer["receipt"]["operation_ids"]
            ]
        data = json.dumps(
            envelope, sort_keys=True, ensure_ascii=False, allow_nan=False
        ).encode()
        records.authorize(owner, answer["project_id"])
    if format == "json":
        return data, "application/json", "checked-answer.json"
    table = io.StringIO(newline="")
    writer = csv.writer(table)
    writer.writerow(
        ["entity_id", "metric", "value", "unit", "formula_id", "evidence_id"]
    )
    for block in answer["blocks"]:
        writer.writerow(
            [
                _cell(block.get(key, ""))
                for key in ("entity_id", "metric", "value", "unit", "formula_id")
            ]
            + [block["source"]["evidence_id"]]
        )
    files = {"answer.json": data, "facts.csv": table.getvalue().encode("utf-8")}
    manifest = {
        "schema_version": 1,
        "answer_id": answer_id,
        "request_scope": answer["receipt"].get("request_scope"),
        "result_slots": answer["receipt"].get("result_slots", []),
        "verdict": answer["verdict"],
        "files": {
            name: {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
            for name, value in files.items()
        },
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, value in files.items():
            bundle.writestr(name, value)
        bundle.writestr("manifest.json", json.dumps(manifest, allow_nan=False))
    return buffer.getvalue(), "application/zip", "checked-answer.zip"
