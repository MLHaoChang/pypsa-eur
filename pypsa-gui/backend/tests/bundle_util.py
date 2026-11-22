"""Test helper: keep a bundle's manifest in step with edited members."""

from __future__ import annotations

import io
import json
import zipfile

from services import bundle_manifest
from services.project_contents import MANIFEST


def refresh_manifest(data: bytes) -> bytes:
    """`data` without its `manifest.json`, then (if it had one) with a fresh one.

    Every other member keeps its name, bytes and ZipInfo (external_attr etc.).
    """
    src = zipfile.ZipFile(io.BytesIO(data))
    names = src.namelist()
    if MANIFEST not in names:
        return data
    kind = json.loads(src.read(MANIFEST)).get("project_kind")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            if info.filename != MANIFEST:
                out.writestr(info, src.read(info))
    return bundle_manifest.add_to(buf.getvalue(), project_kind=kind)
