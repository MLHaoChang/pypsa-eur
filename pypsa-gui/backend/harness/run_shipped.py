# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Shipped explore cases, admission only (study harness 14, S-B1; GK B (56)
C3-C5, C10). Nothing here runs, forks or solves: the engine is S-B2.

**Source (C10).** The cases are the PROJECT's own `cases.json` (a project copy
is editable, which is why the digest exists). The price series they name are
taken from the shipped TEMPLATE dir's `price_series/meta.json`, after
`template_series.verify` has checked every series file against it (S-A): an
unhashed, missing or changed series refuses before any case is admitted. The
digest binds exactly those two sources.

**Admission (C3, C4).** Every case in the file is shown, each with all its
changes; one outside the allow-list is `not_run` with its reason, never
dropped and never counted as run. The allow-list is structural, not a
wildcard over the commercial block (that block also holds the export link, the
connection and the timezone): the two price-reference ids, a contract's
reference price id, price, share and tenor, and the battery's two overnight
cost fields. A contract id and a battery column must exist in the built
template. `old` is compared at write time (S-B2), not here.

**Digest (C5).** sha256 of the whole file as read (version, template, base,
note and every case, run or not) plus the sha256 of each price series any
case names. `require_current` recomputes it and any difference refuses.

**Pick (C3, #1072).** `choose` returns the cases to run: all admissible ones
when they fit the cap, else only the ids the user picked; never the first N.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from services.library import template_series

CASES_FILE = "cases.json"
VERSION = 1
# What a case may change (checked against the built template, never a wildcard).
PRICE_REFS = ("export_price_ref", "import_price_ref")
CONTRACT_NUMBERS = ("price", "share", "tenor_years")
BESS_FIELDS = ("inv_power_overnight", "inv_energy_overnight")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_]*$")
_TYPES = ("string", "number")

ERROR_KINDS = (
    {"error_kind": "shipped_cases_missing"},
    {"error_kind": "shipped_cases_invalid"},
    {"error_kind": "shipped_cases_changed"},
    {"error_kind": "shipped_series_unverified"},
    {"error_kind": "shipped_cases_over_budget"},
    {"error_kind": "shipped_case_not_runnable"},
)


class ShippedError(ValueError):
    """A refusal; `kind` is its error kind."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _invalid(message: str):
    raise ShippedError("shipped_cases_invalid", message)


def _finite(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _read(project_dir) -> dict:
    path = Path(project_dir) / CASES_FILE
    if not path.is_file():
        raise ShippedError(
            "shipped_cases_missing", f"The project has no {CASES_FILE} to run."
        )
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ShippedError(
            "shipped_cases_invalid", f"{CASES_FILE} is not readable JSON: {exc}"
        ) from exc
    return data


def check(data, template_id: str) -> list[dict]:
    """The file's cases, each checked for shape (never for the allow-list):
    a typed refusal for anything malformed."""
    if not isinstance(data, dict):
        _invalid(f"{CASES_FILE} must be an object.")
    if data.get("version") != VERSION:
        _invalid(f"{CASES_FILE} must be version {VERSION}.")
    if data.get("template") != template_id:
        _invalid(f"{CASES_FILE} is for another template than {template_id}.")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        _invalid(f"{CASES_FILE} needs a non-empty list of cases.")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            _invalid("Every case must be an object.")
        cid = case.get("id")
        if not isinstance(cid, str) or not _ID.match(cid):
            _invalid("Every case needs an id of letters, digits and underscores.")
        if cid in seen:
            _invalid(f"Two cases share the id {cid}.")
        seen.add(cid)
        for key in ("label", "answers", "reason"):
            if not isinstance(case.get(key), str):
                _invalid(f"Case {cid} needs a text {key}.")
        changes = case.get("changes")
        if not isinstance(changes, list) or not changes:
            _invalid(f"Case {cid} needs a list of changes.")
        for change in changes:
            if not isinstance(change, dict):
                _invalid(f"Case {cid} has a change that is not an object.")
            if not isinstance(change.get("path"), str) or not change["path"]:
                _invalid(f"Case {cid} has a change with no path.")
            if change.get("value_type") not in _TYPES:
                _invalid(
                    f"Case {cid}: {change['path']} needs a value_type of string or number."
                )
            for side in ("old", "new"):
                if side not in change:
                    _invalid(f"Case {cid}: {change['path']} has no {side} value.")
    return cases


def _contract_ids(cfg) -> set[str]:
    contracts = ((cfg or {}).get("commercial") or {}).get("contracts") or []
    return {c.get("id") for c in contracts if isinstance(c, dict) and c.get("id")}


def refusal(change: dict, cfg, network) -> str | None:
    """Why one change is outside the allow-list, or None when it is allowed."""
    path, kind = change["path"], change["value_type"]
    parts = path.split(".")
    if parts[0] == "commercial":
        number = None
        if len(parts) == 3 and parts[1] in PRICE_REFS and parts[2] == "id":
            number = False
        elif (
            len(parts) == 5
            and parts[1] == "contracts"
            and parts[3:] == ["reference_price", "id"]
        ):
            number = False
        elif (
            len(parts) == 4 and parts[1] == "contracts" and parts[3] in CONTRACT_NUMBERS
        ):
            number = True
        if number is None:
            return (
                f"{path} is not a commercial field a case may change (only the price "
                "references and a contract's reference price, price, share and tenor)."
            )
        if len(parts) >= 4 and parts[2] not in _contract_ids(cfg):
            return f"{path} names a contract the template does not have."
    elif parts[0] == "StorageUnit":
        if len(parts) != 3 or parts[1] != "bess" or parts[2] not in BESS_FIELDS:
            return (
                f"{path} is not a battery field a case may change (only the two "
                "overnight cost fields of the battery)."
            )
        units = getattr(network, "storage_units", None)
        if units is None or "bess" not in units.index or parts[2] not in units.columns:
            return f"{path} is not on the template's battery."
        number = True
    else:
        return f"{path} is outside what a case may change here."
    if number != (kind == "number"):
        return f"{path} is a {'number' if number else 'string'} field, not a {kind}."
    if number and not (_finite(change["new"]) and _finite(change["old"])):
        return f"{path} needs finite numbers."
    if not number and not (
        isinstance(change["new"], str)
        and change["new"]
        and isinstance(change["old"], str)
        and change["old"]
    ):
        return f"{path} needs series names."
    return None


def _is_ref(path: str) -> bool:
    parts = path.split(".")
    return (len(parts) == 3 and parts[1] in PRICE_REFS and parts[2] == "id") or (
        len(parts) == 5 and parts[3:] == ["reference_price", "id"]
    )


def _series_hashes(template_dir) -> dict[str, str]:
    """Each template series name's sha256, after S-B1's source check: the series
    dir verified against its meta.json (S-A)."""
    try:
        template_series.verify(template_dir)
        meta = json.loads(
            (
                Path(template_dir) / template_series.DIR / template_series.META
            ).read_text()
        )
    except template_series.TemplateSeriesError as exc:
        raise ShippedError("shipped_series_unverified", str(exc)) from exc
    return {e["name"]: e["sha256"] for e in meta["series"]}


def _digest(data: dict, template_id: str, series: dict[str, Any]) -> str:
    body = {"v": 1, "template": template_id, "cases": data, "series": series}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def load(project_dir, template_id: str, *, template_dir, cfg, network) -> dict:
    """`{digest, total, cases, admissible}`: every case of the project's file with
    all its changes and its status (`admissible` or `not_run` with `why`)."""
    data = _read(project_dir)
    raw = check(data, template_id)
    hashes = _series_hashes(template_dir)
    named: dict[str, Any] = {}
    cases = []
    for case in raw:
        why: list[str] = []
        for change in case["changes"]:
            if _is_ref(change["path"]):
                for side in ("old", "new"):
                    if isinstance(change[side], str):
                        named[change[side]] = hashes.get(change[side])
                        if change[side] not in hashes:
                            why.append(
                                f"{change['path']} names the series {change[side]}, "
                                "which the template does not ship."
                            )
            reason = refusal(change, cfg, network)
            if reason:
                why.append(reason)
        cases.append(
            {
                "id": case["id"],
                "label": case["label"],
                "answers": case["answers"],
                "reason": case["reason"],
                "changes": [dict(c) for c in case["changes"]],
                "status": "not_run" if why else "admissible",
                "why": " ".join(why) or None,
            }
        )
    return {
        "digest": _digest(data, template_id, named),
        "total": len(cases),
        "cases": cases,
        "admissible": [c["id"] for c in cases if c["status"] == "admissible"],
    }


def require_current(approved: str, project_dir, template_id: str, **sources) -> dict:
    """The same load, refused when its digest is not the approved one: any edit of
    the project's cases.json, or of a series a case names, after approval."""
    shipped = load(project_dir, template_id, **sources)
    if shipped["digest"] != approved:
        raise ShippedError(
            "shipped_cases_changed",
            f"{CASES_FILE} or a price series it names changed after you approved it.",
        )
    return shipped


def choose(shipped: dict, cap: int, picked=None) -> list[str]:
    """The case ids to run. All admissible ones when they fit `cap`; more than that
    needs the user's pick, which must be admissible cases and at most `cap` of them.
    Never the first `cap`."""
    ok = shipped["admissible"]
    if picked is None:
        if len(ok) > cap:
            raise ShippedError(
                "shipped_cases_over_budget",
                f"{len(ok)} of {shipped['total']} cases can run but at most {cap} fit "
                "the solve budget: pick which to run.",
            )
        return list(ok)
    if not isinstance(picked, list) or any(not isinstance(p, str) for p in picked):
        raise ShippedError("shipped_case_not_runnable", "Pick cases by id.")
    ids = list(dict.fromkeys(picked))
    bad = [p for p in ids if p not in ok]
    if bad:
        raise ShippedError(
            "shipped_case_not_runnable", f"{', '.join(bad)} cannot run in this project."
        )
    if not ids or len(ids) > cap:
        raise ShippedError(
            "shipped_cases_over_budget",
            f"Pick between 1 and {cap} cases (of {shipped['total']}).",
        )
    return ids
