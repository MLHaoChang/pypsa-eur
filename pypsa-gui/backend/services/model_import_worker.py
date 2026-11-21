"""
Run one model importer in a child process (project-io 04, Slice A).

`services.model_import_service` starts `read_to` in a `spawn` multiprocessing child and
kills it at its wall-clock budget. The readers (PSS/E, PowerFactory DGS, PLEXOS) parse
files a user uploaded and have no timeout or memory bound of their own, so the API
process never runs one itself. A child, not a `sys.executable` subprocess, for the same
reason as `services.pdf_text_worker`: a frozen desktop build is its own executable.

The child sets its address-space limit, reads, and writes `network.nc` and every
sidecar into `out_dir` (the request's staging folder). It sends back one plain dict and
never pickles a network across the pipe:

- `{"report": <ImportReport.to_dict()>, "sidecars": [names], "bus_count": n,
  "snapshot_count": n, "network_name": str}` on success;
- `{"refused": "<the reader's plain reason>"}` for an `ImportFormatError`;
- `{"error": "<exception class name>"}` for anything else.

A child that dies (the memory limit, a crash) sends nothing; the parent reads that as EOF.
"""

from __future__ import annotations

import json
from pathlib import Path

NETWORK_FILE = "network.nc"


def _write_sidecar(path: Path, content) -> None:
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(
            json.dumps(content, indent=2, allow_nan=False), encoding="utf-8"
        )


def run(
    format_id: str, paths: list[str], options: dict, out_dir: str, declared
) -> dict:
    """Read the files and write the result into `out_dir`; the dict the child sends."""
    from gridspine.ingest import import_result as IR

    try:
        result = IR.read(
            format_id, [Path(p) for p in paths], options, declared=declared
        )
    except IR.ImportFormatError as exc:
        return {"refused": str(exc)}
    from services.pypsa_service import PyPSAService

    out = Path(out_dir)
    n = result.network
    # The one runtime path to a `.nc` file. Its netCDF lock guards one process's
    # HDF5 handles; this child has its own, so the lock is not taken here.
    PyPSAService.export_network_to_netcdf(n, out / NETWORK_FILE)
    for name, content in result.sidecars.items():
        _write_sidecar(out / name, content)
    return {
        "report": result.report.to_dict(),
        "sidecars": sorted(result.sidecars),
        "bus_count": len(n.buses),
        "snapshot_count": len(n.snapshots),
        "network_name": str(n.name or ""),
    }


def read_to(
    conn,
    format_id: str,
    paths: list[str],
    options: dict,
    out_dir: str,
    declared,
    memory: int,
) -> None:
    """The child's entry: limit the address space, read, send the result, exit."""
    import os

    # One BLAS/OpenMP thread: per-thread buffers on a many-core host would otherwise
    # take much of the address-space limit before the read starts.
    for var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[var] = "1"
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    except (
        ImportError,
        ValueError,
        OSError,
    ):  # no `resource` on Windows; the time budget still holds
        pass
    try:
        out = run(format_id, paths, options, out_dir, declared)
    except Exception as exc:  # noqa: BLE001 - the parent turns it into a plain refusal
        out = {"error": type(exc).__name__}
    conn.send(out)
    conn.close()
