"""
Pydantic schemas for chatbot upload + agent-export metadata (Phase A).

Two models:

  * ``UploadMeta`` — the on-disk ``meta.json`` per file. v1 schema; Phase C
    adds optional fields for PDF page count + truncation flag.
  * ``DeleteUploadResponse`` — DELETE endpoint body. Carries `reason` ONLY
    when `deleted=False`; the validator enforces that contract so a future
    refactor can't silently start emitting `reason` on successful deletes.

`extra="ignore"` on both models so a future schema version with extra
fields can be loaded by a v1 reader (forward compat).
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, model_validator


# Per-file kind. `agent_export` files are produced by chat tools (Phase B);
# everything else is a user upload. The UI uses this to render a different
# chip + show a download icon for agent_export entries.
UploadKind = Literal["user_upload", "agent_export"]


class UploadMeta(BaseModel):
    """
    Persisted alongside the blob in ``uploads/<file_id>/meta.json``.

    All upload reads / writes go through this model so a corrupt or
    forward-version file gets a single clear ValidationError at the
    serialization boundary rather than tripping a ``KeyError`` deep
    inside ``list_uploads`` or ``read_excel_sheet``.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    schema_version: int = 1
    file_id: str
    filename: str
    mime: str
    size: int
    sha256: str
    kind: UploadKind = "user_upload"
    uploaded_at: float
    blob_ready: bool = False
    version: int = 1
    # Phase C additions — optional in v1.
    page_count: Optional[int] = None
    truncated_to_100_pages: Optional[bool] = None


class DeleteUploadResponse(BaseModel):
    """
    Body for ``DELETE /api/projects/{name}/uploads/{file_id}``. Returns
    HTTP 200 for both branches; clients should switch on `deleted` not on
    the status code.

    Contract enforced by the validator:
      * ``deleted=True``  → `reason` MUST be absent (None).
      * ``deleted=False`` → `reason` MUST be present.

    The single endpoint shape lets a UI chip's delete button always handle
    "gone / failed" uniformly rather than special-casing 404s.
    """

    model_config = ConfigDict(extra="ignore")

    deleted: bool
    file_id: str
    reason: Optional[Literal["not_found", "in_use"]] = None

    @model_validator(mode="after")
    def _check_reason_contract(self) -> "DeleteUploadResponse":
        if self.deleted and self.reason is not None:
            raise ValueError(
                "DeleteUploadResponse(deleted=True) MUST NOT carry a reason"
            )
        if not self.deleted and self.reason is None:
            raise ValueError(
                "DeleteUploadResponse(deleted=False) MUST carry a reason"
            )
        return self
