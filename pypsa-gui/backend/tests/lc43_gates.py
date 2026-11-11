"""Shared assertion for the LC-43 gate fixtures (``test_lc43_*.py``)."""

import pytest
from fastapi import HTTPException


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
