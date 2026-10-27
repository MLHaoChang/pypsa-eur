"""
LC-16 slice C (scope 5, owner decision Q3): in a chat turn, an edit of several
assets goes through the one preview card (`preview_project_changes` →
`apply_project_changes`), not one card per asset.

`redirects` decides, before any frame for a model response, which calls are
refused with `use_project_preview`:

- `bulk_update_components` on two or more names whose every field the preview
  takes (`assistant_tools.preview_kind`, decided per class);
- `update_component` whose every field the preview takes, when the same
  response holds another such single edit, or one already applied this turn.

A call with a field the preview cannot take (a string, a rename) keeps its own
card: the preview has no row for it. An applied single edit counts until a run
or an applied preview (any execution-tier success), so "edit A, solve, edit B"
keeps B's single card (GK B C1).

The refusal is for the model only: the dispatcher sends no frame for it, so the
user never sees a failed card (UX condition 6). Its `changes` name only calls
of this response, none applied, so it never re-proposes an applied edit (GK B C2).
"""
from __future__ import annotations

import json
from typing import Any

KIND = "use_project_preview"
SINGLE = "update_component"
BULK = "bulk_update_components"
RESET_TIERS = frozenset({"execution", "execution_long_running"})


def preview_changes(tool_name: str, args: dict) -> list[dict] | None:
    """The names-form preview changes for one call, or None when the preview
    cannot take every field of it."""
    from services.assistant_tools import preview_kind

    if not isinstance(args, dict):
        return None
    klass = args.get("component_class")
    if tool_name == SINGLE:
        names, updates = [args.get("name")], args.get("attrs")
        if args.get("new_name"):
            return None
    else:
        names, updates = args.get("names"), args.get("updates")
    if (not isinstance(names, list) or not names or not all(isinstance(n, str) and n for n in names)
            or not isinstance(updates, dict) or not updates):
        return None
    try:
        if any(key == "name" or preview_kind(klass, key) is None for key in updates):
            return None
    except Exception:  # noqa: BLE001 — an unknown class takes the tool's own path and refusal
        return None
    return [{"component_class": klass, "names": list(names), "attribute": key, "value": value}
            for key, value in updates.items()]


def _merged(changes: list[dict]) -> list[dict]:
    """One change per (class, field, value), names in call order."""
    out: dict[tuple, dict] = {}
    for change in changes:
        key = (change["component_class"], change["attribute"], json.dumps(change["value"]))
        entry = out.setdefault(key, {**change, "names": []})
        entry["names"] += [n for n in change["names"] if n not in entry["names"]]
    return list(out.values())


def message(changes: list[dict]) -> str:
    return ("Nothing was changed. An edit of several assets goes through one preview card: call "
            "preview_project_changes with changes=" + json.dumps(changes, separators=(",", ":"))
            + ", then apply_project_changes with its preview_id (one approval, one undo).")


def redirects(tool_uses: list[dict[str, Any]], single_edit_applied: bool) -> dict[str, str]:
    """tool_use_id → refusal message for the calls of one model response that
    go through the preview instead."""
    eligible = []
    for tu in tool_uses:
        name = tu.get("name")
        if name not in (SINGLE, BULK):
            continue
        changes = preview_changes(name, tu.get("input") or {})
        if changes is None:
            continue
        single = name == SINGLE or len(changes[0]["names"]) == 1
        eligible.append((tu.get("id"), single, changes))
    singles = [e for e in eligible if e[1]]
    refused = [e for e in eligible if not e[1]]
    if len(singles) >= 2 or (singles and single_edit_applied):
        refused += singles
    if not refused:
        return {}
    text = message(_merged([c for _, _, changes in refused for c in changes]))
    return {tool_use_id: text for tool_use_id, _, _ in refused}


def note_success(session: Any, tool_name: str, tier: str, args: dict) -> None:
    """Keep the turn's single-edit flag: set by an applied single edit the
    preview could have taken, cleared by a run or an applied preview."""
    if tier in RESET_TIERS:
        session.turn_single_edit_applied = False
    elif tool_name == SINGLE and preview_changes(SINGLE, args) is not None:
        session.turn_single_edit_applied = True
