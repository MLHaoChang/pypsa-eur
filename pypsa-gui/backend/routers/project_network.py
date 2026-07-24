"""
Path-scoped per-project network READ endpoints (B6, increment 1).

Mounted at `/api/projects/{project_id}/network`. Each route serves a SPECIFIC
resident project's data — resolved via `ProjectDep` (resolve-or-load-resident)
— WITHOUT changing the active foreground slot. Reads only; writes are a later
increment.

These reuse the active-network shim's serialisation core (`_serialize_component`
+ `_meta_payload` in routers.network) but feed it the INJECTED context's
network + that context's transient-row set, so a resident project's solver
internals (vintage clones, VOLL slacks) are hidden per-project. The shim
(`/api/network/*`) is untouched and still operates on the active network.

GETs are not gated by the write-middleware, so no middleware change here.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from routers.deps import ProjectDep
from routers.network import _meta_payload, _serialize_component
from services.project_context import ProjectContext
from services.pypsa_service import PyPSAService

router = APIRouter()

# Component classes the path-scoped read serves — the same set the active-network
# shim exposes via its per-component GET routes. The URL segment is the
# lowercase-plural PyPSA DataFrame attribute (`buses`, `lines`, …), matching the
# shim's URL surface (`GET /api/network/buses`); each maps to the PascalCase
# class name the transient-row registry is keyed by. Kept local (not imported
# from network._COMPONENT_ATTRS) so the path-scoped surface is an explicit,
# reviewable allow-list — it intentionally excludes carriers / shunt_impedances /
# global_constraints, which the shim serves on bespoke routes outside this
# 8-component asset set.
_PATH_SCOPED_COMPONENTS: dict[str, str] = {
    "buses":         "Bus",
    "lines":         "Line",
    "links":         "Link",
    "transformers":  "Transformer",
    "generators":    "Generator",
    "storage_units": "StorageUnit",
    "stores":        "Store",
    "loads":         "Load",
}


@router.get("/{project_id}/network/meta")
def get_project_meta(ctx: ProjectContext = ProjectDep) -> dict:
    """The /network/meta payload for a SPECIFIC project's network."""
    return _meta_payload(ctx.network, ctx.loaded_project)


@router.get("/{project_id}/network/{component_class}")
def get_project_component(
    component_class: str, ctx: ProjectContext = ProjectDep
) -> list[dict]:
    """
    Serialise a SPECIFIC project's component table (transient rows hidden via
    THAT project's registry, not the active one). The `component_class` URL
    segment is the lowercase-plural attr (`buses`, `generators`, …). Rejects an
    unknown component_class with 404, mirroring the shim's component allow-list.
    """
    cls = _PATH_SCOPED_COMPONENTS.get(component_class)
    if cls is None:
        raise HTTPException(
            404,
            f"Unknown component_class '{component_class}'. Expected one of: "
            f"{', '.join(sorted(_PATH_SCOPED_COMPONENTS))}.",
        )
    transient = PyPSAService.get_transient_rows_for(ctx, cls)
    return _serialize_component(ctx.network, component_class, transient)
