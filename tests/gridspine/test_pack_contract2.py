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
    CONTRACT_VERSIONS,
    Case,
    battery_json,
    contract_channels,
)
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
    _emt_pack,
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


# --- D-3: unit and value gates ---------------------------------------------------------------------------------


def test_connected_not_in_status_is_unknown_naming_the_unit(tmp_path):
    _, c = _judge(tmp_path, FAULT, _fault_channels(), _status(), unit="pu")
    sc = _rows(c)["stays_connected"]
    assert sc["status"] == "unknown"
    assert "'connected' is in 'pu', expected 'status'" in sc["detail"]
    assert c["status"] == "unknown"


@pytest.mark.parametrize("value", [2.0, 0.5, -1.0])
def test_a_value_outside_0_1_blank_is_unknown_naming_the_sample(tmp_path, value):
    # -1 is the reader's own "not read" marker; written literally it is still not 0, 1 or blank
    j, c = _judge(tmp_path, FAULT, _fault_channels(), _status((0.4, value), (0.41, 1.0)))
    if value == -1.0:
        # a literal -1 cannot be told from a blank once read; it is unknown, never pass
        assert _rows(c)["stays_connected"]["status"] == "unknown"
        return
    assert c["status"] == "unknown" and c["rows"] == []
    assert c["detail"].startswith("not judged: ")
    assert f"got {value!r} at 0.4 s" in c["detail"]
    # the pack is still judged: the error stays with its case
    assert j["header"]["contract_version"] == "2"


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


def test_a_v1_pack_reads_unknown_on_every_stays_connected_row(tmp_path):
    pack = _frozen_v1(tmp_path)
    ids = json.loads((pack / "manifest.json").read_text())["case_ids"]
    fault = next(i for i in ids if i.startswith("fault_3ph_"))
    f_step = next(i for i in ids if i.startswith("f_step_") and i.endswith("_up"))
    (pack / "results").mkdir()
    # both results carry a 'connected' column of 1s; v1 does not read it
    _write(pack / "results" / f"{fault}.csv", _fault_channels(), _status())
    _write(pack / "results" / f"{f_step}.csv", _f_step_channels(), _status())
    j = judge_pack(pack, PROFILE, RATINGS)
    assert j["header"]["contract_version"] == "1"
    assert j["header"]["connected"] == NO_CONNECTED
    cases = _by_id(j)
    judged = [c for c in cases.values() if c["rows"]]
    assert {c["case_id"] for c in judged} == {fault, f_step}
    for c in judged:
        sc = _rows(c)["stays_connected"]
        assert sc["status"] == "unknown"
        assert "no connected channel (contract v1)" in sc["detail"]
        assert c["status"] != "pass"
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
