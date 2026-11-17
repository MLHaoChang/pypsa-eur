"""The `manifest.json` of a project bundle (project-io issue 02, GK B I7).

An export adds one `manifest.json` to the `.pypsaproj.zip`: the bundle schema
version, the app version, the project kind, and the size and sha256 of every
other member. An import checks it before anything is unpacked:

- a corrupted bundle (a member missing, an extra member, a size or a hash that
  does not match, or a manifest that cannot be read) is refused;
- a bundle from a newer schema is refused, with "update the app first";
- an old bundle with no manifest imports, with a warning.

Refusals are `project_contents.BundleRefused`, the same 400 body the member
screen raises, so the route has one refusal path.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
import zlib

from services.app_version import APP_VERSION
from services.project_contents import MANIFEST, BundleRefused

#: The bundle layout this app writes and reads. Raise it when an older app
#: could no longer import what this one exports.
SCHEMA_VERSION = 1

NO_MANIFEST_WARNING = (
    "This bundle has no manifest, so its files could not be checked for "
    "damage. It was made by an older version of the app."
)

_SHA256 = re.compile(r"[0-9a-f]{64}")
_CHUNK = 1024 * 1024


def _sha256(zf: zipfile.ZipFile, name: str) -> str:
    h = hashlib.sha256()
    with zf.open(name) as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _files(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    return [i for i in zf.infolist() if not i.is_dir() and i.filename != MANIFEST]


def build(zf: zipfile.ZipFile, *, project_kind: str | None) -> dict:
    """The manifest of every member already in `zf` (opened for reading)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "app_version": APP_VERSION,
        "project_kind": project_kind,
        "members": {
            i.filename: {"size": i.file_size, "sha256": _sha256(zf, i.filename)}
            for i in _files(zf)
        },
    }


def add_to(data: bytes, *, project_kind: str | None) -> bytes:
    """`data`, a finished bundle zip, with its manifest appended."""
    buf = io.BytesIO(data)
    with zipfile.ZipFile(buf) as zf:
        manifest = build(zf, project_kind=project_kind)
    with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST, json.dumps(manifest, indent=2, sort_keys=True))
    return buf.getvalue()


def _corrupt(message: str) -> BundleRefused:
    return BundleRefused(
        {
            "error_kind": "bundle_corrupt",
            "message": f"{message} The bundle is damaged or was changed after export. "
            "Nothing was imported; export it again.",
        }
    )


def _read(zf: zipfile.ZipFile) -> dict:
    try:
        manifest = json.loads(zf.read(MANIFEST).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, zipfile.BadZipFile) as exc:
        raise _corrupt("Its manifest cannot be read.") from exc
    if not isinstance(manifest, dict):
        raise _corrupt("Its manifest cannot be read.")
    version = manifest.get("schema_version")
    if type(version) is not int or version < 1:
        raise _corrupt("Its manifest has no valid schema version.")
    if version > SCHEMA_VERSION:
        raise BundleRefused(
            {
                "error_kind": "bundle_too_new",
                "message": f"This bundle was made by a newer version of the app "
                f"(bundle format {version}; this app reads up to {SCHEMA_VERSION}). "
                "Update the app, then import it again. Nothing was imported.",
            }
        )
    members = manifest.get("members")
    if not isinstance(members, dict):
        raise _corrupt("Its manifest has no list of files.")
    for name, rec in members.items():
        if not (
            isinstance(rec, dict)
            and type(rec.get("size")) is int
            and isinstance(rec.get("sha256"), str)
            and _SHA256.fullmatch(rec["sha256"])
        ):
            raise _corrupt(f"Its manifest entry for '{name}' cannot be read.")
    return members


def check(zf: zipfile.ZipFile) -> list[str]:
    """Check `zf` against its manifest; the warnings, or `BundleRefused`.

    Every member except the manifest itself is checked: none missing, none
    extra, each size and sha256 equal. Run after `screen_members`, so every
    name here is already a safe path.
    """
    if MANIFEST not in zf.namelist():
        return [NO_MANIFEST_WARNING]
    members = _read(zf)
    present = {i.filename: i for i in _files(zf)}
    missing = sorted(set(members) - set(present))
    if missing:
        raise _corrupt(f"'{missing[0]}' is listed in its manifest but missing.")
    extra = sorted(set(present) - set(members))
    if extra:
        raise _corrupt(f"'{extra[0]}' is in the bundle but not in its manifest.")
    for name in sorted(members):
        rec = members[name]
        if present[name].file_size != rec["size"]:
            raise _corrupt(f"'{name}' has a different size than its manifest says.")
        try:
            digest = _sha256(zf, name)
        except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
            raise _corrupt(f"'{name}' cannot be read.") from exc
        if digest != rec["sha256"]:
            raise _corrupt(f"'{name}' does not match its checksum.")
    return []
