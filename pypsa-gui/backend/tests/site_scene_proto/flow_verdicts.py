"""
D02 + "SC-00 N UI contract": the additive `/flow` scene verdicts.

SC-01 adds `scene: {entry, actions}` to `GET /api/projects/{name}/flow`
beside the existing keys; electrical run_ready and every existing verdict keep
their value (test_site_scene_proto_flow_verdicts checks the real projection is
unchanged by anchor and scene files). The UI renders these verdicts and never
derives availability itself.

`entry` is the one visible creation action (UI contract rule 3): exactly one
label per state, a reason with a colon (never an em dash) and one "›" link to
the reason's home (rule 4). Homes carry both modes: Guided opens in place by
request-then-scroll to the drawer; Expert opens the panel or component.
"Save project first" points at the existing header Save, never a panel, so no
second save button can be rendered for it.
"""
from __future__ import annotations

from typing import Any

# UI contract rule 3, exact strings.
LABEL_CREATE = "Create 3D site"
LABEL_SAVE_FIRST = "Save project first"
LABEL_RESUME = "Resume site setup"
LABEL_OPEN = "Open 3D site"
REASON_NO_ANCHOR = "Add site coordinates to create a 3D site"
REASON_READ_ONLY = "Read-only project: ask an editor to create a 3D site"
REASON_SAVE_FIRST = "Unsaved project: save it to keep a 3D site"
REASON_NO_BOUNDARY = "No campus boundary: draw it to generate buildings"
REASON_NO_TARGETS = "Buildings only: configure model elements to map them"
REASON_NO_RUN = "No native run: run a study to play results"
REASON_NOT_IN_N = "Not available yet: arrives with a later Site 3D slice"
REASON_NO_WORKER = "Not available: this engine is not installed"
REASON_NO_PROPOSAL = "No proposal: generate or edit first"

# Rule 4 homes. `guided.open` is how the jump lands: request-then-scroll to the
# drawer (as requestGridReadiness does), never a bare setSlidePanel.
HOME_COORDINATE = {"guided": {"section": "project", "drawer": "site_anchor", "open": "request_then_scroll"},
                   "expert": {"panel": "horizon"}}
HOME_SAVE = {"header_action": "save"}
HOME_LOADS = {"guided": {"section": "loads", "drawer": None, "open": "request_then_scroll"},
              "expert": {"table": "Loads"}}
HOME_RESULTS = {"guided": {"section": "results", "drawer": None, "open": "request_then_scroll"},
                "expert": {"panel": "results"}}
HOME_SITE_MAP = {"site_part": "map"}

OPTIONAL_ENGINE_ACTIONS = ("process_layout", "reconstruct_photos", "generate_blender")


def _blocker(code: str, reason: str, home: dict[str, Any]) -> dict[str, Any]:
    return {"code": code, "reason": reason, "link": "›", "home": home}


def _action(name: str, available: bool, computes: str, blocker: dict[str, Any] | None = None,
            required_inputs: list[str] | None = None) -> dict[str, Any]:
    return {"action": name, "available": available, "blocker": None if available else blocker,
            "required_inputs": required_inputs or [], "computes": computes}


def entry(*, saved: bool, can_edit: bool, anchor_confirmed: bool, site: str) -> dict[str, Any]:
    """
    `site` is "none" | "unfinished" | "accepted". One label per state; the
    rule 3 order is: unsaved, then an existing Site, then the anchor.
    """
    if not saved:
        return {"label": LABEL_SAVE_FIRST, "enabled": True, "blocker": None, "target": HOME_SAVE,
                "reason": REASON_SAVE_FIRST}
    if site == "accepted":
        return {"label": LABEL_OPEN, "enabled": True, "blocker": None}
    if site == "unfinished":
        return {"label": LABEL_RESUME, "enabled": True, "blocker": None}
    if not can_edit:
        return {"label": LABEL_CREATE, "enabled": False, "blocker": _blocker("read_only", REASON_READ_ONLY, HOME_COORDINATE)}
    if not anchor_confirmed:
        return {"label": LABEL_CREATE, "enabled": False,
                "blocker": _blocker("site_anchor_missing", REASON_NO_ANCHOR, HOME_COORDINATE)}
    return {"label": LABEL_CREATE, "enabled": True, "blocker": None}


def actions(*, saved: bool, can_edit: bool, anchor_confirmed: bool, site: str, boundary: bool,
            buildings: int, targets: int, native_runs: int, pending_proposal: bool,
            installed_engines: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """Per-action verdicts. Each later stage is judged on its own inputs; none needs a Bus."""
    writable = saved and can_edit
    no_write = (_blocker("save_first", REASON_SAVE_FIRST, HOME_SAVE) if not saved
                else _blocker("read_only", REASON_READ_ONLY, HOME_COORDINATE))
    out = [
        _action("open_site_creation", writable and anchor_confirmed, "opens campus mapping at the site anchor",
                no_write if not writable else _blocker("site_anchor_missing", REASON_NO_ANCHOR, HOME_COORDINATE),
                ["saved project", "edit access", "confirmed site coordinates"]),
        _action("create_plot", writable and anchor_confirmed and boundary,
                "saves an empty Site (buses: []) inside the confirmed boundary",
                no_write if not writable else (_blocker("site_anchor_missing", REASON_NO_ANCHOR, HOME_COORDINATE)
                                               if not anchor_confirmed else
                                               _blocker("boundary_missing", REASON_NO_BOUNDARY, HOME_SITE_MAP)),
                ["confirmed site coordinates", "confirmed campus boundary"]),
        _action("generate_native", writable and site != "none" and boundary,
                "previews native buildings from the boundary and inputs; writes nothing until Apply",
                no_write if not writable else _blocker("boundary_missing", REASON_NO_BOUNDARY, HOME_SITE_MAP),
                ["saved Site with a boundary"]),
        _action("assign_native", writable and buildings > 0 and targets > 0,
                "associates a building with an existing model element; no model write",
                no_write if not writable else _blocker("no_model_targets", REASON_NO_TARGETS, HOME_LOADS),
                ["accepted buildings", "existing Loads, assets or processes"]),
        _action("show_connectivity", False, "projects the native 2D/SLD graph onto the scene",
                _blocker("not_in_this_slice", REASON_NOT_IN_N, HOME_SITE_MAP)),
        _action("play_results", False, "plays native hourly results at their own cadence",
                _blocker("no_native_run", REASON_NO_RUN, HOME_RESULTS) if native_runs == 0
                else _blocker("not_in_this_slice", REASON_NOT_IN_N, HOME_RESULTS)),
        _action("export_scene", False, "writes an export snapshot of one generation",
                _blocker("not_in_this_slice", REASON_NOT_IN_N, HOME_SITE_MAP)),
        _action("apply_proposal", writable and pending_proposal, "commits the reviewed proposal as a new revision",
                no_write if not writable else _blocker("no_proposal", REASON_NO_PROPOSAL, HOME_SITE_MAP)),
    ]
    for name in OPTIONAL_ENGINE_ACTIONS:
        # No engine is wired in N; installed or not, the action stays unavailable (no silent enablement).
        out.append(_action(name, False, "optional engine action",
                           _blocker("worker_unavailable", REASON_NO_WORKER, HOME_SITE_MAP)))
    return out
