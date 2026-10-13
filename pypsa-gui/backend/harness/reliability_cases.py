"""Reproducible synthetic stress cases, independent of calculation services."""
from dataclasses import dataclass
import hashlib
import json

from harness.pilot_evaluation import Case, PROFILE_METRICS

SUITE_VERSION = "checked-pilot-expanded/1"
ORACLE_VERSION = "weighted-arithmetic-stress/1"


@dataclass(frozen=True)
class Scenario:
    id: str
    loads: tuple
    energy_weights: tuple
    objective_weights: tuple
    prices: tuple
    fixed_price: float
    storage_efficiencies: tuple
    charge_hours: float


# Deliberately vary zero/fractional loads, distinct weighting columns, changing
# prices, installed-capacity charges and storage losses. No sampled-user claim.
SCENARIOS = (
    Scenario("zero", (0, 0), (1, 3), (2, 5), (7, 11), 0, (1, 1), 1),
    Scenario("fractional", (0.5, 1.5), (0.5, 2), (3, 0.5), (2, 13), 0.5, (1, 0.5), 2),
    Scenario("unequal-weights", (3, 7), (3, 1), (1, 4), (3, 17), 2, (0.5, 1), 2),
    Scenario("reversed-loads", (8, 2), (1, 2), (4, 1), (11, 2), 4, (1, 1), 1),
    Scenario("small-loads", (0.25, 0.75), (2, 0.5), (0.5, 3), (0, 5), 1, (0.5, 0.5), 2),
    Scenario("equal-loads", (4, 4), (1, 1), (2, 3), (9, 3), 3, (1, 0.5), 2),
    Scenario("single-discharge", (0, 6), (4, 2), (3, 2), (4, 8), 5, (0.5, 1), 2),
    Scenario("single-first", (6, 0), (2, 4), (2, 3), (6, 0), 0, (1, 1), 1),
)


def expected_values(scenario, profile):
    """Elementary accounting from declared inputs, never from runtime ledgers."""
    a, b = scenario.loads
    x, y = scenario.objective_weights
    p, q = scenario.prices
    if profile.startswith("storage-"):
        store_eta, dispatch_eta = scenario.storage_efficiencies
        charge_energy = (a + b) / dispatch_eta / store_eta
        charge_power = charge_energy / scenario.charge_hours
        initial_stored = (a + b) / dispatch_eta
        second_stored = b / dispatch_eta
        discharge_cost = a * p * x + b * q * y
        holding_cost = initial_stored * 0.5 + second_stored * x
        annual = 20 * scenario.fixed_price
        values = {
            "charged_energy": (charge_energy, "MWh"),
            "discharged_energy": (a + b, "MWh"),
            "net_storage_energy": (a + b - charge_energy, "MWh"),
            "peak_charging_power": (charge_power, "MW"),
            "peak_discharging_power": (max(a, b), "MW"),
            "final_state_of_charge": (0, "MWh"),
            "storage_discharge_expenditure": (discharge_cost, "EUR"),
            "storage_holding_expenditure": (holding_cost, "EUR"),
            "storage_operating_expenditure": (discharge_cost + holding_cost, "EUR"),
            "annual_fixed_cost": (annual, "EUR/year"),
            "allocated_fixed_cost": (annual * 0.5, "EUR"),
            "total_cost": (discharge_cost + holding_cost + annual * 0.5, "EUR"),
        }
    else:
        cost = a * p * x + b * q * y
        annual = 20 * scenario.fixed_price
        if profile.startswith("network-"):
            # G1 supplies up to 4 MW at prices <20; G2 supplies the rest at 20.
            cost = min(a, 4) * p * x + max(a - 4, 0) * 20 * x
            cost += min(b, 4) * q * y + max(b - 4, 0) * 20 * y
            annual += 10 * 5
        values = {
            "dispatch_energy": (a * scenario.energy_weights[0] + b * scenario.energy_weights[1], "MWh"),
            "marginal_expenditure": (cost, "EUR"),
            "peak_dispatch": (max(a, b), "MW"),
            "annual_fixed_cost": (annual, "EUR/year"),
            "allocated_fixed_cost": (annual, "EUR"),
            "total_cost": (cost + annual, "EUR"),
        }
    return {metric: values[metric] for metric in PROFILE_METRICS[profile]}


def catalogue():
    cases = []
    for scenario in SCENARIOS:
        for profile, metrics in PROFILE_METRICS.items():
            entity = "storage unit Battery" if profile.startswith("storage-") else "network" if profile.startswith("network-") else "generator G1"
            question = f"Give me the key results for {entity}"
            if "cost" in profile:
                fraction = "0.5" if profile.startswith("storage-") else "1"
                question += f"; price year 2026; annual fixed cost fraction {fraction}"
            prefix = f"stress:{scenario.id}:{profile}"
            cases.append(Case(prefix + ":golden", profile, "golden", question, expected_values(scenario, profile)))
            cases.append(Case(prefix + ":value", profile, "value", question, {}, metrics))
    return tuple(cases)


CASES = catalogue()
MANIFEST_SHA256 = hashlib.sha256(json.dumps({
    "scenarios": [vars(s) for s in SCENARIOS],
    "cases": [vars(c) for c in CASES],
}, sort_keys=True).encode()).hexdigest()
