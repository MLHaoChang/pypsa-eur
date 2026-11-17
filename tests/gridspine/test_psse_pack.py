"""
The PSS/E v35 study pack (GC-04 PR5), written next to the campus bundle.

Oracles:
* the generic WECC set (owner decision GC04-DEFAULTS-1): every grid-following
  converter's records carry the YAML's values, each unit's assumed groups
  are listed in the manifest and README, and the pack says it is not for
  submission;
* the bundle stays as it was: its own files, and the pack's .dyr starts with
  the bundle's records byte for byte;
* the .seq's own return (write_seq) is the manifest's, split into not written
  (an omitted earthing transformer, GK Q4) and assumed;
* GC-01: the model set is cited only when one is given (GK S3);
* every script is checked against the manifest's file list (GK S1).
"""

import copy
import datetime
import hashlib
import json

import pytest

from gridspine.handoff import psse_pack as PP
from gridspine.handoff.dyr_writer import PIN_NOTE, PocBranch, machine_records
from gridspine.handoff.psspy_scripts import PENDING as PSSPY_NOTE
from gridspine.handoff.psspy_scripts import check_script
from gridspine.handoff.seq_writer import NOT_WRITTEN, write_seq
from gridspine.schema.contracts import ContractError
from gridspine.schema.model_set import manifest as model_set_manifest
from gridspine.schema.model_set import new_model_set
from gridspine.templates.wecc_generic import NOT_FOR_SUBMISSION, load_generic
from tests.gridspine.test_campus import t
from tests.gridspine.test_campus_bundle import bundle
from tests.gridspine.test_dyr_wecc import _records
from tests.gridspine.test_island_flow import rows, spec
from tests.gridspine.test_island_schema import pv_qu

PLANT = PP.Plant("PV1", PocBranch("trafo", 0, 0))  # TR1, grid end at the PCC
GENERIC = load_generic()


def _pack_of(b):
    return b.path / PP.SUBDIR


def _manifest(b):
    return json.loads((_pack_of(b) / "manifest.json").read_text())


def _hour_campus(tmp_path, s=None):
    """The campus and the bundle's own inputs to the pack, as the bundle passes them."""
    camp, b = bundle(tmp_path / "b", s=s)
    from gridspine.handoff import campus_bundle as CB

    hc = CB.dataclasses.replace(camp, net=copy.deepcopy(camp.net))
    CB.apply_hour(hc, rows())
    lines = (b.path / f"{b.path.name}.dyr").read_text().splitlines()
    grid = next(
        r
        for r in _records((b.path / f"{b.path.name}.dyr").read_text())
        if r[1] == "GENCLS"
    )
    return hc, b.path.name, lines, (grid[0], grid[2])


def _write(tmp_path, s=None, **kw):
    hc, case, lines, grid = _hour_campus(tmp_path, s)
    m = PP.write_psse_pack(tmp_path / "pack", hc, case, lines, grid, **kw)
    return hc, m, tmp_path / "pack"


def _gfl_bess(s=None):
    """The fixture's BESS made grid-following: its island block removed."""
    s = copy.deepcopy(s or spec())
    s["campus"]["units"]["BESS1"].pop("island")
    return s


# --------------------------------------------------------------------------
# the hook in the campus bundle


def test_the_bundle_writes_the_pack_and_keeps_its_own_files(tmp_path):
    camp, b = bundle(tmp_path)
    man = json.loads((b.path / "manifest.json").read_text())
    assert man["files"] == [f"{b.path.name}.{e}" for e in ("raw", "dyr")] + [
        "gfm_models.csv",
        "README.md",
        "manifest.json",
    ]
    assert sorted(p.name for p in b.path.iterdir()) == sorted(
        man["files"] + [PP.SUBDIR]
    )
    assert man["psse_pack"] == _manifest(b)
    for f in man["psse_pack"]["files"]:
        assert (_pack_of(b) / f).is_file(), f


def test_the_pack_dyr_starts_with_the_bundle_records_byte_for_byte(tmp_path):
    _camp, b = bundle(tmp_path)
    own = (b.path / f"{b.path.name}.dyr").read_text()
    pack = (_pack_of(b) / f"{b.path.name}.dyr").read_text()
    assert pack.startswith(own) and len(pack) > len(own)


# --------------------------------------------------------------------------
# generic, tagged (GC04-DEFAULTS-1)


def test_a_grid_following_converter_gets_the_generic_set_tagged_assumed(tmp_path):
    hc, m, out = _write(tmp_path)
    assert m["not_for_submission"] == NOT_FOR_SUBMISSION
    assert m["assumed"] == [
        {
            "unit": "PV1",
            "models": ["REGCA1", "REECA1"],
            "source": "assumed",
            "from": "wecc_generic.yaml",
        }
    ]
    recs = _records((out / f"{m['case']}.dyr").read_text())
    regc = [r for r in recs if r[1] == "REGCA1"]
    assert len(regc) == 1
    # ICON lvplsw, then CON tg: the YAML's own values
    assert [float(v) for v in regc[0][3][:2]] == [
        GENERIC["regc"]["regc_lvplsw"],
        GENERIC["regc"]["regc_tg"],
    ]
    assert [r[1] for r in recs if r[1] in ("REECA1", "REECC1", "REPCA1")] == ["REECA1"]


def test_a_grid_following_battery_gets_reecc1(tmp_path):
    _hc, m, out = _write(tmp_path, _gfl_bess())
    assert {a["unit"]: a["models"] for a in m["assumed"]} == {
        "BESS1": ["REGCA1", "REECC1"],
        "PV1": ["REGCA1", "REECA1"],
    }
    models = [r[1] for r in _records((out / f"{m['case']}.dyr").read_text())]
    assert models.count("REECC1") == 1 and models.count("REECA1") == 1


def test_readme_marks_not_for_submission_and_lists_every_assumed_group(tmp_path):
    _hc, m, out = _write(tmp_path, _gfl_bess(), plant=PLANT)
    readme = (out / "README.md").read_text()
    assert (
        "**Not for submission until replaced with OEM data: "
        "generic WECC parameters (assumed).**"
    ) in readme
    assert "- BESS1: REGCA1, REECC1, every value assumed" in readme
    assert "- PV1: REGCA1, REECA1, REPCA1, every value assumed" in readme


def test_with_no_grid_following_converter_nothing_is_assumed(tmp_path):
    s = spec()
    u = s["campus"]["units"]
    u["PV1"]["island"] = copy.deepcopy(u["BESS1"]["island"])
    _hc, m, out = _write(tmp_path, s)
    assert m["assumed"] == [] and m["not_for_submission"] is None
    assert "not for submission" not in (out / "README.md").read_text().lower()


# --------------------------------------------------------------------------
# the aggregated plant: REPCA1 and the scripts


def test_the_plant_takes_repca1_on_its_poc_branch(tmp_path):
    _hc, m, out = _write(tmp_path, plant=PLANT)
    recs = _records((out / f"{m['case']}.dyr").read_text())
    pcc = next(
        b for b, model, _i, _t in recs if model == "GENCLS"
    )  # the grid sits on the PCC
    pv = next(
        b for b, model, _i, _t in recs if model == "REGCA1"
    )  # PV1 on MV1, TR1's plant end
    repc = [t for _b, model, _i, t in recs if model == "REPCA1"]
    assert len(repc) == 1
    busr, bus1, bus2, ckt = repc[0][:4]
    assert ckt.strip("'").strip() == "1"
    assert (int(busr), int(bus1), int(bus2)) == (pcc, pv, pcc)
    assert m["assumed"][0]["models"] == ["REGCA1", "REECA1", "REPCA1"]
    assert m["scripts_not_written"] is None


def test_without_a_plant_no_scripts_and_the_reason_is_listed(tmp_path):
    _hc, m, out = _write(tmp_path)
    assert m["outputs"] == {} and not list(out.glob("run_*.py"))
    assert m["scripts_not_written"].startswith("psspy scripts not written")
    assert m["scripts_not_written"] in (out / "README.md").read_text()


def test_every_script_passes_check_script_against_the_manifest(tmp_path):
    _hc, m, out = _write(tmp_path, plant=PLANT)
    scripts = [f for f in m["files"] if f.startswith("run_")]
    assert scripts and sorted(p.name for p in out.glob("run_*.py")) == scripts
    allowed = set(m["files"]) | {f for pair in m["outputs"].values() for f in pair}
    for name in scripts:
        check_script((out / name).read_text(), allowed)
    # a manifest that drops the .dyr fails the check (S1 missing reference)
    with pytest.raises(ContractError):
        check_script((out / scripts[0]).read_text(), allowed - {f"{m['case']}.dyr"})


def test_the_battery_built_from_the_grid_lists_its_assumptions(tmp_path):
    _hc, m, _out = _write(tmp_path, plant=PLANT)
    assert m["battery_assumed"] == list(PP.BATTERY_ASSUMED)


@pytest.mark.parametrize("unit", ["GEN1", "BESS1", "NOPE"])
def test_a_plant_that_is_not_a_grid_following_converter_is_refused(tmp_path, unit):
    with pytest.raises(
        ContractError, match=f"plant unit '{unit}' is not a grid-following converter"
    ):
        _write(tmp_path, plant=PP.Plant(unit, PLANT.branch))


# --------------------------------------------------------------------------
# .seq (GK Q4), contingencies, notes


def test_seq_notes_are_write_seqs_own_return_split_by_kind(tmp_path):
    s = spec()
    s["campus"]["earthing_transformers"] = {
        "ET1": {"bus": "MV2", "x0_ohm": t(15.0, "datasheet"), "r_n_ohm": t(25.0)}
    }
    hc, m, _out = _write(tmp_path, s)
    own = write_seq(
        hc, tmp_path / "own.seq", version=35, case="max", f_hz=float(hc.net.f_hz)
    )
    assert m["seq"]["not_written"] == [n for n in own if NOT_WRITTEN in n]
    assert m["seq"]["not_written"] == [f"seq: earthing transformer ET1: {NOT_WRITTEN}"]
    assert (
        m["seq"]["assumed"] == [n for n in own if NOT_WRITTEN not in n]
        and m["seq"]["assumed"]
    )
    readme = (_out / "README.md").read_text()
    assert f"- .seq: earthing transformer ET1: {NOT_WRITTEN}" in readme
    assert "- .seq: grid GRID: no x0x_max; X0 = X1, R0/X0 = R/X (assumed)" in readme
    assert ".seq: seq:" not in readme


def test_the_generated_contingency_set_is_written(tmp_path):
    _hc, m, out = _write(tmp_path)
    con = (out / f"{m['case']}.con").read_text()
    assert m["contingencies"]["written"] == con.count("CONTINGENCY ") > 0
    assert m["contingencies"]["not_written"] == []


def test_pending_notes_in_manifest_and_readme(tmp_path):
    _hc, m, out = _write(tmp_path)
    readme = (out / "README.md").read_text()
    assert (
        PIN_NOTE in m["pending_verification"]
        and PP.BUSR_NOTE in m["pending_verification"]
    )
    assert (
        PP.BUSR_NOTE
        == "BUSR = BUS2, BUS1→BUS2 export: pending verification on first PSS/E load"
    )
    for note in m["pending_verification"]:
        assert f"- {note}" in readme


# --------------------------------------------------------------------------
# GC-01 model set (GK S3)

_NOW = datetime.datetime(2026, 11, 17, tzinfo=datetime.UTC)


def test_the_model_set_is_cited_when_given(tmp_path):
    ms = new_model_set(spec(), {"hour": "h18"}, now=_NOW)
    _hc, m, out = _write(tmp_path, model_set=ms, variant="detailed")
    assert m["model_set"] == model_set_manifest(ms, "detailed")
    readme = (out / "README.md").read_text()
    assert f"- model_set_id: `{ms.model_set_id}`" in readme
    assert (
        f"- data_hash: `{ms.data_hash}`" in readme and "- variant: `detailed`" in readme
    )


def test_no_model_set_no_citation(tmp_path):
    _hc, m, out = _write(tmp_path)
    assert "model_set" not in m
    assert "model_set_id" not in (out / "README.md").read_text()


def test_a_model_set_without_its_variant_is_refused(tmp_path):
    ms = new_model_set(spec(), {}, now=_NOW)
    with pytest.raises(ContractError, match="model_set and its variant together"):
        _write(tmp_path, model_set=ms)


def test_a_variant_without_its_model_set_is_refused(tmp_path):
    with pytest.raises(ContractError, match="model_set and its variant together"):
        _write(tmp_path, variant="detailed")


def test_the_mon_band_is_listed_as_assumed(tmp_path):
    _hc, m, out = _write(tmp_path)
    assert m["mon_assumed"] == PP.MON_ASSUMED
    assert f"- {PP.MON_ASSUMED}" in (out / "README.md").read_text()
    assert "0.900 1.100" in (out / f"{m['case']}.mon").read_text()


# --------------------------------------------------------------------------
# mutant kills: the guards and exact outputs


def test_grid_following_reads_the_kind_and_the_island_control(tmp_path):
    # PV1 carries an island block with a grid-following control (gfl_qu)
    hc, _case, _lines, _grid = _hour_campus(tmp_path, spec(pv_island=pv_qu()))
    assert {
        u: PP.grid_following(hc, u)
        for u in ("PV1", "BESS1", "GEN1", "GRID", "NOPE", "DC_LOAD")
    } == {
        "PV1": True,
        "BESS1": False,
        "GEN1": False,
        "GRID": False,
        "NOPE": False,
        "DC_LOAD": False,
    }


def test_a_gfl_island_block_still_takes_the_generic_set(tmp_path):
    _hc, m, _out = _write(tmp_path, spec(pv_island=pv_qu()))
    assert [a["unit"] for a in m["assumed"]] == ["PV1"]


def test_out_may_be_a_string(tmp_path):
    hc, case, lines, grid = _hour_campus(tmp_path)
    m = PP.write_psse_pack(str(tmp_path / "pack"), hc, case, lines, grid)
    assert json.loads((tmp_path / "pack" / "manifest.json").read_text()) == m


def test_the_manifest_lists_exactly_the_files_written(tmp_path):
    _hc, m, out = _write(tmp_path)
    case = m["case"]
    assert m["files"] == [
        f"{case}.{e}" for e in ("raw", "seq", "dyr", "con", "sub", "mon")
    ] + ["README.md", "manifest.json"]
    assert sorted(p.name for p in out.iterdir()) == sorted(m["files"])


def test_pending_verification_is_the_three_notes_in_order(tmp_path):
    _hc, m, _out = _write(tmp_path)
    assert m["pending_verification"] == [PIN_NOTE, PP.BUSR_NOTE, PSSPY_NOTE]


def test_readme_says_none_assumed_and_nothing_not_written(tmp_path):
    s = spec()
    u = s["campus"]["units"]
    u["PV1"]["island"] = copy.deepcopy(u["BESS1"]["island"])
    _hc, _m, out = _write(tmp_path, s)
    assert "## Assumed values\n\n- none\n" in (out / "README.md").read_text()
    # BESS1 grid-following, so every RAW machine has a .dyr record
    _hc, m, out = _write(tmp_path / "plant", _gfl_bess(), plant=PLANT)
    assert m["seq"]["not_written"] == [] and m["contingencies"]["not_written"] == []
    assert m["dyr"]["not_written"] == []
    assert "## Not written\n\n- nothing\n" in (out / "README.md").read_text()


def test_the_scripts_carry_the_grid_machines_own_mbase(tmp_path):
    hc, m, out = _write(tmp_path, plant=PLANT)
    grid = next(r for r in machine_records(hc.net) if r[0] == "GRID")
    assert grid[3] == 100.0
    line = f"GRID_BUS, GRID_ID, GRID_MBASE = {grid[1]!r}, {grid[2]!r}, 100.0"
    scripts = [f for f in m["files"] if f.startswith("run_")]
    assert scripts
    for name in scripts:
        assert line in (out / name).read_text().splitlines(), name


def test_a_grid_machine_not_in_the_raw_is_refused(tmp_path):
    hc, case, lines, _grid = _hour_campus(tmp_path)
    for bad in [(999999, "1"), None]:
        with pytest.raises(
            ContractError, match=r"grid machine .* is not a machine of the RAW"
        ):
            PP.write_psse_pack(tmp_path / "pack", hc, case, lines, bad, plant=PLANT)


@pytest.mark.parametrize("col", ["rx_max", "rx_min"])
def test_a_zero_grid_rx_is_refused_when_building_the_battery(tmp_path, col):
    hc, case, lines, grid = _hour_campus(tmp_path)
    hc.net.ext_grid[col] = 0.0
    with pytest.raises(ContractError, match=f"the grid's {col}=0.0 must be > 0"):
        PP.write_psse_pack(tmp_path / "pack", hc, case, lines, grid, plant=PLANT)


#: sha256 of the campus fixture's TEST_HUB_h18.dyr as origin/master 5400f7fd wrote it
#: (owner decision GC04-DEFAULTS-1: the campus bundle's own files are unchanged).
_CAMPUS_DYR_SHA256 = "605fa8911ebb98c5942d74069172afbf01713af3ca135f626845b206091923c4"


def test_the_campus_bundle_dyr_bytes_are_masters(tmp_path):
    _camp, b = bundle(tmp_path)
    data = (b.path / f"{b.path.name}.dyr").read_bytes()
    assert hashlib.sha256(data).hexdigest() == _CAMPUS_DYR_SHA256


def test_the_readme_states_the_transformer_impedance_convention(tmp_path):
    _hc, _m, out = _write(tmp_path)
    readme = (out / "README.md").read_text()
    assert "## Transformer impedance\n\n" + PP.TRAFO_NOTE in readme
    assert "Packs written before this fix carry an impedance too high" in PP.TRAFO_NOTE


# --------------------------------------------------------------------------
# RAW machines with no .dyr record, and the missing plant controller


def test_raw_machines_without_a_dyr_record_are_listed_with_their_reason(tmp_path):
    import pandapower as pp

    hc, case, lines, grid = _hour_campus(tmp_path)
    # a compensator in the RAW that is not a campus unit, as a STATCOM is
    pp.create_sgen(hc.net, bus=hc.net.sgen.at[0, "bus"], p_mw=0.0, name="STAT1")
    m = PP.write_psse_pack(tmp_path / "pack", hc, case, lines, grid)
    ids = {r[0]: (r[1], r[2].strip()) for r in machine_records(hc.net)}
    control = hc.island["units"]["BESS1"]["control"]
    assert control in ("gfm_droop", "gfm_vsm")
    assert m["dyr"]["not_written"] == [
        {
            "unit": "BESS1",
            "bus": ids["BESS1"][0],
            "id": ids["BESS1"][1],
            "reason": f"grid-forming converter ({control}): no generic grid-forming model; "
            "its parameters are in the campus bundle's GFM table (gfm_models.csv) only",
        },
        {
            "unit": "STAT1",
            "bus": ids["STAT1"][0],
            "id": ids["STAT1"][1],
            "reason": "not a campus unit (a compensator such as a STATCOM): "
            "no dynamic model supplied",
        },
    ]
    # every machine not listed has a record in the pack's .dyr
    recs = _records((tmp_path / "pack" / f"{case}.dyr").read_text())
    written = {(b, i) for b, _model, i, _t in recs}
    for uid, (num, mid) in ids.items():
        assert ((num, mid) in written) == (uid not in ("BESS1", "STAT1")), uid
    readme = (tmp_path / "pack" / "README.md").read_text()
    for d in m["dyr"]["not_written"]:
        assert (
            f"- .dyr: {d['unit']} (bus {d['bus']}, id '{d['id']}'): {d['reason']}\n"
            in readme
        )


def test_a_machine_of_a_kind_with_no_record_is_listed_by_its_kind(tmp_path):
    hc, case, lines, grid = _hour_campus(tmp_path)
    gen1 = next(r for r in machine_records(hc.net) if r[0] == "GEN1")
    head = f"{gen1[1]:>6d} "
    own = [ln for ln in lines if not ln.startswith(head)]
    assert len(own) < len(lines)  # GEN1's records dropped from the bundle's
    m = PP.write_psse_pack(tmp_path / "pack", hc, case, own, grid)
    assert {
        "unit": "GEN1",
        "bus": gen1[1],
        "id": gen1[2].strip(),
        "reason": "genset: no dynamic record supplied",
    } in m["dyr"]["not_written"]


def test_without_a_plant_the_readme_says_no_plant_controller(tmp_path):
    _hc, m, out = _write(tmp_path)
    assert m["plant_controller_not_written"] == PP._REPCA1_NEEDS_PLANT
    assert (
        "a pack written by the campus bundle has none yet"
        in m["plant_controller_not_written"]
    )
    assert f"- {PP._REPCA1_NEEDS_PLANT}\n" in (out / "README.md").read_text()
    _hc, m, out = _write(tmp_path / "plant", plant=PLANT)
    assert m["plant_controller_not_written"] is None
    assert (
        "plant controller (REPCA1) not written" not in (out / "README.md").read_text()
    )
