"""Shared assertions for the LC-43 gate fixtures (``test_lc43_*.py``)."""

from pathlib import Path

import pytest
from fastapi import HTTPException

from services.pypsa_service import PyPSAService
from tests.test_lc06_d22_undo_checkpoints import fingerprint


def refused(call, status: int, kind: str) -> None:
    """``call`` raises the exact status and kind: ``detail["error_kind"]``, or the plain
    ``detail`` string of a route that refuses without a kind."""
    with pytest.raises(HTTPException) as failure:
        call()
    detail = failure.value.detail
    assert failure.value.status_code == status, detail
    assert (detail.get("error_kind") if isinstance(detail, dict) else detail) == kind, (
        detail
    )


def state(project_dir) -> dict:
    """What a refusal must leave as it was, read from disk and the open network.

    ``network`` is the input-table fingerprint of the open network; ``reports`` maps each
    stored report to its number of version files; ``uploads`` lists every upload
    directory (finished or not) and ``blobs`` counts the blob files among them.
    """
    root = Path(project_dir)
    reports = root / "reports"
    uploads = root / "uploads"
    upload_dirs = (
        sorted(d for d in uploads.iterdir() if d.is_dir()) if uploads.is_dir() else []
    )
    return {
        "network": fingerprint(PyPSAService.get_network()),
        "reports": {
            d.name: len(list(d.glob("v*.json")))
            for d in sorted(reports.iterdir())
            if d.is_dir()
        }
        if reports.is_dir()
        else {},
        "uploads": [d.name for d in upload_dirs],
        "blobs": sum((d / "blob").is_file() for d in upload_dirs),
    }
