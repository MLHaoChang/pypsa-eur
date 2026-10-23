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
from gridspine.static.campus_invest import INVEST_CHECKS, ParallelRules, parallel_check, select_assets
from gridspine.static.campus_reactive import ReactiveRequirement
from gridspine.templates.campus_assets import load_asset_library
from gridspine.templates.grid_codes import load_grid_code
from tests.gridspine.test_campus import campus_spec, direct_net, t
from tests.gridspine.test_campus_flow import rows

PROFILE = load_grid_code("eu_rfg_dcc_ce")
WIDE = ReactiveRequirement(100.0, "test band", "assumed")
RATE = 0.05


def _judged(comp):
    """The rows a profile without SAGC-type limits judges. Its new PCC rows
    (no Q band, no power-factor limit set) are unknown, never pass or fail."""
    checks = comp.index if comp.index.name == "check" else comp["check"]
    new = checks.isin(("pcc_q_band", "pcc_power_factor"))
    assert set(comp[new]["status_with_measures"]) == {"unknown"}
    return comp[~new]


def crf(r, n):
    """Capital recovery factor, restated here: r / (1 - (1 + r)^-n)."""
    return r / (1 - (1 + r) ** -n)


def tr(i, s, capex, vk=12.0, life=40, opex=0.01, vector_group=None, tap=None):
    e = {"id": i, "hv_kv": t(110.0), "lv_kv": t(20.0), "s_mva": t(s), "vk_percent": t(vk),
         "vkr_percent": t(0.4), "pfe_kw": t(25.0), "i0_percent": t(0.05), "capex_eur": t(capex),
         "opex_frac": t(opex), "lifetime_a": t(life)}
    if vector_group is not None:
        e["vector_group"] = t(vector_group)
    if tap is not None:
        e["tap"] = tap
    return e


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
    assert set(_judged(comp)["status_with_measures"]) <= {"pass", "not_rated"}
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
    assert set(_judged(out["compliance"])["status_with_measures"]) <= {"pass", "not_rated"}


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
    it off, and one, two or three of them all fail the AC re-check. A bank
    plus a reactor (cheaper than two banks) adds no capacitive Mvar, so the
    escalation passes over it."""
    lib = library(tmp_path, capacitor_banks=[comp("capacitor_banks", "CAP30", 30.0, 100e3)],
                  shunt_reactors=[comp("shunt_reactors", "SR5", 5.0, 50e3)])
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
    against a 15 Mvar band. A 3 Mvar bank is needed in 2040 only. The 2.3
    Mvar gap is 2.8 Mvar with the 20 % margin, so the cheaper 2.5 Mvar bank
    is not a candidate.

    Plan C14 changed this test's cost on purpose: before it, the bank's
    5-year life ended in 2045 and 2050 cost nothing. Now the bank is bought
    again like for like when its life ends inside the horizon (the spans are
    the 10-year gaps, so H = 2060): in 2045, 2050 and 2055. The cost is
    flat from 2040 and each decade buys two banks."""
    spec = lowpf_spec()
    lib = library(tmp_path, transformers=[tr("T63", 63.0, 1.0e6)],
                  capacitor_banks=[comp("capacitor_banks", "CAP2p5", 2.5, 100e3, life=5),
                                   comp("capacitor_banks", "CAP3", 3.0, 120e3, life=5)],
                  statcoms=[comp("statcoms", "ST5", 5.0, 500e3)])
    table, sel = dark_hours((2030, 20.0), (2040, 25.0), (2050, 20.0))
    out = select_assets(spec, table, sel, lib, ReactiveRequirement(15.0, "c", "code"), PROFILE)
    assert out["history"].empty
    kept = row(out, "transformer TR1+TR2")
    assert kept["status"] == "kept" and bool(kept["existing"]) and kept["annualised_eur_per_a"] == 0.0
    assert out["spec"]["campus"]["transformers"] == spec["campus"]["transformers"]
    cap = row(out, "reactive")
    assert (cap["library_id"], cap["kind"], cap["units"], cap["invest_period"]) == ("CAP3", "capacitor_bank", 1, 2040)
    annual = 120e3 * (crf(RATE, 5) + 0.02)
    assert cap["capex_eur"] == pytest.approx(120e3) and cap["opex_eur_per_a"] == pytest.approx(2400.0)
    assert cap["annualised_eur_per_a"] == pytest.approx(annual)
    cost = out["cost"].set_index("period")
    assert list(cost["annualised_eur_per_a"]) == pytest.approx([0.0, annual, annual])
    assert list(cost["capex_eur"]) == pytest.approx([0.0, 240e3, 240e3])
    assert list(out["investment"].loc[out["investment"]["need"] == "reactive", "build_year"]) == [
        2040, 2045, 2050, 2055]
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
    # every case is solved with that dispatch in place: the flow's PCC Q is the dispatch's
    pcc = out["pcc"].set_index(["period", "case"])
    assert pcc.at[(2040, "intact"), "q_mvar"] == pytest.approx(comp_.at["pcc_reactive", "value_with_measures"], abs=1e-6)
    assert pcc.at[(2040, "N-1:TR1"), "q_mvar"] < 15.0 + 1.0                    # the bank stays in under N-1


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
    for the voltage, and the selector says so instead of trying the rest.

    Plan C15: the unit has no tap data, so the taps cannot act either, and
    the reason says so in place of the old "(out of scope)"."""
    assert solve_direct(H7, [(60.0, 30.0)]).res_bus["vm_pu"].min() < 0.9
    lib = library(tmp_path, transformers=[tr("T60", 60.0, 1.0e6, vk=30.0)],
                  statcoms=[comp("statcoms", "ST10", 10.0, 400e3), comp("statcoms", "ST20", 20.0, 700e3)])
    table, sel = hourly((2030, 7, H7, {}))
    out = select_assets(single_spec(), table, sel, lib, WIDE, PROFILE, SizingCriteria(margin=0.15))
    h = out["history"]
    assert list(h["need"]) == ["reactive", "reactive"] and list(h["check"]) == ["campus_voltage"] * 2
    assert (h.iloc[0]["from"], h.iloc[0]["to"]) == ("none", "1 x ST10")
    got = row(out, "reactive")
    assert got["status"] == "unresolved" and "TR1 has no on-load tap data" in got["reason"]
    assert "MV2" in got["reason"] and "out of scope" not in got["reason"]
    assert row(out, "transformer TR1")["library_id"] == "T60"
    comp_ = out["compliance"].set_index("check")
    assert comp_.at["campus_voltage", "status_with_measures"] == "fail"
    assert comp_.at["campus_voltage", "value_with_measures"] < 0.9


def test_existing_transformers_short_of_the_margin_are_replaced_not_kept(tmp_path):
    """At 30 MW (pf 0.85) a survivor carries about 38.5 MVA (solved here):
    within its 40 MVA, but not with the 20 % margin. The existing pair is
    not offered, so the library pair is chosen without an escalation.

    Plan C13: units may be added next to existing ones, but only when they
    can run in parallel with them. These existing units are wound 110/21 kV
    and the library's 110/20 kV, a ratio 5 % apart against the 0.5 %
    allowed, so nothing can be added and the pair is still replaced whole.
    (The plan's worked example expected this test's outcome to change; it
    missed the 21 kV windings. The add is shown on a 110/20 kV pair below.)"""
    hour = {"DC_LOAD": -30.0, "BESS1": 0.0, "PV1": 0.0, "GEN1": 0.0}
    survivor = max(s_hv(solve_direct(hour, [(40.0, 12.0)] * 2, out=0, lv_kv=21.0, pf=0.85)))
    assert survivor < 40.0 < survivor * 1.2
    assert abs((110.0 / 20.0) / (110.0 / 21.0) - 1) > 0.005                  # the ratio rule, by hand
    lib = library(tmp_path, transformers=[tr("T63", 63.0, 1.2e6)])
    table, sel = dark_hours((2030, 30.0))
    out = select_assets(lowpf_spec(), table, sel, lib, WIDE, PROFILE)
    got = row(out, "transformer TR1+TR2")
    assert (got["library_id"], got["units"], got["status"], bool(got["existing"])) == ("T63", 2, "chosen", False)
    assert got["capex_eur"] == pytest.approx(2 * 1.2e6)
    assert got["action"] == "replace"
    assert out["history"].empty
    need = next(n for n in out["state"].needs if n.key == "transformer TR1+TR2")
    assert need.candidates and not any(c.keep for c in need.candidates)      # nothing could be added


def test_the_pcc_switchgear_counts_the_grid_connection_as_a_bay(tmp_path):
    lib = library(tmp_path, transformers=[tr("T63", 63.0, 1.0e6)], switchgear=[sg("SG110", 110.0, 31.5, 100e3)])
    table, sel = hourly((2030, 7, {**H7, "DC_LOAD": -20.0}, {}))
    out = select_assets(wide_cable_spec(), table, sel, lib, WIDE, PROFILE)
    pcc = row(out, "switchgear PCC")
    assert (pcc["library_id"], pcc["units"]) == ("SG110", 3)           # TR1, TR2 and the grid
    assert out["spec"]["campus"]["pcc"]["ik_rated_ka"]["value"] == 31.5


def test_a_statcom_bought_feeds_the_re_checked_fault_level(tmp_path):
    """The only candidate is a STATCOM; the re-check energises it in the IEC
    60909 run as a current source of 1.2 times its rating (the campus
    file's screening), checked against the hand-built network."""
    import pandapower.shortcircuit as sc
    lib = library(tmp_path, statcoms=[comp("statcoms", "ST10", 10.0, 400e3)])
    table, sel = dark_hours((2040, 25.0))
    out = select_assets(lowpf_spec(), table, sel, lib, ReactiveRequirement(15.0, "c", "code"), PROFILE)
    assert row(out, "reactive")["library_id"] == "ST10"
    net = direct_net()
    net.sgen["in_service"] = False
    pp.create_sgen(net, net.bus.index[net.bus["name"] == "MV1"][0], p_mw=0.0, sn_mva=10.0, k=1.2, rx=0.1,
                   generator_type="current_source")
    sc.calc_sc(net, case="max", ip=True)
    want = float(net.res_bus_sc.at[net.bus.index[net.bus["name"] == "MV1"][0], "ikss_ka"])
    got = out["short_circuit"].set_index(["period", "bus"]).at[(2040, "MV1"), "ikss_max_ka"]
    assert got == pytest.approx(want, rel=1e-9)


def test_without_n_minus_1_the_intact_flow_with_the_margin_decides(tmp_path):
    """One unit, N-1 off: 47.2 MVA at H7 (solved here) needs 56.7 MVA with
    the 20 % margin. A cheaper 50 MVA unit would carry the flow without the
    margin; it is not offered, so nothing is escalated."""
    s = part_one_flow()
    assert 50.0 > s and 50.0 < s * 1.2 < 63.0
    lib = library(tmp_path, transformers=[tr("T50", 50.0, 0.9e6), tr("T63", 63.0, 1.2e6)])
    table, sel = hourly((2030, 7, H7, {}))
    out = select_assets(single_spec(), table, sel, lib, WIDE, PROFILE, SizingCriteria(margin=0.2, n_minus_1=False))
    assert row(out, "transformer TR1")["library_id"] == "T63"
    assert out["history"].empty


def test_a_unit_within_its_rating_but_short_of_the_margin_after_the_re_solve_is_escalated(tmp_path):
    """Rated 0.5 % above part one's flow x 1.2, an 18 % impedance unit loads
    to about 85 %: within its rating, but its re-solved flow x 1.2 is above
    it, so the sizing rule (not the loading) escalates it."""
    s = part_one_flow()
    rating = round(s * 1.2 * 1.005, 3)
    resolved = max(s_hv(solve_direct(H7, [(rating, 18.0)])))
    assert resolved < rating and resolved * 1.2 > rating                   # the oracle for the case
    lib = library(tmp_path, transformers=[tr("T_CHEAP", rating, 1.0e6, vk=18.0), tr("T_OK", 70.0, 1.5e6)])
    table, sel = hourly((2030, 7, H7, {}))
    out = select_assets(single_spec(), table, sel, lib, WIDE, PROFILE, SizingCriteria(margin=0.2))
    assert row(out, "transformer TR1")["library_id"] == "T_OK"
    h = out["history"]
    assert list(h["to"]) == ["1 x T_OK"] and list(h["check"]) == ["transformer_loading"]


def test_pcc_switchgear_owned_by_the_grid_operator_is_not_bought(tmp_path):
    """Owner decision (2026-10-05): costing the PCC bay is a study setting.
    With the operator owning it, the PCC is never a need, neither at the start
    nor when a re-check fails there; its compliance row still reports it."""
    lib = library(tmp_path, transformers=[tr("T63", 63.0, 1.0e6)], switchgear=[sg("SG110", 110.0, 31.5, 100e3)])
    table, sel = hourly((2030, 7, {**H7, "DC_LOAD": -20.0}, {}))
    out = select_assets(wide_cable_spec(), table, sel, lib, WIDE, PROFILE, pcc_switchgear=False)
    assert "switchgear PCC" not in set(out["investment"]["need"])
    assert "ik_rated_ka" not in out["spec"]["campus"]["pcc"]
    horizon = {"period_years": {2030: 1}, "source": {2030: "single period: one year"}, "horizon_end": 2031}
    assert {k: v for k, v in out["scope"]["horizon"].items() if k != "notes"} == horizon      # plan C14
    assert {k: v for k, v in out["scope"].items() if k != "horizon"} == {
        "pcc_switchgear": "grid_operator", "parallel": ParallelRules().scope(), "pcc_tap_control": "campus"}
    assert out["compliance"].set_index("check").at["switchgear", "status_with_measures"] == "not_rated"
    assert {k: v for k, v in select_assets(wide_cable_spec(), table, sel, lib, WIDE, PROFILE)["scope"].items()
            if k != "horizon"} == {"pcc_switchgear": "campus", "parallel": ParallelRules().scope(),
                                   "pcc_tap_control": "campus"}


def test_a_rated_pcc_that_fails_is_reported_not_bought_when_the_operator_owns_it(tmp_path):
    lib = library(tmp_path, transformers=[tr("T63", 63.0, 1.0e6)], switchgear=[sg("SG110", 110.0, 31.5, 100e3)])
    spec = wide_cable_spec()
    spec["campus"]["pcc"]["ik_rated_ka"] = {"value": 0.5, "source": "datasheet"}
    table, sel = hourly((2030, 7, {**H7, "DC_LOAD": -20.0}, {}))
    out = select_assets(spec, table, sel, lib, WIDE, PROFILE, pcc_switchgear=False)
    assert "switchgear PCC" not in set(out["investment"]["need"])
    assert out["compliance"].set_index("check").at["switchgear", "status_with_measures"] == "fail"
    assert out["history"].empty or "switchgear PCC" not in set(out["history"]["need"])


# --------------------------------------------------------------------------
# plan C13: adding units next to an existing transformer group
# --------------------------------------------------------------------------

def unit(s=40.0, vk=12.0, hv=110.0, lv=20.0, vector_group=None, tap=None, existing=True):
    """A campus-file transformer between PCC and MV1."""
    u = {"hv_bus": "PCC", "lv_bus": "MV1", "sn_mva": t(s), "vn_hv_kv": t(hv), "vn_lv_kv": t(lv),
         "vk_percent": t(vk), "vkr_percent": t(0.4), "pfe_kw": t(25.0), "i0_percent": t(0.05)}
    if existing:
        u["existing"] = True
    if vector_group:
        u["vector_group"] = vector_group
    if tap:
        u["tap"] = tap
    return u


def taps(changer="on_load", side="hv", neutral=0, lo=-9, hi=9, step=1.5, pos=None):
    out = {"changer": changer, "side": side, "neutral": neutral, "min": lo, "max": hi, "step_percent": t(step)}
    if pos is not None:
        out["pos"] = pos
    return out


def check(entry, *existing, rules=ParallelRules()):
    return parallel_check(entry, {f"TR{i + 1}": u for i, u in enumerate(existing)}, rules)


def test_the_parallel_rules_default_to_the_owners_choices_and_are_tagged_assumed():
    r = ParallelRules()
    assert (r.ratio_tol, r.uk_tol, r.step_tol_pct, r.unknown_vector_group) == (0.005, 0.10, 0.1, "assume_compatible")
    scope = r.scope()
    assert {k: scope[k]["value"] for k in ("ratio_tol", "uk_tol", "step_tol_pct")} == {
        "ratio_tol": 0.005, "uk_tol": 0.10, "step_tol_pct": 0.1}
    assert all(scope[k]["source"] == "assumed" and scope[k]["note"] for k in ("ratio_tol", "uk_tol", "step_tol_pct"))
    assert scope["unknown_vector_group"] == "assume_compatible" and scope["rating_ratio"] == "not limited"


@pytest.mark.parametrize("vk, ok", [(11.0, True), (11.01, False), (9.0, True), (8.99, False)])
def test_the_impedance_rule_on_its_boundary(vk, ok):
    why, _flags, _ = check(tr("N", 20.0, 1.0, vk=vk), unit(vk=10.0))
    assert (why is None) == ok
    if not ok:
        assert "impedance" in why and "TR1" in why


def test_the_impedance_rule_holds_against_each_existing_unit():
    # 11 % is within 10 % of 10.5 % but not of 9.9 %
    assert check(tr("N", 20.0, 1.0, vk=11.0), unit(vk=10.5))[0] is None
    assert check(tr("N", 20.0, 1.0, vk=11.0), unit(vk=10.5), unit(vk=9.9))[0] is not None


def test_a_110_21_kv_unit_and_a_110_20_kv_unit_do_not_share_a_ratio():
    why, _, _ = check(tr("N", 20.0, 1.0), unit(lv=21.0))
    assert why is not None and "ratio" in why


def test_the_ratio_rule_on_its_boundary():
    """The deviation is |r_new - r_old| / r_old, restated here; a rule at
    exactly that deviation accepts, a hair below refuses."""
    dev = abs(110.0 / 20.0 - 110.0 / 20.1) / (110.0 / 20.1)
    assert dev == pytest.approx(0.005, abs=1e-6)
    assert check(tr("N", 20.0, 1.0), unit(lv=20.1), rules=ParallelRules(ratio_tol=dev))[0] is None
    assert check(tr("N", 20.0, 1.0), unit(lv=20.1), rules=ParallelRules(ratio_tol=dev * (1 - 1e-9)))[0] is not None


def test_the_ratio_is_compared_at_the_existing_units_current_tap():
    """The existing unit sits two 2.5 % steps down on its HV taps: 104.5/20
    kV in effect, 5 % off a plain 110/20 kV unit. At the neutral they would
    agree. A unit with the same taps follows it to the same position."""
    old = unit(tap=taps(changer="off_circuit", lo=-2, hi=2, step=2.5, pos=-2))
    why, _, _ = check(tr("N", 20.0, 1.0), old)
    assert why is not None and "ratio" in why
    same = taps(changer="off_circuit", lo=-2, hi=2, step=2.5)
    why, _, offset = check(tr("N", 20.0, 1.0, tap=same), old)
    assert why is None and offset == -2


@pytest.mark.parametrize("old", [
    unit(hv=115.5, tap=taps(changer="off_circuit", lo=-2, hi=2, step=2.5, pos=-2)),     # 115.5 x 0.95 = 109.7 kV
    unit(lv=19.0, tap=taps(changer="off_circuit", side="lv", lo=-2, hi=2, step=2.5, pos=2)),  # 19 x 1.05 = 19.95 kV
])
def test_a_tapped_unit_is_compared_at_its_effective_ratio(old):
    """The tap's sign by hand: on the HV side the ratio is vn_hv (1 + t s) /
    vn_lv, on the LV side vn_hv / (vn_lv (1 + t s)). Both units here are
    within 0.3 % of a plain 110/20 kV unit at their positions, and 5 % or
    more off it at the neutral or with the sign the other way."""
    assert abs(115.5 * 0.95 / 20 - 5.5) / (115.5 * 0.95 / 20) < 0.005 < abs(115.5 / 0.95 / 20 - 5.5) / 5.5
    assert abs(110 / (19 * 1.05) - 5.5) / (110 / (19 * 1.05)) < 0.005 < abs(110 * 1.05 / 19 - 5.5) / 5.5
    why, _, _ = check(tr("N", 20.0, 1.0), old)
    assert why is None, why


def test_vector_groups_must_share_the_clock_number():
    assert check(tr("N", 20.0, 1.0, vector_group="Dyn5"), unit(vector_group="Dyn11"))[0] is not None
    why, flags, _ = check(tr("N", 20.0, 1.0, vector_group="YNd11"), unit(vector_group="Dyn11"))
    assert why is None and flags == []                       # the clock, not the letters
    assert check(tr("N", 20.0, 1.0, vector_group="Dyn1"), unit(vector_group="Dyn11"))[0] is not None


@pytest.mark.parametrize("new_vg, old_vg", [(None, "Dyn11"), ("Dyn11", None), (None, None)])
def test_an_unknown_vector_group_is_assumed_compatible_and_flagged(new_vg, old_vg):
    why, flags, _ = check(tr("N", 20.0, 1.0, vector_group=new_vg), unit(vector_group=old_vg))
    assert why is None and flags == ["vector group assumed compatible (not given)"]
    why, _, _ = check(tr("N", 20.0, 1.0, vector_group=new_vg), unit(vector_group=old_vg),
                      rules=ParallelRules(unknown_vector_group="refuse"))
    assert why is not None and "vector group" in why


@pytest.mark.parametrize("new_tap, old_tap, ok", [
    (taps(step=1.5), taps(step=1.5), True),
    (taps(step=1.25), taps(step=1.5), False),                 # the plan's boundary case: 0.25 point apart
    (taps(step=1.6), taps(step=1.5), True),                   # 0.1 point apart (0.1000...09 in floating point)
    (taps(step=1.61), taps(step=1.5), False),
    (taps(side="lv"), taps(side="hv"), False),
    (taps(lo=-8, hi=9), taps(lo=-9, hi=9), False),            # cannot reach the existing range
    (taps(lo=-9, hi=8), taps(lo=-9, hi=9), False),
    (taps(neutral=10, lo=1, hi=19), taps(lo=-9, hi=9), True),  # the same range, numbered from 1
    (taps(neutral=5, lo=1, hi=9), taps(lo=-4, hi=4), True),
    (None, taps(), False),                                    # an OLTC unit and one that cannot follow it
    (taps(changer="off_circuit"), taps(changer="on_load"), False),
    (taps(changer="on_load"), taps(changer="off_circuit"), True),
    (None, taps(changer="off_circuit"), True),                # the ratio rule alone, at the existing position
    (taps(), None, True),
    (None, None, True),
])
def test_the_tap_changer_rules(new_tap, old_tap, ok):
    why, _, _ = check(tr("N", 20.0, 1.0, tap=new_tap), unit(tap=old_tap))
    assert (why is None) == ok, why


def test_the_step_rule_is_inclusive_on_an_exact_boundary():
    rule = ParallelRules(step_tol_pct=0.25)
    assert check(tr("N", 20.0, 1.0, tap=taps(step=1.25)), unit(tap=taps(step=1.5)), rules=rule)[0] is None
    rule = ParallelRules(step_tol_pct=0.2499)
    assert check(tr("N", 20.0, 1.0, tap=taps(step=1.25)), unit(tap=taps(step=1.5)), rules=rule)[0] is not None


def test_the_rating_ratio_is_not_limited():
    assert check(tr("N", 1.0, 1.0), unit(s=100.0))[0] is None


# -- the add candidates, the choice and its rows ------------------------------

def rewound_spec(existing=("TR1", "TR2"), **over):
    """The test campus, its pair wound 110/20 kV (so library units can join
    it), the load at pf 0.85."""
    spec = lowpf_spec(existing=False)
    for name in ("TR1", "TR2"):
        spec["campus"]["transformers"][name] = unit(existing=name in existing, **over)
    return spec


def mixed_direct(units, load, out=None):
    """The rewound campus with ``units`` = ``[(s_mva, vk)]`` in parallel,
    every inverter off, written directly in pandapower."""
    net = direct_net()
    net.trafo.drop(net.trafo.index, inplace=True)
    for i, (s, vk) in enumerate(units):
        pp.create_transformer_from_parameters(net, 0, 1, sn_mva=s, vn_hv_kv=110.0, vn_lv_kv=20.0, vkr_percent=0.4,
                                              vk_percent=vk, pfe_kw=25.0, i0_percent=0.05, name=f"T{i}")
    if out is not None:
        net.trafo.at[out, "in_service"] = False
    net.line.at[0, "max_i_ka"] = 3.0
    net.load.at[0, "p_mw"] = load
    net.load.at[0, "q_mvar"] = load * math.tan(math.acos(0.85))
    net.sgen["in_service"] = False
    pp.runpp(net)
    return net


def per_unit_feasible(units, load, margin=0.2):
    """The independent check of a set of parallel units: every case (intact,
    each unit out) converges, each unit's S x (1 + margin) is within its own
    rating, every loading within 100 % and every voltage within 0.90-1.10 pu."""
    for out in [None, *range(len(units))]:
        try:
            net = mixed_direct(units, load, out)
        except pp.LoadflowNotConverged:
            return False
        for i in net.trafo.index[net.trafo["in_service"]]:
            s = math.hypot(net.res_trafo.at[i, "p_hv_mw"], net.res_trafo.at[i, "q_hv_mvar"])
            if s * (1 + margin) > units[i][0] + 1e-9 or net.res_trafo.at[i, "loading_percent"] > 100.0:
                return False
        if not ((net.res_bus["vm_pu"] >= 0.9) & (net.res_bus["vm_pu"] <= 1.1)).all():
            return False
    return True


#: A small library: three sizes, two impedances, one too far off to be added.
SMALL = {"T10": (10.0, 12.0, 0.30e6), "T20": (20.0, 12.0, 0.55e6), "T20B": (20.0, 10.8, 0.50e6),
         "T40": (40.0, 12.0, 0.95e6), "T63": (63.0, 12.0, 1.20e6), "T63X": (63.0, 15.0, 1.10e6)}


def test_the_choice_is_the_least_cost_of_every_replacement_and_addition_by_brute_force(tmp_path):
    """33 MW at pf 0.85: a survivor of the existing pair carries about 43
    MVA, more than its 40 MVA. Every candidate is listed here (replace by 2
    or 3 units of a size; keep both and add 1 to 3 of a size within 10 % of
    their 12 %), each solved directly with the per-unit rule; the cheapest
    that passes is what the selector buys."""
    lib = library(tmp_path, transformers=[tr(i, s, c, vk=vk) for i, (s, vk, c) in SMALL.items()])
    table, sel = dark_hours((2030, 33.0))
    out = select_assets(rewound_spec(), table, sel, lib, WIDE, PROFILE)
    annual = lambda capex: capex * (crf(RATE, 40) + 0.01)
    options = []
    for i, (s, vk, capex) in SMALL.items():
        for n in (2, 3):
            options.append((n * annual(capex), "replace", i, n, [(s, vk)] * n))
        if abs(vk - 12.0) / 12.0 <= 0.10:                                         # compatible with the pair
            for k in (1, 2, 3):
                options.append((k * annual(capex), "add", i, k, [(40.0, 12.0)] * 2 + [(s, vk)] * k))
    best = min(o for o in options if per_unit_feasible(o[4], 33.0))
    assert best[1:4] == ("add", "T20B", 1)
    rows_ = out["investment"][out["investment"]["need"] == "transformer TR1+TR2"]
    chosen = rows_[rows_["status"] == "chosen"].iloc[0]
    assert (chosen["action"], chosen["library_id"], chosen["units"]) == best[1:4]
    assert chosen["annualised_eur_per_a"] == pytest.approx(best[0])
    assert out["history"].empty                                               # the estimate agreed with AC
    assert set(_judged(out["compliance"])["status_with_measures"]) <= {"pass", "not_rated"}


def test_units_are_added_next_to_an_existing_pair_short_of_the_margin(tmp_path):
    """The plan's worked example on a 110/20 kV pair: one T63 at the same 12 %
    added in parallel costs half of replacing the pair by two."""
    hour = {"DC_LOAD": -30.0, "BESS1": 0.0, "PV1": 0.0, "GEN1": 0.0}
    survivor = max(s_hv(solve_direct(hour, [(40.0, 12.0)] * 2, out=0, lv_kv=20.0, pf=0.85)))
    assert survivor < 40.0 < survivor * 1.2
    lib = library(tmp_path, transformers=[tr("T63", 63.0, 1.2e6), tr("T63X", 63.0, 1.0e6, vk=15.0)])
    table, sel = dark_hours((2030, 30.0))
    spec = rewound_spec()
    spec["campus"]["transformers"]["TR1"]["pypsa_name"] = "grid_import"
    out = select_assets(spec, table, sel, lib, WIDE, PROFILE)
    inv = out["investment"]
    rows_ = inv[inv["need"] == "transformer TR1+TR2"].reset_index(drop=True)
    assert len(rows_) == 2
    keep, add = rows_.iloc[0], rows_.iloc[1]
    assert (keep["action"], keep["kind"], keep["units"], keep["status"], bool(keep["existing"])) == (
        "keep", "keep", 2, "kept", True)
    assert keep["capex_eur"] == 0.0 and keep["annualised_eur_per_a"] == 0.0 and pd.isna(keep["library_id"])
    assert (add["action"], add["library_id"], add["units"], add["status"], bool(add["existing"])) == (
        "add", "T63", 1, "chosen", False)
    assert add["capex_eur"] == pytest.approx(1.2e6)                          # the new unit only
    assert add["reason"] == ("added in parallel with TR1, TR2 (existing); "
                             "vector group assumed compatible (not given)")
    # the kept units stay byte for byte; one new unit with the library's values
    trafos = out["spec"]["campus"]["transformers"]
    assert sorted(trafos) == ["TR1", "TR1_2", "TR2"]
    assert trafos["TR1"] == spec["campus"]["transformers"]["TR1"]
    assert trafos["TR2"] == spec["campus"]["transformers"]["TR2"]
    new = trafos["TR1_2"]
    assert new["library_id"] == "T63" and new["sn_mva"] == {"value": 63.0, "source": "assumed"}
    assert "existing" not in new and "pypsa_name" not in new
    # the per-unit judge on the re-solved flows: every unit within its rating with the margin
    sizing = out["state"].study.sizing.set_index("group")
    assert bool(sizing.at["TR1+TR1_2+TR2", "adequate"]) and sizing.at["TR1+TR1_2+TR2", "utilisation"] <= 1.0
    assert out["cost"]["capex_eur"].sum() == pytest.approx(1.2e6)
    assert out["history"].empty


def test_an_existing_pair_with_only_an_incompatible_unit_in_the_library_is_replaced_whole(tmp_path):
    """The same pair, a library holding only a T63 at 15 % (25 % off their 12
    %): nothing can be added, so the pair is replaced, flagged as such."""
    lib = library(tmp_path, transformers=[tr("T63X", 63.0, 1.0e6, vk=15.0)])
    table, sel = dark_hours((2030, 30.0))
    out = select_assets(rewound_spec(), table, sel, lib, WIDE, PROFILE)
    got = row(out, "transformer TR1+TR2")
    assert (got["action"], got["library_id"], got["units"], bool(got["existing"])) == ("replace", "T63X", 2, False)
    assert sorted(out["spec"]["campus"]["transformers"]) == ["TR1", "TR2"]
    assert all("existing" not in x for x in out["spec"]["campus"]["transformers"].values())


@pytest.mark.parametrize("load", [33.0, 35.5])
def test_the_add_candidates_are_k_units_of_each_compatible_entry_costed_for_the_new_units(tmp_path, load):
    """The candidate list, by hand: keep the pair and add k = 1..3 of each
    compatible entry, priced k times the entry; the shares estimated by
    S_r / uk decide which pass, with the outage total from the as-is flows.
    At 35.5 MW the 10.8 % unit's lower impedance makes one of it too small
    beside a 40 MVA survivor, which only the S_r / uk weights see."""
    lib = library(tmp_path, transformers=[tr(i, s, c, vk=vk) for i, (s, vk, c) in SMALL.items()])
    table, sel = dark_hours((2030, load))
    out = select_assets(rewound_spec(), table, sel, lib, WIDE, PROFILE)
    need = next(n for n in out["state"].needs if n.key == "transformer TR1+TR2")
    adds = {(c.items[0][1], c.items[0][2]): c for c in need.candidates if c.keep}
    s_out = max(sum(s_hv(mixed_direct([(40.0, 12.0)] * 2, load, out=k))) for k in (0, 1))
    s_int = sum(s_hv(mixed_direct([(40.0, 12.0)] * 2, load)))
    want = set()
    for i, (s, vk, _c) in SMALL.items():
        if abs(vk - 12.0) / 12.0 > 0.10:
            continue
        for k in (1, 2, 3):
            units = [(40.0, 12.0)] * 2 + [(s, vk)] * k
            w = [r / u for r, u in units]

            def holds(idx, total):
                return all(total * w[j] / sum(w[x] for x in idx) * 1.2 <= units[j][0] for j in idx)
            every = list(range(len(units)))
            if holds(every, s_int) and all(holds([x for x in every if x != j], s_out) for j in every):
                want.add((i, k))
    assert set(adds) == want and ("T10", 1) not in want and ("T10", 2) in want
    assert (("T20B", 1) in want) == (load == 33.0)
    for (i, k), c in adds.items():
        assert c.keep == ("TR1", "TR2") and c.annual == pytest.approx(k * SMALL[i][2] * (crf(RATE, 40) + 0.01))
        assert c.label == f"keep TR1, TR2 + {k} x {i}"
    assert not any(c.items[0][1] == "T63X" for c in need.candidates if c.keep)
    # replace-whole candidates stay beside them, in one list by annual cost
    assert any(not c.keep and not c.existing for c in need.candidates)
    annual = [c.annual for c in need.candidates]
    assert annual == sorted(annual)


def test_a_planned_member_is_dropped_by_an_addition_and_only_existing_units_are_kept(tmp_path):
    lib = library(tmp_path, transformers=[tr("T63", 63.0, 1.2e6)])
    table, sel = dark_hours((2030, 30.0))
    out = select_assets(rewound_spec(existing=("TR1",)), table, sel, lib, WIDE, PROFILE)
    need = next(n for n in out["state"].needs if n.key == "transformer TR1+TR2")
    keeps = {c.keep for c in need.candidates if c.keep}
    assert keeps == {("TR1",)}
    # with TR1 alone kept, one added T63 leaves TR1 to carry the hour alone: refused; two pass
    assert {c.items[0][2] for c in need.candidates if c.keep} == {2, 3}
    # put the addition on the file: TR2 (planned) goes, TR1 stays, two new units
    from gridspine.static.campus_invest import _apply
    need.pos = next(i for i, c in enumerate(need.candidates) if c.keep and c.items[0][2] == 2)
    spec, _ = _apply(rewound_spec(existing=("TR1",)), [need], out["state"].lib_by_id)
    assert sorted(spec["campus"]["transformers"]) == ["TR1", "TR1_2", "TR1_3"]
    assert spec["campus"]["transformers"]["TR1"]["existing"] is True


def test_an_added_unit_takes_the_library_vector_group_and_taps_at_the_existing_position(tmp_path):
    tap = taps(changer="on_load", lo=-9, hi=9, step=1.5)
    lib = library(tmp_path, transformers=[tr("T63", 63.0, 1.2e6, vector_group="Dyn11", tap=tap)])
    table, sel = dark_hours((2030, 30.0))
    spec = rewound_spec(vector_group="Dyn11", tap=taps(pos=1))
    out = select_assets(spec, table, sel, lib, WIDE, PROFILE)
    add = out["investment"][out["investment"]["action"] == "add"].iloc[0]
    assert add["reason"] == "added in parallel with TR1, TR2 (existing)"              # no flag: both given
    new = out["spec"]["campus"]["transformers"]["TR1_2"]
    assert new["vector_group"] == "Dyn11"
    assert new["tap"] == {"changer": "on_load", "side": "hv", "neutral": 0, "min": -9, "max": 9,
                          "step_percent": {"value": 1.5, "source": "assumed"}, "pos": 1}
    camp = build_campus(out["spec"])
    assert camp.taps["TR1_2"]["pos"] == 1 and camp.windings["TR1_2"]["clock"] == 11


def test_a_fully_existing_adequate_group_keeps_keep_first_and_the_additions_follow(tmp_path):
    lib = library(tmp_path, transformers=[tr("T63", 63.0, 1.2e6)])
    table, sel = dark_hours((2030, 20.0))
    out = select_assets(rewound_spec(), table, sel, lib, WIDE, PROFILE)
    need = next(n for n in out["state"].needs if n.key == "transformer TR1+TR2")
    assert need.candidates[0].existing and need.candidates[0].label == "keep (existing)"
    assert any(c.keep for c in need.candidates[1:])
    assert {c.items[0][2] for c in need.candidates if c.keep} == {1, 2, 3}           # k counts from 1
    kept = row(out, "transformer TR1+TR2")
    assert (kept["action"], kept["status"], kept["units"]) == ("keep", "kept", 2)


def test_an_addition_to_a_fully_existing_group_is_bought_when_the_existing_units_first_fail(tmp_path):
    """Three periods: 20 MW in 2030 (a survivor carries about 25 MVA, 30 MVA
    with the margin: the pair holds), 33 MW in 2040 and 2050 (43 MVA: it
    does not). The unit is added in 2040, by hand from the hourly loads."""
    for load, holds in ((20.0, True), (33.0, False)):
        survivor = max(s_hv(mixed_direct([(40.0, 12.0)] * 2, load, out=0)))
        assert (survivor * 1.2 <= 40.0) == holds
    lib = library(tmp_path, transformers=[tr("T20", 20.0, 0.55e6)])
    table, sel = dark_hours((2030, 20.0), (2040, 33.0), (2050, 33.0))
    out = select_assets(rewound_spec(), table, sel, lib, WIDE, PROFILE)
    inv = out["investment"].set_index("action")
    assert inv.at["keep", "invest_period"] == 2030 and inv.at["add", "invest_period"] == 2040
    annual = 0.55e6 * (crf(RATE, 40) + 0.01)
    cost = out["cost"].set_index("period")
    assert list(cost["annualised_eur_per_a"]) == pytest.approx([0.0, annual, annual])
    assert list(cost["capex_eur"]) == pytest.approx([0.0, 0.55e6, 0.0])
    # a replacement of the fully existing pair moves to that period too
    lib = library(tmp_path, transformers=[tr("T63X", 63.0, 1.0e6, vk=15.0)])
    out = select_assets(rewound_spec(), table, sel, lib, WIDE, PROFILE)
    got = row(out, "transformer TR1+TR2")
    assert (got["action"], got["invest_period"]) == ("replace", 2040)
    # a group with a planned member is bought in the first period, to energise the campus
    out = select_assets(rewound_spec(existing=("TR1",)), table, sel, lib, WIDE, PROFILE)
    assert row(out, "transformer TR1+TR2")["invest_period"] == 2030


def test_every_row_says_what_was_done(tmp_path):
    lib = library(tmp_path, transformers=[tr("T63", 63.0, 1.0e6)], capacitor_banks=[comp("capacitor_banks", "CAP3", 3.0, 120e3)])
    table, sel = dark_hours((2030, 25.0))
    out = select_assets(lowpf_spec(existing=False), table, sel, lib, ReactiveRequirement(15.0, "c", "code"), PROFILE)
    acts = out["investment"].set_index("need")["action"]
    assert acts["transformer TR1+TR2"] == "buy" and acts["reactive"] == "buy"
    lib = library(tmp_path, transformers=[tr("T100", 100.0, 1.0e6)])
    table, sel = hourly((2030, 7, H7, {}))
    out = select_assets(wide_cable_spec(), table, sel, lib, WIDE, PROFILE)
    r = row(out, "reactive")
    assert r["kind"] == "none" and (pd.isna(r["action"]) or r["action"] == "")


def test_the_parallel_rules_are_reported_with_the_scope(tmp_path):
    lib = library(tmp_path, transformers=[tr("T63", 63.0, 1.0e6)])
    table, sel = dark_hours((2030, 20.0))
    rules = ParallelRules(uk_tol=0.075)
    out = select_assets(rewound_spec(), table, sel, lib, WIDE, PROFILE, parallel=rules)
    assert out["scope"]["parallel"] == rules.scope() and out["scope"]["parallel"]["uk_tol"]["value"] == 0.075


# --------------------------------------------------------------------------
# plan C14: replacement at the end of life
# --------------------------------------------------------------------------
# Every expected year and amount below is worked by hand: a purchase k + 1 is
# in b_1 + floor(k L + 0.5) (half up), made while it is before the horizon
# end H = last period + its span; a purchase serves until its successor
# starts (or H); the annual cost of a period is each purchase's annuity times
# the share of the period it serves.

from gridspine.static.campus_invest import assumed_commissioning, period_spans, purchase_years, renewal_years  # noqa: E402

DECADES = {2030: 10, 2040: 10, 2050: 10}                  # H = 2060


def test_the_spans_are_the_projects_weightings_and_the_gaps_where_missing():
    assert period_spans([2030, 2040, 2050], DECADES) == (
        {2030: 10, 2040: 10, 2050: 10}, {2030: "project", 2040: "project", 2050: "project"})
    spans, src = period_spans([2030, 2040, 2055], None)
    assert spans == {2030: 10, 2040: 15, 2055: 15}
    assert src == {2030: "assumed: the gap to the next period", 2040: "assumed: the gap to the next period",
                   2055: "assumed: the gap from the previous period"}
    spans, src = period_spans([2030, 2040], {2030: 0, 2040: 5.0})
    assert spans == {2030: 10, 2040: 5.0} and src[2030].startswith("assumed") and src[2040] == "project"
    spans, src = period_spans([2030, 2040], {2030: float("nan"), 2040: -1})
    assert spans == {2030: 10, 2040: 10} and all(s.startswith("assumed") for s in src.values())
    assert period_spans([2030], None) == ({2030: 1}, {2030: "single period: one year"})
    assert period_spans([2030], {2030: 25}) == ({2030: 25}, {2030: "project"})


def test_purchases_by_hand():
    assert purchase_years(2030, 25, 2060) == [2030, 2055]
    assert purchase_years(2030, 40, 2060) == [2030]
    assert purchase_years(2030, 12.5, 2060) == [2030, 2043, 2055]      # 12.5 rounds up, 25.0 is whole
    assert purchase_years(2030, 12.4, 2060) == [2030, 2042, 2055]      # 12.4 rounds down, 24.8 up
    assert purchase_years(2030, 30, 2060) == [2030]                    # 2060 is the horizon end: not made
    assert purchase_years(2030, 29, 2060) == [2030, 2059]              # a year before it is


def test_an_existing_asset_is_renewed_when_its_own_life_ends():
    assert renewal_years(2000, 40, 2030, 2060) == ([2040], False)
    assert assumed_commissioning(2030, 40) == 2010
    assert renewal_years(assumed_commissioning(2030, 40), 40, 2030, 2060) == ([2050], False)
    assert assumed_commissioning(2030, 25) == 2017                     # 12.5 rounds up
    assert assumed_commissioning(2030, 24.8) == 2018                   # 12.4 rounds down
    assert renewal_years(1980, 40, 2030, 2060) == ([2030], True)       # overdue: bought in the first period
    assert renewal_years(1980, 20, 2030, 2060) == ([2030, 2050], True)
    assert renewal_years(1990, 40, 2030, 2060) == ([2030], False)      # due exactly at the first period
    assert renewal_years(2020, 40, 2030, 2060) == ([], False)          # due at H: not made
    assert renewal_years(2019, 40, 2030, 2060) == ([2059], False)
    assert renewal_years(2000, 40, 2030, 2031) == ([], False)          # a single period spans one year
    assert renewal_years(1980, 40, 2030, 2031) == ([2030], True)


def bought_t100(tmp_path, life, periods=DECADES, hours=((2030, 7, H7, {}), (2040, 19, H19, {}), (2050, 3, H3, {}))):
    lib = library(tmp_path, transformers=[tr("T100", 100.0, 1.75e6, life=life)])
    table, sel = hourly(*hours)
    return select_assets(wide_cable_spec(), table, sel, lib, WIDE, PROFILE, period_years=periods)


def test_a_new_purchase_is_bought_again_like_for_like_when_its_life_ends_in_the_horizon(tmp_path):
    out = bought_t100(tmp_path, 25)
    inv = out["investment"]
    rows_ = inv[inv["need"] == "transformer TR1+TR2"].reset_index(drop=True)
    assert len(rows_) == 2
    first, second = rows_.iloc[0], rows_.iloc[1]
    assert (first["purchase"], first["build_year"], first["service_to"], first["invest_period"], first["action"]) == (
        1, 2030, 2055, 2030, "buy")
    assert pd.isna(first["replaces"]) and first["lineage"] == "transformer TR1+TR2|T100"
    assert (second["purchase"], second["build_year"], second["service_to"], second["invest_period"],
            second["action"], second["status"]) == (2, 2055, 2060, 2050, "renew", "chosen")
    assert second["replaces"] == "transformer TR1+TR2|T100|1" and second["lineage"] == first["lineage"]
    assert (second["library_id"], second["units"], bool(second["existing"])) == ("T100", 2, False)
    assert second["capex_eur"] == pytest.approx(3.5e6)
    annual = 3.5e6 * (crf(RATE, 25) + 0.01)
    assert first["annualised_eur_per_a"] == pytest.approx(annual) == second["annualised_eur_per_a"]
    cost = out["cost"].set_index("period")
    assert list(cost["capex_eur"]) == pytest.approx([3.5e6, 0.0, 3.5e6])
    assert list(cost["capex_replacement_eur"]) == pytest.approx([0.0, 0.0, 3.5e6])
    assert list(cost["annualised_eur_per_a"]) == pytest.approx([annual] * 3)     # flat to the horizon
    # the reactive need, not needed, is never renewed
    assert (inv["need"] == "reactive").sum() == 1
    assert out["scope"]["horizon"]["period_years"] == DECADES and out["scope"]["horizon"]["horizon_end"] == 2060


def test_a_life_longer_than_the_horizon_is_not_renewed_and_the_old_rule_holds(tmp_path):
    """Nothing starts or ends inside a period: the cost per period is the
    rule before C14, restated here (every row with invest <= p < invest + L)."""
    out = bought_t100(tmp_path, 40)
    inv = out["investment"]
    assert (inv["action"] == "renew").sum() == 0
    old = [sum(r.annualised_eur_per_a for r in inv.itertuples() if r.status in ("chosen", "kept")
               and r.invest_period <= p < r.invest_period + 40) for p in (2030, 2040, 2050)]
    assert list(out["cost"]["annualised_eur_per_a"]) == pytest.approx(old)
    assert list(out["cost"]["capex_replacement_eur"]) == [0.0, 0.0, 0.0]
    row_ = row(out, "transformer TR1+TR2")
    assert (row_["service_to"], row_["build_year"]) == (2060, 2030)


def test_a_non_whole_lifetime_rounds_half_up_and_the_cost_stays_flat(tmp_path):
    out = bought_t100(tmp_path, 12.5)
    rows_ = out["investment"][out["investment"]["need"] == "transformer TR1+TR2"]
    assert list(rows_["build_year"]) == [2030, 2043, 2055]
    assert list(rows_["service_to"]) == [2043, 2055, 2060]
    assert list(rows_["purchase"]) == [1, 2, 3]
    assert list(rows_["replaces"].fillna("")) == ["", "transformer TR1+TR2|T100|1", "transformer TR1+TR2|T100|2"]
    assert list(rows_["invest_period"]) == [2030, 2040, 2050]
    annual = 3.5e6 * (crf(RATE, 12.5) + 0.01)
    cost = out["cost"].set_index("period")
    assert list(cost["capex_eur"]) == pytest.approx([3.5e6] * 3)
    assert list(cost["capex_replacement_eur"]) == pytest.approx([0.0, 3.5e6, 3.5e6])
    assert list(cost["annualised_eur_per_a"]) == pytest.approx([annual] * 3)


def test_a_single_period_study_spans_one_year_and_buys_nothing_again(tmp_path):
    out = bought_t100(tmp_path, 25, periods=None, hours=((2030, 7, H7, {}),))
    rows_ = out["investment"][out["investment"]["need"] == "transformer TR1+TR2"]
    assert list(rows_["build_year"]) == [2030] and list(rows_["service_to"]) == [2031]
    h = out["scope"]["horizon"]
    assert h["period_years"] == {2030: 1} and h["source"] == {2030: "single period: one year"}
    assert h["horizon_end"] == 2031


def existing_campus(tr1=None, tr2=None, lid="T40", cable_year=None, cable_lid="C1"):
    """The rewound campus: an existing pair of 40 MVA units, adequate at
    20 MW, kept; the cable existing too. ``tr1``/``tr2``/``cable_year``
    are commissioning years (None: not given)."""
    spec = rewound_spec()
    for name, year in (("TR1", tr1), ("TR2", tr2)):
        u = spec["campus"]["transformers"][name]
        if lid:
            u["library_id"] = lid
        if year is not None:
            u["commissioning_year"] = t(year, "datasheet")
    cb = spec["campus"]["cables"]["CB1"]
    cb["existing"] = True
    if cable_lid:
        cb["library_id"] = cable_lid
    if cable_year is not None:
        cb["commissioning_year"] = t(cable_year, "measured")
    return spec


def existing_lib(tmp_path):
    return library(tmp_path, transformers=[tr("T40", 40.0, 0.95e6, life=40)], cables=[cable("C1", 3.0, 200e3)])


def test_kept_existing_assets_are_renewed_from_their_commissioning_year(tmp_path):
    """The pair was commissioned in 2000 (L = 40: renewed in 2040); the
    cable, not a need, in 1995 (renewed in 2035, half-way through 2030's
    span, so 2030 carries half its annuity)."""
    table, sel = dark_hours((2030, 20.0), (2040, 20.0), (2050, 20.0))
    out = select_assets(existing_campus(2000, 2000, cable_year=1995), table, sel, existing_lib(tmp_path), WIDE,
                        PROFILE, period_years=DECADES)
    inv = out["investment"]
    tr_rows = inv[inv["need"] == "transformer TR1+TR2"].reset_index(drop=True)
    assert len(tr_rows) == 2
    keep, renew = tr_rows.iloc[0], tr_rows.iloc[1]
    assert (keep["action"], keep["status"], keep["purchase"], keep["library_id"], keep["units"]) == (
        "keep", "kept", 0, "T40", 2)
    assert (keep["build_year"], keep["commissioned"], keep["commissioned_source"], keep["service_to"]) == (
        2000, 2000, "datasheet", 2040)
    assert bool(keep["existing"]) and keep["capex_eur"] == 0.0 and keep["lineage"] == "transformer TR1+TR2|TR1,TR2"
    assert (renew["action"], renew["status"], renew["purchase"], renew["build_year"], renew["service_to"],
            renew["invest_period"], renew["kind"], renew["units"]) == (
        "renew", "chosen", 1, 2040, 2060, 2040, "transformer", 2)
    assert renew["replaces"] == "transformer TR1+TR2|TR1,TR2|0" and not bool(renew["existing"])
    assert renew["capex_eur"] == pytest.approx(1.9e6)
    cb = inv[inv["need"] == "cable CB1"].reset_index(drop=True)
    assert len(cb) == 2
    assert (cb.at[0, "action"], cb.at[0, "status"], cb.at[0, "library_id"], cb.at[0, "units"],
            cb.at[0, "length_km"], cb.at[0, "build_year"], cb.at[0, "service_to"]) == (
        "keep", "kept", "C1", 1, 2.0, 1995, 2035)
    assert cb.at[0, "annualised_eur_per_a"] == 0.0 and cb.at[0, "commissioned_source"] == "measured"
    assert (cb.at[1, "action"], cb.at[1, "build_year"], cb.at[1, "invest_period"], cb.at[1, "units"]) == (
        "renew", 2035, 2030, 1)
    assert cb.at[1, "capex_eur"] == pytest.approx(400e3)
    a_tr = 1.9e6 * (crf(RATE, 40) + 0.01)
    a_cb = 400e3 * (crf(RATE, 40) + 0.005)
    cost = out["cost"].set_index("period")
    assert list(cost["capex_eur"]) == pytest.approx([400e3, 1.9e6, 0.0])
    assert list(cost["capex_replacement_eur"]) == pytest.approx([400e3, 1.9e6, 0.0])
    assert list(cost["annualised_eur_per_a"]) == pytest.approx([a_cb * 5 / 10, a_cb + a_tr, a_cb + a_tr])
    # the choice is unchanged: the pair is kept, at zero in the ranking
    assert out["spec"]["campus"]["transformers"] == existing_campus(2000, 2000, cable_year=1995)["campus"]["transformers"]


def test_an_existing_asset_without_a_year_is_at_mid_life_and_one_past_its_life_is_overdue(tmp_path):
    table, sel = dark_hours((2030, 20.0), (2040, 20.0), (2050, 20.0))
    out = select_assets(existing_campus(None, 1980, cable_lid=None), table, sel, existing_lib(tmp_path), WIDE,
                        PROFILE, period_years=DECADES)
    inv = out["investment"]
    tr_rows = inv[inv["need"] == "transformer TR1+TR2"].reset_index(drop=True)
    assert list(tr_rows["action"]) == ["keep", "renew", "keep", "renew"]
    assert list(tr_rows["units"]) == [1, 1, 1, 1]
    assert list(tr_rows["build_year"]) == [2010, 2050, 1980, 2030]
    assert list(tr_rows["lineage"]) == ["transformer TR1+TR2|TR1"] * 2 + ["transformer TR1+TR2|TR2"] * 2
    assert tr_rows.at[0, "commissioned_source"] == "assumed: mid-life at the first period"
    assert tr_rows.at[2, "commissioned_source"] == "datasheet"
    assert "overdue" in tr_rows.at[3, "reason"] and "2020" in tr_rows.at[3, "reason"]
    assert tr_rows.at[3, "invest_period"] == 2030 and tr_rows.at[2, "service_to"] == 2030
    # an existing cable without a library_id is not a need and has no row
    assert "cable CB1" not in set(inv["need"])
    notes = " ".join(out["scope"]["horizon"]["notes"])
    assert "TR1" in notes and "mid-life" in notes and "TR2" in notes and "overdue" in notes
    cost = out["cost"].set_index("period")
    assert list(cost["capex_replacement_eur"]) == pytest.approx([0.95e6, 0.0, 0.95e6])


def test_an_existing_asset_without_a_library_id_is_kept_with_its_replacement_unpriced(tmp_path):
    table, sel = dark_hours((2030, 20.0))
    out = select_assets(existing_campus(2000, 2000, lid=None), table, sel, existing_lib(tmp_path), WIDE, PROFILE,
                        period_years={2030: 30})
    inv = out["investment"]
    tr_rows = inv[inv["need"] == "transformer TR1+TR2"]
    assert len(tr_rows) == 1
    keep = tr_rows.iloc[0]
    assert keep["action"] == "keep" and "unpriced" in keep["reason"] and pd.isna(keep["library_id"])
    assert any("unpriced" in n for n in out["scope"]["horizon"]["notes"])
    # one whose library_id is not in the library the run used is unpriced too
    out = select_assets(existing_campus(2000, 2000, lid="T99"), table, sel, existing_lib(tmp_path), WIDE, PROFILE,
                        period_years={2030: 30})
    keep = row(out, "transformer TR1+TR2")
    assert "unpriced" in keep["reason"] and "T99" in keep["reason"]
    assert (out["investment"]["need"] == "transformer TR1+TR2").sum() == 1


def test_a_single_period_study_renews_only_an_overdue_existing_asset(tmp_path):
    table, sel = dark_hours((2030, 20.0))
    out = select_assets(existing_campus(2000, 1980, cable_year=2001), table, sel, existing_lib(tmp_path), WIDE,
                        PROFILE)
    inv = out["investment"]
    renew = inv[inv["action"] == "renew"]
    assert list(renew["need"]) == ["transformer TR1+TR2"] and list(renew["build_year"]) == [2030]
    assert out["cost"]["capex_replacement_eur"].tolist() == pytest.approx([0.95e6])


def test_a_renewal_between_the_periods_spans_is_listed_and_counted_where_it_serves(tmp_path):
    """Spans of one year (pypsa's default weighting) leave 2031-2039 in no
    period: the pair commissioned in 1997 is renewed in 2037, listed under
    2030, counted in no period's capex, and serves the whole of 2040."""
    table, sel = dark_hours((2030, 20.0), (2040, 20.0))
    out = select_assets(existing_campus(1997, 1997, cable_lid=None), table, sel, existing_lib(tmp_path), WIDE,
                        PROFILE, period_years={2030: 1, 2040: 1})
    renew = out["investment"][out["investment"]["action"] == "renew"].iloc[0]
    assert (renew["build_year"], renew["invest_period"], renew["service_to"]) == (2037, 2030, 2041)
    cost = out["cost"].set_index("period")
    assert list(cost["capex_eur"]) == [0.0, 0.0]
    assert list(cost["annualised_eur_per_a"]) == pytest.approx([0.0, 1.9e6 * (crf(RATE, 40) + 0.01)])
    assert any("2037" in n and "no period" in n for n in out["scope"]["horizon"]["notes"])


def test_unresolved_needs_are_never_renewed(tmp_path):
    lib = library(tmp_path, capacitor_banks=[comp("capacitor_banks", "CAP30", 30.0, 100e3, life=5)],
                  shunt_reactors=[comp("shunt_reactors", "SR5", 5.0, 50e3)])
    table, sel = dark_hours((2030, 25.0))
    out = select_assets(lowpf_spec(), table, sel, lib, ReactiveRequirement(15.0, "c", "code"), PROFILE,
                        period_years={2030: 30})
    rows_ = out["investment"][out["investment"]["need"] == "reactive"]
    assert len(rows_) == 1 and rows_.iloc[0]["status"] == "unresolved"
    assert out["cost"]["capex_eur"].sum() == 0.0


def test_an_addition_renews_its_kept_units_and_its_new_ones_on_their_own_lives(tmp_path):
    """Keep 2 + add 1 x T63 (the C13 example), the pair commissioned in 2005
    with library_id T40 (renewed in 2045), the T63 bought in 2030 with
    L = 20 (renewed in 2050)."""
    table, sel = dark_hours((2030, 30.0), (2040, 30.0), (2050, 30.0))
    spec = rewound_spec()
    for name in ("TR1", "TR2"):
        spec["campus"]["transformers"][name].update(library_id="T40", commissioning_year=t(2005))
    lib = library(tmp_path, transformers=[tr("T40", 40.0, 0.95e6, vk=40.0), tr("T63", 63.0, 1.2e6, life=20)])
    out = select_assets(spec, table, sel, lib, WIDE, PROFILE, period_years=DECADES)
    rows_ = out["investment"][out["investment"]["need"] == "transformer TR1+TR2"].reset_index(drop=True)
    assert list(rows_["action"]) == ["keep", "renew", "add", "renew"]
    assert list(rows_["build_year"]) == [2005, 2045, 2030, 2050]
    assert list(rows_["library_id"]) == ["T40", "T40", "T63", "T63"]
    assert list(rows_["purchase"]) == [0, 1, 1, 2]
    assert rows_.at[3, "replaces"] == "transformer TR1+TR2|T63|1" and rows_.at[1, "replaces"] == (
        "transformer TR1+TR2|TR1,TR2|0")


def test_an_existing_compensation_entry_with_a_library_id_is_kept_and_renewed(tmp_path):
    """A 3 Mvar bank in the campus file, commissioned in 2020, library CAP3
    (L = 25): kept at zero, bought again in 2045, inside 2040's span. A bank
    the study buys is no existing entry and gets no keep row."""
    table, sel = dark_hours((2030, 20.0), (2040, 20.0), (2050, 20.0))
    spec = existing_campus(2000, 2000, cable_lid=None)
    spec["campus"]["compensation"] = [{"name": "CAPX", "bus": "MV1", "kind": "capacitor_bank", "q_mvar": t(3.0),
                                       "steps": 1, "library_id": "CAP3", "commissioning_year": t(2020, "datasheet")}]
    lib = library(tmp_path, transformers=[tr("T40", 40.0, 0.95e6, life=40)],
                  capacitor_banks=[comp("capacitor_banks", "CAP3", 3.0, 120e3)])
    out = select_assets(spec, table, sel, lib, WIDE, PROFILE, period_years=DECADES)
    rows_ = out["investment"][out["investment"]["need"] == "compensation CAPX"].reset_index(drop=True)
    assert list(rows_["action"]) == ["keep", "renew"]
    assert (rows_.at[0, "library_id"], rows_.at[0, "units"], rows_.at[0, "build_year"], rows_.at[0, "service_to"]) == (
        "CAP3", 1, 2020, 2045)
    assert (rows_.at[1, "kind"], rows_.at[1, "build_year"], rows_.at[1, "invest_period"]) == (
        "capacitor_bank", 2045, 2040)
    assert rows_.at[1, "capex_eur"] == pytest.approx(120e3)
    assert row(out, "reactive")["kind"] == "none"
    cost = out["cost"].set_index("period")
    assert list(cost["capex_replacement_eur"]) == pytest.approx([0.0, 120e3 + 1.9e6, 0.0])


# --------------------------------------------------------------------------
# the with-measures value carries its own worst hour and case
# --------------------------------------------------------------------------

def test_with_measures_pairs_each_re_checked_value_with_its_own_worst_hour_and_case():
    """The browser run's case: as is, the campus voltage was worst at
    (2030, 158); re-checked with the assets, at (2030, 117) in the outage of
    SITE_TRANSFO. The re-checked value is reported with that hour and case,
    and the as-is columns keep their own."""
    from gridspine.static.campus_invest import with_measures
    as_is = pd.DataFrame([
        {"check": "campus_voltage", "status_as_is": "fail", "status_with_measures": "not_rechecked", "value": 0.88,
         "limit": 0.9, "unit": "pu", "worst_period": 2030, "worst_hour": 158,
         "clause": "c", "source": "assumed", "detail": "as is", "worst_case": "N-1:SITE_TRANSFO",
         "limit_min": 0.9, "limit_max": 1.1},
        {"check": "switchgear", "status_as_is": "not_rated", "status_with_measures": "not_rated", "value": 20.0,
         "limit": float("nan"), "unit": "kA", "worst_period": 2030, "worst_hour": None,
         "clause": "c", "source": "assumed", "detail": "as is", "worst_case": None,
         "limit_min": None, "limit_max": None}])
    recheck = pd.DataFrame([
        {"check": "switchgear", "status_as_is": "pass", "status_with_measures": "pass", "value": 25.1,
         "limit": 31.5, "unit": "kA", "worst_period": 2040, "worst_hour": None, "worst_case": None,
         "clause": "c", "source": "assumed", "detail": "re-checked sc"},
        {"check": "campus_voltage", "status_as_is": "pass", "status_with_measures": "pass", "value": 0.9001,
         "limit": 0.9, "unit": "pu", "worst_period": 2030, "worst_hour": 117, "worst_case": "N-1:SITE_TRANSFO",
         "clause": "c", "source": "assumed", "detail": "re-checked"}])
    table = with_measures(as_is, recheck)
    out = table.set_index("check")
    v = out.loc["campus_voltage"]
    assert (v["worst_period"], v["worst_hour"], v["value"]) == (2030, 158, 0.88)
    assert (v["worst_period_with_measures"], v["worst_hour_with_measures"], v["worst_case_with_measures"],
            v["value_with_measures"], v["status_with_measures"], v["detail_with_measures"]) == (
        2030, 117, "N-1:SITE_TRANSFO", 0.9001, "pass", "re-checked")
    assert list(table.columns) == [
        "check", "status_as_is", "status_with_measures", "value", "limit", "unit", "worst_period", "worst_hour",
        "clause", "source", "detail", "value_with_measures", "detail_with_measures",       # in use first
        "worst_case", "limit_min", "limit_max",
        "worst_period_with_measures", "worst_hour_with_measures", "worst_case_with_measures"]
    s = out.loc["switchgear"]
    assert s["worst_period_with_measures"] == 2040 and pd.isna(s["worst_hour_with_measures"])
    assert pd.isna(s["worst_case_with_measures"])


def test_the_with_measures_worst_is_the_re_checked_studys_own(tmp_path):
    """On a real selection, each with-measures worst (period, hour, case) is
    the re-checked study's worst reading, found here from its tables: the
    lowest campus voltage against the EU band (0.90-1.10 pu below 110 kV,
    restated), the highest transformer and cable loading, the largest PCC
    exchange (one per hour, no case)."""
    lib = library(tmp_path, transformers=[tr(i, s, capex) for i, (s, capex) in SIZES.items()])
    table, sel = hourly((2030, 7, H7, {}), (2030, 19, H19, {}), (2040, 3, H3, {}))
    out = select_assets(wide_cable_spec(), table, sel, lib, WIDE, PROFILE, SizingCriteria(margin=0.2))
    st = out["state"].study
    comp_ = out["compliance"].set_index("check")

    def got(check):
        r = comp_.loc[check]
        return (int(r["worst_period_with_measures"]), int(r["worst_hour_with_measures"]),
                r["worst_case_with_measures"], r["value_with_measures"])

    b = st.bus[st.bus["bus"] != "PCC"]
    key = [max(v - 1.10, 0.90 - v) for v in b["vm_pu"]]
    w = b.iloc[max(range(len(key)), key=lambda i: key[i])]               # the first of the worst
    assert got("campus_voltage") == (w["period"], w["hour"], w["case"], pytest.approx(w["vm_pu"]))
    for check, tbl in (("transformer_loading", st.trafo), ("cable_loading", st.line)):
        w = tbl.loc[tbl["loading_pct"].astype(float).idxmax()]
        assert got(check) == (w["period"], w["hour"], w["case"], pytest.approx(w["loading_pct"]))
    q = st.q.loc[st.q["q0_mvar"].abs().idxmax()]
    r = comp_.loc["pcc_reactive"]
    assert (r["worst_period_with_measures"], r["worst_hour_with_measures"]) == (q["period"], q["hour"])
    assert pd.isna(r["worst_case_with_measures"])


# --------------------------------------------------------------------------
# a replace row says what failed and what it replaces
# --------------------------------------------------------------------------

def test_a_replaced_transformer_group_says_what_failed_and_what_it_replaces(tmp_path):
    """The pair of test_existing_transformers_short_of_the_margin_are_replaced_not_kept:
    at 30 MW a survivor carries about 38.5 MVA (solved here), so a unit
    must be 1.2 x that with the 20 % margin, above its 40 MVA. TR1's
    commissioning year is given, TR2's is not."""
    hour = {"DC_LOAD": -30.0, "BESS1": 0.0, "PV1": 0.0, "GEN1": 0.0}
    survivor = max(s_hv(solve_direct(hour, [(40.0, 12.0)] * 2, out=0, lv_kv=21.0, pf=0.85)))
    spec = lowpf_spec()
    spec["campus"]["transformers"]["TR1"]["commissioning_year"] = t(2004, "datasheet")
    lib = library(tmp_path, transformers=[tr("T63", 63.0, 1.2e6)])
    table, sel = dark_hours((2030, 30.0))
    got = row(select_assets(spec, table, sel, lib, WIDE, PROFILE), "transformer TR1+TR2")
    assert got["action"] == "replace"
    assert got["reason"] == (
        f"replaces TR1 (in service since 2004) and TR2 (commissioning year not given): 2 x 40 MVA as is, "
        f"{survivor * 1.2:.2f} MVA a unit needed with the 20 % design margin")


def test_a_replaced_cable_says_it_was_overloaded_and_what_it_replaces(tmp_path):
    """The existing cable rated 0.8 kA carries 30 MW at pf 0.85 and 20 kV,
    about 1.0 kA (by hand: 30 / 0.85 / (sqrt 3 x 20)), so it is replaced."""
    assert 30.0 / 0.85 / (math.sqrt(3) * 20.0) > 0.8 * 1.2
    spec = existing_campus(tr1=2001, tr2=2001, cable_year=2012)
    spec["campus"]["cables"]["CB1"]["max_i_ka"] = t(0.8)
    lib = library(tmp_path, transformers=[tr("T40", 40.0, 0.95e6), tr("T63", 63.0, 1.2e6)],
                  cables=[cable("C1", 3.0, 200e3)])
    table, sel = dark_hours((2030, 30.0))
    got = row(select_assets(spec, table, sel, lib, WIDE, PROFILE), "cable CB1")
    # the as-is loading, solved here: the pair intact and with each unit out, the worst
    peak = max(float(mixed_direct([(40.0, 12.0)] * 2, 30.0, out=k).res_line["i_ka"].max())
               for k in (None, 0, 1)) / 0.8 * 100.0
    assert got["action"] == "replace"
    assert got["reason"] == f"replaces CB1 (in service since 2012): loaded to {peak:.1f} % as is"


def test_the_horizon_notes_speak_plain_words():
    """The ranking note reaches the panel: no plan step names in it."""
    from gridspine.static.campus_invest import RANKING_NOTE
    assert "plan C" not in RANKING_NOTE and RANKING_NOTE.startswith("Replacements are reported, not ranked")
