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
