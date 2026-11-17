"""
A project keeps a copy of each Library payload it pins (project-io 03).

`library_refs.json` (`bundle_pins`) names `(kind, id, version, hash)`; the
bytes live in the org's Library. So a project moved to another org, or
opened after its Library row was deleted, could not resolve its own refs.
This module keeps the pinned bytes in the project's `library/` folder:

    library/series/<hash>.csv.gz     the series' canonical CSV, gzipped
    library/<kind>/<hash>.json       a tariff, contract or connection agreement

A copy is named by the pin's sha256 alone (content addressing): the pin's id
and version, which an imported bundle can set to anything, never form a path
(GK B3 L1). A copy is used only when its bytes hash to the pin (L2); one that
does not is reported, never read. `read` is the one place a copy is hashed.

Pure service: imports no router and no Library store at module level.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import re
import tempfile
import zlib
from collections.abc import Iterable
from pathlib import Path

DIR = "library"
SERIES = "series"
_SUFFIX = {
    SERIES: ".csv.gz",
    "tariff": ".json",
    "contract": ".json",
    "connection_agreement": ".json",
}
_HASH = re.compile(r"[0-9a-f]{64}")
#: A copy larger than this once unpacked is not read (a Library series is a
#: year of hourly values, well under 1 MB; this bounds a crafted gzip).
MAX_PAYLOAD = 64 * 1024 * 1024
_ID_MAX = 128  # the Library's own name cap (series_store / items `_NAME_MAX`)


def path_for(project_dir: Path, pin: dict) -> Path | None:
    """Where `pin`'s copy lives, or None for a pin no copy can have (an
    unknown kind, or a hash that is not a sha256 hex digest)."""
    kind, digest = pin.get("kind"), pin.get("hash")
    if (
        kind not in _SUFFIX
        or not isinstance(digest, str)
        or not _HASH.fullmatch(digest)
    ):
        return None
    return Path(project_dir) / DIR / kind / f"{digest}{_SUFFIX[kind]}"


def member_name(pin: dict) -> str | None:
    """`pin`'s copy as a bundle member name (`library/<kind>/<file>`)."""
    path = path_for(Path("."), pin)
    return None if path is None else path.as_posix()


def _payload(path: Path, kind: str) -> bytes:
    if path.stat().st_size > MAX_PAYLOAD:
        raise OSError("copy too large")
    raw = path.read_bytes()
    if kind != SERIES:
        return raw
    unzip = zlib.decompressobj(16 + zlib.MAX_WBITS)
    data = unzip.decompress(raw, MAX_PAYLOAD + 1)
    if len(data) > MAX_PAYLOAD or unzip.unconsumed_tail:
        raise OSError("copy too large once unpacked")
    if not unzip.eof:
        raise EOFError("copy is truncated")
    return data


def read(project_dir: Path, pin: dict) -> tuple[bytes | None, bool]:
    """(payload, modified). The payload only when the copy hashes to the pin;
    `modified` when a copy is there but is unreadable or hashes to anything
    else. No copy at all is (None, False)."""
    path = path_for(project_dir, pin)
    if path is None or not path.is_file():
        return None, False
    try:
        data = _payload(path, pin["kind"])
    except (OSError, EOFError, zlib.error):  # BadGzipFile is an OSError
        return None, True
    if hashlib.sha256(data).hexdigest() != pin["hash"]:
        return None, True
    return data, False


def write(project_dir: Path, pin: dict, data: bytes) -> bool:
    """Write `data` as `pin`'s copy when it hashes to the pin and no good copy
    is there. Atomic (a unique temp file + os.replace), so two writers of the
    same pin race safely. True when it wrote."""
    path = path_for(project_dir, pin)
    if path is None or hashlib.sha256(data).hexdigest() != pin["hash"]:
        return False
    if read(project_dir, pin)[0] is not None:
        return False
    body = gzip.compress(data, mtime=0) if pin["kind"] == SERIES else data
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return True


def fill(
    db, org_id, project_dir: Path, pins: Iterable[dict], *, root: Path | None = None
) -> list[str]:
    """Copy each pinned payload the project lacks from `org_id`'s Library, at
    exactly the pinned version and hash. A pin the Library cannot give stays
    without a copy (`check_pins` reports it). A shipped common item needs no
    copy. Returns the member names written; never raises for a pin."""
    from services.library import common
    from services.library import items as I
    from services.library import series_store as S

    written = []
    for pin in pins:
        if path_for(project_dir, pin) is None or read(project_dir, pin)[0] is not None:
            continue
        if pin["kind"] != SERIES and common.is_common(pin["kind"], pin["id"]):
            continue
        try:
            if pin["kind"] == SERIES:
                data = S.payload_bytes(
                    db, org_id, pin["id"], pin["version"], pin["hash"], root=root
                )
            else:
                data = I.payload_bytes(
                    db,
                    org_id,
                    pin["kind"],
                    pin["id"],
                    pin["version"],
                    pin["hash"],
                    root=root,
                )
        except (S.LibraryRefNotFound, S.LibraryRefStale):
            continue
        if write(project_dir, pin, data):
            written.append(member_name(pin))
    return written


def stray_members(members: Iterable[str], pins: Iterable[dict]) -> list[str]:
    """The `library/` members of a bundle that are no pin's copy."""
    wanted = {member_name(p) for p in pins} - {None}
    return sorted(
        m
        for m in members
        if m.startswith(f"{DIR}/") and not m.endswith("/") and m not in wanted
    )


def staged_problems(
    staging: Path, members: Iterable[str], pins: Iterable[dict]
) -> list[str]:
    """Why a staged bundle's `library/` cannot be imported (GK B3 L1, L5), as
    plain sentences; empty when it can. A pin whose id is not a Library name
    (empty, over the Library's length cap, or holding a control character),
    a copy that does not hash to its pin, and a `library/` member that is no
    pin's copy are refused; `pins` are the sidecar's and the config's. A pin
    with no copy is fine: the org's Library may serve it, and `check_pins`
    reports it when it cannot."""
    pins = list(
        {
            (p.get("kind"), p.get("id"), p.get("version"), p.get("hash")): p
            for p in pins
        }.values()
    )
    problems = []
    for pin in pins:
        ident = pin.get("id")
        if (
            not isinstance(ident, str)
            or not 0 < len(ident) <= _ID_MAX
            or any(ord(c) < 32 or ord(c) == 127 for c in ident)
        ):
            problems.append(f"{ident!r} in library_refs.json is not a Library name.")
            continue
        if read(staging, pin)[1]:
            problems.append(
                f"{member_name(pin)} does not match its pin in library_refs.json."
            )
    problems += [
        f"{m} is not the copy of any pin in library_refs.json."
        for m in stray_members(members, pins)
    ]
    return problems
