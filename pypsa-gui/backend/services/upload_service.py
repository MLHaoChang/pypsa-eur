"""
Chatbot upload storage (Phase A).

Per-project file storage at ``projects/<name>/uploads/<file_id>/`` where
``file_id = sha256(bytes)[:16]``. Two-file layout per upload:

    uploads/<file_id>/
        meta.json   — UploadMeta (schema versioned, blob_ready flag)
        blob        — raw bytes

The blob_ready=true flip is the last write under the per-project lock, so a
crashed upload mid-write is invisible to `list_uploads`. `prune_orphans`
reaps incomplete entries after a 60-second mtime grace period (run by the
router on startup + periodically).

Concurrency contract:
  * Per-project RLock (`_project_upload_lock(name)`) guards the entire
    add / list / delete critical section. Re-entrant so a tool dispatch
    that already holds the lock for a multi-step write doesn't deadlock.
  * The lock is released BEFORE control returns to PyPSA mutation code
    (Phase B integration). The 3-level hierarchy described in the plan
    (chat_state.lock ⊃ upload_lock ⊃ pypsa.lock) is enforced by the
    Phase B caller; this module only owns the middle tier.
  * No cross-project deduplication — each project's uploads/ is
    self-contained. Saves bundle/copy semantics from cross-project
    hardlink complexity.

Idempotency:
  * `add_upload(same_bytes)` returns the existing meta with the original
    `uploaded_at` preserved (first-writer wins).
  * `delete_upload(missing)` returns success-style no-op via the caller's
    DeleteUploadResponse contract — `add_upload` semantics are dedup,
    `delete_upload` semantics are tombstone.

Quota:
  * Per-project soft cap of 100 MB by default. Walks uploads/ on every add
    to compute current usage — cheap for the expected dozens-of-files
    scale; revisit if a project ends up with thousands.
"""
from __future__ import annotations

import hashlib
import logging
import os
import pathlib
import shutil
import threading
import time
from dataclasses import dataclass

from fastapi import HTTPException

from models.upload_schemas import DeleteUploadResponse, UploadMeta, UploadKind
from services.filename_service import safe_upload_filename

logger = logging.getLogger(__name__)


# ── Limits ──────────────────────────────────────────────────────────────────
# Per-file 25 MB (locked decision row 1).
MAX_FILE_BYTES = 25 * 1024 * 1024
# Per-project soft cap. 100 MB = ~4 max-size uploads + headroom for exports.
# A future tool will surface usage in `meta` so the UI can show a bar.
DEFAULT_QUOTA_BYTES = 100 * 1024 * 1024
# PDF page cap (Phase C, locked decision row 3) — multimodal blocks are
# built from the first N pages only; the original blob is preserved on disk.
PDF_PAGE_CAP = 100
# Per-image multimodal cap (Phase C) — Anthropic charges per image-token,
# which scales with size. 10 MB matches the plan; 5 MB triggers a frontend
# toast (advisory, not enforced server-side).
MAX_MULTIMODAL_IMAGE_BYTES = 10 * 1024 * 1024
# Per-message multimodal block cap (Phase C) — Anthropic accepts up to
# 100 content blocks, but 20 is generous for typical chat usage and keeps
# token consumption predictable.
MAX_MULTIMODAL_BLOCKS = 20

# Allowlisted MIME types — sniff result must match one of these. PDFs +
# common images route to Anthropic multimodal (Phase C); Excel + Word
# parse server-side (Phase B). CSV is text/plain but worth allowing as a
# poor-man's tabular input.
ALLOWED_MIME_TYPES = frozenset({
    # Images (Phase C multimodal)
    "image/png", "image/jpeg", "image/webp", "image/gif",
    # PDFs (Phase C multimodal)
    "application/pdf",
    # Excel (Phase B server-parse)
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    "application/vnd.ms-excel",  # .xls
    # Word (Phase B server-parse)
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    # CSV / plain text — server-parse to a DataFrame in Phase B.
    "text/csv",
    "text/plain",
})


# ── Per-project lock registry ───────────────────────────────────────────────
# Re-entrant per-project locks keyed by project name. `_locks_master` guards
# the registry itself; once a per-project lock is obtained, the caller holds
# IT (not the master) for the duration of the critical section.
_locks_master = threading.Lock()
_locks: dict[str, threading.RLock] = {}


def _project_upload_lock(name: str) -> threading.RLock:
    """
    Return a re-entrant lock unique to `name`. Cached for the process
    lifetime so two concurrent uploads to the same project serialise on
    the same lock object.
    """
    with _locks_master:
        lock = _locks.get(name)
        if lock is None:
            lock = threading.RLock()
            _locks[name] = lock
        return lock


# ── Path helpers ────────────────────────────────────────────────────────────
@dataclass
class _ProjectPaths:
    project_dir: pathlib.Path
    uploads_dir: pathlib.Path


def _resolve_paths(name: str) -> _ProjectPaths:
    """
    Locate `projects/<name>/uploads/`. Reuses the existing project-dir
    safety guard from routers.projects so this module doesn't duplicate
    the path-traversal regex.
    """
    # Lazy import — routers/projects imports this module's parent service
    # chain in turn, so a top-level import here would create a cycle.
    from routers.projects import _safe_project_dir
    project_dir = _safe_project_dir(name)
    uploads_dir = project_dir / "uploads"
    return _ProjectPaths(project_dir=project_dir, uploads_dir=uploads_dir)


def _file_id_for(blob_bytes: bytes) -> tuple[str, str]:
    """Return (file_id_short, sha256_full) for `blob_bytes`."""
    h = hashlib.sha256(blob_bytes).hexdigest()
    return h[:16], h


def _probe_pdf_page_count(blob_bytes: bytes) -> tuple[int | None, bool]:
    """
    Return ``(page_count, truncated_flag)`` for a PDF blob.

    Used at upload time so the meta.json records the page count and
    whether multimodal pass-through would need to truncate to the first
    ``PDF_PAGE_CAP`` pages. The original blob is always preserved on disk
    unchanged; truncation only happens in-memory in Phase C's multimodal
    block builder.

    Returns ``(None, False)`` on any pypdf error so a malformed PDF still
    uploads cleanly — the multimodal builder will surface the failure
    later if the agent tries to attach it.
    """
    try:
        from pypdf import PdfReader
        reader = PdfReader(io_module_BytesIO_compat(blob_bytes), strict=False)
        n = len(reader.pages)
        return n, n > PDF_PAGE_CAP
    except Exception:  # noqa: BLE001 — pypdf raises a bag of exception types
        return None, False


def io_module_BytesIO_compat(blob: bytes):
    """
    Local import wrapper so `_probe_pdf_page_count` doesn't drag a top-
    level `io` import into the module just for the page-count probe.
    """
    import io as _io
    return _io.BytesIO(blob)


# ── Atomic helpers ──────────────────────────────────────────────────────────
def _atomic_write_bytes(dest: pathlib.Path, data: bytes) -> None:
    """Write `data` to `dest` atomically via tmp + os.replace."""
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, dest)


def _atomic_write_meta(dest: pathlib.Path, meta: UploadMeta) -> None:
    """Serialise `meta` as JSON and write atomically."""
    payload = meta.model_dump_json(indent=2).encode("utf-8")
    _atomic_write_bytes(dest, payload)


def _read_meta(meta_path: pathlib.Path) -> UploadMeta | None:
    """Read meta.json into UploadMeta. None on missing / malformed."""
    if not meta_path.exists():
        return None
    try:
        raw = meta_path.read_text(encoding="utf-8")
        return UploadMeta.model_validate_json(raw)
    except Exception:  # noqa: BLE001 — corrupt JSON is recoverable; log + drop
        logger.warning("upload: malformed meta.json at %s — treating as orphan", meta_path)
        return None


# ── Quota ───────────────────────────────────────────────────────────────────
def _current_usage_bytes(uploads_dir: pathlib.Path) -> int:
    """Walk uploads_dir and sum the size of every blob file."""
    if not uploads_dir.exists():
        return 0
    total = 0
    for sub in uploads_dir.iterdir():
        blob = sub / "blob"
        if blob.exists():
            try:
                total += blob.stat().st_size
            except OSError:
                continue
    return total


# ── Public API ──────────────────────────────────────────────────────────────
def add_upload(
    name: str,
    file_bytes: bytes,
    filename: str,
    sniffed_mime: str,
    kind: UploadKind = "user_upload",
    quota_bytes: int = DEFAULT_QUOTA_BYTES,
) -> UploadMeta:
    """
    Add `file_bytes` to `name`'s uploads dir. Idempotent on bytes — same
    sha256 returns the existing meta unchanged. Caller is responsible
    for size + MIME validation BEFORE this call; this function only
    enforces the quota.

    Raises:
        HTTPException(507, error_kind="upload_quota_exceeded") on quota.
        HTTPException(500, error_kind="file_integrity_error") on a
            hash-collision-style dedup mismatch (effectively impossible
            in practice but caught to fail loudly).
    """
    paths = _resolve_paths(name)
    paths.uploads_dir.mkdir(parents=True, exist_ok=True)
    file_id, sha256_full = _file_id_for(file_bytes)
    file_dir = paths.uploads_dir / file_id
    meta_path = file_dir / "meta.json"
    blob_path = file_dir / "blob"

    safe_name = safe_upload_filename(filename, ext=pathlib.Path(filename).suffix.lstrip("."))

    with _project_upload_lock(name):
        # Dedup hit: existing meta + ready blob → return as-is.
        existing = _read_meta(meta_path)
        if existing is not None and existing.blob_ready:
            if existing.sha256 != sha256_full:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error_kind": "file_integrity_error",
                        "message": (
                            f"existing meta.json for {file_id} has sha256 "
                            f"{existing.sha256[:16]}... but new bytes hash to "
                            f"{sha256_full[:16]}... — possible disk corruption"
                        ),
                    },
                )
            return existing

        # Quota check — only on a fresh upload (dedup hit doesn't grow disk).
        usage = _current_usage_bytes(paths.uploads_dir)
        if usage + len(file_bytes) > quota_bytes:
            raise HTTPException(
                status_code=507,
                detail={
                    "error_kind": "upload_quota_exceeded",
                    "message": (
                        f"project '{name}' would exceed the {quota_bytes // (1024*1024)} MB "
                        f"upload quota ({usage // (1024*1024)} MB used + "
                        f"{len(file_bytes) // (1024*1024)} MB new)"
                    ),
                    "hint": "delete unused uploads via the chat panel chip strip",
                },
            )

        # Fresh upload path.
        file_dir.mkdir(parents=True, exist_ok=True)

        uploaded_at = time.time()
        # Phase C — PDF page-count metadata. Recorded on upload so the
        # multimodal block builder can refuse / truncate without re-parsing.
        # Schema bumps to v2 only when these optional fields are set; v1
        # readers ignore them via `extra="ignore"`.
        page_count = None
        truncated_flag: bool | None = None
        schema_v = 1
        if sniffed_mime == "application/pdf":
            page_count, was_truncated = _probe_pdf_page_count(file_bytes)
            if page_count is not None:
                schema_v = 2
                truncated_flag = was_truncated
        meta = UploadMeta(
            schema_version=schema_v,
            file_id=file_id,
            filename=safe_name,
            mime=sniffed_mime,
            size=len(file_bytes),
            sha256=sha256_full,
            kind=kind,
            uploaded_at=uploaded_at,
            blob_ready=False,
            version=1,
            page_count=page_count,
            truncated_to_100_pages=truncated_flag,
        )
        # Write meta (blob_ready=false) → write blob → flip meta.blob_ready=true.
        # An orphan blob without a meta is invisible to list_uploads.
        _atomic_write_meta(meta_path, meta)
        _atomic_write_bytes(blob_path, file_bytes)
        meta = meta.model_copy(update={"blob_ready": True})
        _atomic_write_meta(meta_path, meta)
        return meta


def list_uploads(name: str) -> list[UploadMeta]:
    """
    Return all blob_ready=true uploads for `name`, newest first by
    uploaded_at. Missing uploads/ dir → []. Holds the per-project lock
    for the read so a concurrent delete can't surface a half-removed
    entry.
    """
    paths = _resolve_paths(name)
    if not paths.uploads_dir.exists():
        return []

    with _project_upload_lock(name):
        out: list[UploadMeta] = []
        for sub in paths.uploads_dir.iterdir():
            if not sub.is_dir():
                continue
            meta = _read_meta(sub / "meta.json")
            if meta is None:
                continue
            if not meta.blob_ready:
                continue
            if not (sub / "blob").exists():
                # Meta is committed but blob vanished — skip gracefully,
                # leave the dir for prune_orphans to clean up.
                continue
            out.append(meta)
        out.sort(key=lambda m: m.uploaded_at, reverse=True)
        return out


def get_upload_meta(name: str, file_id: str) -> UploadMeta:
    """
    Return the UploadMeta for `file_id`. Raises 404 with
    error_kind=upload_not_found on missing.
    """
    paths = _resolve_paths(name)
    with _project_upload_lock(name):
        meta = _read_meta(paths.uploads_dir / file_id / "meta.json")
        if meta is None or not meta.blob_ready:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_kind": "upload_not_found",
                    "message": f"no upload {file_id!r} in project {name!r}",
                },
            )
        return meta


def get_upload_path(name: str, file_id: str) -> pathlib.Path:
    """
    Return the absolute path to the blob for `file_id`. Validates the
    meta is present and blob_ready. Used by the streaming download
    endpoint to keep the path-construction logic centralised.
    """
    paths = _resolve_paths(name)
    blob = paths.uploads_dir / file_id / "blob"
    # Validate via meta read (raises 404 on missing).
    get_upload_meta(name, file_id)
    if not blob.exists():
        raise HTTPException(
            status_code=404,
            detail={
                "error_kind": "upload_not_found",
                "message": f"meta present for {file_id!r} but blob missing — corrupt state",
            },
        )
    return blob


def get_upload_bytes(name: str, file_id: str) -> bytes:
    """Convenience wrapper for tools that need the raw bytes (Phase C)."""
    return get_upload_path(name, file_id).read_bytes()


def delete_upload(name: str, file_id: str) -> DeleteUploadResponse:
    """
    Remove the upload dir. Idempotent — missing file → not_found
    response with HTTP 200 (caller switches on `deleted` field).
    """
    paths = _resolve_paths(name)
    file_dir = paths.uploads_dir / file_id
    with _project_upload_lock(name):
        if not file_dir.exists():
            return DeleteUploadResponse(deleted=False, file_id=file_id, reason="not_found")
        try:
            shutil.rmtree(file_dir)
        except OSError as exc:
            logger.warning("upload: delete %s failed: %s", file_dir, exc)
            return DeleteUploadResponse(deleted=False, file_id=file_id, reason="in_use")
        return DeleteUploadResponse(deleted=True, file_id=file_id)


def get_upload_bytes_truncated_pdf(
    name: str, file_id: str, page_cap: int = PDF_PAGE_CAP,
) -> tuple[bytes, bool]:
    """
    For PDFs with more than ``page_cap`` pages, return ``(truncated_bytes,
    True)`` — a fresh in-memory PDF containing only the first ``page_cap``
    pages. Otherwise return ``(original_bytes, False)``. The on-disk blob
    is NEVER modified.

    Used by the Phase C multimodal block builder so a 200-page PDF
    doesn't blow Anthropic's per-message token cap on the cached system
    prompt + tools + new content.
    """
    raw = get_upload_bytes(name, file_id)
    meta = get_upload_meta(name, file_id)
    if meta.mime != "application/pdf":
        return raw, False
    pages = meta.page_count or 0
    if pages <= page_cap:
        return raw, False
    try:
        from pypdf import PdfReader, PdfWriter
        reader = PdfReader(io_module_BytesIO_compat(raw), strict=False)
        writer = PdfWriter()
        for i in range(min(page_cap, len(reader.pages))):
            writer.add_page(reader.pages[i])
        out = io_module_BytesIO_compat(b"")
        writer.write(out)
        return out.getvalue(), True
    except Exception:  # noqa: BLE001 — fall back to original on any pypdf error
        return raw, False


def build_multimodal_content_blocks(
    name: str, file_ids: list[str],
) -> list[dict]:
    """
    Materialise Anthropic multimodal content blocks for `file_ids` (in the
    order given). Caller MUST append the user text block AFTER this list.

    Block ordering matches `file_ids` exactly — no server-side reordering.

    Raises:
        HTTPException(400, too_many_multimodal_blocks) if > MAX_MULTIMODAL_BLOCKS.
        HTTPException(404, upload_not_found) per file_id with no meta.
        HTTPException(413, image_too_large) if any image exceeds the cap.
        HTTPException(415, mime_not_allowlisted_for_multimodal) for xlsx/docx/csv.

    Empty `file_ids` returns an empty list. PDFs longer than PDF_PAGE_CAP
    are truncated in-memory (original blob preserved). Image MIMEs map to
    Anthropic's `image` block; PDFs to `document`.
    """
    import base64 as _b64

    if not file_ids:
        return []
    if len(file_ids) > MAX_MULTIMODAL_BLOCKS:
        raise HTTPException(
            status_code=400,
            detail={
                "error_kind": "too_many_multimodal_blocks",
                "message": (
                    f"requested {len(file_ids)} multimodal blocks; cap is "
                    f"{MAX_MULTIMODAL_BLOCKS} per turn"
                ),
            },
        )

    image_mimes = {"image/png", "image/jpeg", "image/webp", "image/gif"}
    blocks: list[dict] = []
    for file_id in file_ids:
        meta = get_upload_meta(name, file_id)  # raises 404 on missing
        if meta.mime in image_mimes:
            if meta.size > MAX_MULTIMODAL_IMAGE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail={
                        "error_kind": "image_too_large",
                        "message": (
                            f"image {meta.filename!r} is "
                            f"{meta.size // (1024 * 1024)} MB; cap is "
                            f"{MAX_MULTIMODAL_IMAGE_BYTES // (1024 * 1024)} MB"
                        ),
                    },
                )
            raw = get_upload_bytes(name, file_id)
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": meta.mime,
                    "data": _b64.b64encode(raw).decode("ascii"),
                },
            })
            continue
        if meta.mime == "application/pdf":
            payload, _truncated = get_upload_bytes_truncated_pdf(name, file_id)
            blocks.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": _b64.b64encode(payload).decode("ascii"),
                },
            })
            continue
        # Excel / Word / CSV — agent should use the read_excel_sheet tool
        # instead of the multimodal channel.
        raise HTTPException(
            status_code=415,
            detail={
                "error_kind": "mime_not_allowlisted_for_multimodal",
                "message": (
                    f"MIME {meta.mime!r} is not supported as a multimodal "
                    f"attachment. Use read_excel_sheet / read_upload_meta "
                    f"for Excel/CSV/Word files instead."
                ),
            },
        )
    return blocks


def prune_orphans(name: str, grace_seconds: float = 60.0) -> int:
    """
    Reap upload subdirs whose meta.json is missing OR whose blob_ready
    is False AND whose dir mtime is older than `grace_seconds`. The
    grace period stops a concurrent in-progress upload from being
    pruned mid-write.

    Returns the number of subdirs removed.
    """
    paths = _resolve_paths(name)
    if not paths.uploads_dir.exists():
        return 0
    cutoff = time.time() - grace_seconds
    reaped = 0
    with _project_upload_lock(name):
        for sub in paths.uploads_dir.iterdir():
            if not sub.is_dir():
                continue
            try:
                mtime = sub.stat().st_mtime
            except OSError:
                continue
            if mtime > cutoff:
                continue
            meta_path = sub / "meta.json"
            meta = _read_meta(meta_path)
            if meta is not None and meta.blob_ready:
                continue
            # Orphan — partial write or stale failed upload. Drop it.
            try:
                shutil.rmtree(sub)
                reaped += 1
            except OSError as exc:
                logger.warning("upload: prune %s failed: %s", sub, exc)
    return reaped
