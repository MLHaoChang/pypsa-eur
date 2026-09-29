"""
Opportunistic PDF export (WP13 of
docs/superpowers/plans/2026-09-28-llm-report-generation-increment-1.md).

The report is a Word document by design; a PDF is a convenience the host can
offer WHEN LibreOffice is installed, and nothing more. ``pdf_available()``
answers the capability route (``shutil.which("soffice")`` or
``"libreoffice"``); ``convert_docx_to_pdf`` runs

    soffice --headless --convert-to pdf --outdir <tmp> <tmp>/report.docx

in a private temporary directory and returns the bytes of the ``.pdf`` it
wrote. Every failure is a ``PdfConversionError`` carrying the head of stderr
(the route answers 500 ``pdf_conversion_failed`` with it): a non-zero exit, a
zero exit that wrote no file, a timeout, or a binary that vanished between
the capability check and the call. No profile directory is shared with a
user's LibreOffice: ``-env:UserInstallation`` points into the same temporary
directory, so two conversions cannot fight over one lock file and a stale
profile cannot break the build's export.

``subprocess.run`` and ``shutil.which`` are looked up on this module at call
time so a test can replace them: the container this ships from has a
``soffice`` that cannot load any ``.docx``, so the success path is pinned
with a fake that writes the file the real binary would.
"""
from __future__ import annotations

import logging
import pathlib
import shutil
import subprocess
import tempfile
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["PdfConversionError", "convert_docx_to_pdf", "pdf_available", "soffice_binary"]

PDF_MIME = "application/pdf"
DEFAULT_TIMEOUT_S = 120
_STDERR_HEAD = 400
_BINARIES = ("soffice", "libreoffice")


class PdfConversionError(RuntimeError):
    """LibreOffice did not produce a PDF (→ 500 `pdf_conversion_failed`)."""


def soffice_binary() -> str | None:
    """The LibreOffice launcher on PATH (`soffice` first, then `libreoffice`), or None."""
    for name in _BINARIES:
        found = shutil.which(name)
        if found:
            return found
    return None


def pdf_available() -> bool:
    """`GET …/reports/capabilities` → `{"pdf": …}`."""
    return soffice_binary() is not None


def _head(text: str | bytes | None) -> str:
    if not text:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    text = text.strip()
    return text[:_STDERR_HEAD] + ("…" if len(text) > _STDERR_HEAD else "")


def _reason(proc: Any) -> str:
    """
    The head of what LibreOffice said, stderr first — unless stdout carries
    the line with "Error" in it (soffice prints "Error: source file could
    not be loaded" on STDOUT and a javaldx warning on stderr), in which case
    that line leads so the 500 names the real reason.
    """
    stderr = _head(getattr(proc, "stderr", None))
    stdout = _head(getattr(proc, "stdout", None))
    if "error" in stdout.lower() and "error" not in stderr.lower():
        return stdout if not stderr else f"{stdout} | {stderr}"[:_STDERR_HEAD + 1]
    return stderr or stdout


def convert_docx_to_pdf(docx_bytes: bytes, *, timeout: float = DEFAULT_TIMEOUT_S) -> bytes:
    """
    `docx_bytes` rendered to PDF by LibreOffice, headless. Raises
    `PdfConversionError` (with the head of stderr) on any failure.
    """
    binary = soffice_binary()
    if binary is None:
        raise PdfConversionError("LibreOffice (soffice/libreoffice) is not on PATH")
    with tempfile.TemporaryDirectory(prefix="report-pdf-") as tmp:
        tmp_dir = pathlib.Path(tmp)
        source = tmp_dir / "report.docx"
        source.write_bytes(bytes(docx_bytes))
        profile = (tmp_dir / "profile").as_uri()
        args = [binary, "--headless", "--convert-to", "pdf", "--outdir", str(tmp_dir),
                f"-env:UserInstallation={profile}", str(source)]
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise PdfConversionError(
                f"LibreOffice timed out after {timeout:.0f}s converting the document"
            ) from exc
        except OSError as exc:
            raise PdfConversionError(f"LibreOffice could not be started: {exc}") from exc
        reason = _reason(proc)
        if proc.returncode != 0:
            raise PdfConversionError(
                f"LibreOffice exit {proc.returncode}" + (f": {reason}" if reason else ""))
        target = tmp_dir / "report.pdf"
        if not target.is_file():
            raise PdfConversionError(
                "LibreOffice exited 0 but wrote no PDF" + (f": {reason}" if reason else ""))
        data = target.read_bytes()
    if not data:
        raise PdfConversionError("LibreOffice wrote an empty PDF")
    logger.info("reports: pdf conversion produced %d bytes", len(data))
    return data
