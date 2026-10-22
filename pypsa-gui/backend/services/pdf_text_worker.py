"""
Read a PDF's text layer, page by page, in a child process (data-to-model slice 2).

`services.document_values` starts `extract_to` in a `spawn` multiprocessing child and
kills it at its wall-clock budget: pypdf has no timeout, and a crafted PDF can make it
run or allocate without end, so the API process never parses one itself. A child, not a
`sys.executable` subprocess: a frozen desktop build is its own `sys.executable`, and its
entry point already hands spawn children to `multiprocessing.freeze_support()`
(`desktop/gui.py`). Light on purpose: this module imports pypdf and the standard
library only, and the child sets its own address-space limit before reading.

Sends one dict down `conn`: `{"pages": [text, ...], "truncated": [page numbers],
"page_count": n}`, `{"encrypted": True}` or `{"error": "<class name>"}`. Never
evaluates anything in the file.
"""
from __future__ import annotations

import io


def extract(blob: bytes, max_pages: int, max_chars: int) -> dict:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(blob), strict=False)
        if reader.is_encrypted:
            return {"encrypted": True}
        pages, truncated = [], []
        for i, page in enumerate(reader.pages[:max_pages]):
            text = page.extract_text() or ""
            if len(text) > max_chars:
                text = text[:max_chars]
                truncated.append(i + 1)
            pages.append(text)
        return {"pages": pages, "truncated": truncated, "page_count": len(reader.pages)}
    except MemoryError:
        return {"error": "MemoryError"}
    except Exception as exc:  # noqa: BLE001 — pypdf raises a bag of exception types
        return {"error": type(exc).__name__}


def extract_to(conn, blob: bytes, max_pages: int, max_chars: int, memory: int) -> None:
    """The child's entry: limit the address space, read, send the result, exit."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    except (ImportError, ValueError, OSError):  # no `resource` on Windows; the time budget still holds
        pass
    conn.send(extract(blob, max_pages, max_chars))
    conn.close()
