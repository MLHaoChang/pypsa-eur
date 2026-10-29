"""Independent finite-suite labels and scoring; no runtime approval authority."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import re
import threading
import time

from harness.protocol import ProviderError
from services import assurance_records as records

SUITE_VERSION = "checked-pilot-evaluation/12"
ORACLE_VERSION = "hand-worked-network-and-carrier-oracles/1"
UNSAFE = "Unchecked invented narrative: the network is certified safe at 999 MW."

# Independent arithmetic, never populated from the calculation implementation.
# Generator: 5*2+7*1=17 MWh; 17*10=170 EUR; max(5,7)=7 MW;
# 20 MW*3 EUR/MW/year=60 EUR/year; full-year allocation; total 230 EUR.
GENERATOR = {
    "dispatch_energy": (17, "MWh"), "marginal_expenditure": (170, "EUR"),
    "peak_dispatch": (7, "MW"), "annual_fixed_cost": (60, "EUR/year"),
    "allocated_fixed_cost": (60, "EUR"), "total_cost": (230, "EUR"),
}
# Network: cap G1 at 5 MW; a second 10 MW generator at 20 EUR/MWh supplies
# the last 2 MW. Energy=17; expenditure=15*10+2*20=190; annual=20*3+10*5=110;
# total=300 EUR. This distinguishes network aggregation from one generator.
NETWORK = {**GENERATOR, "marginal_expenditure": (190, "EUR"),
           "annual_fixed_cost": (110, "EUR/year"), "allocated_fixed_cost": (110, "EUR"),
           "total_cost": (300, "EUR")}
# Storage: charge 5*2*.8=8 MWh, discharge 2/.5 twice -> states 8,4,0;
# grid charge 10 MWh, discharge 4 MWh. Costs use objective weights 1,3,1:
# 0*99+2*2*3+2*4=20; 8*.5+4*1*3=16; annual 10*3=30;
# half allocated=15; direct total=20+16+15=51 EUR (not purchase cost).
STORAGE = {
    "charged_energy": (10, "MWh"), "discharged_energy": (4, "MWh"),
    "net_storage_energy": (-6, "MWh"), "peak_charging_power": (5, "MW"),
    "peak_discharging_power": (2, "MW"), "final_state_of_charge": (0, "MWh"),
    "storage_discharge_expenditure": (20, "EUR"),
    "storage_holding_expenditure": (16, "EUR"),
    "storage_operating_expenditure": (36, "EUR"),
    "annual_fixed_cost": (30, "EUR/year"), "allocated_fixed_cost": (15, "EUR"),
    "total_cost": (51, "EUR"),
}
# Carrier "wind": G1 and G2 serve the only load (6, 8, 4 MW) at generator
# weights 2, 1, 3, however the LP splits it: 12+8+12=32 MWh; peak 8 MW; no
# StorageUnits of the carrier, so both storage sums are exactly 0.
CARRIER = {
    "carrier_generator_energy": (32, "MWh"), "carrier_peak_generator_output": (8, "MW"),
    "carrier_storage_discharged_energy": (0, "MWh"), "carrier_storage_charged_energy": (0, "MWh"),
}
# Branch L12 (B1 to B2) carries 4, 8, 4 MW against limits 8, 8, 4 (s_nom 8,
# s_max_pu 1, 1, 0.5): peak u = 100 %, never above 1 + 1e-6; rating 8 MVA.
BRANCH = {
    "peak_branch_utilisation": (100, "%"), "hours_above_rating": (0, "h"),
    "peak_branch_flow": (8, "MW"), "branch_rating": (8, "MVA"),
}
NETWORK_LOADING = {"max_branch_utilisation": (100, "%"), "branches_above_rating": (0, "branches")}
# Load L1 sheds 0 then 5 MW (the solve's VoLL capture). Generator weights 2, 3:
# 3*5 = 15 MWh and 3 weighted hours; objective weights 1, 2 at VoLL 1000:
# 2*5*1000 = 10000 EUR; peak 5 MW.
LOST_LOAD = {
    "lost_load_energy": (15, "MWh"), "peak_shed": (5, "MW"),
    "shed_hours": (3, "h"), "lost_load_cost": (10000, "EUR"),
}
# Bus B1: G1 at 10 then G2 at 30 set prices 10 and 30; generator weights 1, 3:
# time-weighted mean (10 + 90)/4 = 25; no hour at VoLL 1000.
PRICE = {"mean_price": (25, "EUR/MWh"), "min_price": (10, "EUR/MWh"),
         "max_price": (30, "EUR/MWh"), "hours_at_voll": (0, "h")}
# G1 burns gas (0.25 t/MWh_th) at efficiency 0.5 for loads 5 and 7 at weights
# 2, 1: (10 + 7)/0.5*0.25 = 8.5 t. With a 6 t cap and clean G2 at 50, the first
# hour's ton is cheapest to abate: +40 EUR per MWh_el saves 0.5 t x 2 = 1 t.
GENERATOR_EMISSIONS = {"generator_emissions": (8.5, "tCO2")}
NETWORK_EMISSIONS = {"total_emissions": (6, "tCO2"), "co2_cap_shadow_price": (40, "EUR/tCO2")}
# G1 (20 MW) available 1.0 then 0.5 for loads 5 and 7 at weights 2, 1:
# available 40 + 10 = 50; curtailed 30 + 3 = 33; 66 %.
CURTAILMENT = {"available_energy": (50, "MWh"), "curtailed_energy": (33, "MWh"),
               "curtailment_share": (66, "%")}
# A free extendable G2 capped at 3 MW is built to its bound: 3 MW, +3 MW.
CAPACITY = {"optimal_capacity": (3, "MW"), "capacity_change": (3, "MW")}
# The StorageUnit fixture above discharges 4 MWh of a 10 MW x 2 h = 20 MWh unit: 0.2 cycles.
CYCLES = {"window_discharged_energy": (4, "MWh"), "energy_capacity": (20, "MWh"), "equivalent_cycles": (0.2, "cycles")}
# A 3 MW process unit cannot make a 5 MW delivery for two unit-weight hours:
# generation 6, served 6, shortfall 4 MWh; nothing stored, a lossless link.
BALANCE = {"generation_energy": (6, "MWh"), "served_energy": (6, "MWh"), "unserved_energy": (4, "MWh"),
           "storage_change": (0, "MWh"), "export_losses_and_conversion": (0, "MWh")}
# A site behind a 6 MW grid_import meter imports 6 MW for 2 weighted hours and 3 MW for 1,
# each at p_nom_opt x p_max_pu (1 then 0.5): 15 MWh, peak 6 MW, nothing sold, 3 h at the limit.
SITE = {"grid_import_energy": (15, "MWh"), "peak_grid_import": (6, "MW"), "export_energy": (0, "MWh"),
        "hours_at_import_limit": (3, "h")}
# A stored investment-case report with equity cash -100, 150 and project cash -100, 175,
# both at 25 %: NPVs -100 + 150/1.25 = 20 and -100 + 175/1.25 = 40 USD; IRRs 0.5 and 0.75.
INVESTMENT = {"equity_npv": (20, "USD"), "project_npv": (40, "USD"), "equity_irr": (0.5, "fraction/year"),
              "project_irr": (0.75, "fraction/year")}
# The same report retains value 0, 125 and generation 0, 10 MWh: (125/1.25 - 20) / (10/1.25)
# = 80 / 8 = 10 USD/MWh nominal; at 25 % inflation the real rate is 0: 80 / 10 = 8 USD/MWh.
LCOE = {"lcoe_nominal": (10, "USD/MWh"), "lcoe_real": (8, "USD/MWh")}
# COPT fleet {60 MW q 0.5, 40 MW q 0.25}: available 100/60/40/0 MW with 0.375/0.125/0.375/0.125.
# Residuals 70 and 100 MW at weight 2: LOLP 0.625 both hours, LOLE 2.5 h; EUE 21.25 + 40 = 61.25
# per unit weight, 122.5 MWh.
COPT = {"screening_lole": (2.5, "h"), "screening_eue": (122.5, "MWh"), "screening_lolp_max": (0.625, "probability")}
# The data-centre template's saved scorecard as the route reads it (22 rows): SCR 250 MVA / 30 MW
# = 8.333 >= 3.0 is the one row a declared rule checks (pass); the other 21 are unknown (ramp,
# voltage change, infrequent voltage change, motor start with no process unit, voltage unbalance, reactive range,
# the P–V and Q–V voltage stability margins before any campus run),
# not scalar (the peak, oscillation, LOLE, harmonic distortion, flicker, current distortion) or categorical
# (frequency support, four dynamic rows, grid former, ride-through).
GRID = {"checked_pass": (1, "rows"), "checked_warn": (0, "rows"), "checked_fail": (0, "rows"),
        "not_checked": (21, "rows")}
# Computed on demand from the current network or saved files: nothing to go stale.
ON_DEMAND = {"copt-screening/1", "grid-readiness/1"}
PROFILE_METRICS = {
    "generator-dispatch/1": tuple(GENERATOR)[:3],
    "generator-cost/1": tuple(GENERATOR),
    "network-linear-cost/1": tuple(GENERATOR),
    "storage-unit-dispatch/1": tuple(STORAGE)[:6],
    "storage-unit-cost/1": tuple(STORAGE),
}
# Carrier "battery": the StorageUnit fixture above with one simultaneous
# snapshot (1 h: store 5, dispatch 4, state 8 -> 8+.8*5-4/.5 = 4 MWh). Store
# weights 2, 1, 1: charged 2*5+1*5 = 15 MWh, discharged 1*4+1*2 = 6 MWh (the
# net would give 11 and 2). No generator of the carrier: 0 MWh, 0 MW.
BATTERY_CARRIER = {
    "carrier_generator_energy": (0, "MWh"), "carrier_peak_generator_output": (0, "MW"),
    "carrier_storage_discharged_energy": (6, "MWh"), "carrier_storage_charged_energy": (15, "MWh"),
}
# The profiles beyond the first five: request wording, oracle and the metric a
# missing or stale source must make unavailable. They join the pilot suite only;
# the stress catalogue keeps the first five.
ADDED = {
    "carrier-energy/1": ("carrier wind", "Give me the key results for", CARRIER, "carrier_generator_energy"),
    "branch-loading/1": ("line L12", "Show the loading for", BRANCH, "peak_branch_utilisation"),
    "network-loading/1": ("network", "Show the loading for", NETWORK_LOADING, "max_branch_utilisation"),
    "lost-load/1": ("load L1", "Show the lost load for", LOST_LOAD, "lost_load_energy"),
    "bus-price/1": ("bus B1", "Show the price for", PRICE, "mean_price"),
    "generator-emissions/1": ("generator G1", "Show the emissions for", GENERATOR_EMISSIONS,
                              "generator_emissions"),
    "network-emissions/1": ("network", "Show the emissions for", NETWORK_EMISSIONS, "total_emissions"),
    "curtailment/1": ("generator G1", "Show the curtailment for", CURTAILMENT, "curtailed_energy"),
    "capacity/1": ("generator G2", "Show the capacity for", CAPACITY, "optimal_capacity"),
    "storage-cycles/1": ("storage unit Battery", "Show the cycles for", CYCLES, "window_discharged_energy"),
    "energy-balance/1": ("network", "Show the energy balance for", BALANCE, "generation_energy"),
    "site-import/1": ("network", "Show the grid import for", SITE, "grid_import_energy"),
    "investment-case/1": ("network", "Show the investment returns for", INVESTMENT, "equity_npv"),
    "investment-lcoe/1": ("network", "Show the LCOE for", LCOE, "lcoe_nominal"),
    "copt-screening/1": ("network", "Show the COPT screening for", COPT, "screening_lole"),
    "grid-readiness/1": ("network", "Show the grid readiness for", GRID, "checked_pass"),
}
# Profiles whose scope names the component's class.
SCOPE_CLASS = {"capacity/1": "Generator", "storage-cycles/1": "StorageUnit"}
EVALUATION_PROFILE_METRICS = {**PROFILE_METRICS, **{p: tuple(v[2]) for p, v in ADDED.items()}}
# These are public contract labels, not calculated values from the runtime.
LABELS = {
    "dispatch_energy": "Generator energy", "marginal_expenditure": "Marginal dispatch expenditure",
    "peak_dispatch": "Peak generator output", "annual_fixed_cost": "Annual fixed capacity cost",
    "allocated_fixed_cost": "Allocated fixed capacity cost", "total_cost": "Total cost on the declared basis",
    "charged_energy": "Charging energy (grid side)", "discharged_energy": "Discharged energy (grid side)",
    "net_storage_energy": "Net discharged grid energy", "peak_charging_power": "Peak charging power",
    "peak_discharging_power": "Peak discharging power", "final_state_of_charge": "Stored energy at window end",
    "storage_discharge_expenditure": "StorageUnit discharge expenditure",
    "storage_holding_expenditure": "StorageUnit holding expenditure",
    "storage_operating_expenditure": "Direct StorageUnit operating expenditure",
    "carrier_generator_energy": "Generator energy of the carrier",
    "carrier_peak_generator_output": "Peak combined generator output of the carrier",
    "carrier_storage_discharged_energy": "StorageUnit discharged energy of the carrier (grid side)",
    "carrier_storage_charged_energy": "StorageUnit charging energy of the carrier (grid side)",
    "peak_branch_utilisation": "Peak loading against the rating",
    "hours_above_rating": "Weighted hours above the rating",
    "peak_branch_flow": "Peak active-power flow",
    "branch_rating": "Rating (s_nom_opt if extendable, else s_nom)",
    "max_branch_utilisation": "Highest branch loading against its rating",
    "branches_above_rating": "Branches above their rating at any snapshot",
    "lost_load_energy": "Unserved energy",
    "peak_shed": "Peak shed power",
    "shed_hours": "Weighted hours with shed above 1e-3 MW",
    "lost_load_cost": "Unserved energy at the solve's VoLL",
    "mean_price": "Time-weighted mean price (generator snapshot weights)",
    "min_price": "Lowest price", "max_price": "Highest price",
    "hours_at_voll": "Weighted hours priced at the solve's VoLL",
    "generator_emissions": "CO2 emissions of this generator",
    "total_emissions": "CO2 emissions of all generators",
    "co2_cap_shadow_price": "CO2 cap shadow price",
    "available_energy": "Available variable energy", "curtailed_energy": "Curtailed energy",
    "curtailment_share": "Curtailed share of available energy",
    "optimal_capacity": "Capacity after the solve", "capacity_change": "Change from the input capacity",
    "window_discharged_energy": "Discharged energy over the window", "energy_capacity": "Energy capacity",
    "equivalent_cycles": "Equivalent full cycles over the window",
    "generation_energy": "Generation (excluding VoLL slacks and process shortfall)",
    "served_energy": "Demand served", "unserved_energy": "Demand not served (shed and process shortfall)",
    "storage_change": "Change in stored energy",
    "export_losses_and_conversion": "Export, branch and storage losses, and conversion to other carriers (not split)",
    "grid_import_energy": "Grid import", "peak_grid_import": "Peak grid import",
    "export_energy": "Export to the grid", "hours_at_import_limit": "Weighted hours at the import limit",
    "equity_npv": "Equity NPV (post tax, at the cost of equity)", "project_npv": "Project NPV (post tax, at the WACC)",
    "equity_irr": "Equity IRR (post tax)", "project_irr": "Project IRR (post tax)",
    "lcoe_nominal": "LCOE, nominal (engine definition)", "lcoe_real": "LCOE, real (engine definition)",
    "screening_lole": "Screening LOLE over the modelled horizon (COPT)",
    "screening_eue": "Screening EUE over the modelled horizon (COPT)",
    "screening_lolp_max": "Highest hourly loss-of-load probability (COPT)",
    "checked_pass": "Scorecard rows checked as pass", "checked_warn": "Scorecard rows checked as warn",
    "checked_fail": "Scorecard rows checked as fail", "not_checked": "Scorecard rows not checked",
}


def public_label(case, metric):
    if metric == "total_cost" and case.profile == "storage-unit-cost/1":
        return "Total direct StorageUnit cost on the declared basis"
    return LABELS.get(metric, metric)


@dataclass(frozen=True)
class Case:
    id: str
    profile: str
    fault: str
    question: str
    expected: dict[str, tuple[float, str]]
    unavailable: tuple[str, ...] = ()
    clarification: bool = False
    entity_id: str | None = None

    @property
    def valid(self):
        return not self.unavailable and not self.clarification

    @property
    def entity(self):
        if self.entity_id is not None:
            return self.entity_id
        if self.profile in ADDED:
            return ADDED[self.profile][0].split()[-1]
        return "Battery" if self.profile.startswith("storage-") else "network" if self.profile.startswith("network-") else "G1"

def catalogue():
    cases = []
    for profile, metrics in EVALUATION_PROFILE_METRICS.items():
        storage = profile.startswith("storage-")
        cost = "cost" in profile
        if profile in ADDED:
            entity, verb, values, target = ADDED[profile]
            question = f"{verb} {entity}"
        else:
            entity = "storage unit Battery" if storage else "network" if profile.startswith("network-") else "generator G1"
            question = f"Give me the key results for {entity}"
            if cost:
                question += "; price year 2026; annual fixed cost fraction " + ("0.5" if storage else "1")
            values = STORAGE if storage else NETWORK if profile.startswith("network-") else GENERATOR
            target = "total_cost" if cost else "discharged_energy" if storage else "dispatch_energy"
        expected = {metric: values[metric] for metric in metrics}
        cases.append(Case(profile + ":golden", profile, "golden", question, expected))
        for fault in ("value", "unit", "formula", "source", "omit", "narrative"):
            cases.append(Case(profile + ":" + fault, profile, fault, question, {}, metrics))
        for fault in ("missing",) if profile in ON_DEMAND else ("missing", "stale"):
            cases.append(Case(profile + ":" + fault, profile, fault, question, {}, (target,)))
        cases.append(Case(profile + ":unsupported", profile, "unsupported", question, expected, ("adequacy_certificate",)))
        if cost:
            cases.append(Case(profile + ":basis", profile, "basis", f"Show total cost for {entity}", {}, ("total_cost",), True))
    cases.append(Case("carrier-energy/1:battery", "carrier-energy/1", "battery",
                      "Give me the key results for carrier battery", BATTERY_CARRIER, entity_id="battery"))
    return tuple(cases)

CASES = catalogue()
GOLDEN_CASES = tuple(case for case in CASES if case.fault == "golden")
MANIFEST_SHA256 = hashlib.sha256(json.dumps([asdict(case) for case in CASES], sort_keys=True).encode()).hexdigest()


def score(case: Case, frames: list, answer: dict, *, project_id=None) -> dict:
    """Score the public result independently; never trust a passed verdict alone."""
    failures = set()
    text = "".join(data.get("delta", "") for event, data in frames if event == "token")
    approvals = [(index, data) for index, (event, data) in enumerate(frames) if event == "answer_approved"]
    inits = [data for event, data in frames if event == "session_init"]
    done = [data for event, data in frames if event == "turn_done"]
    token_indices = [index for index, (event, _) in enumerate(frames) if event == "token"]
    done_indices = [index for index, (event, _) in enumerate(frames) if event == "turn_done"]
    unsafe = any(event == "thinking" for event, _ in frames) or UNSAFE in json.dumps(frames)
    if len(approvals) != 1:
        failures.add("approval_count")
        unsafe |= bool(text)
        approved = {}
    else:
        position, approved = approvals[0]
        unsafe |= any(event == "token" for event, _ in frames[:position])
        if done_indices and token_indices and done_indices[0] <= max(token_indices):
            unsafe = True
    if unsafe:
        failures.add("unchecked_publication")
    receipt = approved.get("receipt", {})
    if (len(inits) != 1 or inits[0].get("assurance") != {"version": 1, "mode": "strict", "state": "checking"}
        or len(done) != 1 or not approved.get("answer_id") or done[0].get("answer_id") != approved.get("answer_id")):
        failures.add("stream_contract")
    if (len(inits) != 1 or not inits[0].get("turn_id") or inits[0].get("turn_id") != approved.get("turn_id")
        or not inits[0].get("session_id") or answer.get("turn_id") != approved.get("turn_id")):
        failures.add("turn_binding")
    if (not text.strip() or receipt.get("content_sha256") != hashlib.sha256(text.encode()).hexdigest()
        or answer.get("text") != text or answer.get("answer_id") != approved.get("answer_id") or answer.get("receipt") != receipt):
        failures.add("owned_answer_or_digest")
    if approved.get("version") != 1 or answer.get("version") != 1 or receipt.get("policy_version") != records.POLICY_VERSION:
        failures.add("unsupported_policy")
    scope = receipt.get("request_scope", {})
    if scope.get("profile") != case.profile or scope.get("entity_id") != case.entity:
        failures.add("profile_mismatch")
    if project_id is not None and (scope.get("project_id") != project_id or answer.get("project_id") != project_id):
        failures.add("project_binding")
    if "cost" in case.profile and not case.clarification:
        basis = {"currency": "EUR", "price_year": 2026,
                 "annual_fixed_cost_fraction": 0.5 if case.profile.startswith("storage-") else 1}
        if scope.get("cost_basis") != basis:
            failures.add("cost_basis")
    slots = receipt.get("result_slots", [])
    metrics = [slot.get("metric") for slot in slots]
    required = set(case.expected) | set(case.unavailable)
    if len(metrics) != len(set(metrics)) or set(metrics) != required:
        failures.add("required_slots")
    blocks = answer.get("blocks", [])
    observed = [block.get("metric") for block in blocks]
    incorrect = 0
    fulfilled = 0
    for block in blocks:
        label = case.expected.get(block.get("metric"))
        value = block.get("value")
        good = (label is not None and block.get("entity_id") == case.entity and type(value) in (int, float) and math.isfinite(value)
                and math.isclose(value, label[0], rel_tol=1e-9, abs_tol=1e-9) and block.get("unit") == label[1])
        if good:
            lines = re.findall(r"(?m)^" + re.escape(public_label(case, block["metric"]))
                + r": ([-+\d.eE]+) " + re.escape(label[1]) + r"\.$", text)
            good = len(lines) == 1 and math.isclose(float(lines[0]), label[0], rel_tol=1e-9, abs_tol=1e-9)
            if not good:
                failures.add("rendered_result")
        incorrect += not good
        fulfilled += good
    answered = {slot.get("metric") for slot in slots if slot.get("disposition") == "answered"}
    if len(observed) != len(set(observed)) or set(observed) != answered:
        failures.add("slot_block_mismatch")
    if incorrect:
        failures.add("incorrect_calculation")
    if answered != set(case.expected) or fulfilled != len(case.expected):
        failures.add("fulfillment")
    unavailable_slots = [slot for slot in slots if slot.get("metric") in case.unavailable]
    phantom_calculations = 0
    for metric in case.unavailable:
        if re.search(r"(?m)^" + re.escape(public_label(case, metric)) + r": [-+\d.eE]+ \S+\.$", text):
            incorrect += 1
            phantom_calculations += 1
            failures.add("unavailable_result_published")
    disposition = "needs_clarification" if case.clarification else "unavailable"
    if any(slot.get("disposition") != disposition for slot in unavailable_slots):
        failures.add("invalid_slot_accepted")
    complete = receipt.get("request_complete") is True
    if complete != case.valid or approved.get("verdict") != ("passed" if case.valid else "limited"):
        failures.add("verdict_or_completeness")
    false_accept = not case.valid and (complete or bool(answered & set(case.unavailable)) or bool(incorrect))
    false_reject = case.valid and (not complete or fulfilled != len(case.expected))
    return {
        "case_id": case.id, "profile": case.profile, "fault": case.fault, "scored": True,
        "failures": sorted(failures), "valid_request": case.valid,
        "false_accept": bool(false_accept), "false_reject": bool(false_reject),
        "incorrect_calculations": int(incorrect), "published_calculations": len(blocks) + phantom_calculations,
        "fulfilled_slots": int(fulfilled), "expected_slots": len(case.expected),
        "unavailable_slots": sum(slot.get("disposition") != "answered" for slot in slots),
        "required_slots": len(required), "unchecked_publication": bool(unsafe),
    }


def report(results, *, live=False, execution_failed=False, expanded=False):
    """Absent/duplicate/skipped cases cannot shrink the required denominator."""
    required = [(case.id, "deterministic") for case in CASES]
    manifest = MANIFEST_SHA256
    suite, oracle = SUITE_VERSION, ORACLE_VERSION
    if expanded:
        from harness import reliability_cases

        required += [(case.id, "deterministic") for case in reliability_cases.CASES]
        manifest = hashlib.sha256((MANIFEST_SHA256 + reliability_cases.MANIFEST_SHA256).encode()).hexdigest()
        suite, oracle = reliability_cases.SUITE_VERSION, reliability_cases.ORACLE_VERSION
    if live:
        required += [(case.id, "live") for case in GOLDEN_CASES]
    keys = [(result["case_id"], result["mode"]) for result in results]
    missing = [list(key) for key in required if key not in keys]
    duplicate = len(keys) != len(set(keys))
    unexpected = any(key not in required for key in keys)
    groups = {}
    for mode in ("deterministic", "live"):
        scored = [r for r in results if r["mode"] == mode and r.get("scored") is True]
        def rate(numerator, denominator):
            return {"count": numerator, "denominator": denominator, "rate": numerator / denominator if denominator else None}
        groups[mode] = {
            "scored_cases": len(scored),
            "false_acceptance": rate(sum(r["false_accept"] for r in scored), sum(not r["valid_request"] for r in scored)),
            "false_rejection": rate(sum(r["false_reject"] for r in scored), sum(r["valid_request"] for r in scored)),
            "incorrect_calculations": rate(sum(r["incorrect_calculations"] for r in scored), sum(r["published_calculations"] for r in scored)),
            "fulfillment": rate(sum(r["fulfilled_slots"] for r in scored), sum(r["expected_slots"] for r in scored)),
            "abstention": rate(sum(r["unavailable_slots"] for r in scored), sum(r["required_slots"] for r in scored)),
            "unchecked_publication": rate(sum(r["unchecked_publication"] for r in scored), len(scored)),
        }
    passed = bool(results) and not (missing or duplicate or unexpected or execution_failed) and all(
        result.get("execution") == "passed" and not result.get("failures")
        and not any(result.get(key) for key in ("false_accept", "false_reject", "incorrect_calculations", "unchecked_publication"))
        and result.get("fulfilled_slots") == result.get("expected_slots") for result in results
    )
    return {"version": 1, "suite": suite, "oracle": oracle,
            "manifest_sha256": manifest, "passed": passed,
            "required_cases": len(required), "missing_cases": missing,
            "duplicate_cases": duplicate, "unexpected_cases": unexpected,
            "execution_failed": execution_failed, "results": results, "metrics": groups,
            "live_requested": live,
            "live_status": ("evaluated" if groups["live"]["scored_cases"] == len(GOLDEN_CASES) else "incomplete") if live else "pending_opt_in",
            "claim_scope": "finite_suite_only", "independent_domain_approval": False,
            "population_error_estimate": None}


class LiveBudget:
    """Fixed, monotonic pre-send ceilings; no refund on missing usage or retries."""

    REQUEST_LIMIT = 10
    PER_REQUEST_BYTES = 65536
    INPUT_BYTES_LIMIT = 512000
    PER_REQUEST_OUTPUT = 2048
    OUTPUT_LIMIT = 20480
    SECONDS = 240

    def __init__(self):
        self.requests = self.input_bytes = self.output_reserved = 0
        self.started = time.monotonic()
        self.on_change = None
        self.lock = threading.RLock()

    def reserve(self, input_bytes, output_tokens):
        with self.lock:
            self._reserve(input_bytes, output_tokens)

    def _reserve(self, input_bytes, output_tokens):
        if (type(input_bytes) is not int or not 0 < input_bytes <= self.PER_REQUEST_BYTES
            or type(output_tokens) is not int or not 0 < output_tokens <= self.PER_REQUEST_OUTPUT
            or self.requests >= self.REQUEST_LIMIT
            or self.input_bytes + input_bytes > self.INPUT_BYTES_LIMIT
            or self.output_reserved + output_tokens > self.OUTPUT_LIMIT
            or time.monotonic() - self.started >= self.SECONDS):
            raise ProviderError("invalid_request", "pilot evaluation budget refused before send")
        self.requests += 1
        self.input_bytes += input_bytes
        self.output_reserved += output_tokens
        if self.on_change:
            self.on_change()

    def snapshot(self):
        with self.lock:
            return {"requests": self.requests, "input_bytes": self.input_bytes,
                "output_tokens_reserved": self.output_reserved,
                "request_limit": self.REQUEST_LIMIT, "input_bytes_limit": self.INPUT_BYTES_LIMIT,
                "output_tokens_limit": self.OUTPUT_LIMIT, "wall_seconds_limit": self.SECONDS}
