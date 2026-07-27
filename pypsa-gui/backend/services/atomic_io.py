"""
Write-to-tmp-then-rename, in one place (phase 1b, spec E5).

Lifted verbatim out of `routers/projects.py`, where it had grown fourteen
call sites and one importer in another router. Nothing about the mechanism
changed; what changed is that the genuinely destructive paths now use it —
bundle import, from-template and scenario-create all wrote straight over a
live project's `network.nc` with `write_bytes`/`copy2`, so a failure partway
left a truncated file where a working project had been.

**What this guarantees:** the target file is either the old content or the new
content, never a prefix of the new one. `os.replace` swaps a directory entry in
a single operation on POSIX and on NTFS.

**What it does NOT guarantee:** durability. There is no `fsync` of the file or
of its parent directory, so a power loss can still lose the rename even though
no reader ever observes a torn file. Adding `fsync` would cost a stall on every
save; the failure this defends against is a killed process, which is the one
that actually happens.

The `.tmp` sibling is CLEANED UP on an exception — a leftover `.tmp` therefore
means the process was killed, which is exactly what makes it a usable
crash-recovery signal. `routers/projects.py` scans for `f"{name}.tmp"` per
bundle file to raise the corruption banner, so the suffix here is a contract,
not an implementation detail; `test_atomic_io.py` asserts the two agree.
"""
from __future__ import annotations

import os
import pathlib
import shutil
from typing import Callable


def atomic_write_with(path: pathlib.Path, writer: Callable[[pathlib.Path], object]) -> None:
    """
    Run `writer(tmp_path)`, then atomically rename the result over `path`.

    If the process is killed before the rename, `path` keeps its previous
    content and a stale `path.tmp` sibling is left behind for recovery.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        writer(tmp)
        os.replace(str(tmp), str(path))
    except Exception:
        # Clean up so the project directory does not accumulate junk after a
        # failed write. The original `path` is untouched either way.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def atomic_write_text(path: pathlib.Path, text: str) -> None:
    """Atomic-replace shortcut for JSON / config writes."""
    atomic_write_with(path, lambda p: p.write_text(text))


def atomic_write_bytes(path: pathlib.Path, data: bytes) -> None:
    """Atomic-replace shortcut for the bundle-file writes."""
    atomic_write_with(path, lambda p: p.write_bytes(data))


def atomic_copy(src, path: pathlib.Path) -> None:
    """
    Atomic-replace shortcut for `shutil.copy2` into a live project directory.

    The plain `copy2(src, dest)` this replaces truncates `dest` before it reads
    a byte of `src`, so a read error partway through leaves the destination
    shorter than either version.
    """
    atomic_write_with(path, lambda p: shutil.copy2(src, p))
