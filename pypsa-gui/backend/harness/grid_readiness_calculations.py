"""Checked grid-readiness scorecard rows (checked-results S9).

The scorecard as the route and the copilot tool read it (`grid_readiness_for`:
the project's saved network, sidecars and grid-code profile). A row is checked
when it declares the code's own rule for its id and its stored project value,
judged against its stored limit by that rule, gives the engine's verdict. A
disagreement (a rounding boundary) is withheld, never re-verdicted; rows whose
verdict rests on inputs the scorecard does not store, categorical rows and rows
without a value are named, not checked. Only counts are facts; the harness
writes one line per row.
"""

import hashlib

from harness import profile_common as common

PROFILE = "grid-readiness/1"
REFUSALS = frozenset({"network_scope_required", "no_saved_project", "unknown_grid_code_profile",
                      "unsupported_snapshot_scope", "limits_changed_during_read"})
# The warn band is the in-hour uplift; the CI rule; the band of concern (none stored); the per-order limits;
# Pst and Plt, both judged.
NOT_SCALAR = ("energization_peak:", "lole", "oscillation", "harmonic_distortion", "flicker")
CATEGORICAL = ("frequency_support", "dynamic:", "grid_former", "ride_through:")
# Caveats the scorecard states, carried with a checked line so a pass never reads as final.
CAVEATS = {"ramp_rate": "before mitigation", "rapid_voltage_change": "before mitigation; a screening upper bound",
           "rapid_voltage_change_infrequent": "a screening upper bound"}
METRICS = {
    "checked_pass": {"formula_id": "grid-readiness-checked-pass/1", "unit": "rows",
                     "label": "Scorecard rows checked as pass"},
    "checked_warn": {"formula_id": "grid-readiness-checked-warn/1", "unit": "rows",
                     "label": "Scorecard rows checked as warn"},
    "checked_fail": {"formula_id": "grid-readiness-checked-fail/1", "unit": "rows",
                     "label": "Scorecard rows checked as fail"},
    "not_checked": {"formula_id": "grid-readiness-not-checked/1", "unit": "rows",
                    "label": "Scorecard rows not checked"},
}
DISCLOSURE = (
    "The grid-readiness scorecard as the app shows it, from the project's saved network and sidecars. "
    "A row is checked when its stored value, judged against its stored limit by the row's declared "
    "rule, gives the scorecard's verdict; the scorecard is advisory screening, not a grid-code study."
)
DISPOSITION_WORDS = {
    "unknown": "an input is missing",
    "not_scalar": "its verdict rests on inputs the scorecard does not store",
    "categorical": "a yes-or-no requirement, not a number against a limit",
    "boundary_disagreement": "its stored, rounded value sits on the limit, so its verdict cannot be recomputed",
    "rule_mismatch": "it does not declare the rule the scorecard uses for it",
}
COMPARATOR_WORDS = {"<=": "at most", "<": "below", ">=": "at least"}


def _registered():
    from services.adequacy import grid_readiness as G

    return {"scr": G.RULE_SCR, "ramp_rate": G.RULE_RAMP, "rapid_voltage_change": G.RULE_RVC,
            "rapid_voltage_change_infrequent": G.RULE_RVC, "reactive_range": G.RULE_REACTIVE,
            "voltage_unbalance": G.RULE_UNBALANCE}


def _named(row_id, prefixes):
    return any(row_id == p or (p.endswith(":") and row_id.startswith(p)) for p in prefixes)


def source(ctx, entity):
    from fastapi import HTTPException

    from services import campus_electrical_service as ce
    from services import chat_tools, dirty_state, project_registry
    from services.adequacy import grid_limits
    from services.adequacy.grid_readiness import grid_readiness_for

    if entity != "network":
        raise ValueError("network_scope_required")
    if not ctx.project_uuid:
        raise ValueError("no_saved_project")
    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, ctx.project_uuid)
    root = project_registry.project_dir(project)
    if not (root / ce.NETWORK_FILE).is_file():
        raise ValueError("no_saved_project")
    limits = root / grid_limits.SIDECAR_NAME
    before = _file_sha256(limits)
    try:
        card = grid_readiness_for(project)  # the route's own load path, locks and profile resolution
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and exc.detail.get("error_kind") == "unknown_grid_code_profile":
            raise ValueError("unknown_grid_code_profile") from exc
        raise
    if _file_sha256(limits) != before:
        raise ValueError("limits_changed_during_read")
    keep = ("id", "requirement", "limit", "unit", "project_value", "verdict", "assumed", "rule")
    return {
        "version": 1,
        "profile": PROFILE,
        "entity_id": entity,
        "index": ["saved scorecard"],
        "grid_code": {"id": card["profile"]["id"], "title": card["profile"].get("title")},
        "rows": [{k: r.get(k) for k in keep if k in r} for r in card["rows"]],
        "unsaved_edits": bool(dirty_state.is_dirty(ctx)),
        # The user's limits sidecar, whole: any edit to it (a note included) makes
        # earlier evidence stale, even where every row reads the same.
        "limits_sha256": before,
    }


def _file_sha256(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def disposition(row, registered=None):
    """(disposition, recomputed verdict or None) for one stored row."""
    from services.adequacy.grid_readiness import _judge

    registered = _registered() if registered is None else registered
    rid = row["id"]
    if _named(rid, CATEGORICAL):
        return "categorical", None
    if _named(rid, NOT_SCALAR):
        return "not_scalar", None
    value, limit = row.get("project_value"), row.get("limit")
    if (row.get("verdict") == "unknown" or isinstance(value, bool) or isinstance(limit, bool)
            or not isinstance(value, (int, float)) or not isinstance(limit, (int, float))
            or not common.finite([float(value), float(limit)])):
        return "unknown", None
    if "rule" not in row or registered.get(rid) != row["rule"]:
        return "rule_mismatch", None
    verdict = _judge(float(value), float(limit), row["rule"])
    return ("checked" if verdict == row["verdict"] else "boundary_disagreement"), verdict


def calculate(raw, scope):
    if raw.get("version") != 1 or raw.get("profile") != PROFILE or raw["entity_id"] != scope["entity_id"]:
        raise ValueError("grid_readiness_source_identity")
    if "start" in scope:
        raise ValueError("unsupported_snapshot_scope")
    counts = dict.fromkeys(METRICS, 0)
    registered = _registered()
    for row in raw["rows"]:
        kind, _ = disposition(row, registered)
        if kind == "checked" and row["verdict"] in ("pass", "warn", "fail"):
            counts["checked_" + row["verdict"]] += 1
        else:
            counts["not_checked"] += 1
    return common.facts_and_slots(scope, METRICS, counts, {}, len(raw["rows"]))


def _number(v):
    return repr(round(float(v), 4)).removesuffix(".0")


def notes(raw, label):
    title = raw["grid_code"].get("title") or raw["grid_code"].get("id") or "the grid code"
    lines = [f"Against {label(str(title))}, from the project's saved network"
             + ("; unsaved edits are not included." if raw["unsaved_edits"] else ".")]
    registered = _registered()
    for row in raw["rows"]:
        kind, _ = disposition(row, registered)
        name = label(str(row.get("requirement") or row["id"]))
        if kind == "checked":
            unit = f" {label(str(row['unit']))}" if row.get("unit") else ""
            words = COMPARATOR_WORDS[row["rule"]["comparator"]]
            extra = [CAVEATS[row["id"]]] if row["id"] in CAVEATS else []
            if row.get("assumed"):
                extra.append("limit assumed")
            lines.append(f"{name}: {_number(row['project_value'])}{unit} against {words} "
                         f"{_number(row['limit'])}{unit}: {row['verdict']}"
                         + (f" ({'; '.join(extra)})" if extra else "") + ".")
        else:
            lines.append(f"{name}: not checked; {DISPOSITION_WORDS[kind]}.")
    return lines
