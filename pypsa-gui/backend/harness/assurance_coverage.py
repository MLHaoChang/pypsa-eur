"""Version-1 assurance adapters for every tool, Results tab, panel and output channel.

Assurance P0 (A0.2): each surface the assistant can reach maps to one adapter
that names its effects, validity rule, progress source, artifact requirement and
status. Only a `checked` adapter may carry a checked label; every other adapter
shows explicitly unvalidated raw details and names the phase that owns it.

`smoke/assistant_inventory.py` reads this module without importing application
code, so it holds literals and pure functions only. A tool, target or channel
these rules cannot place raises, and the inventory check fails closed.
"""
from __future__ import annotations

VERSION = 1

ADAPTERS: dict[str, dict[str, str]] = {
    # Checked: the strict pilot runs these and binds them to captured evidence.
    "static_input_evidence": {
        "effects": "none", "validity": "exact captured static input fact (P0 pilot)",
        "progress": "tool_result", "artifact": "none", "status": "checked",
    },
    "static_input_page_evidence": {
        "effects": "none",
        "validity": "exact captured static inputs for the rows of one page, input fingerprint rechecked",
        "progress": "tool_result", "artifact": "none", "status": "checked",
    },
    "checked_input_effect": {
        "effects": "static input update", "validity": "operation contract and independent read-back",
        "progress": "operation outcome", "artifact": "none", "status": "checked",
    },
    "navigation_ack": {
        "effects": "view state", "validity": "issued mounted-view acknowledgement",
        "progress": "sent/acknowledged/unavailable", "artifact": "none", "status": "checked",
    },
    "approved_answer": {
        "effects": "none", "validity": "approved answer record and content digest",
        "progress": "answer_approved", "artifact": "none", "status": "checked",
    },
    # Unvalidated: shown as raw details without a checked label until the owning phase lands.
    "read_unvalidated": {
        "effects": "none", "validity": "raw tool result", "progress": "tool_result",
        "artifact": "none", "status": "unvalidated", "pending": "P1 per-family source adapter",
    },
    "write_unvalidated": {
        "effects": "saved state", "validity": "handler return only, no read-back",
        "progress": "tool_result", "artifact": "none", "status": "unvalidated",
        "pending": "P3 outcome adapter",
    },
    "destructive_unvalidated": {
        "effects": "saved state removal", "validity": "handler return only, no read-back",
        "progress": "tool_result", "artifact": "none", "status": "unvalidated",
        "pending": "P3 outcome adapter",
    },
    "job_unvalidated": {
        "effects": "job", "validity": "job status, not completion evidence",
        "progress": "job_adapters", "artifact": "none", "status": "unvalidated",
        "pending": "P3 job outcome adapter",
    },
    "artifact_unvalidated": {
        "effects": "file", "validity": "no manifest", "progress": "tool_result",
        "artifact": "manifest required", "status": "unvalidated", "pending": "P4 verified artifacts",
    },
    "navigation_unvalidated": {
        "effects": "view state", "validity": "event sent, no acknowledgement",
        "progress": "ui_event", "artifact": "none", "status": "unvalidated",
        "pending": "P5 navigation",
    },
    "user_choice": {
        "effects": "none", "validity": "the user's own answer", "progress": "choice_request",
        "artifact": "none", "status": "unvalidated", "pending": "not a factual source",
    },
    "results_view_unvalidated": {
        "effects": "none", "validity": "ordinary authorized result UI", "progress": "none",
        "artifact": "none", "status": "unvalidated", "pending": "P1/P5 result evidence cards",
    },
    "pending_channel": {
        "effects": "none", "validity": "not yet bound to the approved answer", "progress": "none",
        "artifact": "none", "status": "unvalidated", "pending": "P6 durable history and compaction",
    },
}

# The tools a checked turn may run (harness.assurance.CHECKED_TOOLS must equal these keys).
CHECKED_TOOL_ADAPTERS = {
    "get_component": "static_input_evidence",
    "list_components": "static_input_page_evidence",
    "update_component": "checked_input_effect",
    "ui_open_panel": "navigation_ack",
}

EXPLICIT_TOOL_ADAPTERS = {"ask_user": "user_choice"}

ROUTE_ADAPTERS = {"_ui_event_": "navigation_unvalidated"}

ARTIFACT_PREFIXES = ("export_", "download_")
ARTIFACT_TOOLS = frozenset({
    "create_chart", "create_evidence_report", "generate_report", "generate_study_report",
    "build_study_report", "regenerate_report_section",
})

TIER_ADAPTERS = {
    "read": "read_unvalidated",
    "write": "write_unvalidated",
    "destructive": "destructive_unvalidated",
    "execution": "job_unvalidated",
    "execution_long_running": "job_unvalidated",
}

# Every channel that can show or say assistant output.
OUTPUT_CHANNELS = {
    "stream_tokens": "approved_answer",
    "tool_result_frames": "read_unvalidated",
    "history_replay": "approved_answer",
    "answer_fetch": "approved_answer",
    "answer_export": "approved_answer",
    "tts": "approved_answer",
    "live_voice": "approved_answer",
    "conversation_summary": "pending_channel",
    "compaction": "pending_channel",
}


def tool_adapter(name: str, safety_tier: str, route: list) -> str:
    """The adapter for one tool; raises for a tool these rules cannot place."""
    if name in CHECKED_TOOL_ADAPTERS:
        return CHECKED_TOOL_ADAPTERS[name]
    if name in EXPLICIT_TOOL_ADAPTERS:
        return EXPLICIT_TOOL_ADAPTERS[name]
    if len(route) == 1 and isinstance(route[0], str) and route[0] in ROUTE_ADAPTERS:
        return ROUTE_ADAPTERS[route[0]]
    if name.startswith(ARTIFACT_PREFIXES) or name in ARTIFACT_TOOLS:
        return "artifact_unvalidated"
    if safety_tier not in TIER_ADAPTERS:
        raise ValueError(f"No assurance adapter for tool {name!r} (tier {safety_tier!r}).")
    return TIER_ADAPTERS[safety_tier]


def panel_adapter(panel_id: str, ack_panels) -> str:
    return "navigation_ack" if panel_id in ack_panels else "navigation_unvalidated"


def checked_label_allowed(adapter_id: str) -> bool:
    """Unknown adapters fail closed: only a declared checked adapter may be labelled checked."""
    return ADAPTERS.get(adapter_id, {}).get("status") == "checked"
