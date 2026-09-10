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


_RECORD = re.compile(r"^\s*(\d+)\s+'([A-Z]+)'\s+'(.{2})'\s+(.*?)\s*/\s*$")


def _dyr_records(text):
    """(bus_number, model, machine_id, [cons]) per record. The ID is the RAW's
    2-char quoted field ('1 '), so it is matched by width, not split on space."""
    out = []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        m = _RECORD.match(ln)
        assert m, ln
        bus, model, mid, tail = int(m[1]), m[2], m[3].strip(), m[4]
        out.append((bus, model, mid, [float(t) for t in tail.split()]))
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
    """No IBR dynamic model is in scope (REGC/REEC are later). They are left
    out of the .dyr, not silently given a synchronous record — and the caller
    can see the omission because they are absent from the return."""
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


def test_writer_imports_no_engine():
    import gridspine.handoff.dyr_writer as mod

    src = open(mod.__file__, encoding="utf-8").read()
    for banned in ("import pypsa", "import pandapower", "gridspine.producers"):
        assert banned not in src
