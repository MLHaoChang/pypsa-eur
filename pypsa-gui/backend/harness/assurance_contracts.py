"""Frozen version-1 assurance contracts (Assurance P0, A0.4).

One canonical document holds every server-owned contract a checked answer or
effect depends on: the request-scope schema, the profile-to-metric registry and
metric definitions, the observed and computed draft schemas, the effect-guard
schema and the input-schema hash of each checked tool. `assurance_contracts.v1.json`
is the reviewed snapshot; `tests/test_assurance_contracts.py` fails CI when the
live contracts drift from it.

Changing a contract (owner decision AA-V1-CHANGE-1): additive changes stay
in version 1, so a new profile, metric, optional field, enum value or wider bound
is allowed with a reviewed snapshot update. Removing or narrowing anything, or
changing an existing profile, metric definition or checked-tool schema, needs a
new profile version (``x/2``) beside the old one, or a protocol version bump.
``--write`` refuses a non-additive snapshot update.

    python -m harness.assurance_contracts --check
    python -m harness.assurance_contracts --write
"""

from __future__ import annotations

import argparse
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
    "evidence_and_receipt_identity": "02: evidence and receipt identity fields",
    "independent_domain_approval": "10: per-metric domain review; formulas here are editing-agent reviewed only",
}

# JSON-schema keywords whose value may only widen in an additive change.
_GROW = {"maxLength", "maximum", "maxItems", "exclusiveMaximum"}
_SHRINK = {"minLength", "minimum", "minItems", "exclusiveMinimum"}
_OPEN_MAPS = {"properties", "$defs", "profiles", "checked_tools"}
_NOTES = {"title", "description", "default"}


def _schema_sha256(schema) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()


def build() -> dict:
    """Assemble the live contract document; raise when its parts disagree."""
    from typing import get_args

    from harness import calculations
    from harness.assurance import CHECKED_TOOLS, Draft, ProfileDraft
    from harness.assurance_scope import RequestScope
    from harness.catalogue import TOOLS
    from harness.effects import EffectContract

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
    document = {
        "version": VERSION,
        "request_scope": RequestScope.model_json_schema(),
        "observed_draft": Draft.model_json_schema(),
        "computed_draft": ProfileDraft.model_json_schema(),
        "effect_guard": EffectContract.model_json_schema(),
        "profiles": profiles,
        "checked_tools": {
            name: _schema_sha256(tools[name]["input_schema"])
            for name in sorted(CHECKED_TOOLS)
        },
        "pending": PENDING,
    }
    # Normalise tuples and key order to the JSON form the snapshot stores.
    return json.loads(json.dumps(document, sort_keys=True))


def breaking_changes(old, new, path="$") -> list[str]:
    """Every place `new` removes, narrows or changes what `old` promised."""
    if isinstance(old, dict) and isinstance(new, dict):
        found = []
        for key, value in old.items():
            where = f"{path}.{key}"
            if key in _NOTES or where == "$.pending":
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
            else:
                found += breaking_changes(value, new[key], where)
        if "required" in new and "required" not in old:
            found += [
                f"{path}.required adds required {name}" for name in new["required"]
            ]
        if path != "$" and path.rsplit(".", 1)[-1] not in _OPEN_MAPS:
            found += [
                f"{path}.{key} added constraint"
                for key in new
                if key not in old and key not in _NOTES and key != "required"
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
