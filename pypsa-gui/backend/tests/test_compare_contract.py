"""Contract-level checks: the payload is populated, deterministic, classified."""
from __future__ import annotations

import pytest

from models import schemas
from tests import compare_support as cs

MODELS_WITH_PERIOD_VALUES = (
    "CapacityComparison", "DispatchComparison", "LineLoadingEntry",
    "PricesComparison", "CarrierPriceStats", "EmissionsComparison",
    "CarrierEconomics", "AssetLCOHEntry", "CurtailmentComparison",
    "LostLoadComparison", "LostLoadBus", "LostLoadByCarrier",
    "StorageCyclingComparison", "StorageUnitCycles",
)


def _period_value_fields(model_name: str) -> list[str]:
    """Fields whose type is CarrierPeriodValue or dict[str, CarrierPeriodValue]."""
    model = getattr(schemas, model_name)
    out = []
    for field_name, info in model.model_fields.items():
        ann = str(info.annotation)
        if "CarrierPeriodValue" in ann:
            out.append(field_name)
    return out


def test_every_period_bearing_field_is_classified():
    missing = []
    for model_name in MODELS_WITH_PERIOD_VALUES:
        for field in _period_value_fields(model_name):
            if f"{model_name}.{field}" not in cs.KIND:
                missing.append(f"{model_name}.{field}")
    assert not missing, (
        "These fields carry a per-period breakdown but no extensive/intensive/"
        "stock classification, so the additivity suite would skip them "
        "silently:\n  " + "\n  ".join(missing)
    )


def test_the_registry_names_no_field_that_has_been_removed():
    """The mirror image: a renamed field must not leave a dead entry behind."""
    stale = []
    for key in cs.KIND:
        model_name, field = key.split(".", 1)
        model = getattr(schemas, model_name, None)
        if model is None or field not in model.model_fields:
            stale.append(key)
    assert not stale, f"registry entries with no matching field: {stale}"
