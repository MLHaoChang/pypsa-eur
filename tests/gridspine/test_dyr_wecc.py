"""
GC-04 part 3: the WECC second-generation .dyr set beyond REGCA1 + REECA1.

REECC1 (battery electrical control), REPCA1 (plant controller on the PoC
branch) and the type 3 wind set (WTDTA1, WTARA1, WTPTA1, WTTQA1), the
``version=`` argument (33 or 35), the model pins and their sources, and the
refusals: user-only models (REGC_B, REEC_D), a model the version does not
ship, a missing or ambiguous PoC branch, a contradictory flag set and every
range rule of the new groups. The REGCA1/REECA1 and synchronous output stays
byte-identical to master (golden file).
"""

import hashlib
import re
from pathlib import Path

import pandapower as pp
import pandas as pd
import pytest

from gridspine.handoff import dyr_writer
from gridspine.handoff.dyr_writer import (
    MODEL_PIN,
    PIN_NOTE,
    PocBranch,
    UserModelOnly,
    check_model,
    write_dyr,
)
from gridspine.handoff.raw_writer import write_raw
from gridspine.ingest.pandapower_source import load_case39_res
from gridspine.schema.contracts import ContractError
from gridspine.templates import unit_params as up
from gridspine.templates.unit_params import (
    ibr_params,
    load_unit_params,
    load_unit_templates,
    wecc_params,
)
from tests.gridspine.test_dyr_writer import (
    _IBR,
    _fields,
    _raw_machines,
    _raw_section,
    _toy_net_with_inverter,
    _toy_templates,
)

DATA = Path(__file__).parent / "data"

# Distinct printed values, so a swapped pair of CONs cannot hide.
REPCA1 = {
    "repc_vcflag": 1,
    "repc_refflag": 1,
    "repc_fflag": 0,
    "repc_tfltr": 0.021,
    "repc_kp": 18.0,
    "repc_ki": 5.0,
    "repc_tft": 0.001,
    "repc_tfv": 0.075,
    "repc_vfrz": 0.002,
    "repc_rc": 0.003,
    "repc_xc": 0.004,
    "repc_kc": 0.005,
    "repc_emax": 0.1,
    "repc_emin": -0.1,
    "repc_dbd1": -0.006,
    "repc_dbd2": 0.007,
    "repc_qmax": 0.436,
    "repc_qmin": -0.437,
    "repc_kpg": 0.11,
    "repc_kig": 0.05,
    "repc_tp": 0.25,
    "repc_fdbd1": -0.0006,
    "repc_fdbd2": 0.0008,
    "repc_femax": 999.0,
    "repc_femin": -998.0,
    "repc_pmax": 1.0,
    "repc_pmin": 0.008,
    "repc_tg": 0.12,
    "repc_ddn": 20.0,
    "repc_dup": 0.009,
}
WT3 = {
    "wtdt_h": 4.95,
    "wtdt_damp": 0.01,
    "wtdt_htfrac": 0.875,
    "wtdt_freq1": 1.8,
    "wtdt_dshaft": 1.5,
    "wtar_ka": 0.007,
    "wtar_theta0": 0.0,
    "wtpt_kiw": 25.0,
    "wtpt_kpw": 150.0,
    "wtpt_kic": 30.0,
    "wtpt_kpc": 3.0,
    "wtpt_kcc": 0.0,
    "wtpt_tp": 0.3,
    "wtpt_thetamax": 27.0,
    "wtpt_thetamin": -0.5,
    "wtpt_rthetamax": 10.0,
    "wtpt_rthetamin": -10.0,
    "wttq_tflag": 0,
    "wttq_kpp": 3.0,
    "wttq_kip": 0.61,
    "wttq_tp": 0.05,
    "wttq_twref": 60.0,
    "wttq_temax": 1.2,
    "wttq_temin": 0.0,
    "wttq_p1": 0.2,
    "wttq_spd1": 0.58,
    "wttq_p2": 0.4,
    "wttq_spd2": 0.72,
    "wttq_p3": 0.6,
    "wttq_spd3": 0.86,
    "wttq_p4": 0.8,
    "wttq_spd4": 1.02,
    "wttq_trate": 55.0,
}
_DROPPED = ("reec_pflag", "reec_iqfrz", "reec_thld", "reec_thld2", "reec_vbias")
REECC1 = {k: v for k, v in _IBR.items() if k not in _DROPPED} | {
    "reec_t": 999.0,
    "reec_socini": 0.5,
    "reec_socmax": 0.8,
    "reec_socmin": 0.2,
}
PLANT = _IBR | REPCA1  # REGCA1 + REECA1 + REPCA1
WIND3 = _IBR | {"reec_pflag": 1} | WT3  # type 3, no plant controller

_TOKEN = re.compile(r"'[^']*'|\S+")


def _records(text):
    """(bus, model, id, [tokens]) per record; a quoted CKT stays a string."""
    out, pending = [], []
    for ln in text.splitlines():
        pending.append(ln)
        if not ln.rstrip().endswith("/"):
            continue
        toks = _TOKEN.findall(" ".join(pending).rstrip()[:-1])
        pending = []
        out.append(
            (int(toks[0]), toks[1].strip("'"), toks[2].strip("'").strip(), toks[3:])
        )
    return out


def _net():
    """
    The toy (ext grid BUS_01, gen + inverter on BUS_02) with a second
    BUS_01 -> BUS_02 line. Both lines run FROM the grid bus, so the RAW's I is
    the grid side and the PoC line (the second) is CKT '2'.
    """
    net = _toy_net_with_inverter()
    pp.create_line_from_parameters(
        net,
        from_bus=0,
        to_bus=1,
        length_km=1.0,
        r_ohm_per_km=11.9025,
        x_ohm_per_km=119.025,
        c_nf_per_km=0.0,
        max_i_ka=0.418,
    )
    return net


POC = {"W_BUS_02": PocBranch("line", 1, 0)}


def _write(tmp_path, ibr, poc=None, version=33, net=None):
    params, templates = _toy_templates(tmp_path, ibr=ibr)
    net = _net() if net is None else net
    path = tmp_path / "t.dyr"
    write_dyr(net, params, path, ibr=wecc_params(templates), version=version, poc=poc)
    return net, path.read_text()


def _by_model(text):
    return {m: (b, i, t) for b, m, i, t in _records(text)}


# --- pins (P1, D1) --------------------------------------------------------------


def test_every_pin_matches_its_tuples_and_names_a_source():
    authored = {"REECA1": 1, "REECC1": 1, "REPCA1": 4}  # BUSR; BUSR BUS1 BUS2 CKT
    layouts = {
        "REGCA1": (up.REGCA1_ICONS, up.REGCA1_CONS),
        "REECA1": (up.REECA1_ICONS, up.REECA1_CONS),
        "REECC1": (up.REECC1_ICONS, up.REECC1_CONS),
        "REPCA1": (up.REPCA1_ICONS, up.REPCA1_CONS),
        "WTDTA1": ((), up.WTDTA1_CONS),
        "WTARA1": ((), up.WTARA1_CONS),
        "WTPTA1": ((), up.WTPTA1_CONS),
        "WTTQA1": (up.WTTQA1_ICONS, up.WTTQA1_CONS),
    }
    assert set(layouts) == set(MODEL_PIN)
    for model, (icons, cons) in layouts.items():
        n_icon, n_con, first, sources = MODEL_PIN[model]
        assert (len(icons) + authored.get(model, 0), len(cons)) == (n_icon, n_con), (
            model
        )
        assert first in (33, 35)
        assert sources and all("/" in s and "(" in s for s in sources), model


def test_pinned_counts_hand_listed():
    """Hand-listed from the hand-off research, not from MODEL_PIN."""
    assert {m: p[:3] for m, p in MODEL_PIN.items()} == {
        "REGCA1": (1, 14, 33),
        "REECA1": (6, 45, 33),
        "REECC1": (5, 45, 35),
        "REPCA1": (7, 27, 33),
        "WTDTA1": (0, 5, 33),
        "WTARA1": (0, 2, 33),
        "WTPTA1": (0, 10, 33),
        "WTTQA1": (1, 15, 33),
    }


def test_pin_note_says_public_parser_tables_not_the_manual():
    assert PIN_NOTE == (
        "dyr ICON/CON counts and order from public parser tables; "
        "pending verification on first PSS/E load"
    )
    assert "manual" not in " ".join(s for p in MODEL_PIN.values() for s in p[3]).lower()


def test_a_record_off_its_pin_is_refused(monkeypatch):
    monkeypatch.setitem(MODEL_PIN, "WTARA1", (0, 3, 33, ("x/y (z)",)))
    with pytest.raises(ContractError, match="WTARA1: 0 ICONs / 2 CONs, pinned 0 / 3"):
        dyr_writer._record(1, "1", "WTARA1", [], [1.0, 2.0], 33)


# --- user-only models and versions (decision (a), P6) ----------------------------


@pytest.mark.parametrize("model", ["REGCB1", "REECD1"])
def test_user_only_models_are_refused_by_name(model):
    with pytest.raises(
        UserModelOnly, match="user model only \\(USRMDL\\) up to 35.2; not written"
    ):
        check_model(model, 35)
    assert issubclass(UserModelOnly, ContractError)


def test_an_unpinned_model_is_refused():
    with pytest.raises(ContractError, match="no pinned .dyr layout for 'REPCB1'"):
        check_model("REPCB1", 35)


@pytest.mark.parametrize("version", [32, 34, 36, "35", 35.0 + 1e-9])
def test_a_version_outside_33_35_is_refused(tmp_path, version):
    with pytest.raises(ContractError, match="not supported; supported \\(33, 35\\)"):
        _write(tmp_path, PLANT, POC, version=version)


def test_v35_of_a_v33_set_is_byte_identical(tmp_path):
    _, v33 = _write(tmp_path, PLANT | WT3 | {"reec_pflag": 1}, POC, version=33)
    _, v35 = _write(tmp_path, PLANT | WT3 | {"reec_pflag": 1}, POC, version=35)
    assert v33 == v35 and "REPCA1" in v33 and "WTTQA1" in v33


def test_reecc1_is_refused_for_v33(tmp_path):
    with pytest.raises(
        ContractError,
        match="REECC1 is a PSS/E library model from version 35; not in v33",
    ):
        _write(tmp_path, REECC1, version=33)


def test_case39_dyr_is_byte_identical_to_master(tmp_path):
    """
    Golden: master 58d4eb5ae738d697236575580fd2b0d7e83615e3's writer, case39
    with its five REGCA1 + REECA1 wind units.
    """
    net = load_case39_res()
    t = load_unit_templates()
    for ibr in (ibr_params(t), wecc_params(t)):
        write_dyr(net, load_unit_params(), tmp_path / "c.dyr", ibr=ibr)
        assert (tmp_path / "c.dyr").read_bytes() == (DATA / "case39.dyr").read_bytes()


# --- REECC1 ----------------------------------------------------------------------


def test_reecc1_record_layout(tmp_path):
    _, text = _write(tmp_path, REECC1, version=35)
    recs = _by_model(text)
    assert "REECA1" not in recs
    _, mid, toks = recs["REECC1"]
    assert mid == "2"
    icons, cons = [int(t) for t in toks[:5]], [float(t) for t in toks[5:]]
    assert icons == [0, 0, 1, 0, 1]  # BUSR PFFLAG VFLAG QFLAG PQFLAG
    assert len(cons) == 45
    assert cons == [REECC1[n] for n in up.REECC1_CONS]
    assert cons[-4:] == [999.0, 0.5, 0.8, 0.2]  # T SOCini SOCmax SOCmin
    assert cons[9] == REECC1["reec_tp"]  # Tp follows Vref0 (no Iqfrz/Thld/Thld2)


def test_reecc1_units_are_not_in_ibr_params(tmp_path):
    _, templates = _toy_templates(tmp_path, ibr=REECC1)
    assert "W_BUS_02" not in ibr_params(templates).index
    assert "W_BUS_02" in wecc_params(templates).index


# --- REPCA1 on the PoC branch (P2, P3, D2, D3) -------------------------------------


def test_repca1_bus1_is_the_plant_side_when_raw_i_is_the_grid_side(tmp_path):
    net, text = _write(tmp_path, PLANT, POC)
    write_raw(net, tmp_path / "t.raw")
    raw = (tmp_path / "t.raw").read_text()
    branch = [
        _fields(ln)
        for ln in _raw_section(raw, "BEGIN BRANCH DATA", "END OF BRANCH DATA")
    ]
    poc = branch[1]
    assert (int(poc[0]), int(poc[1]), poc[2]) == (
        1,
        2,
        "2",
    )  # RAW I = grid bus 1, CKT '2'
    bus, mid, toks = _by_model(text)["REPCA1"]
    assert (bus, mid) == (2, "2")
    assert [int(t) for t in toks[:3]] == [1, 2, 1]  # BUSR = PoC, BUS1 plant, BUS2 grid
    assert toks[3] == "'2 '"  # the RAW's CKT
    assert [int(t) for t in toks[4:7]] == [1, 1, 0]
    assert [float(t) for t in toks[7:]] == [REPCA1[n] for n in up.REPCA1_CONS]


def test_repca1_on_a_trafo_takes_the_trafo_ckt(tmp_path):
    net = _toy_net_with_inverter()
    b3 = pp.create_bus(net, vn_kv=33.0, name="BUS_03")
    pp.create_transformer_from_parameters(
        net,
        hv_bus=1,
        lv_bus=b3,
        sn_mva=60.0,
        vn_hv_kv=345.0,
        vn_lv_kv=33.0,
        vkr_percent=0.5,
        vk_percent=12.0,
        pfe_kw=0.0,
        i0_percent=0.0,
    )
    net.sgen.at[0, "bus"] = b3
    _, text = _write(tmp_path, PLANT, {"W_BUS_02": PocBranch("trafo", 0, 1)}, net=net)
    _, _, toks = _by_model(text)["REPCA1"]
    assert [int(t) for t in toks[:3]] == [2, 3, 2] and toks[3] == "'1 '"


def test_every_regca1_machine_of_a_controlled_plant_has_exactly_one_repca1(tmp_path):
    net, text = _write(tmp_path, PLANT, POC)
    recs = _records(text)
    regca = {(b, i) for b, m, i, _ in recs if m == "REGCA1"}
    repca = [(b, i) for b, m, i, _ in recs if m == "REPCA1"]
    assert regca and sorted(repca) == sorted(regca)
    write_raw(net, tmp_path / "t.raw")
    raw_ids = {(b, i) for b, i, _ in _raw_machines((tmp_path / "t.raw").read_text())}
    assert set(repca) <= raw_ids


def test_two_machines_on_one_poc_branch_are_refused(tmp_path):
    net = _net()
    pp.create_sgen(net, bus=1, p_mw=20.0, sn_mva=50.0, name="W2")
    poc = {"W_BUS_02": PocBranch("line", 1, 0), "W2": PocBranch("line", 1, 0)}
    with pytest.raises(
        ContractError, match="share PoC branch .* export the GC-01 aggregated model"
    ):
        dyr_writer._poc_ids(net, poc)


@pytest.mark.parametrize(
    "poc, reason",
    [
        (
            PocBranch("line", 7, 0),
            "line 7 is not in the network; REPCA1 is not written",
        ),
        (PocBranch("bus", 0, 0), "table 'bus' is not 'line' or 'trafo'"),
        (PocBranch("line", 1, 5), "grid bus 5 is not an end of line 1"),
    ],
)
def test_a_bad_poc_branch_is_refused(tmp_path, poc, reason):
    with pytest.raises(ContractError, match=reason):
        _write(tmp_path, PLANT, {"W_BUS_02": poc})


def test_a_repca1_group_without_a_poc_branch_is_refused(tmp_path):
    with pytest.raises(ContractError, match="REPCA1 group but has no PoC branch"):
        _write(tmp_path, PLANT, None)


def test_a_poc_branch_for_a_unit_without_repca1_is_refused(tmp_path):
    with pytest.raises(ContractError, match="write no REPCA1 record"):
        _write(tmp_path, _IBR, POC)


# --- type 3 wind -----------------------------------------------------------------


def test_type3_records_in_order_with_their_values(tmp_path):
    _, text = _write(tmp_path, WIND3)
    recs = [r for r in _records(text) if r[2] == "2"]
    assert [m for _, m, _, _ in recs] == [
        "REGCA1",
        "REECA1",
        "WTDTA1",
        "WTARA1",
        "WTPTA1",
        "WTTQA1",
    ]
    by = {m: t for _, m, _, t in recs}
    for model, icons, cons in dyr_writer.WT3_RECORDS:
        toks = by[model]
        assert [int(t) for t in toks[: len(icons)]] == [WIND3[n] for n in icons]
        assert [float(t) for t in toks[len(icons) :]] == [WIND3[n] for n in cons]


# --- template refusals: flags (P4) and ranges (P5) ----------------------------------


def _load(tmp_path, ibr):
    _toy_templates(tmp_path, ibr=ibr)


@pytest.mark.parametrize(
    "ibr, reason",
    [
        (PLANT | {"reec_pfflag": 1}, "reec_pfflag must be 0 under plant control"),
        (WIND3 | {"reec_pflag": 0}, "WTTQA1\\) needs reec_pflag = 1"),
    ],
)
def test_contradictory_flag_sets_are_refused(tmp_path, ibr, reason):
    with pytest.raises(ContractError, match=reason):
        _load(tmp_path, ibr)


def test_consistent_flag_sets_load(tmp_path):
    _load(tmp_path, PLANT | WT3 | {"reec_pflag": 1})
    _load(tmp_path, REECC1 | REPCA1)


@pytest.mark.parametrize(
    "ibr, reason",
    [
        (
            {k: v for k, v in PLANT.items() if k != "repc_dup"},
            "repca1 group is all-or-nothing; missing \\['repc_dup'\\]",
        ),
        (
            {k: v for k, v in WIND3.items() if k != "wtar_ka"},
            "wt3 group is all-or-nothing",
        ),
        ({"k_sc": 1.0} | REPCA1, "repca1 group needs the REGCA1/REECA1 group"),
        (PLANT | {"repc_fflag": 2}, "repc_fflag is a switch"),
        (WIND3 | {"wtdt_h": 0.0}, "wtdt_h must be > 0"),
        (WIND3 | {"wttq_trate": -1.0}, "wttq_trate must be > 0"),
        (PLANT | {"repc_tfltr": -0.01}, "repc_tfltr must be >= 0"),
        (WIND3 | {"wtdt_htfrac": 1.0}, "wtdt_htfrac .* must lie in \\(0, 1\\)"),
        (PLANT | {"repc_qmin": 0.436}, "repc_qmin must be < repc_qmax"),
        (PLANT | {"repc_dbd1": 0.01}, "repc_dbd1 must be <= repc_dbd2"),
        (WIND3 | {"wtpt_thetamin": 27.0}, "wtpt_thetamin must be < wtpt_thetamax"),
        (
            WIND3 | {"wttq_spd3": 0.72},
            "breakpoints wttq_spd1..4 must strictly increase",
        ),
        (WIND3 | {"wttq_p2": 0.1}, "breakpoints wttq_p1..4 must strictly increase"),
        (REECC1 | {"reec_t": 0.0}, "reec_t must be > 0"),
        (REECC1 | {"reec_socmin": 0.8}, "0 <= reec_socmin < reec_socmax <= 1"),
        (REECC1 | {"reec_socmax": 1.2}, "0 <= reec_socmin < reec_socmax <= 1"),
        (REECC1 | {"reec_socini": 0.9}, "reec_socini 0.9 must lie in"),
        (
            {k: v for k, v in REECC1.items() if k != "reec_imax"},
            "REGCA1/REECC1 group is all-or-nothing",
        ),
        (REECC1 | {"reec_pflag": 0}, "a unit carries REECA1 or REECC1, not both"),
        (REECC1 | {"reec_vmin": 1.2}, "reec_vmin must be < reec_vmax"),
    ],
)
def test_each_new_range_rule_is_refused(tmp_path, ibr, reason):
    with pytest.raises(ContractError, match=reason):
        _load(tmp_path, ibr)


@pytest.mark.parametrize("bad", [float("inf"), float("nan")])
def test_non_finite_new_cons_are_refused(tmp_path, bad):
    with pytest.raises(ContractError, match="must be a finite number"):
        _load(tmp_path, PLANT | {"repc_kp": bad})


# --- additive only (P7) ------------------------------------------------------------


def test_existing_tuples_are_unchanged():
    """Hand-listed copies of master's tuples: the new groups are additive."""
    assert up.REGCA1_ICONS == ("regc_lvplsw",)
    assert (
        len(up.REGCA1_CONS) == 14
        and up.REGCA1_CONS[0] == "regc_tg"
        and up.REGCA1_CONS[-1] == "regc_accel"
    )
    assert up.REECA1_ICONS == (
        "reec_pfflag",
        "reec_vflag",
        "reec_qflag",
        "reec_pflag",
        "reec_pqflag",
    )
    assert len(up.REECA1_CONS) == 45 and up.REECA1_CONS[21] == "reec_vbias"
    assert (
        up.IBR_PARAMS
        == up.REGCA1_ICONS + up.REGCA1_CONS + up.REECA1_ICONS + up.REECA1_CONS
    )
    assert up.MODEL_OPTIONAL_PARAMS["inverter"][: len(up.IBR_PARAMS)] == up.IBR_PARAMS
    assert list(ibr_params(load_unit_templates()).columns) == [
        "mbase_mva",
        *up.IBR_PARAMS,
    ]


def test_campus_assets_yaml_is_untouched():
    """The shipped defaults packs pin this file's sha256; PR3 does not edit it."""
    f = Path(up.__file__).parent / "data" / "campus_assets.yaml"
    pinned = "2608e39bdf3cb951f151dfeb90524ea7a285f4ef46ea0fa2b1c2a71c3a06b587"
    assert hashlib.sha256(f.read_bytes()).hexdigest() == pinned


def test_a_con_filling_ten_columns_keeps_a_separator():
    assert dyr_writer._con(-99.0) == "  -99.00000"[1:]  # unchanged: 10 columns
    assert dyr_writer._con(-998.0) == " -998.00000"  # 11: one blank added


def test_reecc1_con_order_hand_listed():
    """
    Hand-listed from the public v35 parser table (VeraGrid v35 dyr.py),
    not derived from REECA1_CONS.
    """
    assert up.REECC1_CONS == tuple(
        "reec_" + n
        for n in (
            "vdip",
            "vup",
            "trv",
            "dbd1",
            "dbd2",
            "kqv",
            "iqh1",
            "iql1",
            "vref0",
            "tp",
            "qmax",
            "qmin",
            "vmax",
            "vmin",
            "kqp",
            "kqi",
            "kvp",
            "kvi",
            "tiq",
            "dpmax",
            "dpmin",
            "pmax",
            "pmin",
            "imax",
            "tpord",
            "vq1",
            "iq1",
            "vq2",
            "iq2",
            "vq3",
            "iq3",
            "vq4",
            "iq4",
            "vp1",
            "ip1",
            "vp2",
            "ip2",
            "vp3",
            "ip3",
            "vp4",
            "ip4",
            "t",
            "socini",
            "socmax",
            "socmin",
        )
    )
    assert up.REECC1_ICONS == ("reec_pfflag", "reec_vflag", "reec_qflag", "reec_pqflag")


@pytest.mark.parametrize(
    "edit, reason",
    [
        (
            {"repc_qmax": {"value": "inf", "source": "assumed"}},
            "must be a finite number",
        ),
        (
            {"repc_qmax": {"value": "nan", "source": "assumed"}},
            "must be a finite number",
        ),
        (
            {"repc_qmin": {"value": 0.5, "source": "assumed"}},
            "repc_qmin must be < repc_qmax",
        ),
        (
            {"reec_pfflag": {"value": 1, "source": "assumed"}},
            "reec_pfflag must be 0 under plant control",
        ),
    ],
)
def test_an_overlay_on_the_new_groups_is_checked(tmp_path, edit, reason):
    _toy_templates(tmp_path, ibr=PLANT)
    with pytest.raises(ContractError, match=reason):
        load_unit_templates(tmp_path / "toy_ibr.yaml", overlay={"W_BUS_02": edit})


def test_a_line_and_a_trafo_on_one_bus_pair_are_not_one_poc_branch(tmp_path):
    net = _net()
    pp.create_sgen(net, bus=1, p_mw=20.0, sn_mva=50.0, name="W2")
    pp.create_transformer_from_parameters(
        net,
        hv_bus=0,
        lv_bus=1,
        sn_mva=60.0,
        vn_hv_kv=345.0,
        vn_lv_kv=345.0,
        vkr_percent=0.5,
        vk_percent=12.0,
        pfe_kw=0.0,
        i0_percent=0.0,
    )
    ids = dyr_writer._poc_ids(
        net, {"W_BUS_02": PocBranch("line", 0, 0), "W2": PocBranch("trafo", 0, 0)}
    )
    assert ids["W_BUS_02"] == ids["W2"] == (1, 2, 1, "1")


def test_two_controlled_machines_each_get_exactly_one_repca1(tmp_path):
    """Two plants on their own PoC lines: one REPCA1 per REGCA1 machine."""
    net = _net()
    params, templates = _toy_templates(tmp_path, ibr=PLANT)
    t2 = templates.params[templates.params["unit_id"] == "W_BUS_02"].assign(
        unit_id="W2"
    )
    templates.params = pd.concat([templates.params, t2], ignore_index=True)
    templates.units.loc["W2"] = templates.units.loc["W_BUS_02"]
    pp.create_sgen(net, bus=1, p_mw=20.0, sn_mva=50.0, name="W2")
    poc = {"W_BUS_02": PocBranch("line", 0, 0), "W2": PocBranch("line", 1, 0)}
    write_dyr(net, params, tmp_path / "t.dyr", ibr=wecc_params(templates), poc=poc)
    recs = _records((tmp_path / "t.dyr").read_text())
    regca = sorted((b, i) for b, m, i, _ in recs if m == "REGCA1")
    repca = sorted((b, i) for b, m, i, _ in recs if m == "REPCA1")
    assert len(regca) == 2 and repca == regca


@pytest.mark.parametrize(
    "ibr",
    [
        REECC1 | {"reec_socmin": 0.0},  # 0 <= SOCmin
        REECC1 | {"reec_socmax": 1.0},  # SOCmax <= 1
        REECC1 | {"reec_socini": 0.2},  # SOCini == SOCmin
        REECC1 | {"reec_socini": 0.8},  # SOCini == SOCmax
        PLANT | {"repc_tfltr": 0.0},  # a lag at 0 is a bypass
        PLANT | {"repc_dbd1": 0.0, "repc_dbd2": 0.0},  # equal deadband edges
    ],
    ids=["socmin0", "socmax1", "socini_lo", "socini_hi", "tfltr0", "dbd_equal"],
)
def test_range_boundaries_that_are_legal_load(tmp_path, ibr):
    _load(tmp_path, ibr)


def test_htfrac_zero_is_refused(tmp_path):
    with pytest.raises(ContractError, match="wtdt_htfrac .* must lie in \\(0, 1\\)"):
        _load(tmp_path, WIND3 | {"wtdt_htfrac": 0.0})


def test_a_ten_column_con_line_keeps_its_separators(tmp_path):
    """
    A REPCA1 line holding femax 999 and femin -998 (both fill ten columns)
    is written with a blank before each, so it reads back as two values.
    """
    _, text = _write(tmp_path, PLANT, POC)
    assert "   999.00000 -998.00000" in text


# GK A2 (P5 per field): one case per field of each range set, from a
# hand-written list, so dropping any field from the checker's tuple fails.
_FULL = PLANT | WT3 | {"reec_pflag": 1}


@pytest.mark.parametrize("field", ["wtdt_h", "wtdt_freq1", "wttq_twref", "wttq_trate"])
@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_each_positive_field_is_refused_at_zero_and_below(tmp_path, field, bad):
    with pytest.raises(ContractError, match=f"unit .*: {field} must be > 0, got {bad}"):
        _load(tmp_path, _FULL | {field: bad})


@pytest.mark.parametrize(
    "field",
    [
        "repc_tfltr",
        "repc_tft",
        "repc_tfv",
        "repc_tp",
        "repc_tg",
        "repc_kc",
        "repc_ddn",
        "repc_dup",
        "wtdt_damp",
        "wtdt_dshaft",
        "wtpt_tp",
        "wttq_tp",
    ],
)
def test_each_non_negative_field_is_refused_below_zero(tmp_path, field):
    with pytest.raises(
        ContractError, match=f"unit .*: {field} must be >= 0, got -0.01"
    ):
        _load(tmp_path, _FULL | {field: -0.01})


@pytest.mark.parametrize(
    "lo, hi, op",
    [
        ("repc_emin", "repc_emax", "<"),
        ("repc_dbd1", "repc_dbd2", "<="),
        ("repc_qmin", "repc_qmax", "<"),
        ("repc_fdbd1", "repc_fdbd2", "<="),
        ("repc_femin", "repc_femax", "<"),
        ("repc_pmin", "repc_pmax", "<"),
        ("wtpt_thetamin", "wtpt_thetamax", "<"),
        ("wtpt_rthetamin", "wtpt_rthetamax", "<"),
        ("wttq_temin", "wttq_temax", "<"),
    ],
)
def test_each_ordered_pair_is_refused_at_its_boundary(tmp_path, lo, hi, op):
    # Strict pairs are refused at equality, non-strict ones just above it;
    # the legal side of each boundary loads.
    bad = _FULL[hi] if op == "<" else _FULL[hi] + 0.001
    with pytest.raises(ContractError, match=f"unit .*: {lo} must be {op} {hi}: "):
        _load(tmp_path, _FULL | {lo: bad})
    _load(tmp_path, _FULL | {lo: _FULL[hi] - 0.001 if op == "<" else _FULL[hi]})
