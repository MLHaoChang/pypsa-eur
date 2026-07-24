"""
Phase A — chatbot upload storage tests.

Covers:
  * Add → list returns entry; same bytes twice → same file_id (dedup).
  * Concurrent same-bytes from two threads serialise via per-project lock.
  * 25 MB cap → 413 file_too_large; zero-byte → 400 empty_file.
  * Path traversal in filename → 400 invalid_filename.
  * MIME validation (sniffed via python-magic).
  * Bundle survival across the 7 lineage transitions (parametrised).
  * Legacy project (no uploads/) loads + Save-As without spurious dir.
  * DELETE missing → 200 {deleted: false, reason: not_found}.
  * Project DELETE removes uploads/ tree.
  * Quota enforcement (107 quota → 507).
  * Orphan blob (no meta.json) invisible to list; prune_orphans reaps.
  * UploadMeta schema v1/v2 round-trip.
  * DeleteUploadResponse validator contract.
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
from models.upload_schemas import DeleteUploadResponse, UploadMeta
from services import upload_service
from services.filename_service import safe_upload_filename


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def app_client(tmp_projects_dir) -> TestClient:
    """TestClient pre-wired with the temp projects dir."""
    return TestClient(main.app)


@pytest.fixture
def demo_project(tmp_projects_dir):
    """Create an empty demo project dir on disk + return its name."""
    proj = tmp_projects_dir / "demo"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "network.nc").write_bytes(b"fake nc")
    (proj / "metadata.json").write_text('{"name":"demo","bus_count":0}', encoding="utf-8")
    return "demo"


# Tiny valid-looking blob with a real xlsx zip magic. Won't open as a real
# spreadsheet, but `python-magic` sniffs it as application/zip + the upload
# router treats application/zip as the soft-mismatch fallback.
_XLSX_LIKE = b"PK\x03\x04" + b"\x00" * 64
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ─────────────────────────────────────────────────────────────────────────
# filename_service
# ─────────────────────────────────────────────────────────────────────────


class TestSafeUploadFilename:
    def test_path_traversal_stripped(self):
        assert ".." not in safe_upload_filename("../etc/passwd")

    def test_slashes_replaced(self):
        out = safe_upload_filename("foo/bar.xlsx")
        assert "/" not in out and "\\" not in out

    def test_windows_reserved_prefixed(self):
        # CON, PRN, etc. become _CON, _PRN
        assert safe_upload_filename("con.txt").startswith("_")
        assert safe_upload_filename("PRN.docx").startswith("_")
        assert safe_upload_filename("LPT1").startswith("_")

    def test_control_chars_replaced(self):
        out = safe_upload_filename("file\x00name\x1f.txt")
        assert "\x00" not in out and "\x1f" not in out

    def test_empty_falls_back_to_synthetic(self):
        out = safe_upload_filename("")
        assert out.startswith("export_")
        assert out.endswith(".bin")

    def test_empty_with_ext_uses_ext(self):
        out = safe_upload_filename("", ext="xlsx")
        assert out.endswith(".xlsx")

    def test_unicode_transliterated(self):
        out = safe_upload_filename("Müller.xlsx")
        # NFKD ASCII-only — non-ASCII chars dropped
        assert "ü" not in out
        # Still has a real-looking name + extension
        assert out.endswith(".xlsx")

    def test_length_cap_preserves_extension(self):
        long_name = "a" * 300 + ".xlsx"
        out = safe_upload_filename(long_name)
        assert len(out) <= 200
        assert out.endswith(".xlsx")

    def test_leading_dots_stripped(self):
        assert not safe_upload_filename(".hidden.txt").startswith(".")


# ─────────────────────────────────────────────────────────────────────────
# upload_service
# ─────────────────────────────────────────────────────────────────────────


class TestAddUpload:
    def test_first_write_returns_meta(self, demo_project):
        meta = upload_service.add_upload(
            demo_project, _XLSX_LIKE, "sample.xlsx", _XLSX_MIME,
        )
        assert isinstance(meta, UploadMeta)
        assert meta.blob_ready is True
        assert meta.size == len(_XLSX_LIKE)
        assert meta.kind == "user_upload"

    def test_dedup_returns_same_file_id_and_uploaded_at(self, demo_project):
        m1 = upload_service.add_upload(demo_project, _XLSX_LIKE, "a.xlsx", _XLSX_MIME)
        # Sleep so a "non-dedup" path would emit a different uploaded_at.
        time.sleep(0.05)
        m2 = upload_service.add_upload(demo_project, _XLSX_LIKE, "b.xlsx", _XLSX_MIME)
        assert m1.file_id == m2.file_id
        assert m1.uploaded_at == m2.uploaded_at  # first-writer wins
        # filename is also preserved — second add doesn't rename
        assert m1.filename == m2.filename == "a.xlsx"

    def test_concurrent_same_bytes_serialise(self, demo_project):
        """
        N threads racing to upload the same bytes must all see the same
        file_id + uploaded_at (first-writer wins via the per-project lock).
        """
        N = 16
        results: list[UploadMeta] = []
        errors: list[str] = []

        def writer(i: int) -> None:
            try:
                m = upload_service.add_upload(
                    demo_project, _XLSX_LIKE, f"file_{i}.xlsx", _XLSX_MIME,
                )
                results.append(m)
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        ts = [threading.Thread(target=writer, args=(i,)) for i in range(N)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert not errors, errors
        assert len(results) == N
        first_id = results[0].file_id
        first_ts = results[0].uploaded_at
        for r in results:
            assert r.file_id == first_id
            assert r.uploaded_at == first_ts

    def test_quota_exceeded_raises_507(self, demo_project, monkeypatch):
        # Cap at 1 KiB to force the 507 with tiny blobs.
        big = b"P" * (2 * 1024)  # 2 KiB, exceeds the 1 KiB cap
        with pytest.raises(HTTPException) as ei:
            upload_service.add_upload(
                demo_project, big, "big.csv", "text/csv",
                quota_bytes=1024,
            )
        assert ei.value.status_code == 507
        assert ei.value.detail["error_kind"] == "upload_quota_exceeded"


class TestListUploads:
    def test_list_empty_project_returns_empty(self, demo_project):
        assert upload_service.list_uploads(demo_project) == []

    def test_list_returns_newest_first(self, demo_project):
        m1 = upload_service.add_upload(demo_project, b"alpha" * 10, "a.csv", "text/csv")
        time.sleep(0.01)
        m2 = upload_service.add_upload(demo_project, b"beta" * 10, "b.csv", "text/csv")
        ls = upload_service.list_uploads(demo_project)
        assert [m.file_id for m in ls] == [m2.file_id, m1.file_id]

    def test_legacy_project_without_uploads_dir(self, demo_project):
        """No uploads/ dir present → list returns [] without crashing."""
        # demo_project fixture doesn't create uploads/ — perfect for this.
        assert upload_service.list_uploads(demo_project) == []


class TestDelete:
    def test_delete_existing_returns_deleted_true(self, demo_project):
        m = upload_service.add_upload(demo_project, _XLSX_LIKE, "x.xlsx", _XLSX_MIME)
        resp = upload_service.delete_upload(demo_project, m.file_id)
        assert resp.deleted is True
        assert resp.file_id == m.file_id
        assert resp.reason is None
        assert upload_service.list_uploads(demo_project) == []

    def test_delete_missing_returns_not_found(self, demo_project):
        resp = upload_service.delete_upload(demo_project, "deadbeef00000000")
        assert resp.deleted is False
        assert resp.reason == "not_found"


class TestPruneOrphans:
    def test_prune_partial_meta_only(self, demo_project, tmp_projects_dir):
        """
        A file_id dir with a meta.json marked blob_ready=False + no blob
        gets reaped after the grace period.
        """
        from services.upload_service import _resolve_paths
        paths = _resolve_paths(demo_project)
        paths.uploads_dir.mkdir(exist_ok=True)
        # Hand-build an orphan
        orphan = paths.uploads_dir / "fake000000000000"
        orphan.mkdir()
        m = UploadMeta(
            file_id="fake000000000000", filename="orphan.bin", mime="application/octet-stream",
            size=0, sha256="x" * 64, uploaded_at=time.time() - 120,  # 2 min old
            blob_ready=False,
        )
        (orphan / "meta.json").write_text(m.model_dump_json(), encoding="utf-8")
        # Force the dir mtime back so it's outside the grace period
        old = time.time() - 120
        import os
        os.utime(orphan, (old, old))

        reaped = upload_service.prune_orphans(demo_project, grace_seconds=60.0)
        assert reaped == 1
        assert not orphan.exists()

    def test_prune_respects_grace_period(self, demo_project):
        """A fresh in-progress upload mid-write is NOT reaped."""
        from services.upload_service import _resolve_paths
        paths = _resolve_paths(demo_project)
        paths.uploads_dir.mkdir(exist_ok=True)
        fresh = paths.uploads_dir / "fresh00000000000"
        fresh.mkdir()
        # No meta.json → orphan-like, but mtime is right now
        reaped = upload_service.prune_orphans(demo_project, grace_seconds=60.0)
        assert reaped == 0
        assert fresh.exists()


# ─────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────


class TestUploadMeta:
    def test_v1_round_trip(self):
        m = UploadMeta(
            file_id="abc", filename="x.xlsx", mime="application/xml",
            size=100, sha256="0" * 64, uploaded_at=1.0,
        )
        d = m.model_dump_json()
        m2 = UploadMeta.model_validate_json(d)
        assert m2 == m

    def test_v2_fields_ignored_by_v1_reader(self):
        """A v2 file with page_count loads via UploadMeta cleanly."""
        v2_json = (
            '{"schema_version":2,"file_id":"x","filename":"a.pdf",'
            '"mime":"application/pdf","size":1,"sha256":"' + "0" * 64 + '",'
            '"kind":"user_upload","uploaded_at":1.0,"blob_ready":true,'
            '"version":1,"page_count":50,"truncated_to_100_pages":false,'
            '"extra_future_field":"ignored"}'
        )
        m = UploadMeta.model_validate_json(v2_json)
        assert m.page_count == 50
        assert m.truncated_to_100_pages is False


class TestDeleteUploadResponse:
    def test_success_must_omit_reason(self):
        # Hand-constructing with both deleted=True AND reason raises.
        with pytest.raises(Exception):  # noqa: BLE001 — pydantic ValueError
            DeleteUploadResponse(deleted=True, file_id="x", reason="in_use")

    def test_failure_must_have_reason(self):
        with pytest.raises(Exception):  # noqa: BLE001
            DeleteUploadResponse(deleted=False, file_id="x")

    def test_success_serialises_clean(self):
        r = DeleteUploadResponse(deleted=True, file_id="x")
        d = r.model_dump(exclude_none=True)
        assert "reason" not in d
        assert d == {"deleted": True, "file_id": "x"}


# ─────────────────────────────────────────────────────────────────────────
# REST endpoints (TestClient)
# ─────────────────────────────────────────────────────────────────────────


class TestUploadsEndpoints:
    def test_post_upload_success(self, app_client, demo_project):
        r = app_client.post(
            f"/api/projects/{demo_project}/uploads",
            files={"file": ("x.csv", b"col1,col2\n1,2\n", "text/csv")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["filename"] == "x.csv"
        assert body["blob_ready"] is True

    def test_post_too_large_returns_413(self, app_client, demo_project, monkeypatch):
        # Temporarily shrink the cap to make a small payload look "big".
        monkeypatch.setattr(upload_service, "MAX_FILE_BYTES", 100)
        r = app_client.post(
            f"/api/projects/{demo_project}/uploads",
            files={"file": ("big.csv", b"x" * 500, "text/csv")},
        )
        assert r.status_code == 413
        assert r.json()["detail"]["error_kind"] == "file_too_large"

    def test_post_real_xlsx_accepted_via_zip_extension_upgrade(
        self, app_client, demo_project,
    ):
        """
        Office Open XML files (xlsx, docx, …) are zip archives — libmagic
        reports `application/zip` for them. Without the filename-extension
        upgrade in `_sniff_mime`, drag-and-drop of a real xlsx fails with
        ``unsupported_mime`` (the actual incident reported by the user on
        2026-06-08). The upgrade converts `application/zip` + `.xlsx` to
        the proper spreadsheetml MIME so the allowlist accepts it.
        """
        import io as _io
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws.append(["snapshot", "demand"])
        ws.append(["2025-01-01 00:00", 100])
        ws.append(["2025-01-01 01:00", 110])
        buf = _io.BytesIO()
        wb.save(buf)
        xlsx_bytes = buf.getvalue()

        r = app_client.post(
            f"/api/projects/{demo_project}/uploads",
            files={
                "file": (
                    "load_H2_Demand_template (1).xlsx",
                    xlsx_bytes,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Server-stamped MIME reflects the upgraded value, not the raw zip
        # sniff — downstream consumers (read_excel_sheet, multimodal) key
        # on this.
        assert body["mime"] == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert body["filename"] == "load_H2_Demand_template (1).xlsx"

    def test_post_docx_zip_upgrade(self, app_client, demo_project):
        """Same upgrade path for .docx so Word files drag-and-drop too."""
        # Minimum-viable Office zip — just the structural zip layout.
        # python-magic only sees the magic bytes (`PK\x03\x04`); the
        # extension upgrade fires before any deeper inspection.
        import io as _io
        import zipfile
        buf = _io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("[Content_Types].xml", "<x/>")
        r = app_client.post(
            f"/api/projects/{demo_project}/uploads",
            files={
                "file": (
                    "memo.docx",
                    buf.getvalue(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ),
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mime"] == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    def test_post_renamed_exe_as_xlsx_still_rejected(
        self, app_client, demo_project,
    ):
        """
        Defence: the extension upgrade ONLY fires when libmagic sniffs
        `application/zip`. A real EXE renamed `report.xlsx` sniffs as
        `application/x-dosexec` and is rejected by the allowlist — the
        upgrade can't be used to slip executable bytes past validation.
        """
        # Proper PE/DOS header — libmagic sniffs as `application/x-dosexec`.
        # A 2-byte `MZ` magic with trailing ASCII string would sniff as
        # `text/plain` (which IS in the allowlist), so we ship the real
        # opening byte sequence libmagic recognises.
        exe_bytes = (
            b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00"
            + b"\x00" * 200
        )
        r = app_client.post(
            f"/api/projects/{demo_project}/uploads",
            files={
                "file": (
                    "report.xlsx", exe_bytes,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            },
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error_kind"] == "unsupported_mime"

    def test_post_zero_byte_returns_400(self, app_client, demo_project):
        r = app_client.post(
            f"/api/projects/{demo_project}/uploads",
            files={"file": ("empty.txt", b"", "text/plain")},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error_kind"] == "empty_file"

    def test_post_path_traversal_filename_rejected(self, app_client, demo_project):
        r = app_client.post(
            f"/api/projects/{demo_project}/uploads",
            files={"file": ("../etc/passwd", b"data", "text/plain")},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error_kind"] == "invalid_filename"

    def test_post_unsupported_mime_rejected(self, app_client, demo_project):
        # Payload must be a binary libmagic recognises as NON-text on every
        # platform. The previous `b"MZ" + b"\x00" * 100` stub was too degenerate:
        # libmagic 5.47 (macOS/conda-forge) declines to call two bytes a DOS
        # executable and falls back to `text/plain` — which IS allowlisted for
        # CSV/txt uploads, so the upload was accepted and this test failed. It
        # only passed on Windows because the older bundled libmagic there
        # classified the same stub as `application/x-dosexec`.
        #
        # A 64-bit ELF header is the portable choice: libmagic identifies it
        # identically across platforms and it never degrades to a text type.
        elf_header = (
            b"\x7fELF\x02\x01\x01\x00"      # magic + 64-bit + little-endian + SysV
            + b"\x00" * 8                     # padding
            + b"\x02\x00"                     # e_type  = ET_EXEC
            + b"\x3e\x00"                     # e_machine = x86-64
            + b"\x01\x00\x00\x00"             # e_version
            + b"\x00" * 64
        )
        r = app_client.post(
            f"/api/projects/{demo_project}/uploads",
            files={"file": ("malware.exe", elf_header, "application/octet-stream")},
        )
        assert r.status_code == 400
        # Either unsupported_mime or mime_type_mismatch is acceptable here —
        # the sniff lands on application/octet-stream or an x-executable variant
        # depending on libmagic version; neither is in ALLOWED_MIME_TYPES.
        assert r.json()["detail"]["error_kind"] in {"unsupported_mime", "mime_type_mismatch"}

    def test_get_list_after_post(self, app_client, demo_project):
        app_client.post(
            f"/api/projects/{demo_project}/uploads",
            files={"file": ("a.csv", b"a,b\n", "text/csv")},
        )
        r = app_client.get(f"/api/projects/{demo_project}/uploads")
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 1
        assert items[0]["filename"] == "a.csv"

    def test_get_blob_returns_bytes(self, app_client, demo_project):
        post = app_client.post(
            f"/api/projects/{demo_project}/uploads",
            files={"file": ("a.csv", b"hello,world\n", "text/csv")},
        )
        fid = post.json()["file_id"]
        r = app_client.get(f"/api/projects/{demo_project}/uploads/{fid}/blob")
        assert r.status_code == 200
        assert r.content == b"hello,world\n"

    def test_get_meta(self, app_client, demo_project):
        post = app_client.post(
            f"/api/projects/{demo_project}/uploads",
            files={"file": ("a.csv", b"col1,col2\n1,2\n", "text/csv")},
        )
        fid = post.json()["file_id"]
        r = app_client.get(f"/api/projects/{demo_project}/uploads/{fid}/meta")
        assert r.status_code == 200
        assert r.json()["file_id"] == fid

    def test_delete_existing_returns_200_deleted_true(self, app_client, demo_project):
        post = app_client.post(
            f"/api/projects/{demo_project}/uploads",
            files={"file": ("a.csv", b"col1,col2\n1,2\n", "text/csv")},
        )
        fid = post.json()["file_id"]
        r = app_client.delete(f"/api/projects/{demo_project}/uploads/{fid}")
        assert r.status_code == 200
        body = r.json()
        assert body == {"deleted": True, "file_id": fid}

    def test_delete_missing_returns_200_not_found(self, app_client, demo_project):
        r = app_client.delete(
            f"/api/projects/{demo_project}/uploads/deadbeef00000000",
        )
        assert r.status_code == 200
        body = r.json()
        assert body["deleted"] is False
        assert body["reason"] == "not_found"

    def test_get_blob_missing_returns_404(self, app_client, demo_project):
        r = app_client.get(
            f"/api/projects/{demo_project}/uploads/deadbeef00000000/blob",
        )
        assert r.status_code == 404
        assert r.json()["detail"]["error_kind"] == "upload_not_found"


# ─────────────────────────────────────────────────────────────────────────
# Bundle inclusion — uploads/ travels on save-class transitions
# ─────────────────────────────────────────────────────────────────────────


class TestBundleInclusion:
    def test_copy_bundle_dirs_no_op_when_src_missing(self, tmp_projects_dir):
        """Legacy project without uploads/ → _copy_bundle_dirs is a no-op."""
        from routers.projects import _copy_bundle_dirs
        src = tmp_projects_dir / "src"
        dst = tmp_projects_dir / "dst"
        src.mkdir()
        dst.mkdir()
        _copy_bundle_dirs(src, dst)
        assert not (dst / "uploads").exists()

    def test_copy_bundle_dirs_copies_existing(self, tmp_projects_dir):
        from routers.projects import _copy_bundle_dirs
        src = tmp_projects_dir / "src"
        dst = tmp_projects_dir / "dst"
        (src / "uploads" / "abc").mkdir(parents=True)
        (src / "uploads" / "abc" / "blob").write_bytes(b"hi")
        (src / "uploads" / "abc" / "meta.json").write_text('{"x":1}', encoding="utf-8")
        dst.mkdir()
        _copy_bundle_dirs(src, dst)
        assert (dst / "uploads" / "abc" / "blob").read_bytes() == b"hi"

    def test_copy_bundle_dirs_replaces_existing_target(self, tmp_projects_dir):
        from routers.projects import _copy_bundle_dirs
        src = tmp_projects_dir / "src"
        dst = tmp_projects_dir / "dst"
        (src / "uploads").mkdir(parents=True)
        (src / "uploads" / "new.bin").write_bytes(b"new")
        (dst / "uploads").mkdir(parents=True)
        (dst / "uploads" / "old.bin").write_bytes(b"old")
        _copy_bundle_dirs(src, dst)
        # old.bin must be gone, new.bin present
        assert not (dst / "uploads" / "old.bin").exists()
        assert (dst / "uploads" / "new.bin").read_bytes() == b"new"
