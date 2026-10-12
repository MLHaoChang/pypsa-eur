"""The island's earth reference and earth-fault protection, I5b (plan 2026-10-07-campus-island-operation).

* Opening the PCC can take away the system's only earth reference, e.g. an
  MV system earthed only through the grid. ``earth_reference`` finds it per
  bus, and says when the island's earth rests only on assumed data (a
  transformer without its vector group).
* ``earth_fault_relay_check``: the residual current 3 I0 through the relay's
  branch for a 1-phase fault at its bus, island min case, against s times
  its earth-fault pickup. Hand oracles: all of the earth current returns
  through the one earthed neutral; an earth path beyond the relay takes its
  share by the zero-sequence current divider; no earth reference, blind.
"""
import math

import pytest

from gridspine.ingest.campus import build_campus
from gridspine.static import island_sc_seq as SEQ
from tests.gridspine.test_campus import t
from tests.gridspine.test_island_relays import feeder

SQ3 = math.sqrt(3.0)


def mv_campus(genset_earthing="isolated", earthing_transformer=False):
    """A 20 kV PCC (the grid, earthed) -> 2 km -> MV2 with a genset."""
    s = feeder([], genset_at_a=True, b_unit=None)
    s["campus"]["units"]["G"]["bus"] = "C"
    s["campus"]["units"]["G"]["island"]["neutral_earthing"] = genset_earthing
    if earthing_transformer:
        s["campus"]["earthing_transformers"] = {"ET": {"bus": "B", "x0_ohm": t(20.0)}}
    return s


def by_bus(df):
    return df.set_index("bus")


def test_an_mv_system_earthed_only_through_the_grid_loses_it_on_islanding():
    er = by_bus(SEQ.earth_reference(build_campus(mv_campus())))
    for bus in ("A", "B", "C"):
        assert er.at[bus, "earthed_grid"] and not er.at[bus, "earthed_island"]
        assert er.at[bus, "lost_on_islanding"]
        assert er.at[bus, "finding"] == "no earth reference in island (earthed when grid-connected)"


@pytest.mark.parametrize("spec", [mv_campus("solid"), mv_campus(earthing_transformer=True)], ids=["genset", "et"])
def test_an_earthed_genset_or_an_earthing_transformer_keeps_the_island_earthed(spec):
    er = by_bus(SEQ.earth_reference(build_campus(spec)))
    assert er["earthed_island"].all() and not er["lost_on_islanding"].any()
    assert (er["finding"] == "earthed in island").all()


def test_an_offline_genset_does_not_earth_the_island():
    er = by_bus(SEQ.earth_reference(build_campus(mv_campus("solid")), online=set()))
    assert er["lost_on_islanding"].all()


def hv_campus(vector_group=None, earthing_lv=None):
    """110 kV PCC -> 40 MVA 110/20 kV -> MV with a GFM battery."""
    s = {"campus": {
        "name": "HV", "f_hz": 50,
        "pcc": {"bus": "PCC", "vn_kv": 110.0, "vm_pu": t(1.0), "sk_max_mva": t(3000.0), "sk_min_mva": t(2000.0),
                "rx_max": t(0.1), "rx_min": t(0.1)},
        "buses": {"MV": {"vn_kv": 20.0}},
        "transformers": {"T1": {"hv_bus": "PCC", "lv_bus": "MV", "sn_mva": t(40.0), "vn_hv_kv": t(110.0),
                                "vn_lv_kv": t(20.0), "vk_percent": t(12.0), "vkr_percent": t(0.4), "pfe_kw": t(0.0),
                                "i0_percent": t(0.0)}},
        "units": {"L": {"kind": "load", "bus": "MV", "p_mw": t(10.0), "pf": t(0.95)}}}}
    if vector_group:
        s["campus"]["transformers"]["T1"]["vector_group"] = vector_group
    if earthing_lv:
        s["campus"]["transformers"]["T1"]["earthing_lv"] = earthing_lv
    return s


def test_an_island_earthed_only_by_an_assumed_vector_group_says_so():
    er = by_bus(SEQ.earth_reference(build_campus(hv_campus())))          # no vector group: Dyn11 assumed
    assert er.at["MV", "earthed_island"] and er.at["MV", "island_assumed"]
    assert er.at["MV", "finding"] == "earth reference in island rests on assumed data"
    # the HV side, behind the delta, is earthed only by the grid
    assert er.at["PCC", "lost_on_islanding"]


def test_the_same_transformer_given_as_dyn11_with_its_earthing_is_not_an_assumption():
    er = by_bus(SEQ.earth_reference(build_campus(hv_campus("Dyn11", earthing_lv="solid"))))
    assert er.at["MV", "finding"] == "earthed in island" and not er.at["MV", "island_assumed"]
    # its neutral earthing left out is still an assumption
    assert by_bus(SEQ.earth_reference(build_campus(hv_campus("Dyn11")))).at["MV", "island_assumed"]


def test_a_ynd_transformer_leaves_its_delta_side_unearthed():
    s = hv_campus("YNd5")
    er = by_bus(SEQ.earth_reference(build_campus(s)))
    assert not er.at["MV", "earthed_grid"] and not er.at["MV", "earthed_island"]
    assert er.at["MV", "finding"] == "no earth reference in island"
    assert not er.at["MV", "lost_on_islanding"]


# ── earth-fault relays ───────────────────────────────────────────────────────

def e_relay(name, branch, fault_bus, pickup):
    return {"name": name, "branch": branch, "fault_bus": fault_bus, "timing": "time_delayed",
            "i_pickup_e_ka": t(pickup, "datasheet")}


def genset_feeder(relays, et_at_c=None):
    """The 20 kV feeder A --F1-- B --F2-- C with a solidly earthed genset at A
    and nothing else; optionally an earthing transformer at C."""
    s = feeder(relays, genset_at_a=True, b_unit=None)
    if et_at_c:
        s["campus"]["earthing_transformers"] = {"ET": {"bus": "C", "x0_ohm": t(et_at_c)}}
    return s


F_T = 1.0 + 0.004 * 60.0                         # cable R at 80 degC, the min case
KG = 1.1 / (1.0 + 0.15 * 0.6)


def _zg(x):
    return KG * complex(0.07 * x, x) * 20.0 ** 2 / 12.5


ZC1 = complex(0.1 * F_T, 0.1)                    # one km
ZC0 = complex((0.1 + 3 * SEQ.R_SCREEN_OHM_PER_KM) * F_T, 0.1)


def test_all_of_the_earth_current_returns_through_the_relay_to_the_one_earthed_neutral():
    camp = build_campus(genset_feeder([e_relay("RE", "F2", "C", 0.1)]))
    r = SEQ.earth_fault_relay_check(camp).set_index("relay").loc["RE"]
    z = _zg(0.15) + 2 * ZC1 + _zg(0.15) + 2 * ZC1 + _zg(0.06) + 2 * ZC0       # x2 = x''d, x0 = 0.4 x''d
    assert r["fault_e_ka"] == pytest.approx(SQ3 * 1.0 * 20.0 / abs(z), rel=1e-9)
    assert r["relay_e_ka"] == pytest.approx(r["fault_e_ka"], rel=1e-9)
    assert r["verdict"] == SEQ.SEES_E and r["sensitivity"] == 1.5


def test_an_earth_path_beyond_the_relay_takes_its_share_by_the_zero_sequence_divider():
    camp = build_campus(genset_feeder([e_relay("RE", "F2", "C", 0.1)], et_at_c=20.0))
    r = SEQ.earth_fault_relay_check(camp).set_index("relay").loc["RE"]
    z_et, z_back = complex(0.0, 20.0), _zg(0.06) + 2 * ZC0
    assert r["relay_e_ka"] == pytest.approx(r["fault_e_ka"] * abs(z_et / (z_et + z_back)), rel=1e-9)
    assert r["relay_e_ka"] < r["fault_e_ka"]


@pytest.mark.parametrize("margin, verdict", [(0.99, SEQ.SEES_E), (1.01, SEQ.BLIND_E)])
def test_the_earth_fault_verdict_on_both_sides_of_s(margin, verdict):
    seen = SEQ.earth_fault_relay_check(build_campus(genset_feeder([e_relay("RE", "F2", "C", 0.1)])))
    i = float(seen["relay_e_ka"].iloc[0])
    camp = build_campus(genset_feeder([e_relay("RE", "F2", "C", i * margin / 1.5)]))
    r = SEQ.earth_fault_relay_check(camp).iloc[0]
    assert r["verdict"] == verdict
    other_s = SEQ.earth_fault_relay_check(camp, sensitivity=1.5 / margin * 1.02).iloc[0]     # needs 1.02 x i
    assert other_s["verdict"] == SEQ.BLIND_E


def test_without_an_earth_reference_the_island_relay_is_blind_but_sees_it_grid_connected():
    s = genset_feeder([e_relay("RE", "F2", "C", 0.05)])
    s["campus"]["units"]["G"]["island"]["neutral_earthing"] = "isolated"
    r = SEQ.earth_fault_relay_check(build_campus(s)).iloc[0]
    assert r["relay_e_ka"] == 0.0 and r["verdict"] == SEQ.BLIND_E and r["detail"] == SEQ.NO_EARTH
    assert r["grid_e_ka"] > 1.5 * 0.05


def test_a_relay_whose_fault_the_network_cannot_carry_is_not_determinable():
    s = feeder([e_relay("RE", "F1", "B", 0.05)], b_unit=None)            # one GFM battery at A, no neg_seq
    s["campus"]["units"]["BA"]["island"]["control"] = "gfm_droop"
    s["campus"]["earthing_transformers"] = {"ET": {"bus": "A", "x0_ohm": t(20.0)}}
    r = SEQ.earth_fault_relay_check(build_campus(s)).iloc[0]
    assert r["verdict"] == "not determinable" and math.isnan(r["relay_e_ka"])


def test_only_relays_with_an_earth_fault_pickup_are_judged():
    phase_only = {"name": "RP", "branch": "F2", "fault_bus": "C", "timing": "time_delayed",
                  "i_pickup_ka": t(0.2)}
    camp = build_campus(genset_feeder([phase_only, e_relay("RE", "F2", "C", 0.1)]))
    assert list(SEQ.earth_fault_relay_check(camp)["relay"]) == ["RE"]


def test_an_island_with_a_given_earth_path_beside_an_assumed_one_does_not_rest_on_assumptions():
    s = hv_campus()                                                        # Dyn11 assumed
    s["campus"]["earthing_transformers"] = {"ET": {"bus": "MV", "x0_ohm": t(20.0)}}
    er = by_bus(SEQ.earth_reference(build_campus(s)))
    assert er.at["MV", "finding"] == "earthed in island" and not er.at["MV", "island_assumed"]
