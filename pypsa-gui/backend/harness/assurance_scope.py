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


def validate(value):
    if not isinstance(value, dict) or type(value.get("version")) is not int:
        raise ValueError("invalid_scope_version")
    scope = RequestScope.model_validate(value).model_dump(exclude_none=True)
    if ("start" in scope) != ("end" in scope):
        raise ValueError("both_window_bounds_required")
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
    return scope


def from_message(message, project_id, ui_context):
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
