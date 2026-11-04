"""The notice when an agent loop ends without a pass (study harness issue 25;
design §6.1 "Exhaustion", §7 `notify`; owner decisions SH-1, SH-ENVELOPE-1).

One email per ending (`no_progress`, `exhausted`, `unverified_write`, `error`)
to the run owner's account address, only when the GRANTED envelope's
`notify.email` is true. A resumed loop is a new `run.loop`, so its ending is a
new notice. The claim is written before the send, so a crash in between shows
as `sent: false, error: "uncertain"` and is never re-sent. The body is built
from native fields only (rule ids, facts, ledger rows); no model text.
"""

from __future__ import annotations

import logging
import time
import uuid

logger = logging.getLogger(__name__)

GRANTED_STATES = ("granted", "exhausted", "expired")
STALE_S = 60  # a claim with no result this long after is a crash between claim and send
ENDINGS = ("no_progress", "exhausted", "unverified_write", "error")
_WORDS = {
    "no_progress": "stopped making progress",
    "exhausted": "used its rounds",
    "unverified_write": "made a change that could not be verified",
    "error": "stopped on an error",
}


def _cell(value):
    from harness import runner

    return runner._plain(value, 80)


def _body(task, loop, link):
    run = task["run"]
    gate = (run["stages"].get(loop.get("stage")) or {}).get("gate") or {}
    lines = [
        f"The input check loop of your harness run {_WORDS[loop['ended']]} and the run needs you.",
        "",
        f"Run: {task['task_id']}",
        f"Ended: {loop['ended']} after {loop.get('rounds', 0)} round(s)",
        f"Open it: {link}",
        "",
        "Failing rules:",
    ]
    failing = [
        r
        for r in gate.get("rules", [])
        if r.get("severity") == "block" and r.get("verdict") != "pass"
    ]
    for rule in failing:
        for fact in (rule.get("facts") or [{}])[:3]:
            lines.append(
                f"- {_cell(rule['rule'])}: {_cell(fact.get('fact'))} = {_cell(fact.get('value'))}"
                f" {_cell(fact.get('unit'))}".rstrip()
            )
    if not failing:
        lines.append("- none recorded")
    ids = {c for h in loop.get("history", []) for c in h.get("changes", [])}
    rows = [r for r in run.get("ledger", []) if r.get("id") in ids]
    lines += ["", "Changes made (target: old -> new, receipt):"]
    for r in rows:
        t = r["target"]
        lines.append(
            f"- {_cell(t['component'])} {_cell(t['name'])}.{_cell(t['attr'])}: "
            f"{_cell(r['old'])} -> {_cell(r['new'])} ({_cell(r['receipt'])})"
        )
    if not rows:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _consented(path):
    """`notify.email` on the envelope's own file, and only for an envelope that was granted (it may since be
    exhausted or expired): a `proposed` or revoked one sends nothing."""
    from services import run_envelope

    envelope = run_envelope.read(path)
    return (
        bool(envelope)
        and envelope["state"] in GRANTED_STATES
        and (envelope.get("notify") or {}).get("email") is True
    )


def _repair_stale(path, loop):
    """A claim with no result long after (a crash between claim and send) is shown uncertain, never re-sent."""
    notice = loop["notice"]
    if notice.get("sent") is None and time.time() - notice["claimed_at"] > STALE_S:
        _settle(path, notice.get("token"), {"sent": False, "error": "uncertain"})


def send_if_due(path):
    """Claim and send the notice of an ended loop, once. Never raises."""
    from harness import runner

    try:
        task = runner._read(path)
        loop = ((task or {}).get("run") or {}).get("loop") or {}
        if loop.get("ended") not in ENDINGS:
            return
        if "notice" in loop:
            _repair_stale(path, loop)
            return
        if not _consented(path):
            return
        token = uuid.uuid4().hex
        claimed = {}

        def claim(t):
            lp = t["run"].get("loop") or {}
            if lp.get("ended") not in ENDINGS or "notice" in lp:
                return False
            lp["notice"] = {"claimed_at": time.time(), "sent": None, "token": token}
            claimed["loop"], claimed["task"] = lp, t

        with runner._file_lock(
            path
        ):  # `_update` is a thread lock; runners in other processes share the file
            runner._update(path, claim)
        if not claimed:
            return
        result = {"sent": False, "error": "send_failed"}
        try:
            from services import email_service

            contact = runner._owner_contact(task)
            if contact is None:
                result["error"] = "no_address"
            else:
                address, _, link = contact
                email_service.send_email(
                    to=address,
                    subject="PyPSA Studio: a harness run needs you",
                    body_text=_body(claimed["task"], claimed["loop"], link),
                    metadata={
                        "kind": "harness_loop_end",
                        "task_id": task["task_id"],
                        "ended": claimed["loop"]["ended"],
                    },
                )
                result = {"sent": True}
        except Exception:  # noqa: BLE001 — the run stays needs_user; the failure is on the run
            logger.warning(
                "harness run %s: the loop-end notice could not be sent",
                task["task_id"],
                exc_info=True,
            )
        _settle(path, token, result)
    except Exception:  # noqa: BLE001
        logger.exception("harness loop notice failed")


def _settle(path, token, result):
    """Write the result only onto the notice this call claimed (the loop may since have been replaced)."""
    from harness import runner

    def change(t):
        notice = (t["run"].get("loop") or {}).get("notice")
        if (
            notice is None
            or token is None
            or notice.get("token") != token
            or notice.get("sent") is not None
        ):
            return False
        notice.update(result, at=time.time())

    runner._update(path, change)
