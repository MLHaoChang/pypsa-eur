"""
GC-06a/06b: the EMT pack at battery contract version 2, with Z0 per corner.

Oracles (GK A2 T6/T7): the zero-sequence impedance per corner is worked out by
hand from spec()'s PCC figures (110 kV; Sk 3000/2000 MVA; R/X 0.1/0.12;
X0/X1 3.0/2.5; R0/X0 0.1/0.2), never from the pack's own reader. The
scalar-pair battery stays byte-identical to the frozen version-1 output.
"""

import dataclasses
import json
import math
from pathlib import Path

import pytest

from gridspine.handoff import emt_pack
from gridspine.handoff.emt_pack import C_NOTE, write_emt_pack
from gridspine.handoff.test_battery import (
    battery_json,
    build_battery,
    cases_from_json,
    corner_level,
)
from gridspine.ingest.campus import build_campus
from gridspine.schema.contracts import ContractError
from tests.gridspine.test_emt_pack import spec

V1_FIXTURE = Path(__file__).parent / "data" / "battery" / "battery_v1.json"

# Hand values. |Z1| = c Un^2 / Sk, c = 1.1 (c_max) on Sk-min and 1.0 (c_min above 1 kV) on Sk-max;
# X1 = |Z1| / sqrt(1 + (R/X)^2); X0 = (X0/X1) X1; R0 = (R0/X0) X0.
_X1_MAX = 1.0 * 110.0**2 / 3000.0 / math.sqrt(1.0 + 0.1**2)
_X1_MIN = 1.1 * 110.0**2 / 2000.0 / math.sqrt(1.0 + 0.12**2)
Z0_OHM = {
    "skmax": (0.1 * 3.0 * _X1_MAX, 3.0 * _X1_MAX),
    "skmin": (0.2 * 2.5 * _X1_MIN, 2.5 * _X1_MIN),
}
# Min case not held: the Sk-min corners take the max case's ratios on their own |Z1|:
# |Z0| = 3.0 * 1.1 * 110^2 / 2000 at atan(X0/R0) = atan(10).
_Z0_MIN_FROM_MAX = 3.0 * 1.1 * 110.0**2 / 2000.0
Z0_OHM_MIN_ASSUMED = (
    _Z0_MIN_FROM_MAX / math.sqrt(101.0),
    _Z0_MIN_FROM_MAX * 10.0 / math.sqrt(101.0),
)


def _pack(tmp_path, **pcc):
    p = write_emt_pack(tmp_path / "emt", build_campus(spec(**pcc)))
    sched = json.loads((p.path / "case_schedule.json").read_text())
    return p, sched


@pytest.fixture
def held(tmp_path):
    return _pack(tmp_path)


def test_every_schedule_row_has_its_corner_z0_by_hand(held):
    _, sched = held
    seen = set()
    for r in sched:
        level = "skmax" if "_skmax_" in r["case_id"] else "skmin"
        seen.add(level)
        r0, x0 = Z0_OHM[level]
        assert r["r0_ohm"] == pytest.approx(r0, rel=1e-12), r["case_id"]
        assert r["x0_ohm"] == pytest.approx(x0, rel=1e-12), r["case_id"]
    assert seen == {"skmax", "skmin"}


@pytest.mark.parametrize(
    "label", ["skmax_xrmin", "skmax_xrmax", "skmin_xrmin", "skmin_xrmax"]
)
def test_each_corner_fault_1ph_uses_its_own_z0(held, label):
    _, sched = held
    (row,) = [r for r in sched if r["case_id"] == f"fault_1ph_{label}_v000"]
    r0, x0 = Z0_OHM[label.split("_")[0]]
    assert (row["r0_ohm"], row["x0_ohm"]) == (
        pytest.approx(r0, rel=1e-12),
        pytest.approx(x0, rel=1e-12),
    )


def test_max_and_min_z0_differ_so_a_swap_is_seen():
    # guards the hand oracle itself: a max/min swap must change every value
    assert all(abs(a - b) > 1.0 for a, b in zip(Z0_OHM["skmax"], Z0_OHM["skmin"]))


def test_min_not_held_skmin_rows_use_the_max_ratios_and_are_assumed(tmp_path):
    p, sched = _pack(tmp_path, x0x_min=None, r0x0_min=None)
    skmin = [r for r in sched if "_skmin_" in r["case_id"]]
    for r in sched:
        want = Z0_OHM_MIN_ASSUMED if "_skmin_" in r["case_id"] else Z0_OHM["skmax"]
        assert r["r0_ohm"] == pytest.approx(want[0], rel=1e-12), r["case_id"]
        assert r["x0_ohm"] == pytest.approx(want[1], rel=1e-12), r["case_id"]
    assert sorted(p.manifest["z0_assumed"]) == sorted(r["case_id"] for r in skmin)
    assert set(p.manifest["z0_assumed"].values()) == {"assumed: from max"}
    assert not any("_skmax_" in cid for cid in p.manifest["z0_assumed"])


def test_held_min_assumes_nothing(held):
    p, _ = held
    assert p.manifest["z0_assumed"] == {}
    assert not any("Sk-min corners" in a for a in p.manifest["assumptions"])
    rows = json.loads((p.path / "thevenin_set.json").read_text())["sources"]
    held_src = (
        "campus PCC corners; Z0 per corner: Sk-max corners from the max case, "
        "Sk-min corners from the min case"
    )
    assert {r["source"] for r in rows if r["group"] == "battery"} == {held_src}


def test_min_not_held_marks_only_the_skmin_corner_rows(tmp_path):
    p, _ = _pack(tmp_path, x0x_min=None, r0x0_min=None)
    assert [a for a in p.manifest["assumptions"] if "Sk-min corners" in a] == [
        "battery zero sequence at the Sk-min corners: assumed: from max "
        "(x0x_max, r0x0_max); x0x_min, r0x0_min not held"
    ]
    rows = json.loads((p.path / "thevenin_set.json").read_text())["sources"]
    src = {r["label"]: r["source"] for r in rows if r["group"] == "battery"}
    base = (
        "campus PCC corners; Z0 Sk-max corners from the max case; Sk-min corners "
        "assumed: from max"
    )
    assert src == {
        "skmax_xrmin": base,
        "skmax_xrmax": base,
        "skmin_xrmin": base + "; this corner's Z0 assumed: from max",
        "skmin_xrmax": base + "; this corner's Z0 assumed: from max",
    }


def test_caller_battery_rows_and_readme_say_caller(tmp_path):
    camp = build_campus(spec(x0x_min=None, r0x0_min=None))
    b = emt_pack.battery_from_campus(camp)
    p = write_emt_pack(tmp_path / "emt", camp, battery=b)
    rows = json.loads((p.path / "thevenin_set.json").read_text())["sources"]
    assert {r["source"] for r in rows if r["group"] == "battery"} == {
        "caller's battery"
    }
    assert p.manifest["z0_assumed"] == {}
    text = (p.path / "README.md").read_text()
    assert (
        "Zero sequence: as in the caller's battery (battery.json); not checked against "
        "the PCC study cases." in text
    )


def test_case_schedule_refuses_a_v1_battery():
    v1 = build_battery(2000, 3000, 8, 10, z0_over_z1=3.0, x0_over_r0=10.0)
    with pytest.raises(ContractError, match="needs the 'connected' channel"):
        emt_pack.case_schedule(v1, 110.0)


@pytest.mark.parametrize("label", ["g", "max", "skmid_xrmin", ""])
def test_corner_level_refuses_a_non_corner_label(label):
    with pytest.raises(ContractError, match="is not a battery corner label"):
        corner_level(label)


def test_every_thevenin_row_still_states_the_c_note(held):
    p, _ = held
    rows = json.loads((p.path / "thevenin_set.json").read_text())["sources"]
    assert rows and all(r["note"] == C_NOTE for r in rows)
    assert "c_max on Sk-min, c_min on Sk-max" in C_NOTE
    assert any(C_NOTE in a for a in p.manifest["assumptions"])


# --- README ----------------------------------------------------------------


def test_readme_defines_connected_and_the_held_z0(held):
    p, _ = held
    text = (p.path / "README.md").read_text()
    assert "Battery contract version 2." in text
    assert "- `connected` [status]: breaker status only" in text
    assert "momentary cessation or current blocking is not a trip" in text
    assert "Without it a stays-connected verdict reads unknown." in text
    assert (
        "Zero sequence: per corner: Sk-max corners from the max case, "
        "Sk-min corners from the min case." in text
    )
    assert C_NOTE in text
    assert "README.md" in p.manifest["files"]


def test_readme_says_an_assumed_z0_never_passes_alone(tmp_path):
    p, _ = _pack(tmp_path, x0x_min=None, r0x0_min=None)
    text = (p.path / "README.md").read_text()
    assert (
        "Zero sequence: Sk-max corners from the max case; Sk-min corners assumed: from max "
        "(x0x_min, r0x0_min not held). An unbalanced-fault verdict at an Sk-min corner never "
        "passes on this assumed Z0 alone; the manifest's `z0_assumed` lists the 28 cases."
    ) in text


def test_readme_without_a_battery_says_why(tmp_path):
    p = write_emt_pack(
        tmp_path / "emt", build_campus(spec(x0x_max=None, r0x0_max=None))
    )
    text = (p.path / "README.md").read_text()
    assert "No battery: the max case's zero sequence is not held" in text


# --- build_battery: per-corner zero sequence --------------------------------


def test_scalar_pair_battery_bytes_are_unchanged_but_for_c():
    # GC-14: the pre-c fixture reads back with no c and re-serialises byte-equal (never
    # re-labelled); a new battery differs from it only by each corner's c.
    old = cases_from_json(V1_FIXTURE.read_text())
    assert {c.grid.c for c in old} == {None}
    assert battery_json(old) == V1_FIXTURE.read_text()
    battery = build_battery(1000, 3000, 3, 10, z0_over_z1=1.5, x0_over_r0=4.0)
    assert (
        battery_json(
            [
                dataclasses.replace(c, grid=dataclasses.replace(c.grid, c=None))
                for c in battery
            ]
        )
        == V1_FIXTURE.read_text()
    )


def test_equal_per_corner_mapping_equals_the_scalar_pair():
    scalar = build_battery(1000, 3000, 3, 10, z0_over_z1=1.5, x0_over_r0=4.0)
    mapped = build_battery(
        1000,
        3000,
        3,
        10,
        z0_over_z1={"skmin": 1.5, "skmax": 1.5},
        x0_over_r0={"skmin": 4.0, "skmax": 4.0},
    )
    assert battery_json(mapped) == battery_json(scalar)


def test_per_corner_mapping_reaches_each_corner():
    b = build_battery(
        1000,
        3000,
        3,
        10,
        z0_over_z1={"skmin": 2.0, "skmax": 1.5},
        x0_over_r0={"skmin": 5.0, "skmax": 4.0},
    )
    pairs = {c.grid.label: (c.grid.z0_over_z1, c.grid.x0_over_r0) for c in b}
    assert pairs == {
        "skmin_xrmin": (2.0, 5.0),
        "skmin_xrmax": (2.0, 5.0),
        "skmax_xrmin": (1.5, 4.0),
        "skmax_xrmax": (1.5, 4.0),
    }


@pytest.mark.parametrize(
    "bad", [{"skmin": 2.0}, {"skmin": 2.0, "skmax": 1.5, "x": 1.0}, {}]
)
def test_per_corner_mapping_with_wrong_keys_refused(bad):
    with pytest.raises(
        ContractError,
        match=r"x0_over_r0: a per-corner zero sequence maps exactly \['skmin', 'skmax'\]",
    ):
        build_battery(1000, 3000, 3, 10, z0_over_z1=1.5, x0_over_r0=bad)


@pytest.mark.parametrize(
    "bad", [{"skmin": 2.0}, {"skmin": 2.0, "skmax": 1.5, "x": 1.0}, {}]
)
def test_per_corner_z0z1_mapping_with_wrong_keys_refused(bad):
    with pytest.raises(
        ContractError,
        match=r"z0_over_z1: a per-corner zero sequence maps exactly \['skmin', 'skmax'\]",
    ):
        build_battery(1000, 3000, 3, 10, z0_over_z1=bad, x0_over_r0=4.0)


@pytest.mark.parametrize("bad", [math.nan, math.inf, True, "2"])
def test_per_corner_nonfinite_refused_by_level(bad):
    with pytest.raises(
        ContractError, match=r"x0_over_r0\[skmax\]=.* is not a finite number"
    ):
        build_battery(
            1000, 3000, 3, 10, z0_over_z1=1.5, x0_over_r0={"skmin": 4.0, "skmax": bad}
        )


def test_emt_pack_is_at_contract_version_two():
    assert emt_pack.EMT_CONTRACT_VERSION == "2"


def test_a_written_v2_pack_s_stub_says_what_connected_means(tmp_path):
    # GC 06c/06d (#1640) gates the sentence on the contract; at v2 the shipped pack carries it
    p = write_emt_pack(tmp_path / "emt", build_campus(spec()))
    stub = (p.path / emt_pack.STUB).read_text()
    assert stub.count(emt_pack.CONNECTED_NOTE) == 1
    assert "momentary cessation or current blocking is not a trip" in stub
