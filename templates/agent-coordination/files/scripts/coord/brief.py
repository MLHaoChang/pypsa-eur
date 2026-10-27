# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""
Fill ``docs/agents/session-brief.md`` from flags and write the brief to a file.

    python3 scripts/coord/brief.py --task-file task.md --decisions USE-2,OWN-PR \\
        --read "path: why" --review "none required" --stream dc \\
        --open-prs prs.txt --sessions sessions.txt --out brief.md

The first fenced block of the template is the brief; the paragraphs labelled
Task, Owner decisions, Read first, Stay out of, Dependencies and Review are
fields, filled from their flags (an unset field reads "none"). The other
paragraphs (Context budget, Process) are kept as they are. ``--stream NAME`` appends the fenced block under the
template's ``## Stream NAME`` heading. "Stay out of" is built from the open PRs'
changed files (``--open-prs``, the file ``frontier.py`` takes) and the live
sessions (``--sessions``, ``sessions.py`` output or issue paths), plus
``--stay-out`` text. ``--model``, ``--title`` and ``--team`` are only echoed back
as the ``create_session`` arguments to pass.
"""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frontier import read_prs  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "docs" / "agents" / "session-brief.md"
FENCE = re.compile(r"```markdown\n(.*?)\n```", re.S)


def template_blocks(text):
    """(brief block, {stream name: block}) from the template document."""
    brief = FENCE.search(text).group(1)
    streams = {}
    for m in re.finditer(r"^## Stream (\S+)\n(.*?)(?=^## |\Z)", text, re.S | re.M):
        block = FENCE.search(m.group(2))
        if block:
            streams[m.group(1).lower()] = block.group(1)
    return brief, streams


def area(path):
    """The directory a changed file sits in, at most three levels deep."""
    parts = path.split("/")[:-1][:3]
    return "/".join(parts) + "/" if parts else path


def stay_out(prs, session_lines, extra):
    out = []
    for pr in sorted(prs, key=int):
        counts = Counter(area(f) for f in prs[pr])
        top = ", ".join(f"`{a}` ({n})" for a, n in counts.most_common(5))
        more = f", +{len(counts) - 5} more" if len(counts) > 5 else ""
        out.append(f"- open PR #{pr}: {top}{more}")
    for text in session_lines:
        out.append(f"- live session: {text[:110]}")
    if extra:
        out.append(f"- {extra}")
    return "\n".join(out) if out else "none"


def fill(block, values):
    """Replace the body of each field paragraph (its label is a key of ``values``)."""
    paragraphs = []
    for para in block.split("\n\n"):
        m = re.match(r"\*\*([^*]+?)\.\*\*", para)
        if m and m.group(1) in values:
            value = values.get(m.group(1), "none") or "none"
            sep = "\n" if value.startswith("- ") else " "
            para = f"**{m.group(1)}.**{sep}{value}"
        paragraphs.append(para)
    return "\n\n".join(paragraphs)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    task = ap.add_mutually_exclusive_group(required=True)
    task.add_argument("--task", help="task text")
    task.add_argument("--task-file", type=Path, help="file holding the task text")
    ap.add_argument("--decisions", default="", help="comma-separated owner decision keys")
    ap.add_argument("--read", action="append", default=[], help="'path: why', repeatable")
    ap.add_argument("--dependencies", default="none")
    ap.add_argument("--review", default="none required")
    ap.add_argument("--stream", default="", help="stream name, e.g. dc")
    ap.add_argument("--open-prs", help="file: '<pr> <changed path>' per line, or JSON")
    ap.add_argument("--exclude-pr", action="append", default=[], help="PR number to leave out")
    ap.add_argument("--sessions", help="file: live sessions, one per line")
    ap.add_argument("--stay-out", default="", help="extra text for Stay out of")
    ap.add_argument("--sha", default="", help="master SHA the session starts from")
    ap.add_argument("--model", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--team", default="build")
    ap.add_argument("--template", type=Path, default=TEMPLATE)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    task = args.task if args.task is not None else args.task_file.read_text().strip()
    prs = read_prs(args.open_prs) if args.open_prs else {}
    for n in args.exclude_pr:
        prs.pop(n.lstrip("#"), None)
    live = []
    if args.sessions:
        live = [
            l.strip()
            for l in Path(args.sessions).read_text().splitlines()
            if l.strip() and "archived" not in l.lower()
        ]
    keys = [k.strip() for k in args.decisions.split(",") if k.strip()]
    if args.sha:
        task += f"\n\nStarts from master at `{args.sha}`."
    values = {
        "Task": task,
        "Owner decisions": ", ".join(f"`{k}`" for k in keys),
        "Read first": "\n".join(f"- {r}" for r in args.read),
        "Stay out of": stay_out(prs, live, args.stay_out),
        "Dependencies": args.dependencies,
        "Review": args.review,
    }
    brief, streams = template_blocks(args.template.read_text())
    text = fill(brief, values)
    if args.stream:
        if args.stream.lower() not in streams:
            ap.error(f"template has no '## Stream {args.stream}' block")
        text += "\n\n" + streams[args.stream.lower()]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text + "\n")
    tags = f"team:{args.team}"
    print(f"wrote {args.out} ({len(text.split())} words)")
    print(f"create_session: model={args.model or '?'} title={args.title or '?'} tags=[{tags}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
