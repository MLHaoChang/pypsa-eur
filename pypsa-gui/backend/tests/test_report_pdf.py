"""
WP13 — `services/reports/pdf.py`: the opportunistic LibreOffice conversion.

`pdf_available()` is `shutil.which("soffice")` or `"libreoffice"`;
`convert_docx_to_pdf(docx_bytes)` runs `soffice --headless --convert-to pdf
--outdir <tmp>` and returns the PDF bytes, or raises `PdfConversionError`
carrying the head of stderr. `subprocess.run` is replaced here: the
container's `soffice` cannot load a `.docx` (a known limitation), so the
success path is pinned with a fake that writes the `.pdf` the real binary
would, and the failure path with a fake that exits non-zero.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services.reports import pdf


def _fake_run(*, returncode: int = 0, stderr: str = "", write: bool = True,
              payload: bytes = b"%PDF-1.4\n%fake\n"):
    calls: list[dict] = []

    def run(args, **kwargs):
        calls.append({"args": list(args), **kwargs})
        outdir = Path(args[args.index("--outdir") + 1])
        source = Path(args[-1])
        assert source.is_file(), "the .docx is written before soffice runs"
        if write:
            (outdir / (source.stem + ".pdf")).write_bytes(payload)
        return subprocess.CompletedProcess(args, returncode, stdout="", stderr=stderr)

    run.calls = calls
    return run


def test_pdf_available_follows_which(monkeypatch):
    monkeypatch.setattr(pdf.shutil, "which", lambda cmd: None)
    assert pdf.pdf_available() is False and pdf.soffice_binary() is None
    monkeypatch.setattr(pdf.shutil, "which",
                        lambda cmd: "/usr/bin/soffice" if cmd == "soffice" else None)
    assert pdf.pdf_available() is True and pdf.soffice_binary() == "/usr/bin/soffice"
    monkeypatch.setattr(pdf.shutil, "which",
                        lambda cmd: "/opt/libreoffice" if cmd == "libreoffice" else None)
    assert pdf.pdf_available() is True and pdf.soffice_binary() == "/opt/libreoffice"


def test_convert_runs_headless_and_returns_the_pdf_bytes(monkeypatch):
    monkeypatch.setattr(pdf.shutil, "which", lambda cmd: "/usr/bin/soffice")
    run = _fake_run(payload=b"%PDF-1.7\nhello\n")
    monkeypatch.setattr(pdf.subprocess, "run", run)
    out = pdf.convert_docx_to_pdf(b"PK\x03\x04 a docx")
    assert out == b"%PDF-1.7\nhello\n"
    call = run.calls[0]
    args = call["args"]
    assert args[0] == "/usr/bin/soffice"
    assert args[1:4] == ["--headless", "--convert-to", "pdf"]
    assert "--outdir" in args and args[-1].endswith(".docx")
    assert call.get("capture_output") is True and call.get("timeout")
    # The temporary directory is gone afterwards.
    assert not Path(args[-1]).exists() and not Path(args[args.index("--outdir") + 1]).exists()


def test_convert_raises_with_the_stderr_head_on_a_non_zero_exit(monkeypatch):
    monkeypatch.setattr(pdf.shutil, "which", lambda cmd: "/usr/bin/soffice")
    noisy = "Error: source file could not be loaded\n" + "x" * 2000
    monkeypatch.setattr(pdf.subprocess, "run", _fake_run(returncode=1, stderr=noisy, write=False))
    with pytest.raises(pdf.PdfConversionError) as exc:
        pdf.convert_docx_to_pdf(b"PK\x03\x04")
    msg = str(exc.value)
    assert "source file could not be loaded" in msg and "exit 1" in msg
    assert len(msg) < 700, "the stderr head, not the whole stream"


def test_convert_raises_when_soffice_exits_zero_but_writes_nothing(monkeypatch):
    monkeypatch.setattr(pdf.shutil, "which", lambda cmd: "/usr/bin/soffice")
    monkeypatch.setattr(pdf.subprocess, "run", _fake_run(returncode=0, write=False))
    with pytest.raises(pdf.PdfConversionError) as exc:
        pdf.convert_docx_to_pdf(b"PK\x03\x04")
    assert "no PDF" in str(exc.value)


def test_the_error_line_on_stdout_leads_the_reason(monkeypatch):
    """LibreOffice prints 'Error: source file could not be loaded' on STDOUT, a javaldx warning on stderr."""
    monkeypatch.setattr(pdf.shutil, "which", lambda cmd: "/usr/bin/soffice")

    def run(args, **kwargs):
        return subprocess.CompletedProcess(
            args, 0, stdout="Error: source file could not be loaded\n",
            stderr="Warning: failed to launch javaldx - java may not function correctly\n")
    monkeypatch.setattr(pdf.subprocess, "run", run)
    with pytest.raises(pdf.PdfConversionError) as exc:
        pdf.convert_docx_to_pdf(b"PK\x03\x04")
    msg = str(exc.value)
    assert msg.index("source file could not be loaded") < msg.index("javaldx")


def test_convert_raises_when_soffice_is_not_on_path_or_times_out(monkeypatch):
    monkeypatch.setattr(pdf.shutil, "which", lambda cmd: None)
    with pytest.raises(pdf.PdfConversionError) as exc:
        pdf.convert_docx_to_pdf(b"PK\x03\x04")
    assert "not on PATH" in str(exc.value)

    monkeypatch.setattr(pdf.shutil, "which", lambda cmd: "/usr/bin/soffice")

    def slow(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs.get("timeout", 0))
    monkeypatch.setattr(pdf.subprocess, "run", slow)
    with pytest.raises(pdf.PdfConversionError) as exc:
        pdf.convert_docx_to_pdf(b"PK\x03\x04", timeout=1)
    assert "timed out" in str(exc.value)


@pytest.mark.skipif(not pdf.pdf_available(), reason="no soffice on PATH")
def test_the_real_binary_answers_pdf_bytes_or_a_conversion_error():
    """
    Environment probe, not a contract: a workstation with a working
    LibreOffice returns a PDF; this container's `soffice` cannot load a
    `.docx` and raises `PdfConversionError`. Either outcome is the module
    doing its job — a crash or a silent empty file would not be.
    """
    from models.report import Paragraph, ReportDocument, Section
    from services.reports.docx_writer import render_document_docx

    doc = ReportDocument(
        report_id="0123456789abcdef", version=1, title="PDF probe",
        created_at="2026-09-29T00:00:00+00:00", evidence_hash="0" * 64,
        mode="evidence_only",
        sections=[Section(section_id="s", heading="S", source="code", status="ok",
                          blocks=[Paragraph(md="One line.")])])
    blob = render_document_docx(doc, figure_bytes={})
    try:
        out = pdf.convert_docx_to_pdf(blob, timeout=180)
    except pdf.PdfConversionError as exc:
        assert str(exc)
    else:
        assert out.startswith(b"%PDF-")
