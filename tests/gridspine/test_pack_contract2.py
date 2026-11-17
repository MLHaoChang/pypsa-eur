# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
Read-back of battery contract v2: the ``connected`` channel (GC-12 PR-D).

Oracles are hand-written result CSVs with literal statuses and times, a
frozen real v1 EMT pack (written by master 0ab07339, before the EMT pack
moved to v2) and a real ``write_emt_pack`` output at v2. GK A2 conditions
D-1..D-5 in .scratch/grid-compliance/issues/12-contract-connected-channel.md.
"""

import csv
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from gridspine.handoff.test_battery import (
    CHANNELS,
    CONTRACT_VERSIONS,
    Case,
    battery_json,
    contract_channels,
)
from gridspine.readback.channels import STATUS_NOT_READ, read_csv
from gridspine.readback.pack import (
    CONNECTED_READ,
    KNOWN_CONTRACT_VERSIONS,
    NO_CONNECTED,
    judge_pack,
)
from gridspine.schema.contracts import ContractError
from tests.gridspine.test_pack import (
    GRID,
    PROFILE,
    RATINGS,
    T,
    _by_id,
    _csv,
    _emt_pack,
    _pscad,
    _ride_through,
    _rows,
)

V1_PACK = Path(__file__).parent / "data" / "emt_pack_v1"

FAULT_ID = "fault_3ph_skmin_xrmin_v030"
F_STEP_ID = "f_step_skmin_xrmin_up"
P_STEP_ID = "p_step_skmin_xrmin_up"


def _case(case_id, kind, params, trgc="07"):
    return Case(case_id, kind, trgc, GRID, 0.2, 1.5, contract_channels("2"), params)


FAULT = _case(FAULT_ID, "fault_3ph", {"v_retained_pu": 0.3, "duration_s": 0.15})
F_STEP = _case(F_STEP_ID, "f_step", {"step_hz": 0.5}, "06")


def _pack2(tmp_path, cases, contract="2"):
    pack = tmp_path / "pack"
    (pack / "results").mkdir(parents=True)
    text = battery_json(cases, contract_version="2")
    (pack / "battery.json").write_text(text, newline="\n")
    m = {
        "pack": "pscad_emt",
        "pack_version": "1",
        "contract_version": contract,
        "files": {"battery.json": hashlib.sha256(text.encode()).hexdigest()},
        "data_hash": "d" * 64,
    }
    (pack / "manifest.json").write_text(json.dumps(m))
    return pack


def _status(*spans):
    """
    The ``connected`` column from literal ``(t_from, value)`` breakpoints:
    1 before the first, then each value from its time on. ``None`` is a blank cell.
    """
    out = [1.0] * len(T)
    for t_from, value in spans:
        for k, t in enumerate(T):
            if t >= t_from - 1e-12:
                out[k] = value
    return out


def _write(path, channels, connected, unit="status"):
    """A result CSV; a ``None`` status is written as an empty cell."""
    names = list(channels)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            ["time [s]"]
            + [f"{n} [{channels[n][0]}]" for n in names]
            + [f"connected [{unit}]"]
        )
        for k, t in enumerate(T):
            s = connected[k]
            w.writerow(
                [repr(float(t))]
                + [repr(float(channels[n][1][k])) for n in names]
                + ["" if s is None else repr(float(s))]
            )


def _fault_channels():
    return _ride_through(connected=False)


def _f_step_channels():
    f = np.where(T < 0.2, 50.0, 50.5)
    p = np.where(T < 0.2, 50.0, 35.0)  # (0.5 - 0.2) / 50 / 0.15 = 4 %: the droop passes
    return {"f_hz": ("Hz", f), "p_poc_mw": ("MW", p)}


def _judge(tmp_path, case, channels, connected, unit="status"):
    pack = _pack2(tmp_path, [case])
    _write(pack / "results" / f"{case.case_id}.csv", channels, connected, unit)
    j = judge_pack(pack, PROFILE, RATINGS)
    return j, _by_id(j)[case.case_id]


# --- D-1: the trip, the blank, the pass ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("case", "channels"),
    [(FAULT, _fault_channels()), (F_STEP, _f_step_channels())],
    ids=["fault_3ph", "f_step"],
)
def test_all_ones_passes(tmp_path, case, channels):
    j, c = _judge(tmp_path, case, channels, _status())
    sc = _rows(c)["stays_connected"]
    assert sc["status"] == "pass"
    assert c["status"] == "pass"
    assert j["header"]["connected"] == CONNECTED_READ


@pytest.mark.parametrize(
    ("case", "channels", "spans", "at"),
    [
        # 0 then 1 again: the first 0 is the trip
        (FAULT, _fault_channels(), [(0.25, 0.0), (0.26, 1.0)], 0.25),
        # blank, then a 0: the 0 still fails
        (FAULT, _fault_channels(), [(0.22, None), (0.3, 0.0)], 0.3),
        # 0, then blanks: a trip, not a poll failure
        (FAULT, _fault_channels(), [(0.24, 0.0), (0.27, None)], 0.24),
        # an f_step trip
        (F_STEP, _f_step_channels(), [(0.5, 0.0)], 0.5),
        (F_STEP, _f_step_channels(), [(0.4, None), (0.7, 0.0), (0.8, 1.0)], 0.7),
    ],
    ids=["0_then_1", "blank_then_0", "0_then_blank", "f_step_trip", "f_step_mixed"],
)
def test_a_zero_fails_at_the_first_zero(tmp_path, case, channels, spans, at):
    _, c = _judge(tmp_path, case, channels, _status(*spans))
    sc = _rows(c)["stays_connected"]
    assert sc["status"] == "fail"
    assert f"disconnected at {at:g} s" in sc["detail"]
    assert c["status"] == "fail"


@pytest.mark.parametrize(
    ("case", "channels"),
    [(FAULT, _fault_channels()), (F_STEP, _f_step_channels())],
    ids=["fault_3ph", "f_step"],
)
def test_blanks_without_a_zero_are_unknown_naming_the_first(tmp_path, case, channels):
    _, c = _judge(tmp_path, case, channels, _status((0.3, None), (0.31, 1.0)))
    sc = _rows(c)["stays_connected"]
    assert sc["status"] == "unknown"
    assert "status poll failed at t=0.3" in sc["detail"]
    assert c["status"] == "unknown"


def test_a_zero_before_the_event_is_not_judged_as_the_trip(tmp_path):
    # the window starts at t_event (0.2 s); a 0 at 0.1 s that recovers is not in it
    conn = _status((0.1, 0.0), (0.15, 1.0))
    _, c = _judge(tmp_path, F_STEP, _f_step_channels(), conn)
    assert _rows(c)["stays_connected"]["status"] == "pass"


def test_a_zero_exactly_at_t_event_is_the_trip(tmp_path):
    # the window is closed at t_event: a sample at 0.2 s is judged
    _, c = _judge(
        tmp_path, F_STEP, _f_step_channels(), _status((0.2, 0.0), (0.201, 1.0))
    )
    sc = _rows(c)["stays_connected"]
    assert sc["status"] == "fail" and "disconnected at 0.2 s" in sc["detail"]


def test_a_v2_pass_row_carries_no_missing_channel_reason(tmp_path):
    _, c = _judge(tmp_path, F_STEP, _f_step_channels(), _status())
    sc = _rows(c)["stays_connected"]
    assert sc["status"] == "pass"
    assert NO_CONNECTED not in sc["detail"] and "None" not in sc["detail"]
    assert not any(
        "connected" in r["detail"] for r in c["rows"] if r["check"] != "stays_connected"
    )


def test_one_stays_connected_row_per_case(tmp_path):
    for case, ch in ((FAULT, _fault_channels()), (F_STEP, _f_step_channels())):
        _, c = _judge(tmp_path / case.kind, case, ch, _status())
        assert [r["check"] for r in c["rows"]].count("stays_connected") == 1, case.kind


@pytest.mark.parametrize("reader", ["csv", "pscad"])
def test_a_v2_result_without_connected_is_unknown_naming_it(tmp_path, reader):
    pack = _pack2(tmp_path, [F_STEP])
    if reader == "csv":
        _csv(pack / "results" / f"{F_STEP_ID}.csv", _f_step_channels())
    else:
        _pscad(pack / "results", F_STEP_ID, _f_step_channels())
    c = _by_id(judge_pack(pack, PROFILE, RATINGS))[F_STEP_ID]
    sc = _rows(c)["stays_connected"]
    assert sc["status"] == "unknown"
    assert "the result has no 'connected' channel" in sc["detail"]
    assert _rows(c)["droop"]["status"] == "pass"


# --- D-3: unit and value gates ---------------------------------------------------------------------------------


def test_connected_not_in_status_is_unknown_naming_the_unit(tmp_path):
    _, c = _judge(tmp_path, FAULT, _fault_channels(), _status(), unit="pu")
    sc = _rows(c)["stays_connected"]
    assert sc["status"] == "unknown"
    assert "'connected' is in 'pu', expected 'status'" in sc["detail"]
    assert c["status"] == "unknown"


@pytest.mark.parametrize("value", [2.0, 0.5])
def test_a_value_outside_0_1_blank_is_unknown_naming_the_sample(tmp_path, value):
    j, c = _judge(
        tmp_path, FAULT, _fault_channels(), _status((0.4, value), (0.41, 1.0))
    )
    assert c["status"] == "unknown" and c["rows"] == []
    assert c["detail"].startswith("not judged: ")
    assert f"got {value!r} at 0.4 s" in c["detail"]
    # the pack is still judged: the error stays with its case
    assert j["header"]["contract_version"] == "2"


def test_a_literal_minus_one_in_a_csv_is_refused_not_read_as_blank(tmp_path):
    # -1 is the reader's own "not read" marker; written literally it is not 0, 1 or blank
    j, c = _judge(tmp_path, FAULT, _fault_channels(), _status((0.4, -1.0), (0.41, 1.0)))
    assert c["status"] == "unknown" and c["rows"] == []
    assert "unreadable" in c["detail"] and "must be 0, 1 or blank" in c["detail"]
    assert "got -1.0 at row 400" in c["detail"]


def test_a_literal_minus_one_in_a_pscad_result_is_refused(tmp_path):
    pack = _pack2(tmp_path, [FAULT])
    ch = dict(_fault_channels())
    ch["connected"] = ("status", np.array(_status((0.4, -1.0), (0.41, 1.0))))
    _pscad(pack / "results", FAULT_ID, ch)
    c = _by_id(judge_pack(pack, PROFILE, RATINGS))[FAULT_ID]
    assert c["status"] == "unknown" and c["rows"] == []
    assert c["detail"].startswith(f"{FAULT_ID}.inf unreadable: ")
    assert "got -1.0 at row 400" in c["detail"]


def test_connected_in_pu_with_a_blank_cell_is_refused_not_read_as_unread(tmp_path):
    # blank_status applies to a 'status' unit only; any other blank is refused
    _, c = _judge(tmp_path, FAULT, _fault_channels(), _status((0.3, None)), unit="pu")
    assert c["status"] == "unknown" and c["rows"] == []
    assert "unreadable" in c["detail"] and "missing or non-numeric value" in c["detail"]


def test_a_status_channel_not_named_blank_status_refuses_a_blank(tmp_path):
    path = tmp_path / "r.csv"
    _write(path, _f_step_channels(), _status((0.3, None)))
    assert read_csv(path, blank_status=("connected",)).channel("connected")[300] == (
        STATUS_NOT_READ
    )
    with pytest.raises(ContractError, match="missing or non-numeric value at row 300"):
        read_csv(path)


def test_a_pscad_connected_in_pu_reads_the_unit_gate_not_unreadable(tmp_path):
    pack = _pack2(tmp_path, [FAULT])
    ch = dict(_fault_channels())
    ch["connected"] = ("pu", np.array(_status((0.4, -1.0), (0.41, 1.0))))
    _pscad(pack / "results", FAULT_ID, ch)
    c = _by_id(judge_pack(pack, PROFILE, RATINGS))[FAULT_ID]
    sc = _rows(c)["stays_connected"]
    assert sc["status"] == "unknown"
    assert "'connected' is in 'pu', expected 'status'" in sc["detail"]


# --- D-2: a v1 pack never passes stays_connected ---------------------------------------------------------------


def _frozen_v1(tmp_path):
    pack = tmp_path / "v1"
    shutil.copytree(V1_PACK, pack, ignore=shutil.ignore_patterns("*.license"))
    return pack


def test_the_frozen_v1_pack_is_real_v1():
    m = json.loads((V1_PACK / "manifest.json").read_text())
    b = json.loads((V1_PACK / "battery.json").read_text())
    assert m["pack"] == "pscad_emt" and m["contract_version"] == "1"
    assert b["contract_version"] == "1"
    assert all("connected" not in c["channels"] for c in b["cases"])


#: one case of each kind GC-07 judges (pack.NOT_JUDGED are the others)
JUDGED_KINDS = ("fault_3ph", "fault_1ph", "p_step", "q_step", "v_step", "f_step")


def _every_v1_channel():
    """Every v1 channel, flat, in its contract unit: enough for each kind to be judged."""
    flat = {
        "v_poc_pu": 1.0,
        "theta_poc_deg": 0.0,
        "f_hz": 50.0,
        "p_poc_mw": 50.0,
        "q_poc_mvar": 5.0,
        "id_pu": 0.5,
        "iq_pu": 0.0,
        "i_poc_pu": 0.5,
    }
    units = {n: CHANNELS[n].unit for n in contract_channels("1")}
    assert set(flat) == set(units)
    return {n: (units[n], np.full_like(T, v)) for n, v in flat.items()}


def test_a_v1_pack_reads_unknown_on_every_stays_connected_row(tmp_path):
    pack = _frozen_v1(tmp_path)
    battery = json.loads((pack / "battery.json").read_text())["cases"]
    picked = {}
    for c in battery:
        picked.setdefault(c["kind"], c["case_id"])
    assert set(JUDGED_KINDS) <= set(picked)
    (pack / "results").mkdir()
    for kind in JUDGED_KINDS:
        # every result carries a 'connected' column of 1s; v1 does not read it
        _write(pack / "results" / f"{picked[kind]}.csv", _every_v1_channel(), _status())
    j = judge_pack(pack, PROFILE, RATINGS)
    assert j["header"]["contract_version"] == "1"
    assert j["header"]["connected"] == NO_CONNECTED
    cases = _by_id(j)
    for kind in JUDGED_KINDS:
        c = cases[picked[kind]]
        sc = _rows(c)["stays_connected"]
        assert sc["status"] == "unknown", kind
        assert "no connected channel (contract v1)" in sc["detail"], kind
        others = [r for r in c["rows"] if r["check"] != "stays_connected"]
        assert others and not any(NO_CONNECTED in r["detail"] for r in others), kind
    assert not any(c["status"] == "pass" for c in cases.values())


# --- D-4: versions ----------------------------------------------------------------------------------------------


def test_known_contract_versions_are_the_battery_s():
    assert KNOWN_CONTRACT_VERSIONS == CONTRACT_VERSIONS == ("1", "2")


@pytest.mark.parametrize("version", ["3", "0", ""])
def test_an_unknown_version_refuses_the_whole_pack(tmp_path, version):
    pack = _pack2(tmp_path, [FAULT], contract=version)
    with pytest.raises(ContractError, match="is not one this reader knows"):
        judge_pack(pack, PROFILE, RATINGS)


# --- D-2: a real v2 EMT pack ------------------------------------------------------------------------------------


def test_a_written_v2_emt_pack_reads_connected(tmp_path):
    p = _emt_pack(tmp_path).path
    m = json.loads((p / "manifest.json").read_text())
    assert m["contract_version"] == "2"
    fault = next(i for i in m["case_ids"] if i.startswith("fault_3ph_"))
    battery = json.loads((p / "battery.json").read_text())
    t_event = next(c for c in battery["cases"] if c["case_id"] == fault)["t_event_s"]
    trip = round(t_event + 0.05, 3)
    assert trip < T[-1]
    (p / "results").mkdir(exist_ok=True)
    _write(p / "results" / f"{fault}.csv", _fault_channels(), _status((trip, 0.0)))
    j = judge_pack(p, PROFILE, RATINGS)
    assert j["header"]["connected"] == CONNECTED_READ
    sc = _rows(_by_id(j)[fault])["stays_connected"]
    assert sc["status"] == "fail" and f"disconnected at {trip:g} s" in sc["detail"]
