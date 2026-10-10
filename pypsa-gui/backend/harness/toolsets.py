"""Provider-neutral catalogue views. Filtering never grants project access."""
from __future__ import annotations

CONTROL_TOOLS = frozenset({
    "list_tasks", "find_capabilities", "start_task", "get_task", "resume_task", "cancel_task", "resolve_task_step",
    "get_file_delivery", "inspect_import", "preview_project_changes", "apply_project_changes", "create_chart", "build_delivery",
    "ask_user", "use_skill", "start_workflow", "advance_workflow", "end_workflow",
    "use_toolset", "project_readiness", "wait_for_job", "get_study_evidence",
})
DOMAINS = ("all", "project", "network", "simulation", "results", "gridspine")
_PROJECT = frozenset({
    "list_projects", "list_scenarios", "create_scenario", "create_project_from_template",
    "activate_project", "load_project", "save_project", "save_project_as",
    "save_project_a_copy", "rename_project", "delete_project", "import_project_bundle",
    "gridspine_create_study", "compare_scenarios",
})


def filter_tools(tools, domain="all"):
    if domain == "all":
        return list(tools)
    if domain not in DOMAINS:
        raise ValueError("unknown toolset")

    def included(name):
        if name in CONTROL_TOOLS or name in _PROJECT:
            return True
        if domain == "project":
            return "project" in name or "scenario" in name or "template" in name
        if domain == "gridspine":
            return name.startswith(("gridspine_", "campus_", "solve_queue_"))
        if domain == "network":
            return any(word in name for word in ("component", "carrier", "network", "timeseries", "snapshot", "profile", "constraint", "investment_period", "vintage", "tariff", "site_connection", "participants"))
        if domain == "simulation":
            return any(word in name for word in ("simulation", "solver", "solve_queue", "validate", "dispatch_status", "sensitivity", "campaign", "study", "sweep", "loop")) or name == "run_ac_pf_stage"
        return any(word in name for word in ("results", "compare", "explain", "report", "export", "capacity", "assessment"))

    return [tool for tool in tools if included(tool["name"])]
