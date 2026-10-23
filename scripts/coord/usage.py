# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
#
"""
Daily token usage from saved list_sessions pages.

usage.py <dir>  reads <dir>/*.json (raw list_sessions output), merges each
session's cumulative usage into <dir>/snapshots.json (one entry per session,
keyed by id, holding the latest totals and the totals seen at the first
snapshot of each Berlin day), and writes <dir>/daily.json and
<dir>/sessions.json for the dashboard.

A session's usage is cumulative. Its consumption on a day is its total at the
day's last snapshot minus the total at the previous day's last snapshot. Before
the first snapshot there is no history, so a session's whole total is credited
to the Berlin day of its last update (backfill; sessions here rarely live past
a day because they rotate at 200k context).
"""

import datetime as dt
import glob
import json
import os
import sys
from zoneinfo import ZoneInfo

BER = ZoneInfo("Europe/Berlin")
FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cost_usd",
)


def sessions(payload):
    if isinstance(payload, list):
        for p in payload:
            yield from sessions(p)
        return
    if not isinstance(payload, dict):
        return
    if "ccr" in payload and isinstance(payload["ccr"], dict):
        yield from sessions(payload["ccr"])
        return
    if "id" in payload and str(payload["id"]).startswith("session_"):
        yield payload
        return
    for k in ("data", "sessions", "items", "results"):
        if k in payload:
            yield from sessions(payload[k])


def load(path):
    text = open(path).read()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Some saved tool outputs wrap the JSON in text: take the outermost object.
        a, b = text.find("{"), text.rfind("}")
        return json.loads(text[a : b + 1]) if a >= 0 else []


def day_of(iso):
    return (
        dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        .astimezone(BER)
        .date()
        .isoformat()
    )


def main(d):
    snap_path = os.path.join(d, "snapshots.json")
    snaps = json.load(open(snap_path)) if os.path.exists(snap_path) else {}
    now = dt.datetime.now(dt.UTC)
    today = now.astimezone(BER).date().isoformat()
    seen = {}
    for f in sorted(
        glob.glob(os.path.join(d, "page-*.json"))
        + glob.glob(os.path.join(d, "cowork-*.json"))
    ):
        for s in sessions(load(f)):
            em = s.get("external_metadata") or {}
            u = em.get("usage") or {}
            if not u:
                continue
            seen[s["id"]] = dict(
                id=s["id"],
                title=s.get("title", ""),
                model=em.get("last_served_model")
                or (s.get("session_context") or {}).get("model", ""),
                created=s.get("created_at", ""),
                updated=s.get("updated_at", ""),
                totals={k: float(u.get(k) or 0) for k in FIELDS},
            )
    for sid, s in seen.items():
        e = snaps.get(sid)
        if e is None:
            # Backfill: everything so far belongs to the day it was last active.
            e = snaps[sid] = dict(
                days={day_of(s["updated"] or now.isoformat()): s["totals"]}
            )
        else:
            e["days"][today] = s["totals"]
        e.update(
            title=s["title"],
            model=s["model"],
            created=s["created"],
            updated=s["updated"],
            totals=s["totals"],
        )
    json.dump(snaps, open(snap_path, "w"))
    meta_path = os.path.join(d, "meta.json")
    first_run = (
        json.load(open(meta_path))["first_run"] if os.path.exists(meta_path) else today
    )
    json.dump({"first_run": first_run}, open(meta_path, "w"))

    daily = {}
    rows = []
    for sid, e in snaps.items():
        prev = {k: 0.0 for k in FIELDS}
        for day in sorted(e["days"]):
            cur = e["days"][day]
            delta = {k: max(0.0, cur[k] - prev[k]) for k in FIELDS}
            prev = cur
            agg = daily.setdefault(day, {k: 0.0 for k in FIELDS} | {"sessions": 0})
            for k in FIELDS:
                agg[k] += delta[k]
            if any(delta[k] for k in FIELDS):
                agg["sessions"] += 1
        t = e["totals"]
        rows.append(
            dict(
                id=sid,
                title=e["title"],
                model=e["model"],
                last_active=day_of(e["updated"]) if e.get("updated") else "",
                input_tokens=t["input_tokens"],
                output_tokens=t["output_tokens"],
                cache_read_tokens=t["cache_read_tokens"],
                cache_write_tokens=t["cache_write_tokens"],
                total_tokens=sum(t[k] for k in FIELDS[:4]),
                cost_usd=round(t["cost_usd"], 4),
            )
        )
    out = []
    for day in sorted(daily):
        a = daily[day]
        out.append(
            dict(
                day=day,
                sessions=a["sessions"],
                input_tokens=round(a["input_tokens"]),
                output_tokens=round(a["output_tokens"]),
                cache_read_tokens=round(a["cache_read_tokens"]),
                cache_write_tokens=round(a["cache_write_tokens"]),
                total_tokens=round(sum(a[k] for k in FIELDS[:4])),
                cost_usd=round(a["cost_usd"], 2),
                estimate="yes" if day <= first_run else "no",
            )
        )
    json.dump({"rows": out}, open(os.path.join(d, "daily.json"), "w"))
    json.dump(
        {"rows": sorted(rows, key=lambda r: -r["cost_usd"])},
        open(os.path.join(d, "sessions.json"), "w"),
    )
    print(f"{len(seen)} sessions read, {len(snaps)} tracked, {len(out)} days")
    for r in out[-10:]:
        print(
            r["day"], r["sessions"], f"{r['total_tokens']:,}", f"${r['cost_usd']:,.2f}"
        )


if __name__ == "__main__":
    main(sys.argv[1])
