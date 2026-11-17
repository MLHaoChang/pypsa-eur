"""
Pack read-back (GC-07 part 5): judge the per-case results of a test pack.

A pack is a directory written by a pack writer (GC-06 ``pscad_emt``): its
``manifest.json`` lists every file with its SHA-256 and a ``data_hash`` over
them; ``battery.json`` holds the GC-04 test battery (contract version in
``test_battery.CONTRACT_VERSION``); the tool's results land in ``results/``,
one per case id:

- PSCAD: ``<case_id>.inf`` with ``<case_id>.out`` or ``<case_id>_01.out``,
  ``_02.out``, ... (both namings present is ambiguous and refused);
- any tool: ``<case_id>.csv`` with the contract channel names
  (``channels.read_csv``).

``judge_pack(pack_dir, profile, ratings)`` binds the battery to the manifest
(its SHA-256 must be the manifest's entry), maps each battery case to a judge
case and returns one judgment per case id. A case with no result file, or
whose file lacks a contract channel the case needs, is ``unknown``, never
``pass``. Every row carries the manifest's ``data_hash`` and the contract
version.

Battery kind to judge kind:

- ``fault_3ph`` / ``fault_1ph`` -> ``lvrt`` on ``v_poc_pu`` (positive-sequence
  voltage), ``t_clear = t_event_s + duration_s``. For a one-phase fault the
  rows say per-phase ride-through is not judged.
- ``v_step`` / ``q_step`` / ``p_step`` -> ``setpoint_step`` on ``v_poc_pu`` /
  ``q_poc_mvar`` / ``p_poc_mw``; the target is the battery's commanded step,
  the pre-step mean plus ``step_pu``, and the rows say so.
- ``f_step`` -> ``frequency_step`` on ``f_hz`` and ``p_poc_mw``.
- ``flat``, ``f_ramp``, ``phase_jump`` -> not judged by GC-07 (no rows, so
  ``unknown``).

MW and Mvar channels become pu of the plant's rated active and apparent power
(``Ratings``); a case that needs a rating the caller has not given is
``unknown``. The contract has no breaker / trip status channel (issue GC-12):
a ``connected`` channel is used only when the result file carries one by that
name, so a fault case reads ``unknown`` from contract channels alone.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from gridspine.handoff.test_battery import CONTRACT_VERSION, cases_from_json
from gridspine.readback.channels import ChannelFrame, read_csv, read_pscad
from gridspine.readback.judge import PRE_WINDOW_S, UNKNOWN, case_status, judge
from gridspine.schema.contracts import ContractError

MANIFEST = "manifest.json"
BATTERY_JSON = "battery.json"
RESULTS_DIR = "results"
#: Pack kinds and versions this reader knows.
KNOWN_PACKS = {"pscad_emt": ("1",)}
#: Channel-contract versions this reader knows.
KNOWN_CONTRACT_VERSIONS = (CONTRACT_VERSION,)
NOT_JUDGED = ("flat", "f_ramp", "phase_jump")
NO_CONNECTED = (
    "the channel contract has no breaker / trip status channel (GC-12) and the "
    "result file carries no 'connected' channel: stays-connected is unknown, so "
    "no fault case passes from contract channels alone"
)
ONE_PHASE = "positive-sequence voltage; per-phase ride-through not judged"


@dataclasses.dataclass(frozen=True)
class Ratings:
    """The plant's rated active (MW) and apparent (MVA) power, each with its source."""

    rated_mw: float | None = None
    mw_source: str = ""
    rated_mva: float | None = None
    mva_source: str = ""

    def __post_init__(self):
        for value, source, name in (
            (self.rated_mw, self.mw_source, "rated_mw"),
            (self.rated_mva, self.mva_source, "rated_mva"),
        ):
            if value is None:
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ContractError(
                    f"{name} must be a finite number > 0, got {value!r}"
                )
            if not isinstance(source, str) or not source.strip():
                raise ContractError(f"{name} needs a stated source")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_manifest(pack_dir) -> dict:
    """
    The pack's manifest, checked.

    A known pack and contract version, and ``battery.json`` matching its
    SHA-256 entry.
    """
    pack_dir = Path(pack_dir)
    path = pack_dir / MANIFEST
    if not path.is_file():
        raise ContractError(
            f"{pack_dir}: no {MANIFEST}; a pack is read through its manifest"
        )
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractError(f"{MANIFEST} does not parse: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ContractError(f"{MANIFEST} is not an object")
    pack, version = manifest.get("pack"), manifest.get("pack_version")
    if version not in KNOWN_PACKS.get(pack, ()):
        raise ContractError(
            f"pack {pack!r} version {version!r} is not one this reader knows: {KNOWN_PACKS}"
        )
    contract = manifest.get("contract_version")
    if contract not in KNOWN_CONTRACT_VERSIONS:
        raise ContractError(
            f"contract_version {contract!r} is not one this reader knows: "
            f"{list(KNOWN_CONTRACT_VERSIONS)}"
        )
    files = manifest.get("files")
    data_hash = manifest.get("data_hash")
    if not isinstance(files, dict) or not isinstance(data_hash, str) or not data_hash:
        raise ContractError(f"{MANIFEST} needs files {{name: sha256}} and a data_hash")
    battery = pack_dir / BATTERY_JSON
    if BATTERY_JSON not in files:
        raise ContractError(f"{MANIFEST} lists no {BATTERY_JSON}")
    if not battery.is_file():
        raise ContractError(f"{pack_dir}: {BATTERY_JSON} is missing")
    if _sha256(battery) != files[BATTERY_JSON]:
        raise ContractError(
            f"{BATTERY_JSON}'s SHA-256 is not the manifest's entry: the battery "
            "is not the one this pack was written with"
        )
    return manifest


def _result_files(results: Path, case_id: str):
    """``(reader, label)`` for the case's result, or ``None`` when there is none."""
    inf = results / f"{case_id}.inf"
    single = results / f"{case_id}.out"
    numbered = sorted(results.glob(f"{case_id}_[0-9][0-9].out"))
    csv = results / f"{case_id}.csv"
    found = []
    if inf.is_file():
        if single.is_file() and numbered:
            raise ContractError(
                f"{case_id}: both {single.name} and {numbered[0].name}... are present; "
                "which run is the result is ambiguous"
            )
        outs = [single] if single.is_file() else None
        found.append((lambda: read_pscad(inf, outs), inf.name))
    if csv.is_file():
        found.append((lambda: read_csv(csv), csv.name))
    if len(found) > 1:
        raise ContractError(
            f"{case_id}: both a PSCAD and a CSV result are present; which is the result "
            "is ambiguous"
        )
    return found[0] if found else None


def _pu(frame: ChannelFrame, name: str, unit: str, rating, label: str):
    """Channel ``name`` in pu of ``rating``, or the reason it cannot be."""
    if name not in frame:
        return None, f"the result has no {name!r} channel"
    if frame.units[name] != unit:
        return None, f"{name!r} is in {frame.units[name]!r}, expected {unit!r}"
    if rating is None:
        return None, f"no {label} given: {name!r} cannot be put in pu"
    return frame.channel(name) / rating, ""


def _pre_mean(frame: ChannelFrame, values, t_event: float, window: float):
    sel = (frame.time >= t_event - window) & (frame.time < t_event)
    return float(np.mean(values[sel])) if sel.any() else None


def _p_base(ratings: Ratings) -> str:
    return (
        f"rated P = {ratings.mw_source}"
        if ratings.rated_mw is not None
        else "no rated P"
    )


def _unknown_case(case_id, kind, why) -> dict:
    return {
        "case_id": case_id,
        "kind": kind,
        "status": UNKNOWN,
        "rows": [],
        "detail": why,
    }


def _judge_one(case, frame: ChannelFrame, profile: dict, ratings: Ratings) -> dict:
    """The judgment of one battery case on its result frame."""
    channels, units = {}, {}
    p = case.params
    jc = {"id": case.case_id, "t_event": case.t_event_s, "pre_window_s": PRE_WINDOW_S}
    note = ""
    p_rows = ()  # the checks that read P in pu of the rated P

    def take(role, values, unit):
        name = role.upper()
        channels[name], units[name] = values, unit
        jc["channels"][role] = name

    jc["channels"] = {}
    need = []
    if case.kind in ("fault_3ph", "fault_1ph"):
        jc.update(kind="lvrt", t_clear=case.t_event_s + p["duration_s"])
        need = [("v_poc_pu", "pu")]
        for role, name in (("v1", "v_poc_pu"), ("iq", "iq_pu")):
            if name in frame:
                take(role, frame.channel(name), frame.units[name])
        if "connected" in frame:
            take("connected", frame.channel("connected"), frame.units["connected"])
        pmw, _ = _pu(frame, "p_poc_mw", "MW", ratings.rated_mw, "rated P")
        if pmw is not None:
            take("p", pmw, "pu")
            p_rows = ("active_power_recovery", "active_power_ramp")
        if case.kind == "fault_1ph":
            note = ONE_PHASE
    elif case.kind in ("v_step", "q_step", "p_step"):
        jc["kind"] = "setpoint_step"
        if case.kind == "v_step":
            if "v_poc_pu" not in frame:
                return _unknown_case(
                    case.case_id, case.kind, "the result has no 'v_poc_pu' channel"
                )
            y = frame.channel("v_poc_pu")
        else:
            name, unit, rating, label = (
                ("q_poc_mvar", "Mvar", ratings.rated_mva, "rated MVA")
                if case.kind == "q_step"
                else ("p_poc_mw", "MW", ratings.rated_mw, "rated P")
            )
            y, why = _pu(frame, name, unit, rating, label)
            if y is None:
                return _unknown_case(case.case_id, case.kind, why)
        take("y", y, "pu")
        if case.kind == "p_step":
            p_rows = ("rise_time", "settling_time", "overshoot")
        y0 = _pre_mean(frame, y, case.t_event_s, PRE_WINDOW_S)
        if y0 is None:
            return _unknown_case(
                case.case_id,
                case.kind,
                f"no sample in the {PRE_WINDOW_S:g} s before the step",
            )
        jc["target"] = y0 + p["step_pu"]
        note = (
            f"target {jc['target']:g} pu = pre-step mean {y0:g} pu over {PRE_WINDOW_S:g} s "
            f"+ the battery's commanded step_pu {p['step_pu']:g}"
        )
    elif case.kind == "f_step":
        jc["kind"] = "frequency_step"
        if "f_hz" not in frame:
            return _unknown_case(
                case.case_id, case.kind, "the result has no 'f_hz' channel"
            )
        pmw, why = _pu(frame, "p_poc_mw", "MW", ratings.rated_mw, "rated P")
        if pmw is None:
            return _unknown_case(case.case_id, case.kind, why)
        take("f", frame.channel("f_hz"), frame.units["f_hz"])
        take("p", pmw, "pu")
        p_rows = ("droop",)
    else:
        return _unknown_case(
            case.case_id, case.kind, f"kind {case.kind!r} is not judged by GC-07"
        )
    for name, unit in need:
        if name not in frame:
            return _unknown_case(
                case.case_id, case.kind, f"the result has no {name!r} channel"
            )
    view = ChannelFrame(frame.time, channels, units, frame.source)
    result = judge(view, jc, profile)
    for row in result["rows"]:
        if note:
            row["detail"] = f"{row['detail']}; {note}" if row["detail"] else note
        if row["check"] in p_rows:
            base = _p_base(ratings)
            row["detail"] = f"{row['detail']}; {base}" if row["detail"] else base
        if row["check"] == "stays_connected" and "connected" not in jc["channels"]:
            row["detail"] = (
                f"{row['detail']}; {NO_CONNECTED}" if row["detail"] else NO_CONNECTED
            )
    result["kind"] = case.kind
    result["judge_kind"] = jc["kind"]
    result["detail"] = ""
    return result


def judge_pack(pack_dir, profile: dict, ratings: Ratings) -> dict:
    """
    ``{header, cases}``: the header names the pack, its ``data_hash``, the
    contract version, the ratings with their sources and the results files
    no case claimed; ``cases`` has one judgment per battery case id.
    """
    if not isinstance(ratings, Ratings):
        raise ContractError(f"ratings must be a Ratings, got {type(ratings).__name__}")
    pack_dir = Path(pack_dir)
    manifest = read_manifest(pack_dir)
    battery = cases_from_json((pack_dir / BATTERY_JSON).read_text(encoding="utf-8"))
    results = pack_dir / RESULTS_DIR
    cases = []
    for case in battery:
        if case.kind in NOT_JUDGED:
            cases.append(
                _unknown_case(
                    case.case_id,
                    case.kind,
                    f"kind {case.kind!r} is not judged by GC-07",
                )
            )
            continue
        found = _result_files(results, case.case_id) if results.is_dir() else None
        if found is None:
            cases.append(_unknown_case(case.case_id, case.kind, "no result file"))
            continue
        read, _label = found
        cases.append(_judge_one(case, read(), profile, ratings))
    stamp = {
        "data_hash": manifest["data_hash"],
        "contract_version": manifest["contract_version"],
    }
    for c in cases:
        c.update(stamp)
        c["status"] = case_status(c["rows"])
        for row in c["rows"]:
            row.update(stamp)
    ids = [c.case_id for c in battery]
    extra = sorted(
        f.name
        for f in (results.iterdir() if results.is_dir() else ())
        if not any(_claims(f.name, i) for i in ids)
    )
    header = {
        "pack": manifest["pack"],
        "pack_version": manifest["pack_version"],
        **stamp,
        "ratings": dataclasses.asdict(ratings),
        "rated_p_base": _p_base(ratings),
        "rated_mva_base": (
            f"rated MVA = {ratings.mva_source}"
            if ratings.rated_mva is not None
            else "no rated MVA"
        ),
        "connected": NO_CONNECTED,
        "extra_files": extra,
    }
    return {"header": header, "cases": cases}


def _claims(name: str, case_id: str) -> bool:
    stem = name.rsplit(".", 1)[0]
    if name.endswith((".inf", ".csv", ".out")) and stem == case_id:
        return True
    return (
        name.endswith(".out")
        and stem.startswith(f"{case_id}_")
        and stem[len(case_id) + 1 :].isdigit()
        and len(stem) == len(case_id) + 3
    )


def status_counts(judgment: dict) -> dict:
    """``{pass, fail, unknown}`` case counts of a :func:`judge_pack` result."""
    counts = {"pass": 0, "fail": 0, "unknown": 0}
    for c in judgment["cases"]:
        counts[c["status"]] += 1
    return counts
