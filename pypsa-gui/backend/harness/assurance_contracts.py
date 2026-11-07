"""Frozen version-1 assurance contracts (Assurance P0, A0.4).

One canonical document holds every server-owned contract a checked answer or
effect depends on: the request-scope schema, the profile-to-metric registry and
metric definitions, the observed and computed draft schemas, the effect-guard
schema and the input-schema hash of each checked tool, plus the returned shapes
in `harness.assurance_shapes`: public evidence identity per adapter version, the
answer receipt per policy version and each checked tool's result. `assurance_contracts.v1.json`
is the reviewed snapshot; `tests/test_assurance_contracts.py` fails CI when the
live contracts drift from it.

Changing a contract (owner decision AA-V1-CHANGE-1): additive changes stay
in version 1. For a contract the server accepts, an optional field, enum value
or wider bound is additive. For a contract the server returns, only a new entry
(a new profile, checked tool, evidence adapter version or receipt policy
version) is additive. Each needs a reviewed snapshot update. Removing or narrowing anything, or
changing an existing profile, metric definition or checked-tool schema, needs a
new profile version (``x/2``) beside the old one, or a protocol version bump.
``--write`` refuses a non-additive snapshot update.

A checked tool's input is a contract the server accepts, so its hash in
``checked_tools`` is direction-aware: ``checked_tool_inputs`` stores each input
schema, and a moved hash is additive when that schema only widened (a new enum
member, an optional property, a wider bound). A narrowing, a removal or a
changed ``default`` still breaks.

    python -m harness.assurance_contracts --check
    python -m harness.assurance_contracts --write
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

VERSION = 1
SNAPSHOT = Path(__file__).with_name("assurance_contracts.v1.json")

# Contracts the reliability tickets still owe; listed so no reader mistakes the
# frozen set for complete coverage. Each names its owning ticket.
PENDING = {
    "arbitrary_request_scope": "09: natural-language scope beyond the bounded profile grammar",
    "action_and_proposal_blocks": "09-11: draft block kinds beyond observed/computed",
    "general_effect_guards": "11: guard/postcondition schemas for effectful tools other than update_component",
    "artifact_evidence_identity": "05: identity of asset and artifact evidence (issue 02 A1.1 blocker)",
    "checked_chart_shape": "05: the receipt's chart object; answer_receipt freezes only its presence",
    "independent_domain_approval": "10: per-metric domain review; formulas here are editing-agent reviewed only",
}

# Which way each contract flows. The server validates an `accepts` contract, so
# accepting more is additive. A consumer reads a `returns` contract, so every
# existing entry is frozen and only new entries (a new profile or checked tool)
# are additive.
DIRECTIONS = {
    "request_scope": "accepts",
    "observed_draft": "accepts",
    "computed_draft": "accepts",
    "effect_guard": "accepts",
    "profiles": "returns",
    "checked_tools": "returns",
    "evidence_identity": "returns",
    "answer_receipt": "returns",
    "checked_tool_results": "returns",
    "checked_tool_input_additions": "accepts",
    "checked_tool_inputs": "accepts",
}

# Enum members a checked tool's input accepted after v1 froze its hash
# (AA-V1-CHANGE-1: accepting more is additive). `checked_tools` hashes the
# input schema without them, so the frozen hash holds; the list itself is an
# `accepts` contract, so dropping a member later breaks. Only enum members of
# top-level properties may be listed. Frozen as the record of #1194: since
# G2P-21 a widening moves the hash and `checked_tool_inputs` shows it is
# additive, so new widenings need no entry here.
V1_INPUT_ADDITIONS: dict[str, dict[str, list[str]]] = {
    "ui_open_panel": {"results_tab": ["grid_risk"]},  # G2P-13 (#1194)
}
_META = {"version", "pending", "directions"}

# JSON-schema keywords whose value may only widen in an additive change.
_GROW = {"maxLength", "maximum", "maxItems", "exclusiveMaximum"}
_SHRINK = {"minLength", "minimum", "minItems", "exclusiveMinimum"}
_OPEN_MAPS = {"properties", "$defs"}
_NOTES = {"title", "description", "default"}
# A checked tool's `default` decides what a call omitting the argument does,
# so inside `checked_tool_inputs` it is a contract, not a note.
_INPUT_NOTES = {"title", "description"}


def _schema_sha256(schema) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()


def _v1_input_schema(name: str, schema: dict, additions: dict) -> dict:
    """`schema` without the enum members `additions` lists; raise on a stale entry."""
    schema = copy.deepcopy(schema)
    for field, members in additions.items():
        prop = schema.get("properties", {}).get(field)
        if not isinstance(prop, dict) or not isinstance(prop.get("enum"), list):
            raise ValueError(f"V1_INPUT_ADDITIONS: {name}.{field} is not an enum input")
        absent = [m for m in members if m not in prop["enum"]]
        if absent:
            raise ValueError(
                f"V1_INPUT_ADDITIONS: {name}.{field} lacks {', '.join(map(str, absent))}"
            )
        prop["enum"] = [v for v in prop["enum"] if v not in members]
    return schema


def build() -> dict:
    """Assemble the live contract document; raise when its parts disagree."""
    from typing import get_args

    from harness import calculations
    from harness import assurance_shapes as shapes
    from harness.assurance import CHECKED_TOOLS, Draft, ProfileDraft
    from harness.assurance_scope import RequestScope
    from harness.catalogue import TOOLS
    from harness.effects import EffectContract
    from services.assurance_records import INPUT_ADAPTERS, POLICY_VERSION

    scope_profiles = set(get_args(RequestScope.model_fields["profile"].annotation))
    if scope_profiles != set(calculations.PROFILES):
        raise ValueError(
            "RequestScope.profile and calculations.PROFILES differ: "
            + ", ".join(sorted(scope_profiles ^ set(calculations.PROFILES)))
        )
    profiles = {}
    for profile, metrics in sorted(calculations.PROFILES.items()):
        definitions = {m: calculations.definition(m, profile) for m in metrics}
        unsupported = sorted(
            m
            for m, d in definitions.items()
            if d.get("formula_id") in (None, "unsupported")
        )
        if unsupported:
            raise ValueError(
                f"{profile} names metrics without a formula: {', '.join(unsupported)}"
            )
        profiles[profile] = {"metrics": list(metrics), "definitions": definitions}
    tools = {t["name"]: t for t in TOOLS}
    missing = sorted(CHECKED_TOOLS - set(tools))
    if missing:
        raise ValueError(
            "Checked tools absent from the catalogue: " + ", ".join(missing)
        )
    stray = sorted(set(V1_INPUT_ADDITIONS) - CHECKED_TOOLS)
    if stray:
        raise ValueError(
            "V1_INPUT_ADDITIONS names unchecked tools: " + ", ".join(stray)
        )
    unshaped = sorted(
        (set(INPUT_ADAPTERS.values()) - set(shapes.EVIDENCE))
        | (CHECKED_TOOLS ^ set(shapes.TOOL_RESULTS))
        | ({POLICY_VERSION} - set(shapes.RECEIPTS))
    )
    if unshaped:
        raise ValueError("Returned shapes missing or stale: " + ", ".join(unshaped))
    document = {
        "version": VERSION,
        "request_scope": RequestScope.model_json_schema(),
        "observed_draft": Draft.model_json_schema(),
        "computed_draft": ProfileDraft.model_json_schema(),
        "effect_guard": EffectContract.model_json_schema(),
        "profiles": profiles,
        "checked_tools": {
            name: _schema_sha256(
                _v1_input_schema(
                    name, tools[name]["input_schema"], V1_INPUT_ADDITIONS.get(name, {})
                )
            )
            for name in sorted(CHECKED_TOOLS)
        },
        "checked_tool_inputs": {
            name: tools[name]["input_schema"] for name in sorted(CHECKED_TOOLS)
        },
        "checked_tool_input_additions": {
            name: {field: {"enum": list(members)} for field, members in fields.items()}
            for name, fields in sorted(V1_INPUT_ADDITIONS.items())
        },
        "evidence_identity": {
            key: shapes.schema(model) for key, model in shapes.EVIDENCE.items()
        },
        "answer_receipt": {
            key: shapes.schema(model) for key, model in shapes.RECEIPTS.items()
        },
        "checked_tool_results": {
            name: shapes.schema(models) for name, models in shapes.TOOL_RESULTS.items()
        },
        "pending": PENDING,
        "directions": DIRECTIONS,
    }
    undeclared = sorted(set(document) - _META - set(DIRECTIONS))
    if undeclared:
        raise ValueError("Contracts without a direction: " + ", ".join(undeclared))
    # Normalise tuples and key order to the JSON form the snapshot stores.
    return json.loads(json.dumps(document, sort_keys=True))


def breaking_changes(old: dict, new: dict) -> list[str]:
    """Every place `new` breaks what the frozen document `old` promised."""
    found = []
    directions = old.get("directions", {})
    for key, value in old.items():
        where = f"$.{key}"
        if key in _META:
            continue
        if key not in new:
            found.append(f"{where} removed")
        elif directions.get(key) != new.get("directions", {}).get(key):
            found.append(f"{where} direction changed")
        elif key == "checked_tool_inputs":
            # Keys are tool names and values whole input schemas.
            found += _accepts_breaks(
                value, new[key], where, notes=_INPUT_NOTES, open_map=True
            )
        elif directions.get(key) == "accepts":
            found += _accepts_breaks(value, new[key], where)
        elif key == "checked_tools":
            found += [
                b
                for b in _returns_breaks(value, new[key], where)
                if not _widened_input(old, new, b)
            ]
        else:
            found += _returns_breaks(value, new[key], where)
    return found


def _input_hash_matches(document: dict, name: str) -> bool:
    """Whether `document` hashes `name`'s stored input schema to its listed hash."""
    schema = document.get("checked_tool_inputs", {}).get(name)
    if not isinstance(schema, dict):
        return False
    additions = {
        field: spec.get("enum", [])
        for field, spec in document.get("checked_tool_input_additions", {})
        .get(name, {})
        .items()
    }
    try:
        sha = _schema_sha256(_v1_input_schema(name, schema, additions))
    except (ValueError, AttributeError, TypeError):
        return False
    return document.get("checked_tools", {}).get(name) == sha


def _widened_input(old: dict, new: dict, broken: str) -> bool:
    """A moved checked-tool hash that `checked_tool_inputs` accounts for.

    Only `changed` qualifies, and only when both documents store the tool's
    input schema and hash it to their listed hash; whether that schema change
    is additive is then judged on `checked_tool_inputs`. Fails closed.
    """
    prefix, suffix = "$.checked_tools.", " changed"
    if not (broken.startswith(prefix) and broken.endswith(suffix)):
        return False
    name = broken[len(prefix) : -len(suffix)]
    return _input_hash_matches(old, name) and _input_hash_matches(new, name)


def _returns_breaks(old: dict, new: dict, path: str) -> list[str]:
    """A consumer reads every existing entry, so any change to one breaks it."""
    found = [f"{path}.{key} removed" for key in old if key not in new]
    return found + [
        f"{path}.{key} changed" for key in old if key in new and old[key] != new[key]
    ]


def _accepts_breaks(old, new, path, notes=_NOTES, open_map=False) -> list[str]:
    """Every place `new` accepts less than `old` did.

    In an open map (`properties`, `$defs`, or a map of tool names) every key is
    a name, never a keyword: an existing entry may not go and its value is
    compared as a schema, while a new entry is additive.
    """
    if isinstance(old, dict) and isinstance(new, dict) and open_map:
        found = []
        for key, value in old.items():
            if key not in new:
                found.append(f"{path}.{key} removed")
            else:
                found += _accepts_breaks(value, new[key], f"{path}.{key}", notes)
        return found
    if isinstance(old, dict) and isinstance(new, dict):
        found = []
        for key, value in old.items():
            where = f"{path}.{key}"
            if key in notes:
                continue
            if key not in new:
                found.append(f"{where} removed")
            elif key == "required" and isinstance(value, list):
                added = sorted(set(new[key]) - set(value))
                found += [f"{where} adds required {name}" for name in added]
            elif key == "enum" and isinstance(value, list):
                gone = [v for v in value if v not in new[key]]
                found += [f"{where} drops {v!r}" for v in gone]
            elif key in _GROW and isinstance(value, (int, float)):
                if not isinstance(new[key], (int, float)) or new[key] < value:
                    found.append(f"{where} narrowed")
            elif key in _SHRINK and isinstance(value, (int, float)):
                if not isinstance(new[key], (int, float)) or new[key] > value:
                    found.append(f"{where} narrowed")
            elif key == "default":
                if new[key] != value:
                    found.append(f"{where} changed")
            else:
                found += _accepts_breaks(
                    value, new[key], where, notes, open_map=key in _OPEN_MAPS
                )
        if "required" in new and "required" not in old:
            found += [
                f"{path}.required adds required {name}" for name in new["required"]
            ]
        found += [
            f"{path}.{key} added " + ("default" if key == "default" else "constraint")
            for key in new
            if key not in old and key not in notes and key != "required"
        ]
        return found
    if old != new:
        return [f"{path} changed"]
    return []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check",
        action="store_true",
        help="Fail when live contracts differ from the snapshot.",
    )
    mode.add_argument(
        "--write",
        action="store_true",
        help="Rewrite the snapshot after an additive change.",
    )
    args = parser.parse_args(argv)
    live = build()
    frozen = json.loads(SNAPSHOT.read_text()) if SNAPSHOT.exists() else None
    if args.check:
        if live != frozen:
            print(
                f"Assurance contracts differ from {SNAPSHOT.name}; review the change and run --write."
            )
            return 1
        print(
            f"Assurance contracts match {SNAPSHOT.name}: {len(live['profiles'])} profiles, "
            f"{len(live['checked_tools'])} checked tools."
        )
        return 0
    if frozen is not None and frozen.get("version") == live["version"]:
        broken = breaking_changes(frozen, live)
        if broken:
            print(
                "Not additive; add a new profile version or bump the protocol version:"
            )
            print("\n".join(f"  {b}" for b in broken[:40]))
            return 1
    SNAPSHOT.write_text(json.dumps(live, indent=1, sort_keys=True) + "\n")
    print(f"Wrote {SNAPSHOT.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
