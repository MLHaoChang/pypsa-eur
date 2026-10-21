"""Explicit, provider-neutral request scope. Client scope is data, never proof."""

from typing import Literal
import re

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt


class CostBasis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    currency: Literal["EUR"]
    price_year: StrictInt = Field(ge=1900, le=2200)
    annual_fixed_cost_fraction: StrictInt | StrictFloat = Field(
        ge=0, le=1, allow_inf_nan=False
    )


class RequestScope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1]
    profile: Literal[
        "generator-dispatch/1", "generator-cost/1", "network-linear-cost/1",
        "storage-unit-dispatch/1", "storage-unit-cost/1", "carrier-energy/1",
        "branch-loading/1", "network-loading/1", "lost-load/1", "bus-price/1",
        "generator-emissions/1", "network-emissions/1", "curtailment/1", "capacity/1",
        "storage-cycles/1", "energy-balance/1", "site-import/1", "investment-case/1", "investment-lcoe/1",
        "copt-screening/1", "grid-readiness/1",
    ]
    project_id: str = Field(min_length=1, max_length=128)
    entity_id: str = Field(min_length=1, max_length=128)
    metrics: list[str] | None = Field(default=None, min_length=1, max_length=20)
    start: str | None = Field(default=None, min_length=1, max_length=64)
    end: str | None = Field(default=None, min_length=1, max_length=64)
    cost_basis: CostBasis | None = None
    retain: bool = False
    evidence_id: str | None = Field(default=None, min_length=36, max_length=36)
    comparison_evidence_id: str | None = Field(
        default=None, min_length=36, max_length=36
    )
    component_class: Literal[
        "Line", "Transformer", "Generator", "Link", "StorageUnit", "Store"
    ] | None = None
    # A checked chart of these evidence series (S10), drawn from this request's evidence only.
    chart: list[str] | None = Field(default=None, min_length=1, max_length=8)


def validate(value):
    if not isinstance(value, dict) or type(value.get("version")) is not int:
        raise ValueError("invalid_scope_version")
    scope = RequestScope.model_validate(value).model_dump(exclude_none=True)
    if ("start" in scope) != ("end" in scope):
        raise ValueError("both_window_bounds_required")
    if "chart" in scope and (len(set(scope["chart"])) != len(scope["chart"])
                             or any(not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", s) for s in scope["chart"])):
        raise ValueError("invalid_chart_series")
    if "metrics" in scope:
        if len(set(scope["metrics"])) != len(scope["metrics"]) or any(
            not 1 <= len(m) <= 64 for m in scope["metrics"]
        ):
            raise ValueError("invalid_required_metrics")
    if scope["profile"] == "network-linear-cost/1" and scope["entity_id"] != "network":
        raise ValueError("network_scope_required")
    if scope["profile"] == "storage-unit-dispatch/1" and (
        scope.get("cost_basis") or scope.get("comparison_evidence_id")
    ):
        raise ValueError("unsupported_storage_cost_scope")
    if scope["profile"] == "carrier-energy/1" and (
        scope.get("cost_basis") or scope.get("comparison_evidence_id")
    ):
        raise ValueError("unsupported_carrier_cost_scope")
    if scope["profile"] in ("branch-loading/1", "network-loading/1") and (
        scope.get("cost_basis") or scope.get("comparison_evidence_id")
    ):
        raise ValueError("unsupported_loading_cost_scope")
    if scope["profile"] in ("bus-price/1", "generator-emissions/1", "network-emissions/1",
                            "curtailment/1") and (
        scope.get("cost_basis") or scope.get("comparison_evidence_id")
    ):
        raise ValueError("unsupported_result_cost_scope")
    if scope["profile"] in ("generator-emissions/1", "network-emissions/1") and (
        (scope["profile"] == "network-emissions/1") != (scope["entity_id"] == "network")
    ):
        raise ValueError("network_scope_required")
    if scope["profile"] == "lost-load/1" and (
        scope.get("cost_basis") or scope.get("comparison_evidence_id")
    ):
        raise ValueError("unsupported_lost_load_cost_scope")
    classed = {"branch-loading/1": ("Line", "Transformer"),
               "capacity/1": ("Generator", "Link", "StorageUnit", "Store", "Line", "Transformer"),
               "storage-cycles/1": ("StorageUnit", "Store")}
    if scope.get("component_class") and scope["component_class"] not in classed.get(scope["profile"], ()):
        raise ValueError("unsupported_component_class")
    if scope["profile"] in ("energy-balance/1", "site-import/1", "investment-case/1", "investment-lcoe/1",
                            "copt-screening/1", "grid-readiness/1") and (
        scope["entity_id"] != "network" or scope.get("cost_basis") or scope.get("comparison_evidence_id")
    ):
        raise ValueError("network_scope_required")
    if scope["profile"] in ("capacity/1", "storage-cycles/1"):
        if not scope.get("component_class"):
            raise ValueError("component_class_required")
        if scope.get("cost_basis") or scope.get("comparison_evidence_id"):
            raise ValueError("unsupported_result_cost_scope")
    if (scope["profile"] == "network-loading/1") != (scope["entity_id"] == "network") and (
        scope["profile"] in ("branch-loading/1", "network-loading/1")
    ) and not scope.get("component_class"):
        raise ValueError("network_scope_required")
    return scope


def from_message(message, project_id, ui_context):
    """Whole-message profile grammar, with an optional trailing "; chart <series>, ..."
    (S10) that asks for a checked chart of those evidence series."""
    match = re.fullmatch(r"(.*?);\s*chart\s+([a-z0-9_]+(?:\s*,\s*[a-z0-9_]+)*)\s*[.!?]?", message.strip(), re.I | re.S)
    if not match:
        return _from_message(message, project_id, ui_context)
    scope = _from_message(match[1], project_id, ui_context)
    if scope is None:
        return None
    return validate({**scope, "chart": [s.strip().lower() for s in match[2].split(",")]})


def _from_message(message, project_id, ui_context):
    """Whole-message profile grammar: no extra quantity or instruction is ignored."""
    selected = ui_context.get("selected_component") if isinstance(ui_context, dict) else None
    if (
        isinstance(selected, dict) and selected.get("class") == "StorageUnit"
        and re.fullmatch(
            r"(?:please\s+)?(?:give me|show me|show)\s+(?:the\s+)?key results[.!?]?",
            message.strip(), re.I,
        )
    ):
        return validate({
            "version": 1, "profile": "storage-unit-dispatch/1",
            "project_id": project_id, "entity_id": selected.get("name"), "retain": False,
        })
    storage = re.fullmatch(
        r"(?:please\s+)?(?:give me|show me|show)\s+(?:the\s+)?key results\s+for\s+"
        r"storage\s*unit\s+([^;]{1,128}?)"
        r"(?:;\s*from\s+(\S+)\s+to\s+(\S+?))?[.!?]?",
        message.strip(), re.I,
    )
    if storage:
        value = {
            "version": 1, "profile": "storage-unit-dispatch/1",
            "project_id": project_id, "entity_id": storage[1], "retain": False,
        }
        if storage[2]:
            value.update(start=storage[2], end=storage[3])
        return validate(value)
    carrier = re.fullmatch(
        r"(?:please\s+)?(?:give me|show me|show)\s+(?:the\s+)?key results\s+for\s+"
        r"carrier\s+([^;]{1,128}?)"
        r"(?:;\s*from\s+(\S+)\s+to\s+(\S+?))?[.!?]?",
        message.strip(), re.I,
    )
    if carrier:
        value = {
            "version": 1, "profile": "carrier-energy/1",
            "project_id": project_id, "entity_id": carrier[1], "retain": False,
        }
        if carrier[2]:
            value.update(start=carrier[2], end=carrier[3])
        return validate(value)
    loading = re.fullmatch(
        r"(?:please\s+)?(?:give me|show me|show)\s+(?:the\s+)?(?:branch\s+)?loading\s+for\s+"
        r"(network|(line|transformer)\s+([^;]{1,128}?))"
        r"(?:;\s*from\s+(\S+)\s+to\s+(\S+?))?[.!?]?",
        message.strip(), re.I,
    )
    if loading:
        network = loading[3] is None
        value = {
            "version": 1, "profile": "network-loading/1" if network else "branch-loading/1",
            "project_id": project_id, "entity_id": "network" if network else loading[3],
            "retain": False,
        }
        if not network:
            value["component_class"] = loading[2].capitalize()
        if loading[4]:
            value.update(start=loading[4], end=loading[5])
        return validate(value)
    balance = re.fullmatch(
        r"(?:please\s+)?(?:give me|show me|show)\s+(?:the\s+)?energy (?:balance|flow totals)\s+for\s+network"
        r"(?:;\s*from\s+(\S+)\s+to\s+(\S+?))?[.!?]?",
        message.strip(), re.I,
    )
    site = re.fullmatch(
        r"(?:please\s+)?(?:give me|show me|show)\s+(?:the\s+)?(?:grid import|site import|import and export)\s+for\s+network"
        r"(?:;\s*from\s+(\S+)\s+to\s+(\S+?))?[.!?]?",
        message.strip(), re.I,
    )
    invest = re.fullmatch(
        r"(?:please\s+)?(?:give me|show me|show)\s+(?:the\s+)?(?:investment case returns|investment returns|npv and irr)"
        r"\s+for\s+network[.!?]?",
        message.strip(), re.I,
    )
    if invest:
        return validate({"version": 1, "profile": "investment-case/1", "project_id": project_id,
                         "entity_id": "network", "retain": False})
    if re.fullmatch(r"(?:please\s+)?(?:give me|show me|show)\s+(?:the\s+)?(?:investment case )?lcoe\s+for\s+network[.!?]?",
                    message.strip(), re.I):
        return validate({"version": 1, "profile": "investment-lcoe/1", "project_id": project_id,
                         "entity_id": "network", "retain": False})
    if re.fullmatch(r"(?:please\s+)?(?:give me|show me|show)\s+(?:the\s+)?grid[- ]readiness(?:\s+scorecard)?"
                    r"\s+for\s+network[.!?]?", message.strip(), re.I):
        return validate({"version": 1, "profile": "grid-readiness/1", "project_id": project_id,
                         "entity_id": "network", "retain": False})
    if re.fullmatch(r"(?:please\s+)?(?:give me|show me|show)\s+(?:the\s+)?(?:copt|screening adequacy|copt screening)"
                    r"\s+for\s+network[.!?]?", message.strip(), re.I):
        return validate({"version": 1, "profile": "copt-screening/1", "project_id": project_id,
                         "entity_id": "network", "retain": False})
    if site:
        value = {"version": 1, "profile": "site-import/1", "project_id": project_id,
                 "entity_id": "network", "retain": False}
        if site[1]:
            value.update(start=site[1], end=site[2])
        return validate(value)
    if balance:
        value = {"version": 1, "profile": "energy-balance/1", "project_id": project_id,
                 "entity_id": "network", "retain": False}
        if balance[1]:
            value.update(start=balance[1], end=balance[2])
        return validate(value)
    sized = re.fullmatch(
        r"(?:please\s+)?(?:give me|show me|show)\s+(?:the\s+)?(capacity|cycles)\s+for\s+"
        r"(generator|link|line|transformer|storage\s*unit|store)\s+([^;]{1,128}?)"
        r"(?:;\s*from\s+(\S+)\s+to\s+(\S+?))?[.!?]?",
        message.strip(), re.I,
    )
    if sized:
        classes = {"generator": "Generator", "link": "Link", "line": "Line", "transformer": "Transformer",
                   "storageunit": "StorageUnit", "store": "Store"}
        component_class = classes[re.sub(r"\s+", "", sized[2].lower())]
        profile = "capacity/1" if sized[1].lower() == "capacity" else "storage-cycles/1"
        if profile == "storage-cycles/1" and component_class not in ("StorageUnit", "Store"):
            return None
        value = {"version": 1, "profile": profile, "project_id": project_id, "entity_id": sized[3],
                 "component_class": component_class, "retain": False}
        if sized[4]:
            if profile == "capacity/1":
                return None  # a capacity has no window; never drop the request's words
            value.update(start=sized[4], end=sized[5])
        return validate(value)
    result = re.fullmatch(
        r"(?:please\s+)?(?:give me|show me|show)\s+(?:the\s+)?(price|emissions|curtailment)\s+for\s+"
        r"(network|(bus|generator)\s+([^;]{1,128}?))"
        r"(?:;\s*from\s+(\S+)\s+to\s+(\S+?))?[.!?]?",
        message.strip(), re.I,
    )
    if result:
        kind, network, word = result[1].lower(), result[3] is None, (result[3] or "").lower()
        profile = {
            ("price", "bus"): "bus-price/1",
            ("emissions", "generator"): "generator-emissions/1",
            ("emissions", ""): "network-emissions/1",
            ("curtailment", "generator"): "curtailment/1",
            ("curtailment", ""): "curtailment/1",
        }.get((kind, word))
        if profile is None:
            return None  # e.g. "price for network" or "emissions for bus X": no such profile
        value = {
            "version": 1, "profile": profile, "project_id": project_id,
            "entity_id": "network" if network else result[4], "retain": False,
        }
        if result[5]:
            value.update(start=result[5], end=result[6])
        return validate(value)
    lost = re.fullmatch(
        r"(?:please\s+)?(?:give me|show me|show)\s+(?:the\s+)?lost load\s+for\s+"
        r"(network|load\s+([^;]{1,128}?))"
        r"(?:;\s*from\s+(\S+)\s+to\s+(\S+?))?[.!?]?",
        message.strip(), re.I,
    )
    if lost:
        value = {
            "version": 1, "profile": "lost-load/1", "project_id": project_id,
            "entity_id": "network" if lost[2] is None else lost[2], "retain": False,
        }
        if lost[3]:
            value.update(start=lost[3], end=lost[4])
        return validate(value)
    storage_cost = re.fullmatch(
        r"(?:please\s+)?(?:give me|show me|show)\s+(?:the\s+)?"
        r"(total cost|key results)\s+for\s+storage\s*unit\s+(.{1,128}?)"
        r"(?:;\s*from\s+(\S+)\s+to\s+(\S+?))?"
        r"(?:;\s*price year\s+(\d{4});\s*annual fixed cost fraction\s+(0(?:\.\d+)?|1(?:\.0+)?))?[.!?]?",
        message.strip(), re.I,
    )
    if storage_cost:
        value = {
            "version": 1, "profile": "storage-unit-cost/1",
            "project_id": project_id, "entity_id": storage_cost[2], "retain": False,
        }
        if storage_cost[1].lower() == "total cost":
            value["metrics"] = ["total_cost"]
        if storage_cost[3]:
            value.update(start=storage_cost[3], end=storage_cost[4])
        if storage_cost[5]:
            value["cost_basis"] = {
                "currency": "EUR", "price_year": int(storage_cost[5]),
                "annual_fixed_cost_fraction": float(storage_cost[6]),
            }
        return validate(value)
    match = re.fullmatch(
        r"(?:please\s+)?(?:give me|show me|show)\s+(?:the\s+)?"
        r"(total cost|key results)\s+for\s+(generator\s+(.{1,128}?)|network)"
        r"(?:;\s*from\s+(\S+)\s+to\s+(\S+))?"
        r"(?:;\s*price year\s+(\d{4});\s*annual fixed cost fraction\s+(0(?:\.\d+)?|1(?:\.0+)?))?[.!?]?",
        message.strip(),
        re.I,
    )
    if not match or (
        match.group(1).lower() == "key results"
        and match.group(3) is not None
        and match.group(4) is None
        and match.group(6) is None
    ):
        return None  # Preserve the existing three-slot generator grammar.
    network = match.group(3) is None
    result = {
        "version": 1,
        "project_id": project_id,
        "profile": "network-linear-cost/1" if network else "generator-cost/1",
        "entity_id": "network" if network else match.group(3),
        "retain": False,
    }
    if match.group(1).lower() == "total cost":
        result["metrics"] = ["total_cost"]
    if match.group(4) is not None:
        result.update(start=match.group(4), end=match.group(5))
    if match.group(6) is not None:
        result["cost_basis"] = {
            "currency": "EUR",
            "price_year": int(match.group(6)),
            "annual_fixed_cost_fraction": float(match.group(7)),
        }
    return validate(result)
