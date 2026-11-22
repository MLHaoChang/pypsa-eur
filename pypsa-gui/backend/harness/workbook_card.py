"""
The workbook import card (excel-input-workbook issue 06, IMPORT-FLOW-1).

`import_input_workbook`'s confirmation carries what approving it creates: the
preview route run again on the upload, the name the new project gets, and the
counts and report the GUI's import dialog shows. Its bytes must be the ones the
model previewed (`expected_sha256`); a different file refuses before any card
(``import_file_changed``), so the card never shows one file and imports another.
The import route checks the hash once more when it runs, and creates a new
project only: a taken name refuses, nothing is overwritten.
"""

from __future__ import annotations

from fastapi import HTTPException

VERSION = 1


def _refuse(kind: str, message: str, status: int = 409):
    """A refusal with a literal kind (the error-kind manifest test reads it)."""
    raise HTTPException(
        status_code=status, detail={"error_kind": kind, "message": message}
    )


def preview(args: dict) -> dict:
    from services import workbook_chat

    expected = str(args.get("expected_sha256") or "").strip().lower()
    out = workbook_chat.preview(str(args.get("file_id") or ""))
    if out["sha256"] != expected:
        _refuse(
            "import_file_changed",
            "The file is not the one that was previewed: preview it again before importing.",
        )
    name = str(args.get("name") or "").strip() or out["suggested_name"]
    return {
        "version": VERSION,
        "kind": "workbook_import",
        "file_id": out["file_id"],
        "filename": out["filename"],
        "sha256": out["sha256"],
        "name": name,
        "summary": out["summary"],
        "report": out["report"],
        "rederived": out["rederived"],
        "not_carried": out["not_carried"],
        "says": f"Import {out['filename']} as a new project '{name}'. Nothing is overwritten; "
        "the open project stays as it is.",
    }
