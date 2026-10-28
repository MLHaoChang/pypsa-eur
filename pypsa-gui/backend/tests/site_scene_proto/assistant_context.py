"""
D11: the bounded per-turn site context the companion will read in SC-07.

Frozen shape: a finite set of fields, each a short scalar or a closed code,
rendered as per-turn user content (harness/README.md contract 5: the system
prompt stays byte-identical). Never a mesh, footprint, coordinate list, media
id, filename or path. The block is capped at `MAX_BLOCK_BYTES`; anything that
would exceed it is cut by dropping whole action lines, never by truncating a
value mid-way, and the block says how many it dropped.

Today `harness/compose._format_ui_context` whitelists its keys, so a
`site_scene` key in ui_context reaches no provider (the journey test proves
it). SC-07 adds this block there; SC-00 N adds no harness code, no tool and no
tool offer change.

Tool tiers frozen for SC-07 (proposed, none registered here):
    site_scene_read      Safety: read     current verdicts/revision/counts
    site_scene_prepare   Safety: read     a native proposal preview; no write
    site_scene_apply     Safety: write    Apply of one exact proposal hash, card-confirmed
A scene approval authorizes only that scene revision: never a model write, a
solve, a worker or a media transfer.
"""
from __future__ import annotations

from typing import Any

MAX_BLOCK_BYTES = 1536
MAX_VALUE_CHARS = 80
FIELDS = ("project", "site", "revision", "anchor", "part", "entry", "buildings", "equipment", "targets")


def block(ctx: dict[str, Any], verdicts: list[dict[str, Any]]) -> str:
    """Render the block from finite fields and per-action verdict codes only."""
    unknown = set(ctx) - set(FIELDS)
    if unknown:
        raise ValueError(f"site context fields are closed; unexpected {sorted(unknown)}")
    for k, v in ctx.items():
        if v is not None and (not isinstance(v, (str, int, bool)) or len(str(v)) > MAX_VALUE_CHARS):
            raise ValueError(f"site context field {k!r} must be a scalar of at most {MAX_VALUE_CHARS} characters")
    head = ["[site 3d]"] + [f"{k}: {ctx[k]}" for k in FIELDS if ctx.get(k) is not None]
    lines = [f"- {v['action']}: {'available' if v['available'] else 'blocked ' + v['blocker']['code']}"
             for v in verdicts]
    while lines:
        text = "\n".join(head + lines)
        if len(text.encode("utf-8")) <= MAX_BLOCK_BYTES:
            return text
        lines.pop()
        head = [*head[:1], *[h for h in head[1:] if not h.startswith("omitted")]]
        head.append(f"omitted actions: {len(verdicts) - len(lines)}")
    text = "\n".join(head)
    if len(text.encode("utf-8")) > MAX_BLOCK_BYTES:
        raise ValueError("site context header exceeds its cap")
    return text
