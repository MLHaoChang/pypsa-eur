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
``unknown``. A pack written with ratings (GC-13: ``ratings.json``, listed with
its SHA-256 and repeated in the manifest) is judged on them, with no caller
input; a caller's ``Ratings`` that differ from them are refused, and every P-
and Q-based row and the header name the ``rating_source``.

Stays connected (GC-12). Every judged case gets a ``stays_connected``
obligation row. Under contract version 2 it reads the result's ``connected``
channel (unit ``status``; the PoC breaker status); an empty cell of a CSV
``connected`` column is a sample not read. Under version 1 the contract has
no such channel, so the row is ``unknown`` ("no connected channel (contract
v1)") and no case passes; a ``connected`` column in a version-1 result is not
read. A missing column, a unit other than ``status`` or a value other than
0/1 makes the row or the case ``unknown`` with the reason, never ``pass``.
A PSS/E script's ``<case_id>.status.txt`` (its failed status polls) is the
case's own file, not an extra one; its lines go into the ``stays_connected``
row's detail as a reason and are never read as samples.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from gridspine.handoff.psspy_scripts import STATUS_SIDECAR
from gridspine.handoff.test_battery import (
    CONNECTED,
    CONTRACT_VERSIONS,
    RATING_KEYS,
    RATINGS_JSON,
    PackRatings,
    cases_from_json,
    contract_channels,
    ratings_from_json,
)
from gridspine.readback.channels import (
    ChannelFrame,
    read_csv,
    read_pscad,
    refuse_status_marker,
)
from gridspine.readback.judge import PRE_WINDOW_S, UNKNOWN, case_status, judge
from gridspine.schema.contracts import ContractError

MANIFEST = "manifest.json"
BATTERY_JSON = "battery.json"
RESULTS_DIR = "results"
#: How many characters of a status sidecar a detail quotes before it marks the rest truncated.
POLL_LOG_CHARS = 2000
#: Pack kinds and versions this reader knows.
KNOWN_PACKS = {"pscad_emt": ("1",)}
#: Channel-contract versions this reader knows.
KNOWN_CONTRACT_VERSIONS = CONTRACT_VERSIONS
NOT_JUDGED = ("flat", "f_ramp", "phase_jump")
NO_CONNECTED = "no connected channel (contract v1)"
CONNECTED_READ = (
    "stays_connected is judged from the contract's 'connected' channel "
    "(PoC breaker status; breaker status only)"
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
    pack_ratings(pack_dir, manifest)  # a ratings file that is not bound is refused here
    return manifest


def pack_ratings(pack_dir, manifest) -> PackRatings | None:
    """
    The ratings a pack was written with (GC-13), or None for a pack with none.

    ``ratings.json`` must match its SHA-256 entry, the ``data_hash`` must be
    the hash of the file list (so it binds the ratings; checked for a rated
    pack only, an unrated one reads as before), and the manifest's copy of its keys must equal it; a manifest that
    carries a rating key without the file is refused.
    """
    pack_dir = Path(pack_dir)
    files = manifest["files"]
    stated = sorted(k for k in RATING_KEYS if k in manifest)
    if RATINGS_JSON not in files:
        if stated:
            raise ContractError(
                f"{MANIFEST} states {stated} but lists no {RATINGS_JSON}: the ratings "
                "are not under the pack's data_hash"
            )
        return None
    path = pack_dir / RATINGS_JSON
    if not path.is_file():
        raise ContractError(f"{pack_dir}: {RATINGS_JSON} is missing")
    if _sha256(path) != files[RATINGS_JSON]:
        raise ContractError(
            f"{RATINGS_JSON}'s SHA-256 is not the manifest's entry: the ratings "
            "are not the ones this pack was written with"
        )
    if _files_hash(files) != manifest["data_hash"]:
        raise ContractError(
            f"{MANIFEST}'s data_hash is not the hash of its file list: the ratings "
            "are not bound by it"
        )
    ratings = ratings_from_json(path.read_text(encoding="utf-8"))
    if {k: manifest.get(k) for k in RATING_KEYS} != ratings.as_dict():
        raise ContractError(f"{MANIFEST}'s ratings differ from {RATINGS_JSON}'s")
    return ratings


def _files_hash(files) -> str:
    """The ``data_hash`` a pack writer gives its file list (``emt_pack.write_emt_pack``)."""
    text = "".join(f"{n}:{h}\n" for n, h in sorted(files.items()))
    return hashlib.sha256(text.encode()).hexdigest()


def _pscad_result(inf, outs) -> ChannelFrame:
    """A PSCAD result; it has no blank cells, so no status sample may read as one."""
    frame = read_pscad(inf, outs)
    if CONNECTED in frame and frame.units[CONNECTED] == "status":
        refuse_status_marker(frame.channel(CONNECTED), CONNECTED, inf.name)
    return frame


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
        found.append((lambda: _pscad_result(inf, outs), inf.name))
    if csv.is_file():
        found.append((lambda: read_csv(csv, blank_status=(CONNECTED,)), csv.name))
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


def _mva_base(ratings: Ratings) -> str:
    return (
        f"rated MVA = {ratings.mva_source}"
        if ratings.rated_mva is not None
        else "no rated MVA"
    )


def _bases(ratings: Ratings, own: PackRatings | None) -> tuple:
    """
    ``(p_base, q_base)``: the text a P- and a Q-based row carries. A rated
    pack names each value with its ``rating_source``, so a reader sees it is
    stated, not derived from the campus; an unrated one keeps the text it had
    before GC-13 (no Q text).
    """
    if own is None:
        return _p_base(ratings), None
    where = f"{own.rating_source}; pack {RATINGS_JSON}"
    return (
        f"rated P {own.rated_mw:g} MW = {where}",
        f"rated MVA {own.rated_mva:g} MVA = {where}",
    )


def _unknown_case(case_id, kind, why) -> dict:
    return {
        "case_id": case_id,
        "kind": kind,
        "status": UNKNOWN,
        "rows": [],
        "detail": why,
    }


def _connected_why(frame: ChannelFrame, contract_version: str):
    """Why the result's ``connected`` channel cannot be read, or None."""
    if CONNECTED not in contract_channels(contract_version):
        return NO_CONNECTED
    if CONNECTED not in frame:
        return f"the result has no {CONNECTED!r} channel"
    if frame.units[CONNECTED] != "status":
        return f"{CONNECTED!r} is in {frame.units[CONNECTED]!r}, expected 'status'"
    return None


def _judge_one(
    case,
    frame: ChannelFrame,
    profile: dict,
    ratings: Ratings,
    contract_version: str,
    bases: tuple,
) -> dict:
    """The judgment of one battery case on its result frame; ``bases`` from ``_bases``."""
    channels, units = {}, {}
    p = case.params
    # judge default pre-window; stays_connected is an obligation on every kind (GK A2 D-2)
    jc = {"id": case.case_id, "t_event": case.t_event_s, "connected_required": True}
    note = ""
    p_rows = ()  # the checks that read P in pu of the rated P
    q_rows = ()  # the checks that read Q in pu of the rated MVA

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
            if frame.units["v_poc_pu"] != "pu":
                return _unknown_case(
                    case.case_id,
                    case.kind,
                    f"'v_poc_pu' is in {frame.units['v_poc_pu']!r}, expected 'pu'",
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
        elif case.kind == "q_step":
            q_rows = ("rise_time", "settling_time", "overshoot")
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
        if frame.units[name] != unit:
            return _unknown_case(
                case.case_id,
                case.kind,
                f"{name!r} is in {frame.units[name]!r}, expected {unit!r}",
            )
    why = _connected_why(frame, contract_version)
    if why is None:
        take("connected", frame.channel(CONNECTED), "status")
    view = ChannelFrame(frame.time, channels, units, frame.source)
    result = judge(view, jc, profile)
    for row in result["rows"]:
        if note:
            row["detail"] = f"{row['detail']}; {note}" if row["detail"] else note
        for rows, base in ((p_rows, bases[0]), (q_rows, bases[1])):
            if base is not None and row["check"] in rows:
                row["detail"] = f"{row['detail']}; {base}" if row["detail"] else base
        if row["check"] == "stays_connected" and why is not None:
            row["detail"] = f"{row['detail']}; {why}" if row["detail"] else why
    result["kind"] = case.kind
    result["judge_kind"] = jc["kind"]
    result["detail"] = ""
    return result


def judge_pack(pack_dir, profile: dict, ratings: Ratings | None = None) -> dict:
    """
    ``{header, cases}``: the header names the pack, its ``data_hash``, the
    contract version, the ratings with their sources (and, for a pack written
    with ratings, its ``rating_source`` and ``campus_data_hash``) and the
    results files no case claimed; ``cases`` has one judgment per battery
    case id. A pack's own ratings are used when it has them; ``ratings``
    is then optional and refused when it differs from them.
    """
    if ratings is not None and not isinstance(ratings, Ratings):
        raise ContractError(f"ratings must be a Ratings, got {type(ratings).__name__}")
    pack_dir = Path(pack_dir)
    manifest = read_manifest(pack_dir)
    own = pack_ratings(pack_dir, manifest)
    if own is not None:
        mine = Ratings(
            own.rated_mw, own.rating_source, own.rated_mva, own.rating_source
        )
        if ratings is not None and ratings != mine:
            raise ContractError(
                f"the caller's ratings {dataclasses.asdict(ratings)} differ from the "
                f"pack's {dataclasses.asdict(mine)} ({RATINGS_JSON}); a pack is judged "
                "on the ratings it was written with"
            )
        ratings = mine
    elif ratings is None:
        ratings = Ratings()
    bases = _bases(ratings, own)
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
        read, label = found
        try:
            frame = read()
        except ContractError as exc:
            cases.append(
                _unknown_case(case.case_id, case.kind, f"{label} unreadable: {exc}")
            )
            continue
        try:
            cases.append(
                _judge_one(
                    case, frame, profile, ratings, manifest["contract_version"], bases
                )
            )
        except ContractError as exc:
            cases.append(_unknown_case(case.case_id, case.kind, f"not judged: {exc}"))
    for c in cases:  # every case, judged or not, carries its status sidecar as a reason
        _add_poll_log(c, results, c["case_id"])
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
        "rated_p_base": bases[0],
        "rated_mva_base": _mva_base(ratings) if own is None else bases[1],
        "connected": (
            CONNECTED_READ
            if CONNECTED in contract_channels(manifest["contract_version"])
            else NO_CONNECTED
        ),
        "extra_files": extra,
    }
    if own is not None:  # an unrated pack's header keeps master's keys (GK A R6)
        header["rating_source"] = own.rating_source
        header["campus_data_hash"] = own.campus_data_hash
    return {"header": header, "cases": cases}


def _add_poll_log(result: dict, results: Path, case_id: str) -> None:
    """
    Add the case's failed-poll sidecar (``<case_id>.status.txt``, written by the
    PSS/E scripts) to its ``stays_connected`` row, or to the case detail when
    there is no such row, as a reason: text, never read as a sample.
    """
    side = results / STATUS_SIDECAR.format(case_id=case_id)
    if not side.is_file():
        return
    try:
        lines = [ln.strip() for ln in side.read_text(encoding="utf-8").splitlines()]
    except (OSError, UnicodeDecodeError) as exc:
        lines = [f"unreadable ({exc})"]
    text = "; ".join(ln for ln in lines if ln)
    if not text:
        return
    if len(text) > POLL_LOG_CHARS:
        text = text[:POLL_LOG_CHARS] + f" ... (truncated, {len(text)} characters)"
    note = f"{side.name}: {text}"
    rows = [r for r in result["rows"] if r["check"] == "stays_connected"]
    for row in rows:
        row["detail"] = f"{row['detail']}; {note}" if row["detail"] else note
    if not rows:
        result["detail"] = f"{result['detail']}; {note}" if result["detail"] else note


def _claims(name: str, case_id: str) -> bool:
    if name == STATUS_SIDECAR.format(case_id=case_id):
        return True
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
