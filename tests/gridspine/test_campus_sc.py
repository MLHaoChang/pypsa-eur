"""Short-circuit levels on the campus against the switchgear (C4c).

For each investment period, IEC 60909 max and min run at every bus, with
every unit installed in that period energised. A bus, the PCC included,
may carry ``ik_rated_ka``, its switchgear's rated short-time current.
The check:
* Ik'' max must not exceed ``ik_rated_ka``;
* the peak ip must not exceed the rated peak withstand, 2.5 x ik_rated at
  50 Hz and 2.6 x at 60 Hz (IEC 62271-1, tagged assumed).
"""
import math

import pandapower.shortcircuit as sc
import pytest

from gridspine.ingest.campus import build_campus
from gridspine.static.campus_sc import PEAK_FACTOR, campus_fault_levels, judge
from tests.gridspine.test_campus import campus_spec

ALL = {"BESS1", "PV1", "GEN1"}


def rated(spec, bus, ka):
    c = spec["campus"]
    target = c["pcc"] if c["pcc"]["bus"] == bus else c["buses"][bus]
    target["ik_rated_ka"] = {"value": ka, "source": "datasheet"}
    return spec


def test_the_pcc_with_nothing_installed_sees_the_grid_alone():
    fl = campus_fault_levels(build_campus(campus_spec()), installed=set()).set_index("bus")
    assert fl.at["PCC", "ikss_max_ka"] == pytest.approx(3000.0 / (math.sqrt(3) * 110.0), rel=1e-9)
    assert fl.at["PCC", "ikss_min_ka"] == pytest.approx(2000.0 / (math.sqrt(3) * 110.0), rel=1e-9)


def test_installed_units_raise_the_mv_level_and_uninstalled_ones_do_not():
    camp = build_campus(campus_spec())
    with_all = campus_fault_levels(camp, installed=ALL).set_index("bus")
    without_gen = campus_fault_levels(camp, installed=ALL - {"GEN1"}).set_index("bus")
    assert with_all.at["MV2", "ikss_max_ka"] > without_gen.at["MV2", "ikss_max_ka"]
    # the campus net itself is not changed
    assert camp.net.sgen["in_service"].all()


def test_the_levels_match_pandapower_on_the_same_net():
    camp = build_campus(campus_spec())
    fl = campus_fault_levels(camp, installed=ALL).set_index("bus")
    import copy
    net = copy.deepcopy(camp.net)
    sc.calc_sc(net, case="max", ip=True)
    by = dict(zip(net.bus["name"], net.res_bus_sc["ikss_ka"]))
    ip = dict(zip(net.bus["name"], net.res_bus_sc["ip_ka"]))
    for bus in by:
        assert fl.at[bus, "ikss_max_ka"] == pytest.approx(by[bus], rel=1e-9)
        assert fl.at[bus, "ip_max_ka"] == pytest.approx(ip[bus], rel=1e-9)


def test_a_bus_within_its_rating_passes_and_one_above_fails():
    spec = rated(rated(campus_spec(), "PCC", 31.5), "MV1", 1.0)
    fl = campus_fault_levels(build_campus(spec), installed=ALL).set_index("bus")
    assert fl.at["PCC", "rated_ka"] == 31.5 and bool(fl.at["PCC", "adequate"])
    assert fl.at["MV1", "rated_ka"] == 1.0 and not bool(fl.at["MV1", "adequate"])
    assert fl.at["MV1", "rating_source"] == "datasheet"


def test_a_bus_without_a_rating_is_reported_but_not_judged():
    fl = campus_fault_levels(build_campus(campus_spec()), installed=ALL).set_index("bus")
    assert math.isnan(fl.at["MV2", "rated_ka"]) and fl.at["MV2", "adequate"] is None


@pytest.mark.parametrize("ik, ip, rated, f, ok, word", [
    (10.0, 24.0, 10.5, 50, True, ""),          # both inside
    (10.0, 26.0, 10.2, 50, False, "peak"),     # Ik'' fits; 26 kA > 2.5 x 10.2 = 25.5
    (10.0, 26.0, 10.2, 60, True, ""),          # at 60 Hz the multiple is 2.6: 26.52 kA
    (11.0, 20.0, 10.0, 50, False, "Ik''"),     # Ik'' above the rating
])
def test_the_rating_judgement(ik, ip, rated, f, ok, word):
    adequate, detail = judge(ik, ip, rated, f)
    assert adequate is ok and word in detail
    assert PEAK_FACTOR == {50: 2.5, 60: 2.6}
