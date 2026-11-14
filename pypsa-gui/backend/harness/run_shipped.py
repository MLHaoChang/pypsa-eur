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
digest binds those two sources and the Library refs (version, hash) the
project's own solver_config holds for each series a case names (a created
project prices from minted refs, not the template files).

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
    {"error_kind": "shipped_case_stale"},
    {"error_kind": "shipped_series_unresolved"},
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
    commercial = (cfg or {}).get("commercial")
    contracts = commercial.get("contracts") if isinstance(commercial, dict) else None
    if not isinstance(contracts, list):
        return set()
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


def _dispatch_contract_ids(cfg) -> set[str] | None:
    """The ids of the contracts whose price, share and tenor enter the LP (a
    dispatch PPA), read from the project's own commercial block; None when it
    does not validate (then nothing about it is known)."""
    from models.commercial import CommercialConfig
    from services.commercial import lp_bindings

    try:
        parsed = CommercialConfig.model_validate((cfg or {}).get("commercial"))
    except Exception:  # noqa: BLE001 -- any invalid block is "unknown"
        return None
    return {c.id for c in lp_bindings.dispatch_ppas(parsed)}


LP, FINANCE, UNKNOWN = "lp", "finance", "unknown"


def classify(change: dict, cfg) -> str:
    """Whether a change moves the LP (`lp`), only the settlement on the solved
    dispatch (`finance`), or is not known (`unknown`) (GK B D5). The price
    references enter the LP as the links' `ic_export_price` / `ic_import_price`;
    a battery cost is an LP cost term; a contract's reference price only settles;
    a contract's price, share and tenor enter the LP only for a dispatch PPA
    (`lp_bindings.dispatch_ppas`), decided from the project's own contract."""
    parts = change["path"].split(".")
    if parts[0] == "StorageUnit":
        return LP
    if parts[0] != "commercial":
        return UNKNOWN
    if len(parts) == 3 and parts[1] in PRICE_REFS:
        return LP
    if len(parts) == 5 and parts[3] == "reference_price":
        return FINANCE
    if len(parts) == 4 and parts[1] == "contracts":
        dispatch = _dispatch_contract_ids(cfg)
        if dispatch is None:
            return UNKNOWN
        return LP if parts[2] in dispatch else FINANCE
    return UNKNOWN


def case_kind(changes: list[dict], cfg) -> str:
    """`lp` when any change moves the LP, else `unknown` when any is unknown,
    else `finance`."""
    kinds = {classify(c, cfg) for c in changes}
    if LP in kinds:
        return LP
    return UNKNOWN if UNKNOWN in kinds else FINANCE


def _stale(path: str, want, found):
    raise ShippedError(
        "shipped_case_stale",
        f"{path} is {found} now, not {want} as the case expects; nothing was changed.",
    )


def apply(cfg: dict, changes: list[dict], resolve_ref, *, check_old: bool = True):
    """`(patched solver_config, battery changes)`: a copy of `cfg` with the
    commercial changes applied, the `old` value of each compared with what `cfg`
    holds now (the caller reads `cfg` and writes the result in one critical
    section, GK B D3) and the commercial block validated like a user edit (D2).
    A series name maps to the project's ref through `resolve_ref(name)` (a ref
    dict, or None: `shipped_series_unresolved`). The battery changes are not
    solver_config: they come back untouched for the caller to write to the
    network. Never mutates `cfg`."""
    from models.commercial import CommercialConfig

    out = json.loads(json.dumps(cfg))
    commercial = out.get("commercial")
    if not isinstance(commercial, dict):
        raise ShippedError(
            "shipped_case_not_runnable", "The project has no commercial block."
        )
    network = []
    for change in changes:
        parts = change["path"].split(".")
        if parts[0] != "commercial":
            network.append(dict(change))
            continue
        if len(parts) == 3:
            holder, field = commercial, parts[1]
        else:
            hits = [
                c
                for c in commercial.get("contracts") or []
                if isinstance(c, dict) and c.get("id") == parts[2]
            ]
            if len(hits) != 1:
                _stale(change["path"], change["old"], "a contract that is not there")
            holder, field = hits[0], parts[3]
        if _is_ref(change["path"]):
            ref = holder.get(field)
            found = ref.get("id") if isinstance(ref, dict) else None
            if check_old and found != change["old"]:
                _stale(change["path"], change["old"], found)
            new = resolve_ref(change["new"])
            if not isinstance(new, dict):
                raise ShippedError(
                    "shipped_series_unresolved",
                    f"{change['path']}: the series {change['new']} has no ref in "
                    "the project's Library.",
                )
            holder[field] = dict(new)
        else:
            found = holder.get(field)
            if check_old and not (
                _finite(found) and math.isclose(found, change["old"], rel_tol=1e-9)
            ):
                _stale(change["path"], change["old"], found)
            holder[field] = change["new"]
    try:
        CommercialConfig.model_validate(commercial)
    except Exception as exc:  # noqa: BLE001 -- the validator's words are the reason
        raise ShippedError(
            "shipped_cases_invalid",
            f"The changed commercial block does not validate: {exc}",
        ) from exc
    return out, network


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


def _project_refs(cfg, names) -> dict[str, list]:
    """The Library refs `cfg` (the PROJECT's solver_config) holds for each named
    series: `{name: sorted [version, hash]}`. A created project prices from refs
    minted into its org's Library, whose hash covers canonical bytes, not the
    template file, so the template hash alone does not bind what the run prices
    from (C10)."""
    commercial = (cfg or {}).get("commercial")
    commercial = commercial if isinstance(commercial, dict) else {}
    holders = [commercial.get(k) for k in PRICE_REFS]
    for contract in commercial.get("contracts") or []:
        if isinstance(contract, dict):
            holders.append(contract.get("reference_price"))
    found: dict[str, set] = {}
    for ref in holders:
        if isinstance(ref, dict) and ref.get("id") in names:
            found.setdefault(ref["id"], set()).add(
                (str(ref.get("version")), str(ref.get("hash")))
            )
    return {n: sorted(map(list, v)) for n, v in sorted(found.items())}


def _digest(
    data: dict, template_id: str, series: dict[str, Any], refs: dict | None = None
) -> str:
    body = {
        "v": 2,
        "template": template_id,
        "cases": data,
        "series": series,
        "project_refs": refs or {},
    }
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
        if not why:
            try:
                apply(
                    cfg,
                    case["changes"],
                    lambda name: {
                        "id": name,
                        "version": 1,
                        "hash": "0" * 64,
                        "source": "template",
                    },
                    check_old=False,
                )
            except ShippedError as exc:
                why.append(str(exc))
        cases.append(
            {
                "id": case["id"],
                "label": case["label"],
                "kind": case_kind(case["changes"], cfg),
                "answers": case["answers"],
                "reason": case["reason"],
                "changes": [dict(c) for c in case["changes"]],
                "status": "not_run" if why else "admissible",
                "why": " ".join(why) or None,
            }
        )
    return {
        "digest": _digest(data, template_id, named, _project_refs(cfg, named)),
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
