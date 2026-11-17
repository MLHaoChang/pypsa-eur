"""
SC-07 S6a: `site_scene_results`, the chat read of the SC-03 scene results
(`GET /api/projects/{name}/sites/{site_id}/scene/results`).

It runs what the route runs, in the route's order (GK B3 P2): the store lock for
the scene read, released, then the network lock for `open_network`,
`results.parse_query`, `results.gate` and `results.project`. Nothing is
computed here. `results.gate` alone decides whether a number is shown (P1):
out-of-date results carry no numbers, only the gate's reason.

What changes from the route's answer:
  * features and groups are named in the words the user sees
    (`chat.feature_words`), never by scene id, and listed in scene order;
  * `building` narrows the answer to one building or group by those words,
    exact and case-insensitive; an unknown or ambiguous word is refused (P3);
  * every user-authored string is capped (G5), whole items are cut to
    `MAX_ITEMS` and the whole answer to `MAX_READ_BYTES`, and each cut is named
    in `truncated` (P4). A leaf's reason is never cut.
"""

from __future__ import annotations

import json
from typing import Any

from services.site_scene import chat
from services.site_scene.contract import SceneError

SIGNS = (
    "Load and Link ports count withdrawal as positive; Generator, Store and StorageUnit "
    "count injection as positive. A Link port marked reversed runs against its role."
)

# The gate's reasons, as the answer words them. A reason not listed is named by its code only.
RUN_WORDS = {
    None: "From the current solve.",
    "project_not_open": "The project is not open, so there are no results.",
    "run_not_solved": "Not solved yet, so there are no results.",
    "run_running": "A solve is running; results show when it finishes.",
    "run_cancelled": "The last solve was cancelled, so there are no results.",
    "run_failed": "The last solve did not finish with a result.",
    "results_not_current": "Out of date: the inputs changed after the solve. Solve again for numbers.",
    "run_changed": "The run asked for is not the current one.",
}

DATA_FIELDS = [
    "project",
    "building",
    "features[].feature",
    "features[].targets[].name",
    "features[].targets[].ports[].bus",
    "features[].targets[].ports[].carrier",
    "features[].targets[].allocation.features[]",
    "groups[].group",
    "groups[].boundary[].bus",
    "groups[].boundary[].carrier",
    "groups[].totals[].carrier",
    "groups[].internal_buses[]",
    "groups[].unavailable_member",
]

_LISTS = ("features", "groups")


def _fence(value: Any) -> Any:
    """Cap every string in the answer (G5). Codes and units are short, so only user text is cut."""
    if isinstance(value, str):
        return chat._text(value)
    if isinstance(value, dict):
        return {k: _fence(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_fence(v) for v in value]
    return value


def _cut(items: list[Any], field: str, truncated: list[dict[str, Any]]) -> list[Any]:
    """Whole items up to MAX_ITEMS (read at call time, so a test can lower it), the cut named."""
    limit = chat.MAX_ITEMS
    if len(items) > limit:
        truncated.append({"field": field, "shown": limit, "total": len(items)})
        return items[:limit]
    return items


def _match(words: dict[str, str], ids: list[str], asked: str) -> str:
    """The one building or group whose words are `asked` (case-insensitive), else invalid_input (P3)."""
    want = " ".join(asked.split()).casefold()
    hits = [i for i in ids if words.get(i, "").casefold() == want]
    if len(hits) == 1:
        return hits[0]
    numbered = [
        words[i] for i in ids if words.get(i, "").casefold().startswith(want + " ")
    ]
    if numbered:
        raise SceneError(
            "invalid_input",
            f"Several items are called that: name one of {', '.join(numbered[: chat.MAX_CANDIDATES])}",
        )
    raise SceneError(
        "invalid_input", "No building or group on this site is called that"
    )


def _target(t: dict[str, Any], words: dict[str, str]) -> dict[str, Any]:
    out = {k: v for k, v in t.items() if k != "uid"}  # a model uid is an id, not a word
    alloc = out.get("allocation")
    if isinstance(alloc, dict) and isinstance(alloc.get("features"), list):
        out["allocation"] = {
            **alloc,
            "features": [chat.feature_word(words, f) for f in alloc["features"]],
        }
    return out


def _fit(out: dict[str, Any]) -> dict[str, Any]:
    """Halve the longer of features and groups until the answer fits MAX_READ_BYTES, naming the cut."""

    def size() -> int:
        return len(json.dumps(out, default=str).encode("utf-8"))

    while size() > chat.MAX_READ_BYTES:
        longest = max(_LISTS, key=lambda k: len(out[k]))
        items = out[longest]
        if not items:
            break
        keep = len(items) // 2
        total = out["counts"][longest]
        out["truncated"] = [t for t in out["truncated"] if t["field"] != longest] + [
            {"field": longest, "shown": keep, "total": total}
        ]
        out[longest] = items[:keep]
    return out


def read(
    site_id: str,
    snapshot: str | None = None,
    period: str | None = None,
    window_from: str | None = None,
    window_to: str | None = None,
    run: str | None = None,
    building: str | None = None,
) -> dict[str, Any]:
    """`site_scene_results`: the scene results at one exact snapshot, bounded and in words."""
    from services import site_bindings
    from services.pypsa_service import PyPSAService
    from services.site_scene import results, store

    with chat.scope() as (_db, _user, project, _owner):
        site_id = chat._site_known(project, site_id)
        with store.project_lock(project.directory):
            scene = store.read_scene(project.directory, site_id)
        if scene["state"] != "enabled":
            raise SceneError(
                "not_found", "This site has no 3D scene yet: create it first"
            )
        with PyPSAService.get_lock():
            n = site_bindings.open_network(project.directory)
            query = (
                None
                if n is None
                else results.parse_query(
                    n,
                    snapshot=snapshot,
                    period=period,
                    window_from=window_from,
                    window_to=window_to,
                )
            )
            verdict = results.gate(PyPSAService.get_active_context(), n, run)
            payload = results.project(scene, n, gate=verdict, query=query)
            words = chat.feature_words(scene, n)

        order = [
            f["id"]
            for f in scene.get("buildings") or []
            if isinstance(f, dict) and f.get("id")
        ]
        group_ids = [
            g["id"]
            for g in scene.get("groups") or []
            if isinstance(g, dict) and g.get("id")
        ]
        feature_ids = order + [f for f in payload["features"] if f not in order]
        group_order = [g for g in group_ids if g in payload["groups"]]

        asked = None
        if building is not None:
            if not isinstance(building, str) or not building.strip():
                raise SceneError(
                    "invalid_input", "Name a building or group, or leave it out"
                )
            asked = _match(words, order + group_ids, building)
            groups = {
                g["id"]: g for g in scene.get("groups") or [] if isinstance(g, dict)
            }
            if asked in groups:
                members = results._members(groups, asked)
                feature_ids = [f for f in feature_ids if f in members]
                group_order = [asked]
            else:
                feature_ids = [f for f in feature_ids if f == asked]
                group_order = [
                    g for g in group_order if asked in results._members(groups, g)
                ]

        truncated: list[dict[str, Any]] = []
        features = []
        for fid in feature_ids:
            entry = payload["features"].get(fid)
            if entry is None:
                continue
            targets = [_target(t, words) for t in entry["targets"]]
            features.append(
                {
                    "feature": chat.feature_word(words, fid),
                    "targets": _cut(targets, "features[].targets", truncated),
                }
            )
        groups_out = []
        for gid in group_order:
            g = dict(payload["groups"][gid])
            g["boundary"] = _cut(list(g["boundary"]), "groups[].boundary", truncated)
            groups_out.append({"group": chat.feature_word(words, gid), **g})

        reason = payload["run"]["reason"]
        out: dict[str, Any] = {
            "version": 1,
            "project": project.name,
            "site_id": site_id,
            "scene_revision": scene["revision"],
            "run": payload["run"],
            "stale": reason == "results_not_current",
            "snapshot": payload["snapshot"],
            "window": payload["window"],
            "building": chat.feature_word(words, asked) if asked else None,
            "counts": {"features": len(features), "groups": len(groups_out)},
            "features": _cut(features, "features", truncated),
            "groups": _cut(groups_out, "groups", truncated),
            "truncated": truncated,
            "data_fields": DATA_FIELDS,
            "limits": {
                "max_items": chat.MAX_ITEMS,
                "max_bytes": chat.MAX_READ_BYTES,
                "max_name_chars": chat.MAX_NAME_CHARS,
            },
        }
        out = _fence(
            out
        )  # fixed wording below is ours, not user text, so it is added after the cap
        out["run_words"] = RUN_WORDS.get(reason, f"No results ({reason}).")
        out["signs"] = SIGNS
        return _fit(out)
