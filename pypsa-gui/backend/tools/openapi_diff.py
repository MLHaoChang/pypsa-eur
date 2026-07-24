"""
openapi_diff.py — print a deterministic route inventory of the pypsa-gui
backend, used by Phase 1 of the chatbot integration v6 plan to build the
authoritative tool->endpoint mapping table (and prevent endpoint drift in
subsequent phases via the regression test referenced in v6's risk #4).

Two modes (offline default — no running backend required):
  * Offline: import the FastAPI app and call ``app.openapi()`` directly.
    Run from this directory:
        python openapi_diff.py
    Or from the backend root:
        python tools/openapi_diff.py
  * Online: hit a running backend's ``/openapi.json``:
        python openapi_diff.py --url http://127.0.0.1:8000/openapi.json

Output: one line per route, sorted by ``(path, method)``, in the form::

    GET    /api/network/buses                                  -> list_buses
    POST   /api/network/buses                                  -> create_bus
    ...

Saved by default to ``backend/tests/fixtures/route_inventory_phase0.txt``.

This is a Phase 0 deliverable; it does NOT modify backend behaviour. Re-run
whenever a new route lands so the Phase 1 endpoint-coverage test can re-baseline.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _route_lines_from_openapi(spec: dict[str, Any]) -> list[str]:
    """Flatten an OpenAPI spec into `METHOD<pad> PATH<pad> -> operationId` rows."""
    paths = spec.get("paths", {}) or {}
    triples: list[tuple[str, str, str]] = []
    methods = {"get", "post", "put", "patch", "delete", "head", "options"}
    for path, ops in paths.items():
        if not isinstance(ops, dict):
            continue
        for method, op in ops.items():
            if method.lower() not in methods or not isinstance(op, dict):
                continue
            op_id = op.get("operationId") or "?"
            triples.append((path, method.upper(), op_id))
    triples.sort(key=lambda t: (t[0], t[1]))
    # Pad columns so a diff is readable.
    if not triples:
        return []
    path_w = max(len(p) for p, _, _ in triples)
    method_w = max(len(m) for _, m, _ in triples)
    return [
        f"{m.ljust(method_w)}  {p.ljust(path_w)}  -> {op_id}"
        for p, m, op_id in triples
    ]


def _load_offline() -> dict[str, Any]:
    """Import the FastAPI app and ask it for its openapi spec."""
    # Make backend root importable when invoked from anywhere.
    backend_root = Path(__file__).resolve().parent.parent
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    from main import app  # noqa: PLC0415
    return app.openapi()


def _load_online(url: str) -> dict[str, Any]:
    """Hit a running backend's /openapi.json. Uses stdlib (no requests dep)."""
    from urllib.request import urlopen  # noqa: PLC0415
    with urlopen(url) as resp:  # noqa: S310 — explicit user-supplied URL
        return json.load(resp)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Dump a sorted route inventory of the pypsa-gui backend for "
            "chatbot integration v6 Phase 1 endpoint mapping."
        ),
    )
    parser.add_argument(
        "--url",
        default=None,
        help=(
            "If set, GET openapi.json from this URL instead of importing the "
            "app offline. Example: http://127.0.0.1:8000/openapi.json"
        ),
    )
    parser.add_argument(
        "--out",
        default=None,
        type=Path,
        help=(
            "Write the inventory to this path instead of stdout. Default for "
            "Phase 0 fixture: backend/tests/fixtures/route_inventory_phase0.txt"
        ),
    )
    parser.add_argument(
        "--phase0-fixture",
        action="store_true",
        help=(
            "Shorthand for --out backend/tests/fixtures/route_inventory_phase0.txt. "
            "Writes the canonical Phase 0 fixture path."
        ),
    )
    args = parser.parse_args()

    spec = _load_online(args.url) if args.url else _load_offline()
    lines = _route_lines_from_openapi(spec)
    text = "\n".join(lines) + ("\n" if lines else "")

    out_path: Path | None = args.out
    if args.phase0_fixture and out_path is None:
        backend_root = Path(__file__).resolve().parent.parent
        out_path = backend_root / "tests" / "fixtures" / "route_inventory_phase0.txt"

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(f"wrote {len(lines)} routes -> {out_path}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
