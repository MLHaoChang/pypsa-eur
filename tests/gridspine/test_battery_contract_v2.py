# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
GC-12: the battery's channel contract version 2 adds ``connected``.

Oracles (GK A2 T2/T7): the frozen real version-1 output of the writer before
version 2 existed (``data/battery/battery_v1.json``), hand-listed channel
tuples, and a hand-written version-2 battery.json. A writer/reader round trip
is never the only check. Each refusal test asserts its own guard's reason.
"""

import json
from pathlib import Path

import pytest

from gridspine.handoff.test_battery import (
    CONTRACT_VERSION,
    CONTRACT_VERSIONS,
    Case,
    Thevenin,
    battery_frame,
    battery_json,
    build_battery,
    cases_from_frame,
    cases_from_json,
    contract_channels,
    validate_battery,
)
from gridspine.schema.contracts import ContractError

V1_FIXTURE = Path(__file__).parent / "data" / "battery" / "battery_v1.json"
Z0 = {"z0_over_z1": 1.5, "x0_over_r0": 4.0}
V1 = (
    "v_poc_pu",
    "theta_poc_deg",
    "f_hz",
    "p_poc_mw",
    "q_poc_mvar",
    "id_pu",
    "iq_pu",
    "i_poc_pu",
)
CONNECTED_ENTRY = {
    "unit": "status",
    "base": "0/1",
    "sign": "1 = PoC breaker closed and the plant not tripped; 0 = open or tripped",
    "point": "point of connection (PoC), grid side of the plant's main transformer",
    "meaning": "breaker status only: momentary cessation or current blocking is not a "
    "trip and does not show on this channel; sampled with the other channels",
}
# Hand-listed channels per kind under version 2, in contract order.
_BASE = ("v_poc_pu", "f_hz", "p_poc_mw", "q_poc_mvar")
_CUR = ("id_pu", "iq_pu", "i_poc_pu")
EXPECTED_V2 = {
    "flat": _BASE + ("connected",),
    "fault_3ph": _BASE + _CUR + ("connected",),
    "fault_1ph": _BASE + _CUR + ("connected",),
    "v_step": _BASE + ("iq_pu", "connected"),
    "q_step": _BASE + ("iq_pu", "connected"),
    "p_step": _BASE + ("id_pu", "connected"),
    "f_step": _BASE + ("connected",),
    "f_ramp": _BASE + ("connected",),
    "phase_jump": ("v_poc_pu", "theta_poc_deg", "f_hz", "p_poc_mw", "q_poc_mvar")
    + _CUR
    + ("connected",),
}
GRID = Thevenin("skmin_xrmin", 1000.0, 3.0, 1.5, 4.0)


def _v1_text():
    return V1_FIXTURE.read_text()


def _v2_case(channels=None):
    return Case(
        "fault_3ph_skmin_xrmin_v000",
        "fault_3ph",
        "04/05",
        GRID,
        1.0,
        10.0,
        channels or (*_BASE, *_CUR, "connected"),
        {"duration_s": 0.15, "v_retained_pu": 0.0},
    )


def _v2_doc(channels=None):
    """A version-2 battery.json written by hand (not by battery_json)."""
    contract = json.loads(_v1_text())["channels"]
    contract["connected"] = dict(CONNECTED_ENTRY)
    return {
        "contract_version": "2",
        "channels": contract,
        "cases": [
            {
                "case_id": "fault_3ph_skmin_xrmin_v000",
                "kind": "fault_3ph",
                "trgc": "04/05",
                "grid": {
                    "label": "skmin_xrmin",
                    "sk_mva": 1000.0,
                    "x_over_r": 3.0,
                    "z0_over_z1": 1.5,
                    "x0_over_r0": 4.0,
                },
                "t_event_s": 1.0,
                "t_end_s": 10.0,
                "channels": list(channels or (*_BASE, *_CUR, "connected")),
                "params": {"duration_s": 0.15, "v_retained_pu": 0.0},
                "source": "assumed",
            }
        ],
    }


# --- versions ------------------------------------------------------------


def test_versions_and_channels_are_literal():
    assert CONTRACT_VERSIONS == ("1", "2")
    assert (
        CONTRACT_VERSION == "1"
    )  # writers' default until a pack records `connected` (T1)
    assert contract_channels("1") == V1
    assert contract_channels("2") == V1 + ("connected",)


# --- version 1: the frozen real output -----------------------------------


def test_v1_fixture_is_the_v1_contract():
    doc = json.loads(_v1_text())
    assert doc["contract_version"] == "1"
    assert tuple(doc["channels"]) == tuple(sorted(V1))
    assert all("connected" not in c["channels"] for c in doc["cases"])


def test_v1_pack_written_before_v2_loads_and_reserialises_byte_equal():
    text = _v1_text()
    cases = cases_from_json(text)
    assert len(cases) == 58
    assert battery_json(cases) == text
    assert battery_json(cases, "1") == text


def test_default_writer_still_writes_the_v1_bytes():
    assert battery_json(build_battery(1000, 3000, 3, 10, **Z0)) == _v1_text()


# --- version 2 -----------------------------------------------------------


def test_v2_battery_puts_connected_on_every_case():
    battery = build_battery(1000, 3000, 3, 10, **Z0, contract_version="2")
    assert len(battery) == 58
    for c in battery:
        assert c.channels == EXPECTED_V2[c.kind], c.case_id


def test_v2_battery_ids_equal_v1_ids():
    v1 = build_battery(1000, 3000, 3, 10, **Z0)
    v2 = build_battery(1000, 3000, 3, 10, **Z0, contract_version="2")
    assert [c.case_id for c in v1] == [c.case_id for c in v2]


def test_hand_written_v2_json_is_accepted():
    (case,) = cases_from_json(json.dumps(_v2_doc()))
    assert case == _v2_case()


def test_v2_json_writer_matches_the_hand_written_doc():
    assert json.loads(battery_json([_v2_case()], "2")) == _v2_doc()


def test_v2_round_trips_through_json_and_frame():
    battery = build_battery(1000, 3000, 3, 10, **Z0, contract_version="2")
    assert cases_from_json(battery_json(battery, "2")) == battery
    assert cases_from_frame(battery_frame(battery)) == battery


# --- the three refusals (T2), each with its own reason ---------------------


def test_v2_case_without_connected_refused():
    doc = _v2_doc(channels=_BASE + _CUR)
    with pytest.raises(
        ContractError, match="needs the 'connected' channel on every case"
    ):
        cases_from_json(json.dumps(doc))


def test_v2_writer_refuses_a_case_without_connected():
    with pytest.raises(ContractError, match="needs the 'connected' channel"):
        battery_json([_v2_case(channels=_BASE)], "2")


def test_v1_case_naming_connected_refused():
    doc = json.loads(_v1_text())
    doc["cases"][0]["channels"].append("connected")
    with pytest.raises(
        ContractError, match=r"\['connected'\] not in contract version 1"
    ):
        cases_from_json(json.dumps(doc))


def test_v1_writer_refuses_a_v2_battery():
    battery = build_battery(1000, 3000, 3, 10, **Z0, contract_version="2")
    with pytest.raises(ContractError, match="not in contract version 1"):
        battery_json(battery)


@pytest.mark.parametrize("version", ["3", "0", "", None, 2, 1.0, ["2"]])
def test_unknown_version_refused(version):
    doc = _v2_doc()
    doc["contract_version"] = version
    with pytest.raises(ContractError, match="is not one of \\['1', '2'\\]"):
        cases_from_json(json.dumps(doc))


def test_missing_version_refused():
    doc = _v2_doc()
    del doc["contract_version"]
    with pytest.raises(ContractError, match="None is not one of"):
        cases_from_json(json.dumps(doc))


@pytest.mark.parametrize("version", ["3", None])
def test_builder_and_writer_refuse_unknown_version(version):
    with pytest.raises(ContractError, match="is not one of"):
        build_battery(1000, 3000, 3, 10, **Z0, contract_version=version)
    with pytest.raises(ContractError, match="is not one of"):
        battery_json([_v2_case()], version)


def test_v2_doc_with_the_v1_contract_refused():
    doc = _v2_doc()
    del doc["channels"]["connected"]
    with pytest.raises(ContractError, match="channel contract differs"):
        cases_from_json(json.dumps(doc))


def test_v1_doc_with_the_v2_contract_refused():
    doc = json.loads(_v1_text())
    doc["channels"]["connected"] = dict(CONNECTED_ENTRY)
    with pytest.raises(ContractError, match="channel contract differs"):
        cases_from_json(json.dumps(doc))


def test_v2_connected_entry_edited_refused():
    doc = _v2_doc()
    doc["channels"]["connected"]["unit"] = "pu"
    with pytest.raises(ContractError, match="channel contract differs"):
        cases_from_json(json.dumps(doc))


def test_validate_without_version_checks_structure_only():
    # cases_from_frame has no version; the version gate sits at the writers and the JSON reader
    assert validate_battery([_v2_case(channels=_BASE)]) == (_v2_case(channels=_BASE),)


# --- T1: a pack declares exactly the channels its schedule records ----------


def test_emt_manifest_declares_exactly_what_its_schedule_records(tmp_path):
    from gridspine.handoff.emt_pack import battery_from_campus, write_emt_pack
    from gridspine.ingest.campus import build_campus
    from tests.gridspine.test_emt_pack import spec

    camp = build_campus(spec())
    p = write_emt_pack(tmp_path / "emt", camp, battery=battery_from_campus(camp))
    manifest = json.loads((p.path / "manifest.json").read_text())
    schedule = json.loads((p.path / "case_schedule.json").read_text())
    recorded = {ch for r in schedule for ch in r["channels"].split("|")}
    assert manifest["contract_version"] == "1"
    assert set(manifest["channels"]) == recorded
    assert manifest["channels"] == list(V1)


def test_emt_pack_refuses_a_v2_battery_until_its_schedule_records_connected(tmp_path):
    from dataclasses import replace

    from gridspine.handoff import emt_pack
    from gridspine.ingest.campus import build_campus
    from tests.gridspine.test_emt_pack import spec

    assert emt_pack.EMT_CONTRACT_VERSION == "1"
    camp = build_campus(spec())
    v2 = tuple(
        replace(c, channels=(*c.channels, "connected"))
        for c in emt_pack.battery_from_campus(camp)
    )
    with pytest.raises(ContractError, match="not in contract version 1"):
        emt_pack.write_emt_pack(tmp_path / "emt", camp, battery=v2)
