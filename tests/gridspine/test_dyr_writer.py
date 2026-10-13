"""Task 11: the PSS/E .dyr writer — the handoff contract's dynamics half.

A .dyr record attaches a dynamic model to a machine by (bus number, machine
ID). Both come from the RAW writer: bus numbers are bus-table order, 1-based,
and machine IDs are a per-bus counter run over gen, then ext_grid, then sgen.
A .dyr whose numbering disagrees with its .raw is worse than no .dyr — it
imports cleanly and attaches machines to the wrong buses — so the
cross-module test below parses BOTH files the writers actually emit and
asserts the (I, ID) sets are identical.

The expected record text for the two-machine toy was written by hand, field
by field against the PSS/E v33 CON layout, BEFORE the writer existed:

  GENROU  I 'GENROU' ID  T'do T''do T'qo T''qo H D Xd Xq X'd X'q X''d Xl S(1.0) S(1.2) /
  GENSAL  I 'GENSAL' ID  T'do T''do T''qo H D Xd Xq X'd X''d Xl S(1.0) S(1.2) /

H and every reactance are on MBASE, the machine base the RAW record declares
(field 9, sn_mva when finite else 100). The template states its base
explicitly; the writer REFUSES a mismatch rather than converting, because a
silently rescaled H is a plausible, wrong swing curve.
"""
import re

import pandapower as pp
import pandas as pd
import pytest
import yaml

from gridspine.handoff.dyr_writer import write_dyr
from gridspine.handoff.raw_writer import write_raw
from gridspine.ingest.pandapower_source import load_case39_res
from gridspine.schema.contracts import ContractError
from gridspine.templates.unit_params import load_unit_params

N_SYNC = 10  # 9 gen + 1 ext_grid on case39


def _raw_section(text, begin, end):
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if begin in ln) + 1
    stop = next(i for i, ln in enumerate(lines) if end in ln)
    return lines[start:stop]


def _fields(line):
    return [t.strip().strip("'").strip() for t in line.split(",")]


def _raw_machines(text):
    """(bus_number, machine_id, mbase) for every GENERATOR record, in file order."""
    out = []
    for ln in _raw_section(text, "BEGIN GENERATOR DATA", "END OF GENERATOR DATA"):
        f = _fields(ln)  # I, ID, PG, QG, QT, QB, VS, IREG, MBASE, ...
        out.append((int(f[0]), f[1], float(f[8])))
    return out


_RECORD = re.compile(r"^\s*(\d+)\s+'([A-Z0-9]+)'\s+'(.{2})'\s+(.*?)\s*/\s*$")


def _dyr_records(text):
    """(bus_number, model, machine_id, [values]) per record. A record ends at
    its '/', so a wrapped record (the converter models) is joined first. The
    ID is the RAW's 2-char quoted field ('1 '), so it is matched by width, not
    split on space. Values are ICONs then CONs, as written."""
    out, pending = [], []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        pending.append(ln)
        if not ln.rstrip().endswith("/"):
            continue
        record = " ".join(pending)
        pending = []
        m = _RECORD.match(record)
        assert m, record
        bus, model, mid, tail = int(m[1]), m[2], m[3].strip(), m[4]
        out.append((bus, model, mid, [float(t) for t in tail.split()]))
    assert not pending, f"unterminated record: {pending}"
    return out


def _toy_net():
    net = pp.create_empty_network(sn_mva=100.0)
    b1 = pp.create_bus(net, vn_kv=345.0, name="BUS_01")
    b2 = pp.create_bus(net, vn_kv=345.0, name="BUS_02")
    pp.create_ext_grid(net, bus=b1, vm_pu=1.0, name="SLK_BUS_01")
    pp.create_gen(net, bus=b2, p_mw=100.0, vm_pu=1.0, name="G_BUS_02")
    pp.create_load(net, bus=b2, p_mw=50.0, q_mvar=10.0)
    pp.create_line_from_parameters(
        net, from_bus=b1, to_bus=b2, length_km=1.0,
        r_ohm_per_km=11.9025, x_ohm_per_km=119.025, c_nf_per_km=0.0, max_i_ka=0.418,
    )
    return net


def _p(value, source="datasheet"):
    return {"value": value, "source": source}


TOY_UNITS = {
    "G_BUS_02": {
        "model": "GENROU", "mbase_mva": 100.0, "include_in_inertia": True,
        "params": {
            "h_s": _p(30.0), "d": _p(0.0, "assumed"),
            "xd": _p(0.3), "xq": _p(0.28), "xd_p": _p(0.07), "xq_p": _p(0.17),
            "xd_pp": _p(0.05, "assumed"), "xl": _p(0.035),
            "t_do_p": _p(6.5), "t_qo_p": _p(1.5),
            "t_do_pp": _p(0.05, "assumed"), "t_qo_pp": _p(0.05, "assumed"),
            "s1": _p(0.05, "assumed"), "s12": _p(0.3, "assumed"),
        },
    },
    "SLK_BUS_01": {
        "model": "GENSAL", "mbase_mva": 100.0, "include_in_inertia": False,
        "params": {
            "h_s": _p(20.0), "d": _p(0.0, "assumed"),
            "xd": _p(0.1), "xq": _p(0.069), "xd_p": _p(0.031),
            "xd_pp": _p(0.0218, "assumed"), "xl": _p(0.0125),
            "t_do_p": _p(10.2), "t_do_pp": _p(0.05, "assumed"), "t_qo_pp": _p(0.05, "assumed"),
            "s1": _p(0.05, "assumed"), "s12": _p(0.3, "assumed"),
        },
    },
}

# Hand-written BEFORE the writer. RAW order is gen then ext_grid, so G_BUS_02
# (bus 2) comes first. Every CON is %10.5f; the ID is the RAW's 2-char field.
EXPECTED_TOY_DYR = (
    "     2 'GENROU' '1 '"
    "    6.50000   0.05000   1.50000   0.05000  30.00000   0.00000"
    "   0.30000   0.28000   0.07000   0.17000   0.05000   0.03500"
    "   0.05000   0.30000 /\n"
    "     1 'GENSAL' '1 '"
    "   10.20000   0.05000   0.05000  20.00000   0.00000"
    "   0.10000   0.06900   0.03100   0.02180   0.01250"
    "   0.05000   0.30000 /\n"
)


def _toy_params(tmp_path, units=TOY_UNITS):
    f = tmp_path / "toy.yaml"
    f.write_text(yaml.safe_dump({"units": units}, sort_keys=False))
    return load_unit_params(f)


# --------------------------------------------------------------------------
# the record text, field by field
# --------------------------------------------------------------------------

def test_two_machine_toy_matches_the_hand_written_records(tmp_path):
    net = _toy_net()
    out = write_dyr(net, _toy_params(tmp_path), tmp_path / "toy.dyr")
    assert (tmp_path / "toy.dyr").read_text() == EXPECTED_TOY_DYR
    assert out == {"G_BUS_02": 2, "SLK_BUS_01": 1}


def test_genrou_and_gensal_con_counts_are_the_v33_layout(tmp_path):
    net = _toy_net()
    write_dyr(net, _toy_params(tmp_path), tmp_path / "toy.dyr")
    recs = {model: cons for _b, model, _i, cons in _dyr_records((tmp_path / "toy.dyr").read_text())}
    assert len(recs["GENROU"]) == 14
    assert len(recs["GENSAL"]) == 12
    # T'do first, H fifth (GENROU) / fourth (GENSAL): the two most often transposed.
    assert recs["GENROU"][0] == 6.5 and recs["GENROU"][4] == 30.0
    assert recs["GENSAL"][0] == 10.2 and recs["GENSAL"][3] == 20.0


# --------------------------------------------------------------------------
# THE cross-module contract: (I, ID) identical to the .raw
# --------------------------------------------------------------------------

def test_dyr_bus_numbers_and_machine_ids_match_the_raw(tmp_path):
    """Parsed off both files as written. Not reconstructed from a shared helper,
    so a shared bug cannot pass. Includes the machine ID: bus 33 carries a gen
    AND an sgen, so a writer that ignored the per-bus counter still gets the
    bus right and the ID wrong."""
    net = load_case39_res()
    params = load_unit_params()
    nums = write_raw(net, tmp_path / "c39.raw", f_hz=60.0)
    written = write_dyr(net, params, tmp_path / "c39.dyr")

    raw = _raw_machines((tmp_path / "c39.raw").read_text())
    raw_sync = raw[:N_SYNC]                                # gen + ext_grid precede sgen
    dyr = _dyr_records((tmp_path / "c39.dyr").read_text())

    assert len(dyr) == N_SYNC
    assert [(b, i) for b, _m, i, _c in dyr] == [(b, i) for b, i, _mb in raw_sync]
    assert set(written) == set(params.index)
    assert all(written[u] == nums[params.at[u, "bus"] if "bus" in params.columns else _bus_of(net, u)]
               for u in written)


def _bus_of(net, unit_id):
    for tbl in (net.gen, net.ext_grid):
        hit = tbl[tbl["name"] == unit_id]
        if len(hit):
            return net.bus.at[int(hit["bus"].iloc[0]), "name"]
    raise KeyError(unit_id)


def test_dyr_uses_the_raws_mbase_and_refuses_a_template_on_another_base(tmp_path):
    net = load_case39_res()
    params = load_unit_params()
    raw_mbase = {(b, i): mb for b, i, mb in _raw_machines(_raw_text(net, tmp_path))[:N_SYNC]}
    assert set(raw_mbase.values()) == {100.0}
    assert (params["mbase_mva"] == 100.0).all()
    write_dyr(net, params, tmp_path / "ok.dyr")

    # Give one machine a real sn_mva: the RAW's MBASE becomes 200, the template still says 100.
    net.gen.at[net.gen.index[0], "sn_mva"] = 200.0
    with pytest.raises(ContractError, match="mbase"):
        write_dyr(net, params, tmp_path / "bad.dyr")


def _raw_text(net, tmp_path):
    write_raw(net, tmp_path / "probe.raw", f_hz=60.0)
    return (tmp_path / "probe.raw").read_text()


def test_case39_records_come_in_raw_machine_order(tmp_path):
    """Order is the RAW's (gen table, then ext_grid), not bus-number order —
    so a reader can walk the two files side by side."""
    net = load_case39_res()
    write_dyr(net, load_unit_params(), tmp_path / "c39.dyr")
    buses = [b for b, _m, _i, _c in _dyr_records((tmp_path / "c39.dyr").read_text())]
    expected = [int(net.bus.index.get_loc(b)) + 1 for b in net.gen["bus"]] + \
               [int(net.bus.index.get_loc(b)) + 1 for b in net.ext_grid["bus"]]
    assert buses == expected
    assert buses != sorted(buses)


def test_inverters_get_no_record_and_are_not_in_the_return(tmp_path):
    """Without an ``ibr`` frame (or for an inverter not in it) there is no
    converter record. They are left out of the .dyr, not silently given a
    synchronous record — and the caller can see the omission because they are
    absent from the return."""
    net = load_case39_res()
    written = write_dyr(net, load_unit_params(), tmp_path / "c39.dyr")
    text = (tmp_path / "c39.dyr").read_text()
    assert not any(u.startswith(("W_", "S_")) for u in written)
    assert len(_dyr_records(text)) == N_SYNC


# --------------------------------------------------------------------------
# refusals
# --------------------------------------------------------------------------

def test_a_machine_with_no_template_row_raises(tmp_path):
    net = _toy_net()
    params = _toy_params(tmp_path).drop(index="G_BUS_02")
    with pytest.raises(ContractError, match="G_BUS_02"):
        write_dyr(net, params, tmp_path / "x.dyr")


def test_a_legacy_h_only_unit_cannot_be_written(tmp_path):
    """The v1 flat form has H and nothing else; a GENROU from it would be invented."""
    units = dict(TOY_UNITS)
    units["G_BUS_02"] = {"h_s": 30.0, "mbase_mva": 100.0, "source": "datasheet", "include_in_inertia": True}
    with pytest.raises(ContractError, match="legacy"):
        write_dyr(_toy_net(), _toy_params(tmp_path, units), tmp_path / "x.dyr")


def test_an_unknown_model_class_raises(tmp_path):
    params = _toy_params(tmp_path)
    params.loc["G_BUS_02", "model"] = "GENCLS"
    with pytest.raises(ContractError, match="GENCLS"):
        write_dyr(_toy_net(), params, tmp_path / "x.dyr")


def test_a_missing_con_raises_rather_than_writing_zero(tmp_path):
    params = _toy_params(tmp_path)
    params.loc["G_BUS_02", "xq_p"] = float("nan")
    with pytest.raises(ContractError, match="xq_p"):
        write_dyr(_toy_net(), params, tmp_path / "x.dyr")


# --------------------------------------------------------------------------
# Increment 8: converter records — REGCA1 + REECA1 per inverter
# --------------------------------------------------------------------------

from gridspine.templates.unit_params import ibr_params, load_unit_templates

N_RES = 5

_IBR = {
    "regc_lvplsw": 1, "regc_tg": 0.02, "regc_rrpwr": 10.0, "regc_brkpt": 0.9,
    "regc_zerox": 0.4, "regc_lvpl1": 1.22, "regc_volim": 1.2, "regc_lvpnt1": 0.8,
    "regc_lvpnt0": 0.4, "regc_iolim": -1.3, "regc_tfltr": 0.02, "regc_khv": 0.7,
    "regc_iqrmax": 99.0, "regc_iqrmin": -99.0, "regc_accel": 0.7,
    "reec_pfflag": 0, "reec_vflag": 1, "reec_qflag": 0, "reec_pflag": 0,
    "reec_pqflag": 1,
    "reec_vdip": 0.9, "reec_vup": 1.1, "reec_trv": 0.02, "reec_dbd1": -0.05,
    "reec_dbd2": 0.05, "reec_kqv": 2.0, "reec_iqh1": 1.05, "reec_iql1": -1.05,
    "reec_vref0": 0.0, "reec_iqfrz": 0.0, "reec_thld": 0.0, "reec_thld2": 0.0,
    "reec_tp": 0.02, "reec_qmax": 0.436, "reec_qmin": -0.436, "reec_vmax": 1.1,
    "reec_vmin": 0.9, "reec_kqp": 0.0, "reec_kqi": 0.1, "reec_kvp": 0.0,
    "reec_kvi": 40.0, "reec_vbias": 0.0, "reec_tiq": 0.02, "reec_dpmax": 99.0,
    "reec_dpmin": -99.0, "reec_pmax": 1.0, "reec_pmin": 0.0, "reec_imax": 1.1,
    "reec_tpord": 0.02,
    "reec_vq1": 0.2, "reec_iq1": 1.1, "reec_vq2": 0.5, "reec_iq2": 1.1,
    "reec_vq3": 0.8, "reec_iq3": 1.1, "reec_vq4": 1.0, "reec_iq4": 1.1,
    "reec_vp1": 0.2, "reec_ip1": 1.1, "reec_vp2": 0.5, "reec_ip2": 1.0,
    "reec_vp3": 0.8, "reec_ip3": 1.1, "reec_vp4": 1.0, "reec_ip4": 1.2,
}


def _toy_net_with_inverter():
    """The toy plus a 50 MVA inverter on BUS_02, which already carries the
    gen — so its machine ID is the RAW counter's second, '2 '."""
    net = _toy_net()
    pp.create_sgen(net, bus=1, p_mw=20.0, sn_mva=50.0, q_mvar=0.0, name="W_BUS_02")
    return net


def _toy_templates(tmp_path, mbase=50.0, ibr=_IBR):
    units = dict(TOY_UNITS)
    units["W_BUS_02"] = {
        "model": "inverter", "mbase_mva": mbase, "include_in_inertia": False,
        "params": {"k_sc": _p(1.2, "assumed"), "rx_sc": _p(0.1, "assumed"),
                   **{k: _p(v, "assumed") for k, v in ibr.items()}},
    }
    f = tmp_path / "toy_ibr.yaml"
    f.write_text(yaml.safe_dump({"units": units}, sort_keys=False))
    return load_unit_params(f), load_unit_templates(f)


# Hand-written from the PSS/E library layout, not from the writer's tuples:
# REGCA1 = ICON Lvplsw; CONs Tg Rrpwr Brkpt Zerox Lvpl1 Volim Lvpnt1 Lvpnt0
#   Iolim Tfltr Khv Iqrmax Iqrmin Accel.
# REECA1 = ICONs BUSR PFFLAG VFLAG QFLAG PFLAG PQFLAG; CONs Vdip Vup Trv dbd1
#   dbd2 Kqv Iqh1 Iql1 Vref0 Iqfrz Thld Thld2 Tp QMax QMin VMAX VMIN Kqp Kqi
#   Kvp Kvi Vbias Tiq dPmax dPmin PMAX PMIN Imax Tpord, then the VDL1 pairs
#   Vq1 Iq1 .. Vq4 Iq4, then VDL2 Vp1 Ip1 .. Vp4 Ip4.
# This pins the text, not every position: many fields share a value (0.02,
# 0.0, 1.1). `test_every_converter_field_sits_at_its_library_position` pins
# the positions.
EXPECTED_TOY_IBR = (
    "     2 'REGCA1' '2 '     1\n"
    "     0.02000  10.00000   0.90000   0.40000   1.22000\n"
    "     1.20000   0.80000   0.40000  -1.30000   0.02000\n"
    "     0.70000  99.00000 -99.00000   0.70000 /\n"
    "     2 'REECA1' '2 '     0     0     1     0     0     1\n"
    "     0.90000   1.10000   0.02000  -0.05000   0.05000\n"
    "     2.00000   1.05000  -1.05000   0.00000   0.00000\n"
    "     0.00000   0.00000   0.02000   0.43600  -0.43600\n"
    "     1.10000   0.90000   0.00000   0.10000   0.00000\n"
    "    40.00000   0.00000   0.02000  99.00000 -99.00000\n"
    "     1.00000   0.00000   1.10000   0.02000   0.20000\n"
    "     1.10000   0.50000   1.10000   0.80000   1.10000\n"
    "     1.00000   1.10000   0.20000   1.10000   0.50000\n"
    "     1.00000   0.80000   1.10000   1.00000   1.20000 /\n"
)


def test_toy_inverter_records_match_the_hand_written_text(tmp_path):
    params, templates = _toy_templates(tmp_path)
    out = write_dyr(_toy_net_with_inverter(), params, tmp_path / "t.dyr", ibr=ibr_params(templates))
    # Synchronous records unchanged, converter records after them (RAW order).
    assert (tmp_path / "t.dyr").read_text() == EXPECTED_TOY_DYR + EXPECTED_TOY_IBR
    assert out == {"G_BUS_02": 2, "SLK_BUS_01": 1, "W_BUS_02": 2}


# The PSS/E library layout typed out again, independently of the writer's
# tuples, in the library's own names (lower-cased).
_REGCA1_BY_HAND = ["lvplsw", "tg", "rrpwr", "brkpt", "zerox", "lvpl1", "volim",
                   "lvpnt1", "lvpnt0", "iolim", "tfltr", "khv", "iqrmax",
                   "iqrmin", "accel"]
_REECA1_BY_HAND = ["pfflag", "vflag", "qflag", "pflag", "pqflag",
                   "vdip", "vup", "trv", "dbd1", "dbd2", "kqv", "iqh1", "iql1",
                   "vref0", "iqfrz", "thld", "thld2", "tp", "qmax", "qmin",
                   "vmax", "vmin", "kqp", "kqi", "kvp", "kvi", "vbias", "tiq",
                   "dpmax", "dpmin", "pmax", "pmin", "imax", "tpord",
                   "vq1", "iq1", "vq2", "iq2", "vq3", "iq3", "vq4", "iq4",
                   "vp1", "ip1", "vp2", "ip2", "vp3", "ip3", "vp4", "ip4"]


def test_every_converter_field_sits_at_its_library_position(tmp_path):
    """Every field gets a DISTINCT value, so any transposition shows. The frame
    is built by hand, not loaded: distinct values are not a possible
    converter, and the writer's job here is placement only."""
    names = ["regc_" + n for n in _REGCA1_BY_HAND] + ["reec_" + n for n in _REECA1_BY_HAND]
    value = {n: float(k + 2) for k, n in enumerate(names)}      # 2, 3, 4, ... (ICONs too)
    ibr = pd.DataFrame([{"mbase_mva": 50.0, **value}], index=pd.Index(["W_BUS_02"], name="unit_id"))
    params, _t = _toy_templates(tmp_path)
    write_dyr(_toy_net_with_inverter(), params, tmp_path / "t.dyr", ibr=ibr)
    recs = {m: v for _b, m, _i, v in _dyr_records((tmp_path / "t.dyr").read_text())}
    assert recs["REGCA1"] == [value["regc_" + n] for n in _REGCA1_BY_HAND]
    assert recs["REECA1"] == [0.0] + [value["reec_" + n] for n in _REECA1_BY_HAND]


def test_converter_record_counts_are_the_library_layout(tmp_path):
    params, templates = _toy_templates(tmp_path)
    write_dyr(_toy_net_with_inverter(), params, tmp_path / "t.dyr", ibr=ibr_params(templates))
    recs = {m: v for _b, m, _i, v in _dyr_records((tmp_path / "t.dyr").read_text())}
    assert len(recs["REGCA1"]) == 1 + 14
    assert len(recs["REECA1"]) == 6 + 45
    assert recs["REECA1"][0] == 0, "BUSR: the unit's own terminal"


def test_no_dyr_line_is_longer_than_80_characters(tmp_path):
    # GENROU's single line is ~170 chars and has always been accepted; the
    # converter records are wrapped so REECA1's 51 values do not become one
    # 520-character line for an importer with a line buffer to overflow.
    params, templates = _toy_templates(tmp_path)
    write_dyr(_toy_net_with_inverter(), params, tmp_path / "t.dyr", ibr=ibr_params(templates))
    ibr_text = (tmp_path / "t.dyr").read_text().split(EXPECTED_TOY_DYR, 1)[1]
    assert max(len(ln) for ln in ibr_text.splitlines()) <= 80


def test_case39_every_machine_has_a_record_and_the_ids_match_the_raw(tmp_path):
    """THE cross-module contract, now over all 15 machines: parsed off both
    files. Bus 33 carries G_BUS_33 and W_BUS_33, so the wind farm's records
    must say '2 ' — a writer that restarted the counter for sgens gets the
    bus right and the ID wrong."""
    net = load_case39_res()
    t = load_unit_templates()
    write_raw(net, tmp_path / "c39.raw", f_hz=60.0)
    written = write_dyr(net, load_unit_params(), tmp_path / "c39.dyr", ibr=ibr_params(t))
    raw = _raw_machines((tmp_path / "c39.raw").read_text())
    dyr = _dyr_records((tmp_path / "c39.dyr").read_text())
    sync = [(b, i) for b, m, i, _v in dyr if m.startswith("GEN")]
    regc = [(b, i) for b, m, i, _v in dyr if m == "REGCA1"]
    reec = [(b, i) for b, m, i, _v in dyr if m == "REECA1"]
    assert sync == [(b, i) for b, i, _mb in raw[:N_SYNC]]
    assert regc == reec == [(b, i) for b, i, _mb in raw[N_SYNC:]]
    assert len(regc) == N_RES and len(written) == N_SYNC + N_RES
    assert (33, "2") in regc


def test_case39_converter_base_is_the_installed_rating_after_a_snapshot(tmp_path):
    """The bug this increment found: the RAW's MBASE for an inverter followed
    the hour's dispatch. With the rating on the net as ``sn_mva``, an hour
    that dispatches 33 MW from a 600 MW farm still agrees with the template."""
    net = load_case39_res()
    net.sgen["p_mw"] = 33.186
    write_dyr(net, load_unit_params(), tmp_path / "c39.dyr",
              ibr=ibr_params(load_unit_templates()))


def test_a_converter_template_on_another_base_is_refused(tmp_path):
    params, templates = _toy_templates(tmp_path, mbase=20.0)   # RAW says 50
    with pytest.raises(ContractError, match="W_BUS_02.*mbase"):
        write_dyr(_toy_net_with_inverter(), params, tmp_path / "t.dyr",
                  ibr=ibr_params(templates))


def test_an_inverter_outside_the_ibr_frame_is_omitted_and_the_rest_written(tmp_path):
    net = load_case39_res()
    ibr = ibr_params(load_unit_templates()).drop(index="S_BUS_36")
    written = write_dyr(net, load_unit_params(), tmp_path / "c39.dyr", ibr=ibr)
    assert "S_BUS_36" not in written
    assert {u for u in written if u.startswith(("W_", "S_"))} == set(ibr.index)


def test_a_curtailed_inverter_still_gets_its_records(tmp_path):
    """Offline units are STAT=0 in the RAW, not omitted, and the .dyr follows:
    a curtailed converter is still a machine the engineer may switch in."""
    net = _toy_net_with_inverter()
    net.sgen["in_service"] = False
    params, templates = _toy_templates(tmp_path)
    out = write_dyr(net, params, tmp_path / "t.dyr", ibr=ibr_params(templates))
    assert "W_BUS_02" in out


def test_a_missing_converter_value_raises_rather_than_writing_zero(tmp_path):
    params, templates = _toy_templates(tmp_path)
    ibr = ibr_params(templates)
    ibr.loc["W_BUS_02", "reec_imax"] = float("nan")
    with pytest.raises(ContractError, match="reec_imax"):
        write_dyr(_toy_net_with_inverter(), params, tmp_path / "t.dyr", ibr=ibr)


def test_writer_imports_no_engine():
    import gridspine.handoff.dyr_writer as mod

    src = open(mod.__file__, encoding="utf-8").read()
    for banned in ("import pypsa", "import pandapower", "gridspine.producers"):
        assert banned not in src
