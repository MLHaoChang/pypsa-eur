"""Data requests and their answers, mid-run (study harness issue 21 §3-4;
owner decision SH21-ACCEPT-1 Q2; GK B conditions B1-B4).

A run that L1 cannot unblock stops `needs_user` with one data request
listing every gap. The request's id names its items, and no item is asked
twice in one run. The user answers through ordinary chat and its cards
(COP-1). This module only decides whether one carded write is an **answer**
or ordinary **drift**. It writes nothing.

An answer row is admitted only when every condition holds:

* B1: the run waits on a data request (`run.waiting.kind == "data"`) whose
  `ref` is the request named; the writer is the run's owner, on the run's own
  project, by a card decision (`by == "user"`).
* B2: the write's whole diff lies inside the request's open items. A write
  of an item plus any other field is all drift.
* B3: the freshness chain's head equals the fingerprint just before the
  write. The caller checks this under the task lock, and its link becomes the
  fingerprint just after.
* B4: the row records the card decision id. `resume_task` still checks the
  gate again from native facts; the row is never taken as the fact.
"""

from __future__ import annotations

import hashlib
import json


def _key(item) -> tuple:
    t = item.get("target")
    if t:
        return ("target", t["component"], t["name"], t["attr"])
    return ("param", item["param"])


def request(asked: list, items: list) -> dict | None:
    """The next data request: the items no earlier request of this run named
    (none is asked twice), with an id over them; None when nothing is new.
    `asked` is the run's earlier requests (`run.requests`)."""
    before = {_key(i) for r in asked or [] for i in r["items"]}
    new, seen = [], set()
    for item in items:
        key = _key(item)
        if key in before or key in seen:
            continue
        seen.add(key)
        new.append(item)
    if not new:
        return None
    rid = hashlib.sha256(
        json.dumps([len(asked or []), [list(_key(i)) for i in new]]).encode()
    ).hexdigest()[:16]
    return {"id": f"req-{rid}", "items": new, "answered": []}


def open_items(req: dict) -> set:
    answered = {tuple(a) for a in req.get("answered", [])}
    return {_key(i) for i in req["items"]} - answered


def answer(
    task: dict,
    req: dict | None,
    *,
    actor_id,
    project_id,
    decision: dict | None,
    diff,
    before,
    head,
):
    """`(row, None)` when the carded write answers open items of the run's
    open request, else `(None, reason)` and the write is drift. `diff` is
    every `(component, name, attr)` the write changed; `before` the
    fingerprint just before it, and `head` the chain's last link read under
    the task lock."""
    run = task.get("run") or {}
    waiting = run.get("waiting") or {}
    if waiting.get("kind") != "data" or req is None or waiting.get("ref") != req["id"]:
        return None, "not_waiting_on_data"
    if actor_id != task["owner_id"] or project_id != task["project_id"]:
        return None, "not_the_runs_owner_or_project"
    if not decision or decision.get("by") != "user" or not decision.get("id"):
        return None, "not_a_card_decision"
    changed = {("target", *t) for t in diff}
    if not changed:
        return None, "no_change"
    if not changed <= open_items(req):
        return (
            None,
            "outside_the_request",
        )  # B2: an item plus anything else is all drift
    if before is None or head != before:
        return None, "chain_moved"  # B3: a write came in between
    return {
        "answers": req["id"],
        "decision_id": decision["id"],
        "items": sorted(list(k) for k in changed),
    }, None


def record(req: dict, row: dict) -> bool:
    """Marks the row's items answered; True when the request is complete."""
    req["answered"] = sorted(
        {tuple(a) for a in req.get("answered", [])} | {tuple(i) for i in row["items"]}
    )
    req["answered"] = [list(a) for a in req["answered"]]
    return not open_items(req)


def data_items(envelope: dict | None, profile: dict, *, unset, present) -> list[dict]:
    """The items of the one data notice when L1 ends (GK B (20), Q2), read
    from the state at that moment: each envelope `gaps` target `unset(target)`
    still reports unset, and each profile `inputs.required` item `present(item)`
    reports absent (absent, not falsy). Other failed rules are never items:
    they stay in the needs_user notice."""
    items, seen = [], set()
    for gap in (envelope or {}).get("gaps", []):
        t = gap["target"]
        key = (t["component"], t["name"], t["attr"])
        if key not in seen and unset(t):
            seen.add(key)
            items.append(
                {
                    "target": dict(t),
                    "label": f"{t['name']} {t['attr']}",
                    "tool": gap["tool"],
                }
            )
    for param in (profile.get("inputs") or {}).get("required", []):
        if not present(param):
            items.append({"param": param, "label": param})
    return items
