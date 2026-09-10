"""
Phase C — multimodal pass-through tests.

Covers:
  * PDF page count probed on upload + schema_version=2 + truncated flag
  * build_multimodal_content_blocks: ordering, caps, MIME rejection
  * run_turn forwards multimodal blocks BEFORE the text block
  * reconstruct_network_from_image vision tool (mocked SDK)
  * Signature endpoint shape + 5-min TTL
"""
from __future__ import annotations

import base64
import io
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from openpyxl import Workbook

import main
from services import chat_tools, chat_service, upload_service


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def app_client(tmp_projects_dir) -> TestClient:
    return TestClient(main.app)


@pytest.fixture
def demo_project(tmp_projects_dir):
    proj = tmp_projects_dir / "demo"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "network.nc").write_bytes(b"fake nc")
    (proj / "metadata.json").write_text('{"name":"demo"}', encoding="utf-8")
    return "demo"


# Tiny valid PNG (1x1 transparent) — exact bytes copied from the Wikipedia
# minimal-PNG example so the magic check + decoder both succeed.
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01"
    b"\xf3\xff\xa9\xed\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _make_pdf(n_pages: int) -> bytes:
    """Create an in-memory PDF with `n_pages` blank pages."""
    from pypdf import PdfWriter
    writer = PdfWriter()
    for _ in range(n_pages):
        writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["a", "b"])
    ws.append([1, 2])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────
# PDF page-count + truncation metadata on upload
# ─────────────────────────────────────────────────────────────────────────


class TestPdfPageCount:
    def test_small_pdf_records_page_count(self, demo_project):
        pdf = _make_pdf(3)
        meta = upload_service.add_upload(
            demo_project, pdf, "small.pdf", "application/pdf",
        )
        assert meta.page_count == 3
        assert meta.truncated_to_100_pages is False
        # schema_version bumps to 2 only when these fields are set
        assert meta.schema_version == 2

    def test_large_pdf_flags_truncated(self, demo_project):
        pdf = _make_pdf(120)  # > PDF_PAGE_CAP=100
        meta = upload_service.add_upload(
            demo_project, pdf, "big.pdf", "application/pdf",
        )
        assert meta.page_count == 120
        assert meta.truncated_to_100_pages is True

    def test_non_pdf_skips_probe(self, demo_project):
        meta = upload_service.add_upload(
            demo_project, _TINY_PNG, "x.png", "image/png",
        )
        assert meta.page_count is None
        assert meta.truncated_to_100_pages is None
        assert meta.schema_version == 1  # not bumped for non-PDFs


class TestPdfTruncation:
    def test_truncated_bytes_smaller_than_original(self, demo_project):
        pdf = _make_pdf(150)
        meta = upload_service.add_upload(
            demo_project, pdf, "big.pdf", "application/pdf",
        )
        trunc, was_truncated = upload_service.get_upload_bytes_truncated_pdf(
            demo_project, meta.file_id,
        )
        assert was_truncated is True
        assert len(trunc) < len(pdf)
        # Verify the truncated PDF really has 100 pages
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(trunc), strict=False)
        assert len(reader.pages) == 100

    def test_small_pdf_returned_unchanged(self, demo_project):
        pdf = _make_pdf(5)
        meta = upload_service.add_upload(
            demo_project, pdf, "small.pdf", "application/pdf",
        )
        out, was_truncated = upload_service.get_upload_bytes_truncated_pdf(
            demo_project, meta.file_id,
        )
        assert was_truncated is False
        assert out == pdf


# ─────────────────────────────────────────────────────────────────────────
# build_multimodal_content_blocks
# ─────────────────────────────────────────────────────────────────────────


class TestBuildMultimodalContentBlocks:
    def test_empty_returns_empty(self, demo_project):
        assert upload_service.build_multimodal_content_blocks(demo_project, []) == []

    def test_single_image_block(self, demo_project):
        meta = upload_service.add_upload(
            demo_project, _TINY_PNG, "x.png", "image/png",
        )
        blocks = upload_service.build_multimodal_content_blocks(
            demo_project, [meta.file_id],
        )
        assert len(blocks) == 1
        assert blocks[0]["type"] == "image"
        assert blocks[0]["source"]["media_type"] == "image/png"
        # base64 round-trip
        decoded = base64.b64decode(blocks[0]["source"]["data"])
        assert decoded == _TINY_PNG

    def test_single_pdf_block(self, demo_project):
        pdf = _make_pdf(2)
        meta = upload_service.add_upload(
            demo_project, pdf, "x.pdf", "application/pdf",
        )
        blocks = upload_service.build_multimodal_content_blocks(
            demo_project, [meta.file_id],
        )
        assert len(blocks) == 1
        assert blocks[0]["type"] == "document"
        assert blocks[0]["source"]["media_type"] == "application/pdf"

    def test_order_preserved_pdf_then_image(self, demo_project):
        m1 = upload_service.add_upload(
            demo_project, _make_pdf(1), "a.pdf", "application/pdf",
        )
        m2 = upload_service.add_upload(
            demo_project, _TINY_PNG, "b.png", "image/png",
        )
        blocks = upload_service.build_multimodal_content_blocks(
            demo_project, [m1.file_id, m2.file_id],
        )
        assert blocks[0]["type"] == "document"
        assert blocks[1]["type"] == "image"

    def test_xlsx_raises_415(self, demo_project):
        meta = upload_service.add_upload(
            demo_project, _make_xlsx(), "x.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        with pytest.raises(HTTPException) as ei:
            upload_service.build_multimodal_content_blocks(
                demo_project, [meta.file_id],
            )
        assert ei.value.status_code == 415
        assert ei.value.detail["error_kind"] == "mime_not_allowlisted_for_multimodal"

    def test_too_many_blocks_raises_400(self, demo_project):
        ids = [f"deadbeef{i:08x}" for i in range(25)]
        with pytest.raises(HTTPException) as ei:
            upload_service.build_multimodal_content_blocks(demo_project, ids)
        assert ei.value.status_code == 400
        assert ei.value.detail["error_kind"] == "too_many_multimodal_blocks"

    def test_missing_file_id_raises_404(self, demo_project):
        with pytest.raises(HTTPException) as ei:
            upload_service.build_multimodal_content_blocks(
                demo_project, ["deadbeef00000000"],
            )
        assert ei.value.status_code == 404
        assert ei.value.detail["error_kind"] == "upload_not_found"

    def test_image_too_large_raises_413(self, demo_project, monkeypatch):
        # Cap to 10 bytes so the tiny PNG (~67 bytes) overflows
        monkeypatch.setattr(upload_service, "MAX_MULTIMODAL_IMAGE_BYTES", 10)
        meta = upload_service.add_upload(
            demo_project, _TINY_PNG, "x.png", "image/png",
        )
        with pytest.raises(HTTPException) as ei:
            upload_service.build_multimodal_content_blocks(
                demo_project, [meta.file_id],
            )
        assert ei.value.status_code == 413
        assert ei.value.detail["error_kind"] == "image_too_large"

    def test_pdf_truncated_in_block(self, demo_project):
        """A 120-page PDF passed to the multimodal builder ships only 100 pages."""
        pdf = _make_pdf(120)
        meta = upload_service.add_upload(
            demo_project, pdf, "big.pdf", "application/pdf",
        )
        blocks = upload_service.build_multimodal_content_blocks(
            demo_project, [meta.file_id],
        )
        decoded = base64.b64decode(blocks[0]["source"]["data"])
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(decoded), strict=False)
        assert len(reader.pages) == 100


# ─────────────────────────────────────────────────────────────────────────
# Signature endpoint
# ─────────────────────────────────────────────────────────────────────────


# Step 0a: the upload/signature ROUTES resolve `{name}` through the project
# registry inside the caller's org, so they need an authenticated client and a
# real project row — a bare directory under the projects root is no longer a
# project. The service-level tests below keep using `demo_project` (a flat dir),
# which still works: `upload_service._resolve_paths` falls back to the flat
# layout for callers that are not routes. Step 1 retires that fallback.
@pytest.fixture
def route_project(api_project):
    return api_project("demo")


class TestSignatureEndpoint:
    def test_signature_shape(self, client, route_project):
        post = client.post(
            f"/api/projects/{route_project}/uploads",
            files={"file": ("x.png", _TINY_PNG, "image/png")},
        )
        fid = post.json()["file_id"]
        r = client.get(
            f"/api/projects/{route_project}/uploads/{fid}/signature",
            params={"session_id": "sess123"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["file_id"] == fid
        assert isinstance(body["signature"], str)
        assert len(body["signature"]) == 64  # sha256 hex
        assert body["expires_at"] > int(time.time())
        # 5-min TTL within tolerance
        assert body["expires_at"] - int(time.time()) <= 300
        assert body["expires_at"] - int(time.time()) > 290
        assert body["version"] >= 1
        assert len(body["sha256"]) == 64

    def test_missing_file_returns_404(self, client, route_project):
        r = client.get(
            f"/api/projects/{route_project}/uploads/deadbeef00000000/signature",
            params={"session_id": "sess"},
        )
        assert r.status_code == 404
        assert r.json()["detail"]["error_kind"] == "upload_not_found"

    def test_signatures_are_session_scoped(self, client, route_project):
        """Same file + different session_ids → different signatures."""
        post = client.post(
            f"/api/projects/{route_project}/uploads",
            files={"file": ("x.png", _TINY_PNG, "image/png")},
        )
        fid = post.json()["file_id"]
        s1 = client.get(
            f"/api/projects/{route_project}/uploads/{fid}/signature",
            params={"session_id": "alice"},
        ).json()["signature"]
        s2 = client.get(
            f"/api/projects/{route_project}/uploads/{fid}/signature",
            params={"session_id": "bob"},
        ).json()["signature"]
        assert s1 != s2


# ─────────────────────────────────────────────────────────────────────────
# run_turn wire-in (mocked Anthropic client)
# ─────────────────────────────────────────────────────────────────────────


class _FakeStreamEvent:
    def __init__(self, etype, **fields):
        self.type = etype
        for k, v in fields.items():
            setattr(self, k, v)


class _FakeBlock:
    def __init__(self, btype, **fields):
        self.type = btype
        for k, v in fields.items():
            setattr(self, k, v)


class _FakeFinalMessage:
    def __init__(self, content, usage):
        self.content = content
        self.usage = usage


class _FakeUsage:
    def __init__(self, input_tokens=10, output_tokens=20,
                 cache_read_input_tokens=0, cache_creation_input_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read_input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens


class _RecordingStream:
    """Captures messages.stream(**kwargs) so tests can assert the content array."""

    def __init__(self, captured):
        self._captured = captured

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        # Yield a single text event then a stop
        yield _FakeStreamEvent("content_block_delta",
                                delta=_FakeBlock("text_delta", text="ack"))
        yield _FakeStreamEvent("message_delta",
                                delta=_FakeBlock("delta", stop_reason="end_turn"))

    def get_final_message(self):
        return _FakeFinalMessage(
            content=[_FakeBlock("text", text="ack")],
            usage=_FakeUsage(),
        )


class _RecordingMessages:
    def __init__(self):
        self.captured: list[dict] = []

    def stream(self, **kwargs):
        self.captured.append(kwargs)
        return _RecordingStream(self.captured)


class _RecordingClient:
    def __init__(self):
        self.messages = _RecordingMessages()


class TestRunTurnMultimodal:
    def test_image_attachment_prepended_to_user_content(
        self, tmp_projects_dir, install_network,
    ):
        """
        When attachment_file_ids is set, the SDK receives a multimodal
        content list with the image block BEFORE the user's text block.
        """
        from tests.conftest import build_network
        n = build_network()
        install_network(n, name="P")
        (tmp_projects_dir / "P").mkdir(parents=True, exist_ok=True)
        (tmp_projects_dir / "P" / "network.nc").write_bytes(b"")

        meta = upload_service.add_upload("P", _TINY_PNG, "diagram.png", "image/png")

        client = _RecordingClient()
        session = chat_service.ChatSession()
        events = list(chat_service.run_turn(
            session, "what do you see?",
            client=client,
            attachment_file_ids=[meta.file_id],
        ))
        # We should have at least one stream call captured
        assert client.messages.captured, "messages.stream was never called"
        msgs = client.messages.captured[0]["messages"]
        # Find the user message
        user_msg = next(m for m in msgs if m["role"] == "user")
        content = user_msg["content"]
        assert isinstance(content, list)
        # Image block FIRST, text block LAST
        assert content[0]["type"] == "image"
        assert content[-1]["type"] == "text"
        assert content[-1]["text"] == "what do you see?"

    def test_no_attachments_uses_string_content(
        self, tmp_projects_dir, install_network,
    ):
        """Without attachments, the user message is a plain string (Phase 3 shape)."""
        from tests.conftest import build_network
        install_network(build_network(), name="P")

        client = _RecordingClient()
        session = chat_service.ChatSession()
        list(chat_service.run_turn(session, "hello", client=client))
        msgs = client.messages.captured[0]["messages"]
        user_msg = next(m for m in msgs if m["role"] == "user")
        assert user_msg["content"] == "hello"

    def test_xlsx_attachment_becomes_tool_accessible_prefix(
        self, tmp_projects_dir, install_network,
    ):
        """
        Xlsx files attached via the chip strip used to 415 because the
        multimodal API doesn't accept Office formats. The fix splits
        attachments into multimodal-eligible (image/pdf) and tool-
        accessible (xlsx/docx/csv): xlsx files now annotate the user text
        block so the agent knows to call `read_excel_sheet` instead of
        being silently dropped. The SDK DOES get called now (with a
        text-only content block carrying the attachment hint).
        """
        from tests.conftest import build_network
        install_network(build_network(), name="P")
        (tmp_projects_dir / "P").mkdir(parents=True, exist_ok=True)
        (tmp_projects_dir / "P" / "network.nc").write_bytes(b"")

        meta = upload_service.add_upload(
            "P", _make_xlsx(), "demand.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        client = _RecordingClient()
        session = chat_service.ChatSession()
        events = list(chat_service.run_turn(
            session, "use this for the H2 demand on bus 5",
            client=client,
            attachment_file_ids=[meta.file_id],
        ))
        # No error frame — the xlsx is gracefully promoted to a text prefix.
        kinds = [p.get("error_kind") for ev, p in events if ev == "error"]
        assert "mime_not_allowlisted_for_multimodal" not in kinds
        # SDK WAS called with the user message. The captured "messages"
        # list IS the same list run_turn appends assistant_blocks to
        # after the stream call, so we look up the user role explicitly
        # rather than indexing by position.
        assert client.messages.captured
        user_msg = next(
            m for m in client.messages.captured[0]["messages"] if m["role"] == "user"
        )
        content = user_msg["content"]
        # text-only (no image / document block) since xlsx isn't multimodal
        assert isinstance(content, list)
        assert all(b.get("type") == "text" for b in content)
        text = content[-1]["text"]
        # The agent sees the filename + file_id + a tool hint
        assert "demand.xlsx" in text
        assert meta.file_id in text
        assert "read_excel_sheet" in text or "apply_demand_from_excel" in text
        # AND the original user message survives intact below the prefix
        assert "use this for the H2 demand on bus 5" in text

    def test_mixed_image_and_xlsx_attachment(
        self, tmp_projects_dir, install_network,
    ):
        """
        Image + xlsx attached together: the image goes through the
        multimodal block (Anthropic vision), and the xlsx annotates the
        text-block prefix. Both surfaces work without conflict.
        """
        from tests.conftest import build_network
        install_network(build_network(), name="P")
        (tmp_projects_dir / "P").mkdir(parents=True, exist_ok=True)
        (tmp_projects_dir / "P" / "network.nc").write_bytes(b"")

        img_meta = upload_service.add_upload(
            "P", _TINY_PNG, "topology.png", "image/png",
        )
        xlsx_meta = upload_service.add_upload(
            "P", _make_xlsx(), "demand.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        client = _RecordingClient()
        session = chat_service.ChatSession()
        list(chat_service.run_turn(
            session, "build a network from this diagram and use the xlsx for demand",
            client=client,
            attachment_file_ids=[img_meta.file_id, xlsx_meta.file_id],
        ))
        assert client.messages.captured
        user_msg = next(
            m for m in client.messages.captured[0]["messages"] if m["role"] == "user"
        )
        content = user_msg["content"]
        # Image block FIRST, text block LAST
        assert content[0]["type"] == "image"
        assert content[-1]["type"] == "text"
        # Text carries the xlsx hint but NOT the image (image lives in its
        # own block; mentioning it again would be redundant)
        assert "demand.xlsx" in content[-1]["text"]
        assert "topology.png" not in content[-1]["text"]


# ─────────────────────────────────────────────────────────────────────────
# reconstruct_network_from_image (vision sub-call mocked)
# ─────────────────────────────────────────────────────────────────────────


class _VisionStream:
    """Returns a structured JSON response from the fake vision call."""

    def __init__(self, json_payload: str):
        self._json = json_payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        # Empty event iterator — the test only asserts on get_final_message
        return iter([])

    def get_final_message(self):
        return _FakeFinalMessage(
            content=[_FakeBlock("text", text=self._json)],
            usage=_FakeUsage(),
        )


class _VisionMessages:
    def __init__(self, json_payload):
        self._json = json_payload

    def stream(self, **kwargs):
        return _VisionStream(self._json)


class _VisionClient:
    def __init__(self, json_payload):
        self.messages = _VisionMessages(json_payload)


class TestReconstructNetworkFromImage:
    def test_happy_path_creates_buses_and_lines(
        self, tmp_projects_dir, install_network,
    ):
        from tests.conftest import build_network
        n = build_network()
        install_network(n, name="P")
        (tmp_projects_dir / "P").mkdir(parents=True, exist_ok=True)
        (tmp_projects_dir / "P" / "network.nc").write_bytes(b"")

        meta = upload_service.add_upload("P", _TINY_PNG, "topology.png", "image/png")

        payload = (
            '{"buses": ['
            '{"name": "B_NEW1", "px": 100, "py": 80, "v_nom": 380},'
            '{"name": "B_NEW2", "px": 200, "py": 80}'
            '], "lines": ['
            '{"name": "L_NEW1", "bus0": "B_NEW1", "bus1": "B_NEW2"}'
            ']}'
        )
        client = _VisionClient(payload)
        result = chat_tools.reconstruct_network_from_image(
            file_id=meta.file_id, client=client,
        )
        assert result["ok"] is True
        assert "B_NEW1" in result["buses_created"]
        assert "B_NEW2" in result["buses_created"]
        assert "L_NEW1" in result["lines_created"]

        # Verify the components actually landed in n
        assert "B_NEW1" in n.buses.index
        assert "B_NEW2" in n.buses.index
        assert "L_NEW1" in n.lines.index

    def test_markdown_fenced_json_still_parses(
        self, tmp_projects_dir, install_network,
    ):
        from tests.conftest import build_network
        n = build_network()
        install_network(n, name="P")
        (tmp_projects_dir / "P").mkdir(parents=True, exist_ok=True)
        (tmp_projects_dir / "P" / "network.nc").write_bytes(b"")

        meta = upload_service.add_upload("P", _TINY_PNG, "x.png", "image/png")
        payload = (
            "```json\n"
            '{"buses": [{"name": "X_FENCE", "px": 50, "py": 50}], "lines": []}\n'
            "```"
        )
        result = chat_tools.reconstruct_network_from_image(
            file_id=meta.file_id, client=_VisionClient(payload),
        )
        assert "X_FENCE" in result["buses_created"]

    def test_invalid_json_returns_502(
        self, tmp_projects_dir, install_network,
    ):
        from tests.conftest import build_network
        install_network(build_network(), name="P")
        (tmp_projects_dir / "P").mkdir(parents=True, exist_ok=True)
        (tmp_projects_dir / "P" / "network.nc").write_bytes(b"")

        meta = upload_service.add_upload("P", _TINY_PNG, "x.png", "image/png")
        with pytest.raises(HTTPException) as ei:
            chat_tools.reconstruct_network_from_image(
                file_id=meta.file_id, client=_VisionClient("not json at all"),
            )
        assert ei.value.status_code == 502
        assert ei.value.detail["error_kind"] == "vision_invalid_json"

    def test_non_image_returns_415(
        self, tmp_projects_dir, install_network,
    ):
        from tests.conftest import build_network
        install_network(build_network(), name="P")
        (tmp_projects_dir / "P").mkdir(parents=True, exist_ok=True)
        (tmp_projects_dir / "P" / "network.nc").write_bytes(b"")

        meta = upload_service.add_upload(
            "P", _make_xlsx(), "x.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        with pytest.raises(HTTPException) as ei:
            chat_tools.reconstruct_network_from_image(
                file_id=meta.file_id, client=_VisionClient("{}"),
            )
        # Either 415 from build_multimodal or 415 from the explicit check
        assert ei.value.status_code == 415

    def test_coordinate_transform_applied(
        self, tmp_projects_dir, install_network,
    ):
        """User-supplied origin + scale → bus x/y matches the formula."""
        from tests.conftest import build_network
        n = build_network()
        install_network(n, name="P")
        (tmp_projects_dir / "P").mkdir(parents=True, exist_ok=True)
        (tmp_projects_dir / "P" / "network.nc").write_bytes(b"")

        meta = upload_service.add_upload("P", _TINY_PNG, "x.png", "image/png")
        payload = '{"buses": [{"name": "B_T1", "px": 100, "py": 50}], "lines": []}'
        chat_tools.reconstruct_network_from_image(
            file_id=meta.file_id,
            origin_x=50.0, origin_y=200.0,
            scale_x=0.5, scale_y=0.25,
            client=_VisionClient(payload),
        )
        # gx = (100 - 50) * 0.5 = 25.0
        # gy = (200 - 50) * 0.25 = 37.5
        assert n.buses.loc["B_T1", "x"] == 25.0
        assert n.buses.loc["B_T1", "y"] == 37.5


# ─────────────────────────────────────────────────────────────────────────
# C-3 — `reconstruct_network_from_image` was entirely profile-blind.
#
# It built an Anthropic client via `_build_anthropic_client()` and hardcoded
# `DEFAULT_MODEL`, whatever profile the turn was actually running on. The
# spec's capability-enforcement requirement was skipped for this tool; only a
# redaction line landed.
#
# Two bad outcomes on a deployment that deliberately does not use Anthropic:
# either it demands an ANTHROPIC_API_KEY the operator chose not to have, or —
# when one happens to be in the environment — it silently bills a
# `claude-sonnet-5` call AND ships the user's image to Anthropic, while the
# user believes their chosen local profile is handling it.
#
# The tool speaks the Anthropic SDK's `messages.stream` directly, so it
# cannot run on the openai wire without porting it to the provider seam
# (blocked behind C-2, which questions whether that wire works at all).
# Until then the honest behaviour is to REFUSE with the same
# `capability_unsupported` shape `run_turn` already uses for vision — never
# to silently substitute Anthropic.
# ─────────────────────────────────────────────────────────────────────────


class _ModelRecordingVisionMessages(_VisionMessages):
    def __init__(self, json_payload):
        super().__init__(json_payload)
        self.model_used = None

    def stream(self, **kwargs):
        self.model_used = kwargs.get("model")
        return _VisionStream(self._json)


class _ModelRecordingVisionClient:
    def __init__(self, json_payload):
        self.messages = _ModelRecordingVisionMessages(json_payload)


_EMPTY_VISION_PAYLOAD = '{"buses": [], "lines": []}'


@pytest.fixture
def _bound_turn_profile(monkeypatch):
    """Bind a turn profile the way `run_turn` does, and clear it after."""
    from services import chat_tools as _ct

    def _bind(**overrides):
        from services import llm_config
        fields = dict(
            id="p", label="My Profile", preset="custom", wire="anthropic",
            base_url=None, model="claude-sonnet-5", tools=True, vision=True,
            auth="none", fallback_model=None, max_output_tokens=None,
        )
        fields.update(overrides)
        profile = llm_config.LLMProfile(**fields)
        _ct.set_turn_profile(profile)
        return profile

    yield _bind
    _ct.set_turn_profile(None)


class TestReconstructRespectsTheTurnProfile:
    def _upload(self, tmp_projects_dir, install_network):
        from tests.conftest import build_network
        n = build_network()
        install_network(n, name="P")
        (tmp_projects_dir / "P").mkdir(parents=True, exist_ok=True)
        (tmp_projects_dir / "P" / "network.nc").write_bytes(b"")
        return upload_service.add_upload(
            "P", _TINY_PNG, "topology.png", "image/png")

    def test_refuses_when_the_turn_profile_is_not_anthropic_wire(
        self, tmp_projects_dir, install_network, _bound_turn_profile,
    ):
        """An Ollama/LM Studio turn must not silently reach Anthropic."""
        meta = self._upload(tmp_projects_dir, install_network)
        _bound_turn_profile(wire="openai", label="Ollama (local)")

        with pytest.raises(HTTPException) as exc:
            chat_tools.reconstruct_network_from_image(file_id=meta.file_id)
        detail = exc.value.detail
        assert detail["error_kind"] == "capability_unsupported", detail
        # Names the LABEL only — never an id or a base_url (redaction is
        # secrets-only and would not scrub either).
        assert "Ollama (local)" in detail["message"]

    def test_refuses_when_the_turn_profile_has_vision_disabled(
        self, tmp_projects_dir, install_network, _bound_turn_profile,
    ):
        meta = self._upload(tmp_projects_dir, install_network)
        _bound_turn_profile(vision=False, label="No Vision")

        with pytest.raises(HTTPException) as exc:
            chat_tools.reconstruct_network_from_image(file_id=meta.file_id)
        assert exc.value.detail["error_kind"] == "capability_unsupported"

    def test_uses_the_turn_profiles_model_not_a_hardcoded_default(
        self, tmp_projects_dir, install_network, _bound_turn_profile,
    ):
        """
        DISCRIMINATION — a vision-capable Anthropic profile still works, and
        the sub-call bills the model the user actually selected.
        """
        meta = self._upload(tmp_projects_dir, install_network)
        _bound_turn_profile(model="claude-opus-5", label="Claude Opus")

        client = _ModelRecordingVisionClient(_EMPTY_VISION_PAYLOAD)
        result = chat_tools.reconstruct_network_from_image(
            file_id=meta.file_id, client=client,
        )
        assert result["ok"] is True
        assert client.messages.model_used == "claude-opus-5", (
            f"vision sub-call used {client.messages.model_used!r}, not the "
            f"turn profile's model"
        )

    def test_no_bound_profile_keeps_the_pre_existing_behaviour(
        self, tmp_projects_dir, install_network,
    ):
        """
        SIBLING PATH — called outside a turn (direct invocation, and every
        pre-existing test in this file), there is no profile to honour and
        the tool must behave exactly as before.
        """
        meta = self._upload(tmp_projects_dir, install_network)
        client = _ModelRecordingVisionClient(_EMPTY_VISION_PAYLOAD)
        result = chat_tools.reconstruct_network_from_image(
            file_id=meta.file_id, client=client,
        )
        assert result["ok"] is True
        assert client.messages.model_used == chat_service.DEFAULT_MODEL

    def test_a_finished_turn_does_not_leave_its_profile_bound(
        self, tmp_projects_dir, install_network, tmp_path, monkeypatch,
    ):
        """
        The turn profile must not outlive the turn. Otherwise a tool called
        outside a turn would silently inherit whatever ran before it — and in
        a shared-process test session that makes the tool's behaviour depend
        on test ORDER.
        """
        monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
        from services import chat_tools as _ct
        from services import llm_config
        from services.llm_fake import FakeProvider
        from services.llm_provider import LLMEvent

        profile = llm_config.LLMProfile(
            id="leaky", label="Leaky", preset="custom", wire="anthropic",
            base_url=None, model="claude-opus-5", tools=False, vision=True,
            auth="none", fallback_model=None, max_output_tokens=None)
        llm_config.save_profiles([profile], "anthropic-sonnet")

        session = chat_service.ChatSession(model="claude-opus-5")
        session.profile_id = "leaky"
        fake = FakeProvider([{
            "events": [LLMEvent(type="text_delta", text="hi")],
            "blocks": [{"type": "text", "text": "hi"}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }])
        list(chat_service.run_turn(session, "hello", provider=fake))

        assert _ct.turn_profile() is None, (
            "the finished turn left its profile bound to this context"
        )

    def test_the_vision_client_uses_the_profiles_OWN_key_slot(
        self, tmp_projects_dir, install_network, _bound_turn_profile,
        monkeypatch,
    ):
        """
        C-3's CREDENTIAL half, which a mutation audit found untested.

        Every other test in this class injects `client=`, so the
        `if client is None` branch — the entire reason
        `_anthropic_client_for_profile` was extracted — never executed. Its
        docstring's claim that the sub-call "resolves its credentials the SAME
        way a turn does, instead of always reaching for the ambient
        ANTHROPIC_API_KEY" was unverified: reverting the call to
        `_build_anthropic_client()` left every test passing.

        A custom anthropic-wire profile owns a private
        `PYPSA_GUI_LLM_KEY__<SLUG>` slot. The sub-call must use THAT, not the
        instance-wide Anthropic key — otherwise a deployment that deliberately
        separates them silently bills and authenticates against the wrong one.
        """
        import sys

        meta = self._upload(tmp_projects_dir, install_network)
        profile = _bound_turn_profile(
            id="vision-slot", preset="custom", auth="bearer",
            label="Private Slot", model="claude-opus-5")
        assert profile.key_env == "PYPSA_GUI_LLM_KEY__VISION_SLOT"

        monkeypatch.setenv("PYPSA_GUI_LLM_KEY__VISION_SLOT", "sk-ant-profile-slot-value")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ambient-instance-wide")

        seen_keys: list = []

        class _FakeAnthropic:
            def __init__(self, api_key=None, **kw):
                seen_keys.append(api_key)
                self.messages = _ModelRecordingVisionMessages(_EMPTY_VISION_PAYLOAD)

        fake_mod = type(sys)("anthropic")
        fake_mod.Anthropic = _FakeAnthropic
        monkeypatch.setitem(sys.modules, "anthropic", fake_mod)

        # The ambient path must not be what answers this.
        built_ambient: list = []
        monkeypatch.setattr(
            chat_service, "_build_anthropic_client",
            lambda: (built_ambient.append(True), (None, "missing_api_key"))[1],
        )

        result = chat_tools.reconstruct_network_from_image(file_id=meta.file_id)

        assert result["ok"] is True
        assert built_ambient == [], (
            "the vision sub-call went through `_build_anthropic_client()` — "
            "it is reaching for the ambient ANTHROPIC_API_KEY instead of the "
            "profile's own key slot"
        )
        assert seen_keys == ["sk-ant-profile-slot-value"], (
            f"client was built with {seen_keys!r}, not the profile's slot value"
        )
