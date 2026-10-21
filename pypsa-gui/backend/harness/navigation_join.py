"""Join a Load flow displayed-data ACK to a checked loading answer (checked-results S3b).

A version-2 Load flow action records the view's binding (project, source, the
exact input fingerprint, window positions). When the browser has confirmed the
display (outcome "completed") in a checked `branch-loading/1` or
`network-loading/1` turn, the receipt says whether the tab is showing the solve
and window that were checked. The claim is "same solve and same window", never
"the same values": the view's data hash is the tab's own and is not recomputed.
An AC power flow at commit refuses the join, since the tab may then show PF
values whatever the binding says.
"""

PROFILES = frozenset({"branch-loading/1", "network-loading/1"})
TARGET = {"panel_id": "results", "results_tab": "loadflow"}
LIMITATION = "Load flow was opened on a different window or solve than the one checked."


def join(ctx, action, scope, evidence):
    """{"shows": ..., "reason"?: ..., "window"?: ...} for a Load flow action, else None."""
    from services import workflow_tools

    if scope.get("profile") not in PROFILES or action.get("target") != TARGET:
        return None
    if action.get("outcome") != "completed" or action.get("version") != 2:
        return {"shows": "not_confirmed"}  # opened; the tab has not reported what it shows
    binding = action.get("source_binding") or {}
    raw, window = evidence["raw"], evidence["scope"]
    index = raw["index"]
    try:
        a, b = index.index(window["start"]), index.index(window["end"])
    except ValueError:
        return {"shows": "different_view", "reason": "window_differs"}
    state, n = ctx.solver_state, ctx.network
    recorded = n.meta.get("assistant_solve_exact_input_fingerprint")
    if binding.get("project_id") != evidence["project_id"]:
        reason = "project_differs"
    elif (binding.get("source") != "lopf" or state.get("ac_pf_results") is not None
          or state.get("lopf_results") is not None):
        reason = "source_not_lopf"
    elif not isinstance(recorded, str) or len(binding.get("input_sha256") or "") != 64:
        reason = "fingerprint_basis_unknown"
    elif not (binding["input_sha256"] == recorded == workflow_tools._network_input_fingerprint(ctx, exact=True)):
        reason = "inputs_changed"
    elif (binding.get("window_from"), binding.get("window_to")) != (a, b):
        reason = "window_differs"
    else:
        return {"shows": "checked_evidence", "window": {"start": window["start"], "end": window["end"]}}
    return {"shows": "different_view", "reason": reason}
