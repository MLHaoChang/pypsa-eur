"""The campus PSS/E handoff, I8 PSS/E path (plan 2026-10-07-campus-island-operation).

Oracles:
* identity: every .dyr record and every gfm_models.csv row attaches to a
  (bus number, machine ID) the .raw declares, parsed back from both files;
* values: the genset's H, x''d, x_d and droop are its island data's;
  DEGOV1's K = 1 / DROOP and TMAX = P_n / MBASE; SEXS's EMAX follows the
  excitation; the GFM's droops are converted from P_n to S_n by hand;
* the owner's decisions: REGFM records stay out of the .dyr (named in the
  CSV, pending CON-order verification); every governor is DEGOV1, ledgered;
* nothing is invented: a grid-following converter, a UPS and a genset
  without island data are omitted and listed; every drafted value is listed
  with its source.
"""
import ast
import json
import pathlib

import pandas as pd
import pytest

from gridspine.handoff import campus_bundle as CB
from gridspine.ingest.campus import build_campus
from tests.gridspine.test_dyr_writer import _dyr_records, _raw_machines, _raw_section
from tests.gridspine.test_island_flow import rows, spec
from tests.gridspine.test_island_schema import genset, vsm_bess

ROOT = pathlib.Path(__file__).resolve().parents[2]


def bundle(tmp_path, s=None, r=None, hour="h18"):
    camp = build_campus(s or spec())
    return camp, CB.export_campus_bundle(tmp_path, camp, r if r is not None else rows(), hour)


def read(b, name):
    return (b.path / name).read_text()


def test_the_bundle_stands_alone_with_its_files_and_manifest(tmp_path):
    camp, b = bundle(tmp_path)
    case = f"{camp.name}_h18"
    assert b.path.name == case
    man = json.loads(read(b, "manifest.json"))
    for f in man["files"]:
        assert (b.path / f).is_file(), f
    assert man["records"] == {"GENSAL": 1, "DEGOV1": 1, "SEXS": 1, "GENCLS": 1}
    assert man["gfm_units"] == ["BESS1"] and man["pending_verification"] == CB.PENDING


def test_every_record_attaches_to_a_machine_the_raw_declares(tmp_path):
    camp, b = bundle(tmp_path)
    case = b.path.name
    raw = {(num, mid) for num, mid, _ in _raw_machines(read(b, f"{case}.raw"))}
    recs = _dyr_records(read(b, f"{case}.dyr"))
    assert {(num, mid) for num, _, mid, _ in recs} <= raw
    gfm = pd.read_csv(b.path / "gfm_models.csv", dtype={"machine_id": str})
    assert set(zip(gfm["bus_number"], gfm["machine_id"].str.strip())) <= raw
    # the genset's three records share one identity; the grid's GENCLS has its own
    by_model = {model: (num, mid) for num, model, mid, _ in recs}
    assert by_model["GENSAL"] == by_model["DEGOV1"] == by_model["SEXS"] != by_model["GENCLS"]
    man = json.loads(read(b, "manifest.json"))
    assert tuple(man["event"]["trip_machine"]) == by_model["GENCLS"]


def _values(b, model):
    case = b.path.name
    return next(v for _, m, _, v in _dyr_records(read(b, f"{case}.dyr")) if m == model)


def test_the_genset_machine_carries_its_island_data(tmp_path):
    camp, b = bundle(tmp_path)
    g = genset()
    v = dict(zip(CB.DYR_CONS["GENSAL"], _values(b, "GENSAL")))
    assert v["h_s"] == g["H_s"]["value"] and v["xd"] == g["xd_sat"]["value"]
    assert v["xd_pp"] == pytest.approx(camp.params.set_index(["element", "param"]).at[("GEN1", "xd_pp"), "value"])
    assert v["xd"] > v["xd_p"] > v["xd_pp"] > v["xl"] > 0 and v["xq"] > v["xd_pp"]


def test_degov1_droops_on_electrical_power_with_k_its_reciprocal(tmp_path):
    camp, b = bundle(tmp_path)
    icons, cons = ("m",), ("t1", "t2", "t3", "k", "t4", "t5", "t6", "td", "tmax", "tmin", "droop", "te")
    v = dict(zip(icons + cons, _values(b, "DEGOV1")))
    assert v["m"] == 1 and v["droop"] == 0.04 and v["k"] == pytest.approx(25.0)
    assert v["tmax"] == pytest.approx(10.0 / 12.5)                  # P_n / MBASE
    assert "DEGOV1" in read(b, "README.md") and CB.LEDGER[0] in read(b, "README.md")


def test_an_isochronous_genset_has_no_droop(tmp_path):
    s = spec(gen_island={**genset(), "governor": "isochronous"})
    del s["campus"]["units"]["GEN1"]["island"]["droop_pct"]
    _, b = bundle(tmp_path, s)
    v = _values(b, "DEGOV1")
    assert v[11] == 0.0 and v[4] == pytest.approx(1.0 / CB.ISO_DROOP)


@pytest.mark.parametrize("excitation, emax", [("avr_pmg", 4.0), ("avr_shunt", 3.0)])
def test_the_exciter_follows_the_excitation(tmp_path, excitation, emax):
    _, b = bundle(tmp_path, spec(gen_island={**genset(), "excitation": excitation}))
    assert _values(b, "SEXS")[-1] == emax


def test_a_genset_without_excitation_control_gets_no_exciter(tmp_path):
    _, b = bundle(tmp_path, spec(gen_island={**genset(), "excitation": "none"}))
    assert b.records["SEXS"] == 0 and b.records["GENSAL"] == 1


def _gfm(b):
    return pd.read_csv(b.path / "gfm_models.csv").set_index("parameter")


def test_a_droop_gfm_is_named_regfm_a1_with_its_droops_on_the_inverter_base(tmp_path):
    _, b = bundle(tmp_path)
    g = _gfm(b)
    assert set(g["model"]) == {"REGFM_A1"} and set(g["unit_id"]) == {"BESS1"}
    assert g.at["mp", "value"] == pytest.approx(0.04 * 22.0 / 20.0)       # R S_n / P_n
    assert g.at["mq", "value"] == pytest.approx(0.04 * 22.0 / 20.0)
    assert (g.at["Pmax", "value"], g.at["Pmin", "value"]) == pytest.approx((20.0 / 22.0, -20.0 / 22.0))
    assert (g.at["ImaxF", "value"], g.at["TPf", "value"]) == (1.2, 0.1)
    assert g.at["kiv", "source"] == CB.SPEC["REGFM_A1"] and g.at["mp", "source"].startswith("campus")
    assert set(CB.REGFM_A1_EXAMPLE) <= set(g.index)


def test_a_vsm_is_named_regfm_b1_with_its_inertia(tmp_path):
    _, b = bundle(tmp_path, spec(bess_island=vsm_bess()))
    g = _gfm(b)
    assert set(g["model"]) == {"REGFM_B1"}
    assert g.at["H", "value"] == vsm_bess()["H_v_s"]["value"] and g.at["ESFlag", "value"] == 1
    assert set(CB.REGFM_B1_EXAMPLE) <= set(g.index)


def test_no_regfm_record_reaches_the_dyr(tmp_path):
    for s in (spec(), spec(bess_island=vsm_bess())):
        _, b = bundle(tmp_path, s)
        dyr = read(b, f"{b.path.name}.dyr")
        assert "REGFM" not in dyr and "REGCA1" not in dyr
        assert CB.PENDING in read(b, "README.md")


def test_what_has_no_data_is_omitted_and_listed_never_invented(tmp_path):
    s = spec()
    del s["campus"]["units"]["GEN1"]["island"]
    _, b = bundle(tmp_path, s)
    omitted = dict(b.omitted)
    assert set(omitted) == {"PV1", "GEN1"}
    assert "grid-following" in omitted["PV1"] and "without island data" in omitted["GEN1"]
    assert b.records["GENSAL"] == 0
    text = read(b, "README.md")
    assert "- PV1: a grid-following converter" in text and "- GEN1: a genset without island data" in text


def test_every_drafted_value_is_listed_with_its_source(tmp_path):
    _, b = bundle(tmp_path)
    text = read(b, "README.md")
    assert len(b.drafted) == 12 + 13 + 6                             # GENSAL, DEGOV1, SEXS
    for r in b.drafted.itertuples():
        assert f"| {r.unit} | {r.record} | {r.param} | {r.value:g} | {r.source} |" in text
    assert (b.drafted.set_index("param").loc["t_do_p", "source"]) == CB.TYPICAL
    assert b.drafted.query("record == 'GENSAL' and param == 'h_s'")["source"].iloc[0].startswith("campus")


def test_each_island_hour_gets_its_own_bundle_with_its_dispatch(tmp_path):
    camp = build_campus(spec())
    a = CB.export_campus_bundle(tmp_path, camp, rows(gen=5.0), "h01")
    b = CB.export_campus_bundle(tmp_path, camp, rows(gen=8.0), "h02")
    assert a.path != b.path

    def pg(bundle):
        text = read(bundle, f"{bundle.path.name}.raw")
        return sorted(float(ln.split(",")[2]) for ln in _raw_section(text, "BEGIN GENERATOR DATA",
                                                                     "END OF GENERATOR DATA"))
    assert 5.0 in pg(a) and 8.0 in pg(b)


def test_no_gridspine_module_imports_andes():
    """ANDES is GPL-3.0-or-later: it runs only in its own process (owner Q4)."""
    bad = []
    for path in (ROOT / "gridspine").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            if any(n == "andes" or n.startswith("andes.") for n in names):
                bad.append(str(path.relative_to(ROOT)))
    assert not bad


# ── closing tests (mutation round 1) ─────────────────────────────────────────

def test_a_genset_with_a_large_subtransient_reactance_keeps_the_ordering(tmp_path):
    s = spec()
    s["campus"]["units"]["GEN1"]["xd_pp"] = {"value": 0.3, "source": "datasheet"}
    _, b = bundle(tmp_path, s)
    v = dict(zip(CB.DYR_CONS["GENSAL"], _values(b, "GENSAL")))
    assert v["xd_p"] == pytest.approx(CB.XDP_OVER_XDPP * 0.3) and v["xd_p"] > v["xd_pp"] == 0.3


def test_a_ups_is_omitted_as_a_ups(tmp_path):
    from tests.gridspine.test_island_schema import ups
    s = spec()
    s["campus"]["units"]["UPS1"] = {"kind": "ups", "bus": "MV2", "p_mw": {"value": 5.0, "source": "datasheet"},
                                    "e_mwh": {"value": 2.0, "source": "datasheet"},
                                    "s_mva": {"value": 6.0, "source": "datasheet"},
                                    "island": {**ups(), "it_load": ["DC_LOAD"]}}
    r = pd.concat([rows(), pd.DataFrame({"unit_id": ["UPS1"], "p_mw": [0.0], "status": [1]})], ignore_index=True)
    _, b = bundle(tmp_path, s, r)
    assert dict(b.omitted)["UPS1"].startswith("a UPS")


def test_a_control_record_with_a_missing_value_is_refused():
    from gridspine.handoff.dyr_writer import format_record
    from gridspine.schema.contracts import ContractError
    with pytest.raises(ContractError, match="te"):
        format_record(3, "1", "SEXS", {"ta_tb": 0.1, "tb": 10.0, "k": 50.0, "emin": 0.0, "emax": 4.0})
    assert format_record(3, "1", "GENCLS", {"h": 0.0, "d": 0.0}) == "     3 'GENCLS' '1 '    0.00000   0.00000 /"


# GC-04c: the bundle hands the aggregated plant and the GC-01 model set to its PSS/E pack

#: sha256 of every file the bundle writes for the island fixture at h18 with no
#: plant or model set, recorded from origin/master 8a5b1296 (before GC-04c). A
#: change that means to alter the default output re-records these.
#: GC-14 (IEC 60909 c in the Thevenin source) re-recorded the six marked entries.
#: Its follow-up (the c note cites IEC 60909-0 Table 1) re-recorded the five EMT and top-level ones.
MASTER_SHA256 = {
    "README.md": "697a8c45a0d7f303b1d194ed8721154a625eee2dacef5caa59d786d1a59575a6",
    "TEST_HUB_h18.dyr": "605fa8911ebb98c5942d74069172afbf01713af3ca135f626845b206091923c4",
    "TEST_HUB_h18.raw": "e326b91847fc5ff6f943f6ccf7529ed9bf1e18a4b262b41619444047aef06ef7",
    "emt/README.md": "fb4939ffe8a47faf49f4ad8f9379610771fe0d4c1a09b3794659d3c527ce8995",  # GC-14: README's source-impedance line states the c bracket; GC-14 follow-up: its basis is IEC 60909-0 Table 1
    "emt/RSCAD_NOTES.md": "be12376e26799425ed992cfa063df31874849a655a581168f6fe4b1aabf3c46f",
    "emt/cables.csv": "ab71639d60c2f1f9ef6c5e6b74022ebe8170a14a91269250f7b692ba53fb12bf",
    "emt/converters.csv": "94986f0fe8b579855935079cd0534b049a595e4643b9d91a1805c8543efc3de8",
    "emt/earthing_transformers.csv": "c37f283c8998522b5f445ef65a77a0820cb8db13c1b7c9a8569053748f2cb051",
    "emt/filters.csv": "dea3c8e1b304894569137c8b04bdeed1163c841bbab0b2a3c1d8076b3b0c885d",
    "emt/gensets.csv": "c3f9f15688f2c0b527af561171aacf8ef9b2732dde1debddcb84554441a6bb8e",
    "emt/insulation.csv": "28545e4d1ba616b621789a96554cdf91a8d23e555a4dbb6134665d9b47a3e940",
    "emt/manifest.json": "99166506a34db6c97c5f5ecd738490c61524a60989bc561fcf8b226139b4b5a4",  # GC-14: assumptions carry the c-bracket C_NOTE; follow-up: IEC 60909-0 Table 1 basis
    "emt/plant_units.csv": "6b419a3a3a03dd4179da259cb635987dce13ae850f95e0d2335aad2d5e0da542",
    "emt/protection_relays.csv": "8146034b47da26b89b6e1dc7b7417c18cec06ca47301fa815b86a56be9330cc5",
    "emt/thevenin_set.csv": "f6cde30356a138b0b6edeb34de8b9bbfb02fbfaef349f2a43017c0c9e19792d5",  # GC-14: c column, |Z| = c·Un²/Sk, C_NOTE; follow-up: IEC 60909-0 Table 1 basis
    "emt/thevenin_set.json": "533a775cc47d37c4a7b213bf529360609045ec2333bb8492d854711b3857a8d2",  # GC-14: c per row, |Z| = c·Un²/Sk, C_NOTE; follow-up: IEC 60909-0 Table 1 basis
    "emt/thevenin_stages.csv": "bf2bef4209441e202a650c3cbf0c6fb890be191746d2f94007fe645aef3c1365",
    "emt/to_be_supplied.csv": "5f38eedac5f8eb50f4ff2d0fc5c367d0bfdd8d464975a6ce7b5f0ed0d4013b74",
    "emt/transformers.csv": "4c884604346c64d7008be9d76cb52a2d88a80e02173c8c98700c10b79eb2abef",
    "gfm_models.csv": "bac9e6eabd15162aba26ea15de2cf27d491c150b9b20127dec64e1c743f86077",
    "manifest.json": "942ffe28f8f6332e61032831cc434789b584bb15cb06995dcba977da25f044a3",  # GC-14: only the EMT pack's data_hash moves (the default bundle's PSS/E pack has no battery)
    "psse_v35/README.md": "b05848f4a2932fcf9531d0ffeeaacec81affa1d62557c62a0a1014b7ee273259",  # GC-14: the c README section (no battery in the default bundle, so no battery_assumed line)
    "psse_v35/TEST_HUB_h18.con": "e646da8c52d6c573434fc702d8af4f5f03c3dba47b2430bffab220d1d8302d63",
    "psse_v35/TEST_HUB_h18.conmap.csv": "4d343381be2bcda347d5fd9044ab9e6da9eb2f229a231eda4e39b1659df3d257",
    "psse_v35/TEST_HUB_h18.dyr": "1f7c7882464baa79aa2d4032d6cbf726c8ef9b22ac0e29711a03d729f9c67f61",
    "psse_v35/TEST_HUB_h18.mon": "919bd3668f0ae9dad850c9ffe65d3d901f8eb256abd78b4240533187276322d2",
    "psse_v35/TEST_HUB_h18.raw": "f0a2da11ff28cb02890c6c905c43a84978e92908c5479563a980aa142487acd0",
    "psse_v35/TEST_HUB_h18.seq": "4a2e6414ffe78e19b299e7490bfe2dbc2516e2b4d3be86e65a596f425bea547d",
    "psse_v35/TEST_HUB_h18.sub": "723fb87c92a186e6a0e78dd08f54e44e41f01f2a446eb54161cc8d3b595139b4",
    "psse_v35/manifest.json": "f1ec4db45decf47f4d5ec5166e20a6673aa373e4bdd03d8e225c1088be5641ad",
}

#: One psspy script per case of the default test battery (GC-04), by name.
SCRIPTS = [
    "run_f_ramp_skmax_xrmax_down.py",
    "run_f_ramp_skmax_xrmax_up.py",
    "run_f_step_skmax_xrmax_down.py",
    "run_f_step_skmax_xrmax_up.py",
    "run_fault_1ph_skmax_xrmax_v000.py",
    "run_fault_1ph_skmax_xrmax_v020.py",
    "run_fault_1ph_skmax_xrmax_v050.py",
    "run_fault_1ph_skmax_xrmin_v000.py",
    "run_fault_1ph_skmax_xrmin_v020.py",
    "run_fault_1ph_skmax_xrmin_v050.py",
    "run_fault_1ph_skmin_xrmax_v000.py",
    "run_fault_1ph_skmin_xrmax_v020.py",
    "run_fault_1ph_skmin_xrmax_v050.py",
    "run_fault_1ph_skmin_xrmin_v000.py",
    "run_fault_1ph_skmin_xrmin_v020.py",
    "run_fault_1ph_skmin_xrmin_v050.py",
    "run_fault_3ph_skmax_xrmax_v000.py",
    "run_fault_3ph_skmax_xrmax_v020.py",
    "run_fault_3ph_skmax_xrmax_v050.py",
    "run_fault_3ph_skmax_xrmin_v000.py",
    "run_fault_3ph_skmax_xrmin_v020.py",
    "run_fault_3ph_skmax_xrmin_v050.py",
    "run_fault_3ph_skmin_xrmax_v000.py",
    "run_fault_3ph_skmin_xrmax_v020.py",
    "run_fault_3ph_skmin_xrmax_v050.py",
    "run_fault_3ph_skmin_xrmin_v000.py",
    "run_fault_3ph_skmin_xrmin_v020.py",
    "run_fault_3ph_skmin_xrmin_v050.py",
    "run_flat_skmax_xrmax.py",
    "run_flat_skmax_xrmin.py",
    "run_flat_skmin_xrmax.py",
    "run_flat_skmin_xrmin.py",
    "run_p_step_skmax_xrmax_down.py",
    "run_p_step_skmax_xrmax_up.py",
    "run_p_step_skmax_xrmin_down.py",
    "run_p_step_skmax_xrmin_up.py",
    "run_p_step_skmin_xrmax_down.py",
    "run_p_step_skmin_xrmax_up.py",
    "run_p_step_skmin_xrmin_down.py",
    "run_p_step_skmin_xrmin_up.py",
    "run_phase_jump_skmin_xrmin_down.py",
    "run_phase_jump_skmin_xrmin_up.py",
    "run_q_step_skmax_xrmax_down.py",
    "run_q_step_skmax_xrmax_up.py",
    "run_q_step_skmax_xrmin_down.py",
    "run_q_step_skmax_xrmin_up.py",
    "run_q_step_skmin_xrmax_down.py",
    "run_q_step_skmin_xrmax_up.py",
    "run_q_step_skmin_xrmin_down.py",
    "run_q_step_skmin_xrmin_up.py",
    "run_v_step_skmax_xrmax_down.py",
    "run_v_step_skmax_xrmax_up.py",
    "run_v_step_skmax_xrmin_down.py",
    "run_v_step_skmax_xrmin_up.py",
    "run_v_step_skmin_xrmax_down.py",
    "run_v_step_skmin_xrmax_up.py",
    "run_v_step_skmin_xrmin_down.py",
    "run_v_step_skmin_xrmin_up.py",
]


def _plant_and_model_set():
    import datetime

    from gridspine.handoff.dyr_writer import PocBranch
    from gridspine.handoff.psse_pack import Plant
    from gridspine.schema.model_set import new_model_set

    ms = new_model_set(spec(), {"hour": "h18"}, aggregated=spec(),
                       now=datetime.datetime(2026, 11, 17, tzinfo=datetime.UTC))
    return Plant("PV1", PocBranch("trafo", 0, 0)), ms  # TR1, grid end at the PCC


def _wired(tmp_path, **kw):
    plant, ms = _plant_and_model_set()
    kw = {"plant": plant, "model_set": ms, "variant": "aggregated"} | kw
    return CB.export_campus_bundle(tmp_path, build_campus(spec()), rows(), "h18", **kw)


def test_a_bundle_given_the_plant_and_model_set_writes_repca1_scripts_and_the_citation(tmp_path):
    from gridspine.handoff.psse_pack import SUBDIR

    b = _wired(tmp_path)
    pack = b.path / SUBDIR
    m = json.loads((pack / "manifest.json").read_text())
    dyr = (pack / "TEST_HUB_h18.dyr").read_text().splitlines()
    # the grid on the PCC (bus 1), PV1 on MV1 (bus 2), one REPCA1 measuring TR1 (bus 2 -> PCC)
    assert "     1 'GENCLS' '1 '    0.00000   0.00000 /" in dyr
    assert sum("'REGCA1'" in ln for ln in dyr) == 1 and "     2 'REGCA1' '2 '     0" in dyr
    assert [ln for ln in dyr if "'REPCA1'" in ln] == [
        "     2 'REPCA1' '2 '     1     2     1  '1 '     0     1     1"]
    assert sorted(f for f in m["files"] if f.startswith("run_")) == SCRIPTS
    assert sorted(p.name for p in pack.glob("run_*.py")) == SCRIPTS
    assert m["model_set"] == {
        "model_set_id": "ms-8eba13315269",
        "data_hash": "8eba13315269b4117d6633adacf1ce575ef29db8babfaf76666b830c6f206fe9",
        "variant": "aggregated",
        "model_hash": "f86341d95509d9127406a0f4bb6c7b77847ff491172f5d61d71c1f7b7e260539",
    }
    assert m["plant_controller_not_written"] is None and m["scripts_not_written"] is None
    assert json.loads(read(b, "manifest.json"))["psse_pack"] == m


def test_the_bundles_pack_manifest_is_the_one_write_psse_pack_writes(tmp_path):
    """The writer -> bundle contract: the same plant and model set given to
    ``write_psse_pack`` directly yield the same manifest."""
    from gridspine.handoff.psse_pack import write_psse_pack
    from tests.gridspine.test_psse_pack import _hour_campus

    plant, ms = _plant_and_model_set()
    b = _wired(tmp_path / "bundle")
    hc, case, lines, grid = _hour_campus(tmp_path / "direct")
    direct = write_psse_pack(tmp_path / "pack", hc, case, lines, grid, plant=plant, model_set=ms,
                             variant="aggregated")
    assert json.loads(read(b, "manifest.json"))["psse_pack"] == direct


def test_without_a_plant_or_model_set_every_bundle_file_matches_master(tmp_path):
    import hashlib

    _camp, b = bundle(tmp_path)
    got = {p.relative_to(b.path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
           for p in b.path.rglob("*") if p.is_file()}
    assert got == MASTER_SHA256


def test_the_plant_and_model_set_leave_the_bundles_own_files_byte_identical(tmp_path):
    _camp, a = bundle(tmp_path / "bare")
    b = _wired(tmp_path / "wired")
    for name in ("TEST_HUB_h18.raw", "TEST_HUB_h18.dyr", "README.md", "gfm_models.csv"):
        assert (a.path / name).read_bytes() == (b.path / name).read_bytes(), name


def test_a_bundle_given_a_model_set_without_its_variant_is_refused(tmp_path):
    from gridspine.schema.contracts import ContractError

    with pytest.raises(ContractError, match="model_set and its variant together"):
        _wired(tmp_path, plant=None, variant=None)


@pytest.mark.parametrize("unit", ["GEN1", "BESS1", "NOPE"])
def test_a_bundle_given_a_plant_that_is_not_a_grid_following_converter_is_refused(tmp_path, unit):
    from gridspine.handoff.dyr_writer import PocBranch
    from gridspine.handoff.psse_pack import Plant
    from gridspine.schema.contracts import ContractError

    with pytest.raises(ContractError, match=f"plant unit '{unit}' is not a grid-following converter"):
        _wired(tmp_path, plant=Plant(unit, PocBranch("trafo", 0, 0)))
