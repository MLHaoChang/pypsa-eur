"""The checked time-to-power fact of a data-centre project (DC 27).

Read from the project's saved network (the grid import's energisation stages)
and its stored data-centre spec (``project_start``, ``connection_date``, the
COD phases); ``services.dc_time_to_power`` holds the rule (owner decision
DC27-TTP-1). A project the rule cannot answer has the fact withheld with its
reason, never 0.
"""

from harness import profile_common as common

PROFILE = "time-to-power/1"
REFUSALS = frozenset({"network_scope_required", "no_saved_project", "unsupported_snapshot_scope"})
METRICS = {
    "time_to_power_months": {"formula_id": "dc-time-to-power/1", "unit": "months",
                             "label": "Time to power from project start"},
}
DISCLOSURE = (
    "Time to power is calendar months from the stated project start to first power, a partial month "
    "rounded up. First power is the stated grid connection date, else the first model period whose "
    "energised grid import is above 0 MW for the whole period while data-centre load is in service; "
    "such a date is only as fine as that period and the true first power may be up to a period earlier. "
    "Grid-delivered power only: on-site generation that could carry the load before energisation is "
    "not counted."
)
SPEC_KEYS = ("project_start", "connection_date", "phases")


def _spec(meta):
    from services.adequacy.dc_backup import DC_TEMPLATES

    block = meta.get("dc_load") if isinstance(meta, dict) else None
    spec = block.get("spec") if isinstance(block, dict) else None
    if meta.get("id") not in DC_TEMPLATES or not isinstance(spec, dict):
        return None
    return {"project_start": spec.get("project_start"), "connection_date": spec.get("connection_date"),
            "phases": [{"cod": p.get("cod"), "it_mw": p.get("it_mw")} for p in spec.get("phases") or []]}


def source(ctx, entity):
    import pypsa

    from services import campus_electrical_service as ce
    from services import chat_tools, dirty_state, project_registry
    from services import dc_time_to_power as D
    from services.template_marker import read_marker

    if entity != "network":
        raise ValueError("network_scope_required")
    if not ctx.project_uuid:
        raise ValueError("no_saved_project")
    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, ctx.project_uuid)
    root = project_registry.project_dir(project)
    nc = root / ce.NETWORK_FILE
    if not nc.is_file():
        raise ValueError("no_saved_project")
    meta = read_marker(root)
    meta = meta if isinstance(meta, dict) else {}
    with ce._netcdf_lock():
        n = pypsa.Network(str(nc))
    stages, stage_reason = [], None
    try:
        stages = D.import_stages(n, meta)
    except D.StagesUnavailable as exc:
        stage_reason = {"reason": exc.reason, "detail": exc.detail}
    return {
        "version": 1,
        "profile": PROFILE,
        "entity_id": entity,
        "index": ["saved project"],
        "spec": _spec(meta),
        "stages": stages,
        "stage_reason": stage_reason,
        "unsaved_edits": bool(dirty_state.is_dirty(ctx)),
    }


def result(raw):
    from services import dc_time_to_power as D

    return D.time_to_power(raw["spec"], raw["stages"], raw["stage_reason"])


def calculate(raw, scope):
    if raw.get("version") != 1 or raw.get("profile") != PROFILE or raw["entity_id"] != scope["entity_id"]:
        raise ValueError("time_to_power_source_identity")
    if "start" in scope:
        raise ValueError("unsupported_snapshot_scope")
    out = result(raw)
    if out["status"] == "available":
        return common.facts_and_slots(scope, METRICS, {"time_to_power_months": float(out["months"])}, {}, 1)
    return common.facts_and_slots(scope, METRICS, {}, {"time_to_power_months": out["reason"]}, 1)


def notes(raw, label):
    """The answer's lines: where the figure comes from and how fine it is,
    or why it is withheld."""
    out = result(raw)
    saved = ("from the project's saved network and data-centre inputs"
             + ("; unsaved edits are not included." if raw["unsaved_edits"] else "."))
    if out["status"] != "available":
        detail = f" ({label(str(out['detail']))})" if out.get("detail") else ""
        return [f"Time to power is not available: {out['words']}{detail}.", saved]
    if out["source"] == "connection_date":
        how = f"First power on {out['first_power']}, the stated grid connection date"
        fine = "exact to the day"
    else:
        how = (f"First power on {out['first_power']}, the first model period ({label(str(out['stage']))}) "
               "with grid power while data-centre load is in service")
        fine = (f"only as fine as that {out['resolution_months']}-month period: "
                "first power may be up to a period earlier")
    return [f"{how}, counted from the project start {raw['spec']['project_start']}; {fine}.", saved]
