# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""
Print the frontier: the issues a new session can start now.

The issue files' headers (``Status:``, ``Blocked by:``, ``Model tier:``,
``Gated:``) are the only source. Run from the repo root::

    python3 scripts/coord/frontier.py [--open-prs prs.txt] [--sessions s.txt]

An issue is on the frontier when its Status starts with ``ready-for-agent``,
every issue it is blocked by (same effort folder) is done, no live session works
on it and no open PR touches its file. One line per package: path, model tier,
gated, title, and the open PRs that change a file the issue names.

Inputs, both optional and plain text:

* ``--open-prs``: one ``<pr> <path>`` pair per line (``#12``, ``12:`` also
  work), e.g. ``for n in 12 15; do git diff --name-only origin/master...pr/$n
  | sed "s/^/$n /"; done``. A JSON object ``{"12": ["a.py", ...]}`` also works.
* ``--sessions``: any text naming the issues in progress: the issue paths or
  ``<effort> <NN>`` pairs, one per line, or ``sessions.py`` output whose titles
  say ``<effort> <NN>`` or carry the file stem (``16-heat-pump-cop-...``).

Modes: ``--stages`` prints ``<effort>/<file>`` and a dashboard stage for every
issue (Done, In review, In progress, Ready, Blocked by NN, Needs triage, Needs
owner, Won't do); ``--held`` also lists the ready issues that are not on the
frontier, with the reason; ``--json`` prints the same data as JSON.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIERS = ("opus", "sonnet", "haiku")


def clean(text):
    return re.sub(r"[*`]", "", text).strip()


def header(lines, key):
    """The text after ``key:`` of the first matching line, markdown stripped."""
    for line in lines:
        m = re.match(rf"\**{key}\b\W*(.*)", line, re.I)
        if m:
            return clean(m.group(1))
    return None


def status_kind(text):
    """Map the free-text Status to (kind, pending). Kinds: done, ready,
    triage, owner, wontfix, claimed."""
    head = re.match(r"[\s:*`]*(?:status[\s:*`]*)?([a-z-]+)", text.lower())
    word = head.group(1) if head else ""
    if word in ("done", "resolved"):
        return "done", bool(re.search(r"pending", text, re.I))
    kinds = {
        "ready-for-agent": "ready",
        "needs-info": "owner",
        "ready-for-human": "owner",
        "wontfix": "wontfix",
        "claimed": "claimed",
    }
    return kinds.get(word, "triage"), False


def num_key(text):
    """``4`` -> ``04``, ``04b`` -> ``04b``: the number as written, zero-padded."""
    m = re.match(r"(\d+)([a-z]?)$", text, re.I)
    return f"{int(m.group(1)):02d}{m.group(2).lower()}"


def blockers_of(lines, status):
    """(refs, free text): per item of ``Blocked by:`` a leading issue number
    (``NN``, ``NN–MM``) or a tag such as ``RP-04`` (as ``tag:RP-04``), plus
    ``still blocked by NN`` in the Status line. Other items are free text."""
    nums, free = [], []
    raw = header(lines, "Blocked by") or ""
    if not re.match(r"(none|—|-|n/a)\b", raw, re.I) and raw:
        for item in re.split(r"[;,]|\bplus\b", raw):
            item = item.strip()
            m = re.match(r"(\d{1,3})([a-z]?)(?:\s*[–-]\s*(\d{1,3}))?(?![\w/#-])", item)
            if m and m.group(2) and m.group(3):
                free.append(item)
            elif m and m.group(2):
                nums.append(f"{int(m.group(1)):02d}{m.group(2)}")
            elif m:
                lo, hi = int(m.group(1)), int(m.group(3) or m.group(1))
                nums += [f"{n:02d}" for n in range(lo, hi + 1)]
            elif item and not re.match(r"(none|—)", item, re.I):
                tags = re.findall(r"\b[A-Za-z]{1,4}-\d{1,3}\b", item.split("(")[0])
                if tags and re.match(r"[A-Za-z]{1,4}-\d", item):
                    nums += [f"tag:{t.upper()}" for t in tags]
                else:
                    free.append(item)
    nums += [num_key(m) for m in re.findall(r"still blocked by (\d{1,3}[a-z]?)\b", status, re.I)]
    return sorted(set(nums)), free


def named_files(text):
    """Backticked path-like tokens in the issue body."""
    found = set()
    for tok in re.findall(r"`([^`\s]+)`", text):
        tok = re.sub(r":\d+(-\d+)?$", "", tok)
        if "/" in tok and not tok.startswith(("http", "..", "-")):
            found.add(tok)
    return found


def load_issues(root):
    issues = {}
    for path in sorted(root.glob(".scratch/*/issues/*.md")):
        m = re.match(r"(\d+[a-z]?)-", path.name)
        if not m:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()[:40]
        status = header(lines, "Status") or ""
        kind, pending = status_kind(status)
        nums, free = blockers_of(lines, status)
        title = next((l[2:].strip() for l in lines if l.startswith("# ")), path.stem)
        gated = (header(lines, "Gated") or "").lower().startswith("yes") or "gated" in status.lower()
        tier = header(lines, "Model tier") or ""
        tier = next((t for t in TIERS if t in (tier + " " + status).lower()), "?")
        issues[path.relative_to(root).as_posix()] = {
            "path": path.relative_to(root).as_posix(),
            "effort": path.parts[-3],
            "num": num_key(m.group(1)),
            "title": title,
            "kind": kind,
            "pending": pending,
            "tag": (re.match(r"([A-Za-z]{1,4}-\d+)\b", title) or [None, ""])[1].upper(),
            "blockers": nums,
            "free_blockers": free,
            "tier": tier,
            "gated": gated,
            "files": sorted(named_files(text)),
            "status": status[:80],
        }
    return issues


def read_prs(path):
    """PR number -> set of changed files, from ``<pr> <path>`` lines or JSON."""
    text = Path(path).read_text(encoding="utf-8")
    prs = {}
    try:
        for pr, files in json.loads(text).items():
            prs[str(pr).lstrip("#")] = set(files)
        return prs
    except (json.JSONDecodeError, AttributeError):
        pass
    for line in text.splitlines():
        m = re.match(r"\s*#?(\d+):?\s+(\S.*?)\s*$", line)
        if m:
            prs.setdefault(m.group(1), set()).update(m.group(2).split())
    return prs


def in_session(issue, text):
    stem = Path(issue["path"]).stem
    if stem in text or issue["path"] in text:
        return True
    pair = rf"{re.escape(issue['effort'])}[\s/#:._-]+0*{issue['num'].lstrip('0') or '0'}\b"
    return bool(re.search(pair, text, re.I))


def overlap(issue, prs):
    hits = {}
    for pr, files in prs.items():
        got = sorted(
            f
            for f in files
            for n in issue["files"]
            if f == n or f.startswith(n.rstrip("/") + "/")
        )
        if got:
            hits[pr] = got
    return hits


def evaluate(issues, prs, sessions_text):
    """Add ``stage``, ``held`` (reason or None) and ``overlap`` to each issue."""
    for it in issues.values():
        it["covered_by"] = sorted(p for p, f in prs.items() if it["path"] in f)
        it["in_session"] = bool(sessions_text) and in_session(it, sessions_text)
        it["overlap"] = overlap(it, prs)
    for it in issues.values():
        open_blockers = []
        for n in it["blockers"]:
            key = "tag" if n.startswith("tag:") else "num"
            dep = next(
                (
                    d
                    for d in issues.values()
                    if d["effort"] == it["effort"] and d[key] == n.removeprefix("tag:")
                ),
                None,
            )
            if dep is None or dep["kind"] != "done" or dep["pending"]:
                open_blockers.append(n.removeprefix("tag:"))
        it["open_blockers"] = open_blockers
        kind = it["kind"]
        if kind == "done":
            it["stage"] = "In review" if it["pending"] else "Done"
        elif kind == "wontfix":
            it["stage"] = "Won't do"
        elif it["in_session"] or kind == "claimed":
            it["stage"] = "In progress"
        elif it["covered_by"]:
            it["stage"] = "In review"
        elif kind == "ready" and open_blockers:
            it["stage"] = "Blocked by " + ", ".join(open_blockers)
        elif kind == "ready":
            it["stage"] = "Ready"
        elif kind == "owner":
            it["stage"] = "Needs owner"
        else:
            it["stage"] = "Needs triage"
        it["held"] = None
        if kind == "ready" and it["stage"] != "Ready":
            it["held"] = it["stage"]


def line(it):
    out = f"{it['path']}  {it['tier']}  {'gated' if it['gated'] else 'ungated'}  {it['title']}"
    if it["overlap"]:
        out += "  overlap " + "; ".join(
            f"#{pr}: {', '.join(f[:3])}{'…' if len(f) > 3 else ''}"
            for pr, f in sorted(it["overlap"].items())
        )
    if it["free_blockers"]:
        out += "  CHECK blocker text: " + "; ".join(it["free_blockers"])[:80]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=ROOT, help="repo root")
    ap.add_argument("--open-prs", help="file: '<pr> <changed path>' per line, or JSON")
    ap.add_argument("--sessions", help="file naming the issues in progress")
    ap.add_argument("--stages", action="store_true", help="print a stage per issue")
    ap.add_argument("--held", action="store_true", help="also list held ready issues")
    ap.add_argument("--json", action="store_true", help="print JSON")
    args = ap.parse_args(argv)

    issues = load_issues(args.root)
    prs = read_prs(args.open_prs) if args.open_prs else {}
    sessions = Path(args.sessions).read_text(encoding="utf-8") if args.sessions else ""
    evaluate(issues, prs, sessions)
    rows = list(issues.values())

    if args.stages:
        if args.json:
            print(json.dumps({r["path"]: r["stage"] for r in rows}, indent=1))
        else:
            for r in rows:
                print(f"{r['path']}\t{r['stage']}")
        return 0

    frontier = [r for r in rows if r["kind"] == "ready" and r["stage"] == "Ready"]
    held = [r for r in rows if r["held"]]
    if args.json:
        keep = ("path", "title", "tier", "gated", "overlap", "free_blockers", "files")
        data = {"frontier": [{k: r[k] for k in keep} for r in frontier]}
        data["held"] = [{"path": r["path"], "reason": r["held"]} for r in held]
        print(json.dumps(data, indent=1))
        return 0
    print(f"frontier {len(frontier)}")
    for r in frontier:
        print(line(r))
    if args.held:
        print(f"held {len(held)}")
        for r in held:
            print(f"{r['path']}  {r['held']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
