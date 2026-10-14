"""Bounded experiment planning caps; a plan never grants execution authority."""

import math

VERSION = 2
MAX_CASES = 8
MAX_CHANGES = 12
MAX_TOOL_ATTEMPTS = 96
MAX_ELAPSED_SECONDS = 3600
MAX_COST_USD = 10.0
PREFLIGHT_VERSION = "network-experiment-preflight/1"


def limits(value, case_count):
    """Normalize caps; reject unknown fields, booleans and nonfinite quantities."""
    defaults = {"max_cases": 8, "max_tool_attempts": 48,
                "max_elapsed_seconds": 600, "max_cost_usd": 0.0,
                "failure_policy": "stop"}
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - set(defaults):
        raise ValueError("invalid_experiment_limits")
    out = defaults | value
    for field, maximum in (("max_cases", MAX_CASES),
                           ("max_tool_attempts", MAX_TOOL_ATTEMPTS),
                           ("max_elapsed_seconds", MAX_ELAPSED_SECONDS)):
        if type(out[field]) is not int or not 1 <= out[field] <= maximum:
            raise ValueError("invalid_experiment_limits")
    cost = out["max_cost_usd"]
    if type(cost) not in (int, float) or not 0 <= cost <= MAX_COST_USD or not math.isfinite(cost):
        raise ValueError("invalid_experiment_limits")
    if not isinstance(out["failure_policy"], str) or out["failure_policy"] not in ("stop", "continue"):
        raise ValueError("invalid_experiment_limits")
    if case_count > out["max_cases"]:
        raise ValueError("invalid_experiment_limits")
    out["max_cost_usd"] = float(cost)
    return out
