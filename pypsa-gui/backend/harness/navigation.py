"""Issued, expiring mounted-view acknowledgements bound to a turn and revision."""

import time
import uuid

from fastapi import HTTPException

from harness.durable import network_guard, transaction
from services import assurance_records as records, workflow_tools
from services.pypsa_service import PyPSAService

PANELS = frozenset(
    {
        "results",
        "overview",
        "issues",
        "simparams",
        "timeseries",
        "capacityBounds",
        "scenarios",
        "snapshots",
        "horizon",
        "solveQueue",
        "hubDesign",
        "workspace",
        "settings",
        "gridspine",
        "reports",
        "campusElectrical",
        "campusStudy",
        "campusResults",
    }
)


# Live-voice 07: optional Load flow arguments on a results/loadflow navigate.
# Source and window bind through the view's own binding; the highlight joins
# the ACK target.
LOADFLOW_KEYS = ("loadflow_source", "window_from", "window_to", "highlight")


def loadflow_target(source, window_from, window_to, highlight):
    """Read the Load flow view once and place the requested branch in it.

    Returns ``(view, facts)``; ``facts`` is None without a highlight. Raises a
    typed refusal when the view is unavailable or the branch is unknown. The
    caller holds (or takes) the network guard, so the binding and the branch
    facts come from the same read.
    """
    from services.loadflow_summary import get_loadflow_view

    with network_guard():
        if PyPSAService.get_active_context().network is None:
            _refuse_no_network()
        try:
            view = get_loadflow_view(source or "lopf", window_from, window_to)
        except HTTPException as exc:
            # The axis can shrink after the tool's own check: refuse as it would.
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            if detail.get("error_kind") in ("invalid_loadflow_window", "invalid_loadflow_argument"):
                raise HTTPException(400, {
                    "error_kind": "invalid_navigation", "field": "window_from",
                    "message": detail.get("message") or "The snapshot window is not valid."}) from None
            raise
        if view.get("status") != "ok":
            raise HTTPException(409, {
                "error_kind": "loadflow_view_unavailable",
                "unavailable_kind": view.get("unavailable_kind"),
                "message": view.get("message") or "The load flow view is unavailable."})
        if highlight is None:
            return view, None
        cls, name = highlight["component"], highlight["name"]
        facts = {"component": cls, "name": name}
        rows = view.get("rows") or []
        for rank, row in enumerate(rows, start=1):
            if row.get("class") == cls and row.get("name") == name:
                return view, {**facts, "ranked": True, "rank": rank, "shown": True,
                              "basis": row.get("basis")}
        for skip in view.get("skipped") or []:
            if skip.get("class") == cls and skip.get("name") == name:
                return view, {**facts, "ranked": False, "shown": False,
                              "reason": skip.get("reason"),
                              "note": "Not ranked, so neither loaded nor unloaded can be said."}
        network = PyPSAService.get_network()
        table = network.lines if cls == "Line" else network.transformers
        if name not in table.index:
            raise HTTPException(404, {
                "error_kind": "unknown_branch", "component": cls, "name": name,
                "message": f"{cls} '{name}' is not in this project's network."})
        # Present but in neither shown list: only a truncated list can hide it.
        rows_cut, skipped_cut = bool(view.get("rows_truncated")), bool(view.get("skipped_truncated"))
        ranked = True if rows_cut and not skipped_cut else False if skipped_cut and not rows_cut else None
        reason = {True: "outside_shown_rows", False: "skipped_list_truncated",
                  None: "outside_shown_lists"}[ranked]
        return view, {**facts, "ranked": ranked, "shown": False, "reason": reason}


def _refuse_no_network():
    raise HTTPException(409, {
        "error_kind": "loadflow_view_unavailable", "unavailable_kind": "no_network",
        "message": "No project network is open, so there is no load flow to show."})


def cursor_id(owner, session_id):
    return str(uuid.uuid5(uuid.UUID(owner), "navigation/" + session_id))


def begin_turn(turn, generation):
    turn.binding_generation = generation
    oid = cursor_id(turn.owner, turn.session.session_id)
    if not records._path(turn.owner, oid).exists():
        return
    with transaction(records._path(turn.owner, oid).parent):
        if not records._path(turn.owner, oid).exists():
            return
        records.save(
            {
                "version": 1,
                "record_kind": "ui_action",
                "owner_id": turn.owner,
                "project_id": turn.project_id,
                "turn_id": turn.turn_id,
                "binding_generation": generation,
                "action_id": None,
                "expires_at": time.time() + records.RETENTION_SECONDS,
            },
            oid,
        )


def issue(turn, payload):
    """Issue an ACK for a mounted-view navigate, or None when none applies.

    With Load flow arguments the view is re-read here, under the guard that
    issues the record, and a refusal raises the same typed HTTPException as
    the tool; the returned ACK then carries ``loadflow`` facts for the model.
    """
    target = {k: v for k, v in payload.items() if k in {"panel_id", "results_tab"}}
    loadflow = {k: payload[k] for k in LOADFLOW_KEYS if k in payload}
    if (
        payload.get("kind") != "navigate"
        or target.get("panel_id") not in PANELS
        or set(payload) - {"kind", "panel_id", "results_tab", "tool_use_id", *LOADFLOW_KEYS}
        or ("results_tab" in target and target["panel_id"] != "results")
        or (loadflow and target != {"panel_id": "results", "results_tab": "loadflow"})
    ):
        return None
    if "highlight" in loadflow:
        target["highlight"] = dict(loadflow["highlight"])
    ctx = PyPSAService.get_active_context()
    if loadflow and ctx.network is None:
        _refuse_no_network()
    if ctx.network is None:
        # Networkless study navigation remains submitted until a study-specific
        # revision adapter exists. Do not invent a network fingerprint or abort
        # an otherwise valid navigation while trying to issue its ACK.
        return None
    oid = str(uuid.uuid4())
    cursor = cursor_id(turn.owner, turn.session.session_id)
    with network_guard(), transaction(records._path(turn.owner, oid).parent):
        records.authorize(turn.owner, turn.project_id)
        if ctx.project_uuid != turn.project_id:
            return None
        record = {
            "version": 1,
            "record_kind": "ui_action",
            "owner_id": turn.owner,
            "project_id": turn.project_id,
            "project_name": ctx.loaded_project,
            "action_id": oid,
            "turn_id": turn.turn_id,
            "session_id": turn.session.session_id,
            "binding_generation": turn.binding_generation,
            "target": target,
            "input_sha256": workflow_tools._network_input_fingerprint(ctx),
            "outcome": "submitted",
            "expires_at": time.time() + 30,
        }
        # Only this results adapter can attest displayed data today. Other
        # version-1 actions retain their explicit mounted-view meaning.
        facts = None
        if loadflow:
            view, facts = loadflow_target(loadflow.get("loadflow_source"),
                                          loadflow.get("window_from"),
                                          loadflow.get("window_to"),
                                          loadflow.get("highlight"))
            record.update(version=2, source_binding=view["binding"])
        elif target == {"panel_id": "results", "results_tab": "loadflow"}:
            from services.loadflow_summary import get_loadflow_view

            view = get_loadflow_view()
            if view.get("status") != "ok":
                return None
            record.update(version=2, source_binding=view["binding"])
        records.save(record, oid)
        if not records._path(turn.owner, cursor).exists():
            records.save(
                {
                    "version": 1,
                    "record_kind": "ui_action",
                    "owner_id": turn.owner,
                    "project_id": turn.project_id,
                    "turn_id": turn.turn_id,
                    "binding_generation": turn.binding_generation,
                    "action_id": None,
                    "expires_at": time.time() + records.RETENTION_SECONDS,
                },
                cursor,
            )
        current = records.load(turn.owner, cursor, "ui_action")
        if current["turn_id"] != turn.turn_id:
            return None
        records.save({**current, "action_id": oid}, cursor)
        issued = {
            k: record[k]
            for k in (
                "version",
                "action_id",
                "turn_id",
                "binding_generation",
                "target",
                "project_id",
                "project_name",
                "input_sha256",
            )
        }
        if record["version"] == 2:
            issued["source_binding"] = record["source_binding"]
        if loadflow:
            issued["loadflow"] = {"source_binding": record["source_binding"],
                                  "highlight": facts}
        return issued


def acknowledge(owner, body):
    required = {"version", "action_id", "turn_id", "binding_generation", "target"}
    if isinstance(body, dict) and body.get("version") == 2:
        required.add("source_binding")
    if (
        not isinstance(body, dict)
        or set(body) != required
        or type(body.get("version")) is not int
        or body["version"] not in (1, 2)
        or type(body.get("binding_generation")) is not int
        or not isinstance(body.get("target"), dict)
        or not all(
            isinstance(body.get(k), str) and 1 <= len(body[k]) <= 128
            for k in ("action_id", "turn_id")
        )
    ):
        raise HTTPException(422, {"error_kind": "invalid_assurance_request"})
    if body["version"] == 2:
        binding = body["source_binding"]
        keys = {"version", "project_id", "project_name", "scope", "source",
                "input_sha256", "data_sha256", "window_from", "window_to"}
        if (not isinstance(binding, dict) or set(binding) != keys
                or type(binding.get("version")) is not int or binding["version"] != 1
                or any(type(binding.get(k)) is not int or binding[k] < 0
                       for k in ("window_from", "window_to"))
                or any(not isinstance(binding.get(k), str)
                       for k in keys - {"version", "window_from", "window_to"})):
            raise HTTPException(422, {"error_kind": "invalid_assurance_request"})
    try:
        with (
            network_guard(),
            transaction(records._path(owner, body["action_id"]).parent),
        ):
            record = records.load(owner, body["action_id"], "ui_action")
            current = records.load(
                owner, cursor_id(owner, record["session_id"]), "ui_action"
            )
            ctx = PyPSAService.get_active_context()
            if (
                any(record[k] != body[k] for k in required)
                or current["action_id"] != body["action_id"]
                or current["turn_id"] != body["turn_id"]
                or current["binding_generation"] != body["binding_generation"]
                or ctx.project_uuid != record["project_id"]
                or workflow_tools._network_input_fingerprint(ctx)
                != record["input_sha256"]
            ):
                raise ValueError("mismatch")
            if record["version"] == 2:
                from services.loadflow_summary import get_loadflow_view
                from services import chat_tools

                binding = record["source_binding"]
                # This request is a browser read-back, often outside a model
                # turn. Its authenticated actor is authoritative here.
                previous = chat_tools.acting_user_id()
                chat_tools.set_acting_user(owner)
                try:
                    fresh = get_loadflow_view(binding["source"], binding["window_from"],
                                              binding["window_to"])
                finally:
                    chat_tools.set_acting_user(previous)
                if fresh.get("status") != "ok" or fresh.get("binding") != binding:
                    raise ValueError("result evidence changed")
            if record["outcome"] == "submitted":
                record.update(
                    outcome="completed",
                    acknowledged_at=time.time(),
                    verification=("browser_displayed_data" if record["version"] == 2
                                  else "browser_mounted_view"),
                )
                records.save(record, body["action_id"])
            return {
                "version": record["version"],
                "action_id": body["action_id"],
                "outcome": record["outcome"],
                "verification": record["verification"],
            }
    except (HTTPException, ValueError, KeyError, TypeError):
        raise HTTPException(409, {"error_kind": "ui_ack_mismatch"}) from None


# ── Ordinary turns (LC-03, LC-09 item 3) ─────────────────────────────────────
# The same issued actions and ACK records as checked turns, with no new
# vocabulary. The words say "showing" only after a displayed-data ACK.

ACK_WAIT_SECONDS = 4.0
_ACK_POLL_SECONDS = 0.05


class OrdinaryTurn:
    """The identity `issue` binds an action to, for a turn that is not checked."""

    def __init__(self, session, ctx, generation):
        self.session = session
        self.turn_id = str(uuid.uuid4())
        self.owner = session.owner_user_id or records.actor_id()
        self.project_id = ctx.project_uuid
        self.binding_generation = generation
        # results tab -> {source, window_from, window_to} of this turn's last read
        self.reads = {}

    def note_read(self, tool_name, result):
        """Remember what this turn read, so "showing" means the data read."""
        if tool_name == "get_loadflow_summary" and isinstance(result, dict) \
                and result.get("status") == "ok":
            ref = result.get("ui_reference") or {}
            self.reads["loadflow"] = {k: ref.get(k) for k in ("source", "window_from", "window_to")}


def open_ordinary(session, generation):
    """Bind this turn's navigation for ACKs; None when nothing can be bound."""
    ctx = PyPSAService.get_active_context()
    if ctx.project_uuid is None:
        return None
    turn = OrdinaryTurn(session, ctx, generation)
    begin_turn(turn, generation)
    return turn


def rebind(turn):
    """Follow a server-issued in-turn project switch (activate_project and
    friends): later navigation binds to the new project, and nothing read
    in the old one can make it "showing"."""
    turn.project_id = PyPSAService.get_active_context().project_uuid
    turn.reads = {}


def display(turn, acknowledgement, wait=ACK_WAIT_SECONDS):
    """What the model may say about an issued navigation: `showing` only after
    the browser acknowledged the exact displayed data. Waits briefly for the
    ACK (the frame has already been sent); the model never polls."""
    if not acknowledgement:
        return {"verification": "submitted", "say": "opened"}
    deadline = time.monotonic() + wait
    while True:
        try:
            record = records.load(turn.owner, acknowledgement["action_id"], "ui_action")
        except (HTTPException, OSError, KeyError, ValueError):
            record = {}
        if record.get("outcome") == "completed" or time.monotonic() >= deadline:
            break
        time.sleep(_ACK_POLL_SECONDS)
    verification = record.get("verification") if record.get("outcome") == "completed" else "submitted"
    out = {"verification": verification or "submitted", "say": "opened"}
    if verification == "browser_displayed_data":
        binding = acknowledgement.get("source_binding") or {}
        shown = {k: binding.get(k) for k in ("source", "window_from", "window_to")}
        read = turn.reads.get((acknowledgement.get("target") or {}).get("results_tab"))
        if read == shown:
            out["say"] = "showing"
        else:
            # The tab shows its own default view; the words describe the read.
            out["differs_from_read"] = {"read": read, "shown": shown}
    return out
