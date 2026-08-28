import numpy as np
import pandapower as pp
import pytest

from gridspine.handoff.raw_writer import write_raw
from gridspine.ingest.pandapower_source import load_case39


def toy_net():
    net = pp.create_empty_network(sn_mva=100.0)
    b1 = pp.create_bus(net, vn_kv=345.0, name="BUS_01")
    b2 = pp.create_bus(net, vn_kv=345.0, name="BUS_02")
    pp.create_ext_grid(net, bus=b1, vm_pu=1.03, name="SLK_BUS_01")
    pp.create_gen(net, bus=b2, p_mw=120.0, vm_pu=1.02, sn_mva=200.0, name="G_BUS_02")
    pp.create_load(net, bus=b2, p_mw=100.0, q_mvar=30.0)
    pp.create_line_from_parameters(
        net, from_bus=b1, to_bus=b2, length_km=1.0,
        r_ohm_per_km=11.9025, x_ohm_per_km=119.025,  # 0.01 / 0.10 pu @ 100 MVA, 345 kV
        c_nf_per_km=0.0, max_i_ka=0.418,
    )
    return net


def _records(text, section_idx):
    """Split into sections on '0 /' terminators; return comma-token lists."""
    sections, cur = [], []
    for line in text.splitlines()[3:]:  # skip header + 2 title lines
        if line.strip().startswith("0 /") or line.strip() == "Q":
            sections.append(cur)
            cur = []
        else:
            cur.append([t.strip().strip("'").strip() for t in line.split(",")])
    return sections[section_idx]


def test_header_and_determinism(tmp_path):
    net = toy_net()
    m1 = write_raw(net, tmp_path / "a.raw")
    m2 = write_raw(net, tmp_path / "b.raw")
    assert m1 == m2 == {"BUS_01": 1, "BUS_02": 2}
    head = (tmp_path / "a.raw").read_text().splitlines()[0]
    assert head.split(",")[0].strip() == "0"          # IC
    assert float(head.split(",")[1]) == 100.0          # SBASE
    assert head.split(",")[2].strip() == "33"          # version


def test_bus_records(tmp_path):
    net = toy_net()
    write_raw(net, tmp_path / "t.raw")
    buses = _records((tmp_path / "t.raw").read_text(), 0)
    assert len(buses) == 2
    assert (buses[0][0], buses[0][1], buses[0][3]) == ("1", "BUS_01", "3")  # slack IDE=3
    assert float(buses[0][2]) == pytest.approx(345.0)
    assert buses[1][3] == "2"                          # gen bus IDE=2


def test_branch_impedance_in_pu(tmp_path):
    net = toy_net()
    write_raw(net, tmp_path / "t.raw")
    branches = _records((tmp_path / "t.raw").read_text(), 4)
    assert len(branches) == 1
    assert float(branches[0][3]) == pytest.approx(0.01, rel=1e-3)   # R pu
    assert float(branches[0][4]) == pytest.approx(0.10, rel=1e-3)   # X pu


def test_offline_gen_written_with_stat0(tmp_path):
    net = toy_net()
    net.gen.loc[net.gen["name"] == "G_BUS_02", "in_service"] = False
    write_raw(net, tmp_path / "t.raw")
    gens = _records((tmp_path / "t.raw").read_text(), 3)
    grec = [g for g in gens if g[0] == "2"][0]
    assert grec[14] == "0"                             # STAT field
    assert grec[1] == "1"                              # machine ID present, not omitted


def test_case39_roundtrip_via_pandapower_importer(tmp_path):
    conv = pytest.importorskip("pandapower.converter")
    from_psse = getattr(conv, "from_psse", None)
    if from_psse is None:
        pytest.skip("pandapower.converter.from_psse unavailable (see Task 8 probe)")
    net = load_case39()
    write_raw(net, tmp_path / "c39.raw")
    net2 = from_psse(str(tmp_path / "c39.raw"))
    assert len(net2.bus) == 39
    pp.runpp(net2)
    assert net2.converged


# ---------------------------------------------------------------------------
# Added v33-correctness tests (beyond the brief; brief tests above unchanged)
# ---------------------------------------------------------------------------

def test_header_basfrq_defaults_50_and_is_parameterised(tmp_path):
    net = toy_net()
    write_raw(net, tmp_path / "a.raw")
    head = (tmp_path / "a.raw").read_text().splitlines()[0]
    assert float(head.split(",")[5]) == pytest.approx(50.0)   # BASFRQ default
    write_raw(net, tmp_path / "b.raw", f_hz=60.0)
    head = (tmp_path / "b.raw").read_text().splitlines()[0]
    assert float(head.split(",")[5]) == pytest.approx(60.0)


def test_line_charging_b_uses_f_hz_consistently(tmp_path):
    net = toy_net()
    # give the line real shunt capacitance: 1000 nF total at 345 kV
    net.line.loc[:, "c_nf_per_km"] = 1000.0
    z_base = 345.0 ** 2 / 100.0
    for f in (50.0, 60.0):
        write_raw(net, tmp_path / "t.raw", f_hz=f)
        br = _records((tmp_path / "t.raw").read_text(), 4)[0]
        expected = 2 * np.pi * f * 1000.0e-9 * z_base
        assert float(br[5]) == pytest.approx(expected, rel=1e-3)
        assert float(br[5]) > 0.0                       # charging B is positive


def test_lf_only_line_endings(tmp_path):
    net = toy_net()
    write_raw(net, tmp_path / "t.raw")
    assert b"\r" not in (tmp_path / "t.raw").read_bytes()


def _toy_net_with_trafo(tap_pos=2):
    net = pp.create_empty_network(sn_mva=100.0)
    b1 = pp.create_bus(net, vn_kv=345.0, name="BUS_01")
    b2 = pp.create_bus(net, vn_kv=138.0, name="BUS_02")
    pp.create_ext_grid(net, bus=b1, vm_pu=1.0, name="SLK_BUS_01")
    pp.create_load(net, bus=b2, p_mw=50.0, q_mvar=10.0)
    pp.create_transformer_from_parameters(
        net, hv_bus=b1, lv_bus=b2, sn_mva=200.0,
        vn_hv_kv=345.0, vn_lv_kv=138.0,
        vkr_percent=1.0, vk_percent=10.0, pfe_kw=0.0, i0_percent=0.0,
        shift_degree=0.0, tap_side="hv", tap_neutral=0, tap_pos=tap_pos,
        tap_step_percent=1.25, name="T1",
    )
    return net


def test_transformer_record_is_four_lines_v33(tmp_path):
    net = _toy_net_with_trafo()
    write_raw(net, tmp_path / "t.raw")
    trafo = _records((tmp_path / "t.raw").read_text(), 5)
    assert len(trafo) == 4                              # v33 2-winding = 4 lines
    l1, l2, l3, l4 = trafo
    # line 1: I,J,K,CKT,CW,CZ,CM,MAG1,MAG2,NMETR,NAME,STAT,O1,F1
    assert len(l1) == 14
    assert (l1[0], l1[1], l1[2]) == ("1", "2", "0")     # K=0 marks 2-winding
    assert l1[3] == "1"                                 # CKT
    assert (l1[4], l1[5], l1[6]) == ("1", "1", "1")     # CW=CZ=CM=1
    assert (float(l1[7]), float(l1[8])) == (0.0, 0.0)   # MAG1, MAG2
    assert l1[11] == "1"                                # STAT
    # line 2: R1-2, X1-2, SBASE1-2 — CZ=1: pu on system base
    assert len(l2) == 3
    assert float(l2[0]) == pytest.approx(0.005, rel=1e-3)   # 1% on 200 MVA -> 0.005 @100
    assert float(l2[1]) == pytest.approx(np.sqrt(0.05**2 - 0.005**2), rel=1e-3)
    assert float(l2[2]) == pytest.approx(200.0)         # SBASE1-2 = winding MVA
    # line 3: 17 fields through CNXA1
    assert len(l3) == 17
    assert float(l3[0]) == pytest.approx(1.025)         # WINDV1 carries the tap
    assert float(l3[1]) == pytest.approx(345.0)         # NOMV1
    assert float(l3[2]) == pytest.approx(0.0)           # ANG1
    assert float(l3[3]) == pytest.approx(200.0)         # RATA1 = sn_mva
    # line 4: WINDV2, NOMV2
    assert len(l4) == 2
    assert float(l4[0]) == pytest.approx(1.0)
    assert float(l4[1]) == pytest.approx(138.0)


def test_case39_trafo_taps_not_dropped(tmp_path):
    net = load_case39()
    write_raw(net, tmp_path / "c39.raw")
    trafo_lines = _records((tmp_path / "c39.raw").read_text(), 5)
    assert len(trafo_lines) == 4 * len(net.trafo)
    windv1 = [float(trafo_lines[4 * k + 2][0]) for k in range(len(net.trafo))]
    expected = [
        1.0 + (tr["tap_pos"] - tr["tap_neutral"]) * tr["tap_step_percent"] / 100.0
        for _, tr in net.trafo.iterrows()
    ]
    assert windv1 == pytest.approx(expected)
    assert max(windv1) > 1.0                            # case39 has off-nominal taps


def test_machine_ids_unique_when_slack_bus_also_has_gen(tmp_path):
    net = toy_net()
    # add a second machine on the slack bus: (I, ID) must stay unique
    pp.create_gen(net, bus=0, p_mw=10.0, vm_pu=1.03, sn_mva=50.0, name="G_BUS_01")
    write_raw(net, tmp_path / "t.raw")
    buses = _records((tmp_path / "t.raw").read_text(), 0)
    assert buses[0][3] == "3"                           # slack wins IDE classification
    gens = _records((tmp_path / "t.raw").read_text(), 3)
    ids_at_bus1 = [g[1] for g in gens if g[0] == "1"]
    assert len(ids_at_bus1) == 2
    assert len(set(ids_at_bus1)) == 2                   # distinct machine IDs


def test_parallel_branches_get_distinct_ckt(tmp_path):
    net = toy_net()
    pp.create_line_from_parameters(
        net, from_bus=0, to_bus=1, length_km=1.0,
        r_ohm_per_km=11.9025, x_ohm_per_km=119.025,
        c_nf_per_km=0.0, max_i_ka=0.418,
    )
    write_raw(net, tmp_path / "t.raw")
    branches = _records((tmp_path / "t.raw").read_text(), 4)
    assert len(branches) == 2
    assert branches[0][2] != branches[1][2]             # CKT '1' vs '2'


def test_offline_load_written_with_stat0(tmp_path):
    net = toy_net()
    net.load.loc[:, "in_service"] = False
    write_raw(net, tmp_path / "t.raw")
    loads = _records((tmp_path / "t.raw").read_text(), 1)
    assert len(loads) == 1
    assert loads[0][2] == "0"                           # STATUS field


def test_case39_section_counts_and_terminators(tmp_path):
    net = load_case39()
    mapping = write_raw(net, tmp_path / "c39.raw")
    text = (tmp_path / "c39.raw").read_text()
    assert len(mapping) == len(net.bus)
    assert sorted(mapping.values()) == list(range(1, len(net.bus) + 1))
    assert len(_records(text, 0)) == len(net.bus)
    assert len(_records(text, 2)) == 0                  # FIXED SHUNT empty
    assert len(_records(text, 3)) == len(net.gen) + len(net.ext_grid)
    assert len(_records(text, 4)) == len(net.line)
    assert text.rstrip().splitlines()[-1] == "Q"
    assert "END OF INDUCTION MACHINE DATA" in text
