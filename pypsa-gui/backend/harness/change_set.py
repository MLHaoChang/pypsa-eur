"""
LC-47 (owner decisions C1C9-FILE-1, COPILOT-1): one change-set card for a
model response that proposes several ordinary writes, with a tick box per row,
instead of one card per write.

The seam has four parts, all keyed by the response's `tool_use_id`s:

- `offer` runs once per model response, after LC-16 routing (`edit_routing`)
  and before any tool frame. It picks the members (`exclusion` says why a
  call stays out), and raises ONE `tool_pending_confirmation` frame carrying
  `change_set: {version, items, digest}` under one token. It blocks on the
  decision and stores what each member may do on `session.change_set`. Fewer
  than two members: no card, every call keeps its own path.
- `member_gate` runs inside `confirm._confirm_destructive_tool` for a member,
  in place of its own card. A ticked member proceeds only while its fresh
  preview still has the digest the card showed (`edit_preview_stale`
  otherwise). An unticked one is refused with `change_unticked` and nothing
  is written. A denied, expired or revoked set denies every member the way a
  single card does.
- `note_receipt` / `note_error` record each member's outcome as it is
  dispatched. The ordinary write path (require_current, the handler, the
  native read-back) is unchanged, so each row is read back on its own.
- `receipt_frame` is one `change_set_receipt` frame after the batch. Each row
  is applied (verified and changed), unchanged (verified, nothing differed),
  uncertain (not confirmed by read-back), failed, stale or unticked, plus a
  `landed` count. `all_applied` is true only when every ticked row landed.

Destructive rows (deletes, imports), `CARD_ONLY_TOOLS`, memory proposals and
requirements never join. Membership is off (`ENABLED`) until the card renders
(LC-47 S2), so master never emits a frame the browser cannot show.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from collections.abc import Callable, Generator, Iterable

ENABLED = False
VERSION = 1
TOOL = "change_set"
SOURCE_ASSISTANT = "proposed by the assistant"
# Written as `{"error_kind": ...}` literals so the kind manifest test sees them.
_UNTICKED = {"error_kind": "change_unticked"}
_STALE = {"error_kind": "edit_preview_stale"}
_INVALID_TICKS = {"error_kind": "invalid_decision"}
# The predicate kinds a union of the members' rows checks exactly (GK B K1,
# option A): both are read per row or per tool, never across components.
_ROW_CHECKED_KINDS = frozenset({"scalar_bound", "preserve_horizon"})


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()


def item_digest(tool: str, args: dict, card: dict) -> str:
    """What the card freezes for one row (#1257): the edit card's digest (its
    native rows and binding) and every argument the apply receives."""
    return _digest({"tool": tool, "card": card.get("digest"), "args": args})


def exclusion(
    session, tool_use: dict, *, tier: str, external: bool, redirects: dict | None
) -> str | None:
    """Why this call keeps its own path, or None when it may join a set."""
    from harness import confirm, edit_card, job_holds, requirements
    from services import assistant_tasks

    name, args = tool_use.get("name") or "", tool_use.get("input") or {}
    if not edit_card.is_edit(name):
        return "not_edit"
    if tier != "write":
        return "tier"  # deletes and imports are destructive: one card each
    if (
        name in confirm.CARD_ONLY_TOOLS
        or name in confirm.ALWAYS_CARD_TOOLS
        or name == requirements.TOOL
    ):
        return "card_only"
    if external:
        return "external"
    if job_holds.is_job_tool(name):
        return "job"
    if assistant_tasks.is_next_step(session, name, args) or assistant_tasks.governs(
        session, name, args
    ):
        return "task"
    if getattr(session, "assurance_turn", None) is not None:
        return "assurance"
    if (
        requirements._is_run_session(session)
        or getattr(session, "decision_guard", None) is not None
    ):
        return "run_session"
    if tool_use.get("id") in (redirects or {}):
        return "routed"
    return None


def _admitted(session, name: str, args: dict, tier: str) -> str | None:
    """The dispatcher's own refusals ahead of the card, run early so a call that
    will be refused never shows as a row."""
    from harness import guards, restrictions
    from services.chat_tools import PRE_DISPATCH_VALIDATORS

    try:
        restrictions.require_allowed(restrictions.capture(session), tier)
    except Exception:  # noqa: BLE001 — a rule that blocks or cannot be read: own path
        return "restricted"
    if guards.check(name, args, tier):
        return "guard"
    validator = PRE_DISPATCH_VALIDATORS.get(name)
    try:
        if validator is not None and validator(args):
            return "invalid"
    except Exception:  # noqa: BLE001 — the dispatcher treats a raising validator as advisory
        pass
    return None


def _targets(card: dict) -> set[tuple]:
    """Every (class, name) a card's rows touch, derived rows included (K2)."""
    return {
        (row.get("component_class"), row.get("name"))
        for row in card.get("rows") or []
        if row.get("role") != "note"
    }


def _shows_everything(card: dict) -> bool:
    rows = card.get("rows") or []
    return (
        bool(card.get("complete"))
        and bool(rows)
        and not any(r.get("role") == "note" and r.get("field") == "more" for r in rows)
    )


def _combined_ok(session, items: list[dict]) -> bool:
    """K1 (option A): the confirmed requirements checked on the union of the
    members' native rows. With no two members on one component (K2), the union
    IS the combined state for every row-checked predicate; any other kind fails
    closed (no set)."""
    from harness import requirements
    from services.pypsa_service import PyPSAService

    try:
        _, active = requirements.active_for(session)
        if not active:
            return True
        if any(r["predicate"].get("kind") not in _ROW_CHECKED_KINDS for r in active):
            return False
        union = {
            "complete": True,
            "rows": [row for item in items for row in item["edit_card"]["rows"]],
        }
        network = PyPSAService.get_network()
        if requirements.violations(TOOL, {}, union, active, network):
            return False
        return not any(
            requirements.violations(
                item["tool"], item["args"], item["edit_card"], active, network
            )
            for item in items
        )
    except Exception:  # noqa: BLE001 — requirements that cannot be checked: no set
        return False


def members(
    session,
    tool_uses: Iterable[dict],
    *,
    tier_of: Callable[[str], str],
    external_of: Callable[[str], bool],
    redirects: dict | None,
) -> list[dict]:
    """The set's items in call order; [] when fewer than two calls may join."""
    from harness import edit_card, requirements

    items: list[dict] = []
    taken: set[tuple] = set()
    for tu in tool_uses:
        name, args = tu.get("name") or "", tu.get("input") or {}
        if not isinstance(args, dict):
            continue
        tier = tier_of(name)
        if exclusion(
            session, tu, tier=tier, external=external_of(name), redirects=redirects
        ):
            continue
        if _admitted(session, name, args, tier):
            continue
        try:
            card = edit_card.preview(name, args)
            card = requirements.annotate(session, name, args, card)
        except Exception:  # noqa: BLE001 — a refused preview keeps its own path and its own refusal
            continue
        if (
            card is None
            or card.get("requirement_violations")
            or not _shows_everything(card)
        ):
            continue
        targets = _targets(card)
        if targets & taken:
            continue  # K2: the later write on a component keeps its own card
        taken |= targets
        items.append(
            {
                "tool_use_id": tu.get("id"),
                "tool": name,
                "tier": tier,
                "args": args,
                "edit_card": card,
                "digest": item_digest(name, args, card),
            }
        )
    if len(items) < 2 or not _combined_ok(session, items):
        return []
    return items


def _source(card: dict) -> str:
    """K9: only an adapter's own row field names a source; never model text."""
    sources = [row.get("source") for row in card.get("rows") or [] if row.get("source")]
    return "rows" if sources else SOURCE_ASSISTANT


def card_payload(items: list[dict]) -> dict:
    shown = [
        {
            "tool_use_id": i["tool_use_id"],
            "tool": i["tool"],
            "edit_card": i["edit_card"],
            "source": _source(i["edit_card"]),
            "digest": i["digest"],
        }
        for i in items
    ]
    return {
        "version": VERSION,
        "items": shown,
        "digest": _digest([i["digest"] for i in items]),
    }


def validate_ticks(pc, decision: str, ticked: Any) -> list[str] | None:
    """K5, for `/confirm`: the ticked ids a decision carries, or a 400 that
    keeps the token alive (the caller re-inserts it). None means "all"."""
    from fastapi import HTTPException

    if ticked is None:
        return None
    if pc.tool_name != TOOL:
        raise HTTPException(
            400,
            {**_INVALID_TICKS, "message": "ticked applies only to a change-set card"},
        )
    ids = list((pc.args or {}).get("tool_use_ids") or [])
    if (
        not isinstance(ticked, list)
        or not all(isinstance(t, str) for t in ticked)
        or len(set(ticked)) != len(ticked)
        or not set(ticked) <= set(ids)
    ):
        raise HTTPException(
            400, {**_INVALID_TICKS, "message": "ticked must list rows of this card"}
        )
    return list(ticked)


def offer(
    session,
    tool_uses: list[dict],
    *,
    tier_of: Callable[[str], str],
    external_of: Callable[[str], bool],
    redirects: dict | None,
) -> Generator[tuple[str, dict], None, None]:
    """Raise the one card for this response's members and record the decision
    on `session.change_set`; nothing when there is no set."""
    from harness import approvals, attention, confirm
    from harness import session as harness_session

    session.change_set = None
    if not ENABLED:
        return
    items = members(
        session,
        tool_uses,
        tier_of=tier_of,
        external_of=external_of,
        redirects=redirects,
    )
    if not items:
        return
    revision = confirm.confirmation_revision()
    ids = [i["tool_use_id"] for i in items]
    pc = session.issue_confirmation(
        tool_name=TOOL, args={"tool_use_ids": ids}, safety_tier="write"
    )
    if not all(
        approvals.asked(
            session,
            call_id=i["tool_use_id"],
            tool=i["tool"],
            tier=i["tier"],
            token=pc.token,
        )
        for i in items
    ):
        # A card nobody can audit is never shown: each member is denied as unrecordable.
        session.withdraw_confirmation(pc.token)
        session.change_set = _state(pc.token, items, "unavailable", [], revision)
        return
    attention.confirmation(session, pc, ids[0])
    yield (
        "tool_pending_confirmation",
        {
            "tool_use_id": ids[0],
            "tool_name": TOOL,
            "args": {"tool_use_ids": ids},
            "safety_tier": "write",
            "confirmation_token": pc.token,
            "ttl_seconds": harness_session.CONFIRMATION_TTL_SECONDS,
            "change_set": card_payload(items),
        },
    )
    decision = session.wait_for_decision(pc.token)
    with session._lock:
        ticked = session.confirmation_ticks.pop(pc.token, None)
    if decision == "approve" and confirm.confirmation_revision() != revision:
        decision = "revoked"
    ticked = ids if ticked is None else [t for t in ids if t in ticked]
    if decision == "approve" and not ticked:
        decision = "deny"  # K5: approving no rows is a deny
    attention.confirmation(
        session,
        pc,
        ids[0],
        confirm._DECISION_OUTCOMES.get(decision, ("unavailable",))[0],
    )
    session.change_set = _state(pc.token, items, decision, ticked, revision)


def _state(
    token: str, items: list[dict], decision: str, ticked: list[str], revision: tuple
) -> dict:
    return {
        "token": token,
        "decision": decision,
        "revision": revision,
        "rows": {
            i["tool_use_id"]: {
                "tool": i["tool"],
                "digest": i["digest"],
                "ticked": i["tool_use_id"] in ticked,
                "status": None,
                "card": i["edit_card"],
                "error_kind": None,
                "label": i["edit_card"].get("says"),
            }
            for i in items
        },
        "order": [i["tool_use_id"] for i in items],
    }


def _row_key(row: dict) -> tuple:
    return (
        row.get("component_class"),
        row.get("name"),
        row.get("field"),
        row.get("role"),
        json.dumps(row.get("new"), sort_keys=True, default=str),
    )


def _still_shown(
    state: dict, held_row: dict, tool: str, args: dict, fresh: dict | None
) -> bool:
    """K3: the fresh preview still writes what the card showed. Fields, new
    values, arguments and binding must match exactly; an old value may differ
    only where an earlier write of this response wrote the same (class, field),
    e.g. a first write that created the column turns "not set" into its
    default, and a side (non-typed) row on such a field may come or go."""
    if fresh is None:
        return False
    if item_digest(tool, args, fresh) == held_row["digest"]:
        return True
    shown = held_row["card"]
    if fresh.get("binding") != shown.get("binding") or fresh.get(
        "complete"
    ) != shown.get("complete"):
        return False
    written = state.setdefault("written", set())

    def kept(
        rows,
    ):  # a side row on a field an earlier write already filled may come or go
        return [
            r
            for r in rows or []
            if r.get("role") == "typed"
            or (r.get("component_class"), r.get("field")) not in written
        ]

    old_rows, new_rows = kept(shown.get("rows")), kept(fresh.get("rows"))
    if [_row_key(r) for r in old_rows] != [_row_key(r) for r in new_rows]:
        return False
    return all(
        a.get("old") == b.get("old")
        or (a.get("component_class"), a.get("field")) in written
        for a, b in zip(old_rows, new_rows)
    )


def held(session, tool_use_id: str) -> dict | None:
    state = getattr(session, "change_set", None)
    return (state or {}).get("rows", {}).get(tool_use_id)


def member_gate(
    session,
    row: dict,
    *,
    tool_use_id: str,
    tool_name: str,
    tier: str,
    args: dict,
    edit_card: dict | None,
    link: dict,
    restriction_snapshot: dict | None,
    tool_results_collector: list[dict],
) -> Generator[tuple[str, dict], None, bool]:
    """A member's admission, in place of its own card (K3, K8)."""
    from harness import approvals, confirm

    state = session.change_set
    decision = state["decision"]
    token6 = state["token"][:6]
    if decision == "approve" and not row["ticked"]:
        approvals.decided(
            session,
            call_id=tool_use_id,
            tool=tool_name,
            tier=tier,
            outcome="rejected",
            by="user",
            link=link,
        )
        yield from confirm._deny(
            tool_use_id,
            tool_name,
            _UNTICKED["error_kind"],
            "The user left this change unticked on the change-set card; nothing was written.",
            tool_results_collector,
        )
        return False
    if decision == "approve" and not _still_shown(
        state, row, tool_name, args, edit_card
    ):
        approvals.decided(
            session,
            call_id=tool_use_id,
            tool=tool_name,
            tier=tier,
            outcome="revoked",
            by="system",
            link=link,
        )
        yield from confirm._deny(
            tool_use_id,
            tool_name,
            _STALE["error_kind"],
            "This change's inputs moved after the card was shown (an earlier row or "
            "another edit); nothing was written. Propose it again.",
            tool_results_collector,
        )
        return False
    if decision == "approve" and restriction_snapshot is not None:
        from harness import restrictions

        try:
            restrictions.require_current(restriction_snapshot, session)
        except Exception:  # noqa: BLE001 — a failed required policy read cannot approve work
            decision = "revoked"
    if decision == "approve" and confirm.confirmation_revision() != state["revision"]:
        decision = "revoked"
    if decision == "unavailable":
        approvals.decided(
            session,
            call_id=tool_use_id,
            tool=tool_name,
            tier=tier,
            outcome="unavailable",
            by="system",
            link=link,
        )
        yield from confirm._deny(
            tool_use_id,
            tool_name,
            confirm._UNAVAILABLE["error_kind"],
            f"approval for {tool_name!r} could not be recorded",
            tool_results_collector,
        )
        return False
    if decision == "approve":
        if approvals.decided(
            session,
            call_id=tool_use_id,
            tool=tool_name,
            tier=tier,
            outcome="approved",
            by=f"{TOOL}:{token6}",
            link=link,
        ):
            return True
        yield from confirm._deny(
            tool_use_id,
            tool_name,
            confirm._UNAVAILABLE["error_kind"],
            f"approval for {tool_name!r} could not be recorded",
            tool_results_collector,
        )
        return False
    outcome, by = confirm._DECISION_OUTCOMES.get(decision, ("unavailable", "system"))
    approvals.decided(
        session,
        call_id=tool_use_id,
        tool=tool_name,
        tier=tier,
        outcome=outcome,
        by=by,
        link=link,
    )
    kind = confirm._REFUSALS.get(decision, confirm._UNKNOWN)["error_kind"]
    yield from confirm._deny(
        tool_use_id,
        tool_name,
        kind,
        f"{decision} on the change-set card for {tool_name!r}",
        tool_results_collector,
    )
    return False


def note_receipt(session, tool_use_id: str, receipt: dict) -> None:
    """K7: a row lands only when read back verified AND this call changed it.
    Any write of the response (a member, or a call on its own card) also notes
    the fields it wrote, for `_still_shown`."""
    state = getattr(session, "change_set", None)
    if not state:
        return
    state.setdefault("written", set()).update(
        (r.get("component_class"), r.get("field"))
        for r in receipt.get("rows") or []
        if r.get("role") not in ("note", "at_solve")
    )
    row = held(session, tool_use_id)
    if row is None:
        return
    changed = any(
        r.get("role") not in ("note", "at_solve", "derived")
        and r.get("old") != r.get("new")
        for r in receipt.get("rows") or []
    )
    if receipt.get("outcome") != "verified":
        row["status"] = "uncertain"
    else:
        row["status"] = "applied" if changed else "unchanged"


def note_error(session, tool_use_id: str, error_kind: str | None) -> None:
    row = held(session, tool_use_id)
    if row is None or row["status"] is not None:
        return
    row["error_kind"] = error_kind
    row["status"] = {"change_unticked": "unticked", "edit_preview_stale": "stale"}.get(
        error_kind or "", "failed"
    )


def observe(session, frames: Generator) -> Generator[tuple[str, dict], None, Any]:
    """Pass a dispatch's frames through, noting each member's refusal."""
    while True:
        try:
            event, payload = next(frames)
        except StopIteration as stop:
            return stop.value
        if event == "tool_error" and isinstance(payload, dict):
            note_error(session, payload.get("tool_use_id"), payload.get("error_kind"))
        yield event, payload


def receipt_frame(session) -> dict | None:
    """The set's one receipt, after the batch; None without a set."""
    state = getattr(session, "change_set", None)
    if not state:
        return None
    rows = []
    for tool_use_id in state["order"]:
        row = state["rows"][tool_use_id]
        status = row["status"] or (
            "uncertain"
            if row["ticked"] and state["decision"] == "approve"
            else "failed"
        )
        rows.append(
            {
                "tool_use_id": tool_use_id,
                "tool": row["tool"],
                "status": status,
                "error_kind": row["error_kind"],
                "label": row["label"],
            }
        )
    ticked = (
        [r for r in rows if r["status"] != "unticked"]
        if state["decision"] == "approve"
        else []
    )
    landed = sum(r["status"] == "applied" for r in rows)
    failed = sum(r["status"] in ("failed", "stale") for r in rows)
    return {
        "version": VERSION,
        "decision": state["decision"],
        "rows": rows,
        "ticked": len(ticked),
        "landed": landed,
        "failed": failed,
        "all_applied": bool(ticked) and all(r["status"] == "applied" for r in ticked),
    }
