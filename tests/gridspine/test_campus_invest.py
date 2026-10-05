"""Least-cost electrical assets from the library, re-checked by AC load flow
(plan C8).

For every need part one finds (a transformer group, the reactive gap, an
overloaded cable, an under-rated bus), the selector takes the cheapest
library candidate, re-solves every selected hour and case with the choice in
place, and escalates a need while a check fails.

The oracles do not run the code under test:
* the least-cost choice is a brute-force minimum over the candidate set,
  each candidate built directly in pandapower and solved in this file;
* the escalation and the unresolved cases are built from flows solved
  directly in pandapower here;
* the costs are restated with the annuity formula.
"""
import math

import pandapower as pp
import pandas as pd
import pytest
import yaml

from gridspine.ingest.campus import build_campus, load_campus
from gridspine.static.campus_flow import SizingCriteria
from gridspine.static.campus_invest import INVEST_CHECKS, select_assets
from gridspine.static.campus_reactive import ReactiveRequirement
from gridspine.templates.campus_assets import load_asset_library
from gridspine.templates.grid_codes import load_grid_code
from tests.gridspine.test_campus import campus_spec, direct_net, t
from tests.gridspine.test_campus_flow import rows

PROFILE = load_grid_code("eu_rfg_dcc_ce")
WIDE = ReactiveRequirement(100.0, "test band", "assumed")
RATE = 0.05


def crf(r, n):
    """Capital recovery factor, restated here: r / (1 - (1 + r)^-n)."""
    return r / (1 - (1 + r) ** -n)


def tr(i, s, capex, vk=12.0, life=40, opex=0.01):
    return {"id": i, "hv_kv": t(110.0), "lv_kv": t(20.0), "s_mva": t(s), "vk_percent": t(vk),
            "vkr_percent": t(0.4), "pfe_kw": t(25.0), "i0_percent": t(0.05), "capex_eur": t(capex),
            "opex_frac": t(opex), "lifetime_a": t(life)}


def cable(i, max_i, per_km, r=0.1, x=0.1, c=300.0):
    return {"id": i, "vn_kv": t(20.0), "cross_section_mm2": t(100.0), "r_ohm_per_km": t(r),
            "x_ohm_per_km": t(x), "c_nf_per_km": t(c), "max_i_ka": t(max_i), "capex_eur_per_km": t(per_km),
            "opex_frac": t(0.005), "lifetime_a": t(40)}


def comp(kind, i, q, capex, life=25, steps=None, opex=0.02):
    e = {"id": i, "vn_kv": t(20.0), "q_mvar": t(q), "capex_eur": t(capex), "opex_frac": t(opex), "lifetime_a": t(life)}
    if kind == "capacitor_banks":
        e["steps"] = t(steps or 1)
    if kind == "statcoms":
        e["losses_percent"] = t(1.0)
    return e


def library(tmp_path, **kinds):
    data = {"discount_rate": t(RATE), "currency": "EUR", "price_year": 2026,
            **{k: kinds.get(k, []) for k in ("transformers", "cables", "capacitor_banks", "shunt_reactors",
                                              "statcoms", "switchgear")}}
    path = tmp_path / "lib.yaml"
    path.write_text(yaml.safe_dump(data))
    return load_asset_library(path)                      # the test library is itself a valid library


def hourly(*hours):
    """``(period, h, {unit: p}, {unit: status})`` -> the hourly table and the selection."""
    frame = pd.concat([rows(hour=p_mw, status=st, period=period, h=h) for period, h, p_mw, st in hours],
                      ignore_index=True)
    return frame, frame[["period", "hour"]].drop_duplicates().reset_index(drop=True)


def row(out, need):
    inv = out["investment"]
    return inv[inv["need"] == need].iloc[0]


def solve_direct(hour, trafos, out=None, lv_kv=20.0, cable_max_i=3.0, pf=0.98):
    """The test campus written in pandapower with ``trafos`` = ``[(s_mva, vk), ...]``
    between PCC and MV1, at one hour."""
    net = direct_net()
    net.trafo.drop(net.trafo.index, inplace=True)
    for i, (s, vk) in enumerate(trafos):
        pp.create_transformer_from_parameters(net, 0, 1, sn_mva=s, vn_hv_kv=110.0, vn_lv_kv=lv_kv, vkr_percent=0.4,
                                              vk_percent=vk, pfe_kw=25.0, i0_percent=0.05, name=f"T{i}")
    if out is not None:
        net.trafo.at[out, "in_service"] = False
    net.line.at[0, "max_i_ka"] = cable_max_i
    net.load.at[0, "p_mw"] = -hour["DC_LOAD"]
    net.load.at[0, "q_mvar"] = -hour["DC_LOAD"] * math.tan(math.acos(pf))
    for name in ("BESS1", "PV1", "GEN1"):
        net.sgen.loc[net.sgen["name"] == name, "p_mw"] = hour[name]
    pp.runpp(net)
    return net


def s_hv(net):
    return list(((net.res_trafo["p_hv_mw"] ** 2 + net.res_trafo["q_hv_mvar"] ** 2) ** 0.5)[net.trafo["in_service"]])


H7 = {"DC_LOAD": -45.0, "BESS1": -10.0, "PV1": 12.0, "GEN1": 0.0}
H19 = {"DC_LOAD": -50.0, "BESS1": -12.0, "PV1": 0.0, "GEN1": 0.0}
H3 = {"DC_LOAD": -55.0, "BESS1": -12.0, "PV1": 0.0, "GEN1": 0.0}


def wide_cable_spec():
    spec = campus_spec()
    spec["campus"]["cables"]["CB1"]["max_i_ka"] = t(3.0)
    return spec


# --------------------------------------------------------------------------
# the least-cost choice is the brute-force minimum of the AC-feasible set
# --------------------------------------------------------------------------

SIZES = {"T31": (31.5, 0.80e6), "T40": (40.0, 0.95e6), "T50": (50.0, 1.20e6), "T63": (63.0, 1.40e6),
         "T100": (100.0, 1.75e6)}


def ac_feasible(n, s, hours, margin=0.2):
    """The independent AC check of n units of s MVA: every hour solved
    intact and with each unit out, the sizing rule on the solved flows
    (max(intact share, survivor) x (1 + margin) <= s), every loading within
    100 % and every voltage within the EU bands (0.90-1.10 pu below 110 kV,
    0.90-1.118 pu at 110 kV)."""
    for hour in hours:
        cases = [solve_direct(hour, [(s, 12.0)] * n)] + [solve_direct(hour, [(s, 12.0)] * n, out=k) for k in range(n)]
        intact = sum(s_hv(cases[0])) / n
        survivor = max(max(s_hv(c)) for c in cases[1:])
        if max(intact, survivor) * (1 + margin) > s + 1e-9:
            return False
        for c in cases:
            if (c.res_trafo["loading_percent"][c.trafo["in_service"]] > 100).any():
                return False
            if not ((c.res_bus["vm_pu"] >= 0.9) & (c.res_bus["vm_pu"] <= 1.1)).all():
                return False
    return True


def test_the_chosen_transformers_are_the_cheapest_combination_that_passes_an_independent_ac_check(tmp_path):
    lib = library(tmp_path, transformers=[tr(i, s, capex) for i, (s, capex) in SIZES.items()])
    table, sel = hourly((2030, 7, H7, {}), (2030, 19, H19, {}), (2040, 3, H3, {}))
    out = select_assets(wide_cable_spec(), table, sel, lib, WIDE, PROFILE, SizingCriteria(margin=0.2))
    # brute force: two parallel units stay redundant, so n is 2 or 3
    hours = [H7, H19, H3]
    feasible = [(n * capex * (crf(RATE, 40) + 0.01), n, i) for i, (s, capex) in SIZES.items() for n in (2, 3)
                if ac_feasible(n, s, hours)]
    best_cost, best_n, best_id = min(feasible)
    got = row(out, "transformer TR1+TR2")
    assert (got["library_id"], got["units"]) == (best_id, best_n) == ("T100", 2)
    assert got["annualised_eur_per_a"] == pytest.approx(best_cost)
    assert got["status"] == "chosen" and not got["existing"]
    # the invested file carries the library type, every value with its tag
    trafos = out["spec"]["campus"]["transformers"]
    assert sorted(trafos) == ["TR1", "TR2"]
    assert trafos["TR1"]["sn_mva"] == {"value": 100.0, "source": "assumed"} and trafos["TR1"]["library_id"] == "T100"
    assert trafos["TR1"]["vn_lv_kv"]["value"] == 20.0
    # the cost per period: the pair is bought in 2030 and counts in both periods
    cost = out["cost"].set_index("period")
    assert cost.at[2030, "capex_eur"] == pytest.approx(2 * 1.75e6) and cost.at[2040, "capex_eur"] == 0.0
    assert list(cost["annualised_eur_per_a"]) == pytest.approx([best_cost, best_cost])
    # the "with measures" column is the re-solved network: every check passes
    comp = out["compliance"].set_index("check")
    assert list(comp.index) == list(INVEST_CHECKS)
    assert comp.at["transformer_loading", "status_as_is"] == "fail"
    assert set(comp["status_with_measures"]) <= {"pass", "not_rated"}
    assert "not_rechecked" not in set(comp["status_with_measures"])
    assert out["history"].empty and out["unresolved"] == []


def test_the_invested_campus_file_builds_and_round_trips_through_yaml(tmp_path):
    lib = library(tmp_path, transformers=[tr(i, s, capex) for i, (s, capex) in SIZES.items()])
    table, sel = hourly((2030, 7, H7, {}), (2040, 3, H3, {}))
    out = select_assets(wide_cable_spec(), table, sel, lib, WIDE, PROFILE)
    path = tmp_path / "invested.yaml"
    path.write_text(yaml.safe_dump(out["spec"], sort_keys=False))
    camp = load_campus(path)
    assert sorted(camp.net.trafo["name"]) == sorted(out["spec"]["campus"]["transformers"])


def test_three_units_get_canonical_names_and_the_project_name_stays_on_the_first(tmp_path):
    lib = library(tmp_path, transformers=[tr("T50", 50.0, 1.0e6)])
    spec = wide_cable_spec()
    spec["campus"]["transformers"]["TR1"]["pypsa_name"] = "grid_import"
    table, sel = hourly((2040, 3, H3, {}))
    out = select_assets(spec, table, sel, lib, WIDE, PROFILE)
    trafos = out["spec"]["campus"]["transformers"]
    assert sorted(trafos) == ["TR1", "TR1_2", "TR2"]
    assert trafos["TR1"].get("pypsa_name") == "grid_import" and "pypsa_name" not in trafos["TR1_2"]
    assert row(out, "transformer TR1+TR2")["units"] == 3


# --------------------------------------------------------------------------
# escalation, and needs the library cannot meet
# --------------------------------------------------------------------------

def single_spec():
    spec = wide_cable_spec()
    del spec["campus"]["transformers"]["TR2"]
    return spec


def part_one_flow():
    """The single 40 MVA unit's flow at H7, solved here."""
    return s_hv(solve_direct(H7, [(40.0, 12.0)], lv_kv=21.0))[0]


def test_a_transformer_that_meets_the_rule_but_fails_the_ac_recheck_is_escalated(tmp_path):
    """With no margin, a unit rated 0.5 % above part one's flow meets the
    sizing rule. Its own 18 % impedance draws more reactive power, so the
    re-solved flow is above its rating, and the selector moves on."""
    s = part_one_flow()
    cheap = tr("T_CHEAP", round(s * 1.005, 3), 1.0e6, vk=18.0)
    ok = tr("T_OK", round(s * 1.3, 3), 1.5e6)
    assert max(s_hv(solve_direct(H7, [(s * 1.005, 18.0)]))) > s * 1.005           # the oracle: it fails
    assert max(s_hv(solve_direct(H7, [(s * 1.3, 12.0)]))) < s * 1.3               # and the next one holds
    lib = library(tmp_path, transformers=[cheap, ok])
    table, sel = hourly((2030, 7, H7, {}))
    out = select_assets(single_spec(), table, sel, lib, WIDE, PROFILE, SizingCriteria(margin=0.0))
    got = row(out, "transformer TR1")
    assert got["library_id"] == "T_OK" and got["units"] == 1 and got["status"] == "chosen"
    h = out["history"]
    assert len(h) == 1
    first = h.iloc[0]
    assert (first["iteration"], first["need"], first["from"], first["to"], first["check"]) == (
        1, "transformer TR1", "1 x T_CHEAP", "1 x T_OK", "transformer_loading")
    assert set(out["compliance"]["status_with_measures"]) <= {"pass", "not_rated"}


def dark_hours(*loads):
    """One dark hour per period (every inverter off), the load at ``loads``."""
    off = {"BESS1": 0, "PV1": 0, "GEN1": 0}
    dark = {"BESS1": 0.0, "PV1": 0.0, "GEN1": 0.0}
    return hourly(*((period, 1, {"DC_LOAD": -mw, **dark}, off) for period, mw in loads))


def lowpf_spec(existing=True):
    spec = wide_cable_spec()
    spec["campus"]["units"]["DC_LOAD"]["pf"] = t(0.85)
    for name in ("TR1", "TR2"):
        spec["campus"]["transformers"][name]["existing"] = existing
    return spec


def test_a_need_that_runs_out_of_candidates_is_reported_unresolved_not_passed(tmp_path):
    """The PCC draws 17.3 Mvar against a 15 Mvar band (2.3 Mvar short). The
    only bank is one 30 Mvar step: its rating covers the gap, but switched in
    it overshoots the band by more than it was short, so the dispatch leaves
    it off, and one, two or three of them all fail the AC re-check."""
    lib = library(tmp_path, capacitor_banks=[comp("capacitor_banks", "CAP30", 30.0, 100e3)])
    table, sel = dark_hours((2030, 25.0))
    out = select_assets(lowpf_spec(), table, sel, lib, ReactiveRequirement(15.0, "c", "code"), PROFILE)
    got = row(out, "reactive")
    assert got["status"] == "unresolved" and got["library_id"] == "CAP30" and got["units"] == 3
    assert "pcc_reactive" in got["reason"] and "no further candidate" in got["reason"]
    assert out["unresolved"] == [{"need": "reactive", "reason": got["reason"]}]
    h = out["history"]
    assert list(h["to"]) == ["2 x CAP30", "3 x CAP30", "none left"]
    assert list(h["from"]) == ["1 x CAP30", "2 x CAP30", "3 x CAP30"]
    comp_ = out["compliance"].set_index("check")
    assert comp_.at["pcc_reactive", "status_with_measures"] == "fail"           # the re-solve, not a pass
    assert out["cost"]["annualised_eur_per_a"].sum() == 0.0                        # unresolved is not counted


def test_a_library_with_no_adequate_candidate_gives_an_unresolved_need_with_a_reason(tmp_path):
    lib = library(tmp_path, transformers=[tr("T10", 10.0, 0.5e6)])
    table, sel = hourly((2030, 7, H7, {}))
    out = select_assets(single_spec(), table, sel, lib, WIDE, PROFILE)
    got = row(out, "transformer TR1")
    assert got["status"] == "unresolved" and got["library_id"] is None
    assert "no library transformer" in got["reason"] and "1 to 3 units" in got["reason"]
    comp = out["compliance"].set_index("check")
    # the campus keeps its own 40 MVA unit, which the re-solve finds overloaded
    assert comp.at["transformer_loading", "status_with_measures"] == "fail"
    assert out["spec"]["campus"]["transformers"]["TR1"]["sn_mva"]["value"] == 40.0


# --------------------------------------------------------------------------
# cables and switchgear
# --------------------------------------------------------------------------

def test_an_overloaded_cable_gets_the_cheapest_section_or_parallel_runs_that_carry_it(tmp_path):
    spec = campus_spec()                                  # CB1: 1.2 kA, loaded about 110 % at H7
    i_worst = float(solve_direct(H7, [(40.0, 12.0)] * 2, lv_kv=21.0, cable_max_i=1.2).res_line.at[0, "i_ka"])
    lib = library(tmp_path, transformers=[tr("T63", 63.0, 1.0e6)],
                  cables=[cable("C240", 0.6, 100e3), cable("C400", 0.8, 150e3), cable("C630", 1.0, 200e3)])
    table, sel = hourly((2030, 7, H7, {}))
    out = select_assets(spec, table, sel, lib, WIDE, PROFILE, SizingCriteria(margin=0.2))
    # by hand: runs x max_i >= i_worst x 1.2, cheapest per km x 2 km x runs
    options = [(n * per_km, i, n) for i, (imax, per_km) in {"C240": (0.6, 100e3), "C400": (0.8, 150e3),
                                                             "C630": (1.0, 200e3)}.items()
               for n in (1, 2, 3) if n * imax >= i_worst * 1.2]
    _, best_id, best_n = min(options)
    got = row(out, "cable CB1")
    assert (got["library_id"], got["units"], got["length_km"]) == (best_id, best_n, 2.0)
    capex = best_n * 2.0 * dict(C240=100e3, C400=150e3, C630=200e3)[best_id]
    assert got["capex_eur"] == pytest.approx(capex)
    assert got["annualised_eur_per_a"] == pytest.approx(capex * (crf(RATE, 40) + 0.005))
    cb = out["spec"]["campus"]["cables"]["CB1"]
    assert cb["parallel"] == best_n and cb["max_i_ka"]["value"] == dict(C240=0.6, C400=0.8, C630=1.0)[best_id]
    comp = out["compliance"].set_index("check")
    assert comp.at["cable_loading", "status_as_is"] == "fail"
    assert comp.at["cable_loading", "status_with_measures"] == "pass"


def sg(i, kv, ik, capex):
    return {"id": i, "vn_kv": t(kv), "ik_rated_ka": t(ik), "ip_rated_ka": t(2.5 * ik), "capex_eur": t(capex),
            "opex_frac": t(0.01), "lifetime_a": t(40)}


def fault_direct(trafos, lv_kv):
    """IEC 60909 max at every bus of the hand-built campus with ``trafos``,
    every unit energised as the campus file screens it: ``{bus: (Ik'', ip)}``."""
    import pandapower.shortcircuit as sc
    net = direct_net()
    net.trafo.drop(net.trafo.index, inplace=True)
    for s_mva, vk in trafos:
        pp.create_transformer_from_parameters(net, 0, 1, sn_mva=s_mva, vn_hv_kv=110.0, vn_lv_kv=lv_kv,
                                              vkr_percent=0.4, vk_percent=vk, pfe_kw=25.0, i0_percent=0.05)
    for name, k, rx in (("BESS1", 1.2, 0.1), ("PV1", 1.1, 0.1), ("GEN1", 1 / 0.15, 0.07)):
        i = net.sgen.index[net.sgen["name"] == name][0]
        net.sgen.loc[i, ["k", "rx"]] = k, rx
        net.sgen.at[i, "generator_type"] = "current_source"
    sc.calc_sc(net, case="max", ip=True)
    return {b: (float(net.res_bus_sc.at[i, "ikss_ka"]), float(net.res_bus_sc.at[i, "ip_ka"]))
            for i, b in zip(net.bus.index, net.bus["name"])}


def test_switchgear_is_the_cheapest_rating_that_withstands_the_re_solved_fault_level_times_its_bays(tmp_path):
    """Part one's 40 MVA units give MV1 19.4 kA and MV2 15.6 kA, so the 20 kA
    rating is the first choice at both. The 63 MVA units bought raise both,
    and the re-check escalates each bus until its rating holds. MV1 has the
    two transformers, the cable, the battery and the PV: 5 bays. MV2 has the
    cable, the load and the genset: 3 bays."""
    ratings = {"SG20": (20.0, 10e3), "SG25": (25.0, 12e3), "SG31": (31.5, 15e3)}
    lib = library(tmp_path, transformers=[tr("T63", 63.0, 1.0e6)],
                  switchgear=[sg(i, 20.0, ik, capex) for i, (ik, capex) in ratings.items()])
    table, sel = hourly((2030, 7, {**H7, "DC_LOAD": -20.0}, {}))
    out = select_assets(wide_cable_spec(), table, sel, lib, WIDE, PROFILE)
    before, after = fault_direct([(40.0, 12.0)] * 2, 21.0), fault_direct([(63.0, 12.0)] * 2, 20.0)
    holds = lambda ik, ip, r: ik <= r and ip <= 2.5 * r
    for bus, bays in (("MV1", 5), ("MV2", 3)):
        first = min(ratings, key=lambda i: ratings[i][1] if holds(*before[bus], ratings[i][0]) else math.inf)
        final = min(ratings, key=lambda i: ratings[i][1] if holds(*after[bus], ratings[i][0]) else math.inf)
        assert first == "SG20" and final != first                 # the case escalates
        got = row(out, f"switchgear {bus}")
        assert (got["library_id"], got["units"]) == (final, bays)
        assert got["capex_eur"] == pytest.approx(bays * ratings[final][1])
        assert out["spec"]["campus"]["buses"][bus]["ik_rated_ka"]["value"] == ratings[final][0]
        assert out["history"][out["history"]["need"] == f"switchgear {bus}"].iloc[0]["from"] == "SG20 per bay"
    assert set(out["history"]["check"]) == {"switchgear"}
    assert "switchgear PCC" not in set(out["investment"]["need"])        # no 110 kV switchgear in this library
    comp_ = out["compliance"].set_index("check")
    assert comp_.at["switchgear", "status_as_is"] == "not_rated"
    assert comp_.at["switchgear", "status_with_measures"] == "not_rated"  # the PCC stays unrated
    assert "MV1" not in comp_.at["switchgear", "detail_with_measures"]


def test_a_rated_bus_whose_rating_holds_is_not_a_need(tmp_path):
    spec = wide_cable_spec()
    spec["campus"]["buses"]["MV2"]["ik_rated_ka"] = t(40.0, "datasheet")
    lib = library(tmp_path, transformers=[tr("T63", 63.0, 1.0e6)], switchgear=[sg("SG20", 20.0, 20.0, 10e3)])
    table, sel = hourly((2030, 7, {**H7, "DC_LOAD": -20.0}, {}))
    out = select_assets(spec, table, sel, lib, WIDE, PROFILE)
    assert "switchgear MV2" not in set(out["investment"]["need"])
    assert out["spec"]["campus"]["buses"]["MV2"]["ik_rated_ka"]["value"] == 40.0


# --------------------------------------------------------------------------
# timing and cost
# --------------------------------------------------------------------------

def test_an_existing_adequate_transformer_is_kept_at_zero_and_compensation_is_bought_when_first_needed(tmp_path):
    """Three periods. Every inverter is off, the load is at pf 0.85; the PCC
    draws 13.5 Mvar at 20 MW (2030, 2050) and 17.3 Mvar at 25 MW (2040),
    against a 15 Mvar band. A 3 Mvar bank is needed in 2040 only; with a
    5-year life it is gone by 2050."""
    spec = lowpf_spec()
    lib = library(tmp_path, transformers=[tr("T63", 63.0, 1.0e6)],
                  capacitor_banks=[comp("capacitor_banks", "CAP3", 3.0, 120e3, life=5)],
                  statcoms=[comp("statcoms", "ST5", 5.0, 500e3)])
    table, sel = dark_hours((2030, 20.0), (2040, 25.0), (2050, 20.0))
    out = select_assets(spec, table, sel, lib, ReactiveRequirement(15.0, "c", "code"), PROFILE)
    kept = row(out, "transformer TR1+TR2")
    assert kept["status"] == "kept" and bool(kept["existing"]) and kept["annualised_eur_per_a"] == 0.0
    assert out["spec"]["campus"]["transformers"] == spec["campus"]["transformers"]
    cap = row(out, "reactive")
    assert (cap["library_id"], cap["kind"], cap["units"], cap["invest_period"]) == ("CAP3", "capacitor_bank", 1, 2040)
    annual = 120e3 * (crf(RATE, 5) + 0.02)
    assert cap["capex_eur"] == pytest.approx(120e3) and cap["opex_eur_per_a"] == pytest.approx(2400.0)
    assert cap["annualised_eur_per_a"] == pytest.approx(annual)
    cost = out["cost"].set_index("period")
    assert list(cost["annualised_eur_per_a"]) == pytest.approx([0.0, annual, 0.0])
    assert list(cost["capex_eur"]) == pytest.approx([0.0, 120e3, 0.0])
    d = out["dispatch"]
    steps = d[d["kind"] == "capacitor_bank"].sort_values("period")
    assert list(steps["element"]) == ["CAP_1"] * 3 and list(steps["steps"]) == [0, 1, 0]
    assert out["spec"]["campus"]["compensation"] == [
        {"name": "CAP_1", "bus": "MV1", "kind": "capacitor_bank", "q_mvar": {"value": 3.0, "source": "assumed"},
         "library_id": "CAP3", "steps": 1}]
    comp_ = out["compliance"].set_index("check")
    assert comp_.at["pcc_reactive", "status_as_is"] == "fail"
    assert comp_.at["pcc_reactive", "status_with_measures"] == "pass"
    assert abs(comp_.at["pcc_reactive", "value_with_measures"]) <= 15.0 + 0.011


def test_a_campus_without_needs_beyond_its_transformers_reports_reactive_as_not_needed(tmp_path):
    lib = library(tmp_path, transformers=[tr("T100", 100.0, 1.0e6)])
    table, sel = hourly((2030, 7, H7, {}))
    out = select_assets(wide_cable_spec(), table, sel, lib, WIDE, PROFILE)
    r = row(out, "reactive")
    assert r["status"] == "not_needed" and r["kind"] == "none" and r["annualised_eur_per_a"] == 0.0


def test_a_voltage_compensation_cannot_lift_is_unresolved_with_the_tap_change_named(tmp_path):
    """A 30 % impedance unit carries the flow but leaves MV2 below 0.90 pu
    (solved here). A voltage maps to the reactive need, so a STATCOM is
    tried; it is dispatched to the PCC band, which holds, so it does nothing
    for the voltage, and the selector says so instead of trying the rest."""
    assert solve_direct(H7, [(60.0, 30.0)]).res_bus["vm_pu"].min() < 0.9
    lib = library(tmp_path, transformers=[tr("T60", 60.0, 1.0e6, vk=30.0)],
                  statcoms=[comp("statcoms", "ST10", 10.0, 400e3), comp("statcoms", "ST20", 20.0, 700e3)])
    table, sel = hourly((2030, 7, H7, {}))
    out = select_assets(single_spec(), table, sel, lib, WIDE, PROFILE, SizingCriteria(margin=0.15))
    h = out["history"]
    assert list(h["need"]) == ["reactive", "reactive"] and list(h["check"]) == ["campus_voltage"] * 2
    assert (h.iloc[0]["from"], h.iloc[0]["to"]) == ("none", "1 x ST10")
    got = row(out, "reactive")
    assert got["status"] == "unresolved" and "tap change" in got["reason"]
    assert row(out, "transformer TR1")["library_id"] == "T60"
    comp_ = out["compliance"].set_index("check")
    assert comp_.at["campus_voltage", "status_with_measures"] == "fail"
    assert comp_.at["campus_voltage", "value_with_measures"] < 0.9
