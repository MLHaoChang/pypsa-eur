"""
Drift hashes survive schema growth (IC P2 WP2.0 review, condition 1).

A drift hash is compared with the SAME recipe that made it: every committed
record carries `hash_version`; records without one were made by recipe 1 (a
full `model_dump`). Recipe 2 hashes only fields that differ from their
defaults, so a new optional field never changes a hash. Recipe 1 comparisons
drop the fields added after it while they still hold their default, so a
P1-solved project does not flip to `config_changed_since_solve` when P2 adds a
field.
"""
from __future__ import annotations

from pydantic import BaseModel

from services.commercial import hashing as H


class _Before(BaseModel):
    a: int
    b: str | None = None


class _After(BaseModel):
    a: int
    b: str | None = None
    new_field: list[float] | None = None
    new_flag: bool = False


def test_recipe_2_ignores_a_new_field_at_its_default():
    assert H.digest(_Before(a=1), version=2) == H.digest(_After(a=1), version=2)
    assert H.digest(_After(a=1, new_flag=True), version=2) != H.digest(_After(a=1), version=2)


def test_recipe_1_is_a_full_dump_minus_later_fields_at_their_default(monkeypatch):
    monkeypatch.setattr(H, "FIELDS_AFTER_V1", {("_After", "new_field"): None,
                                               ("_After", "new_flag"): False})
    assert H.digest(_After(a=1), version=1) == H.digest(_Before(a=1), version=1)
    assert H.digest(_After(a=1, new_flag=True), version=1) != H.digest(_Before(a=1), version=1)
    assert H.digest(_After(a=1, b=None), version=1) != H.digest(_After(a=1, b="x"), version=1)


def test_the_p1_agreement_hash_is_pinned():
    """The P1 (recipe 1) hash of the reconciliation gate's FEE agreement — a
    stored P1 record compares equal after P2 grows the models."""
    from models.commercial import ConnectionAgreement
    from services.commercial.connection import agreement_hash

    fee = ConnectionAgreement.model_validate({
        "kind": "firm", "import_cap_mw": 70.0, "available_from": "2030-01-01",
        "capacity_fee": {"id": "fee", "kind": "capacity", "unit": "per_kw_year",
                         "periods": [{"name": "all", "rate": 60.0}]}})
    assert agreement_hash(fee, version=1) == "4194685a58fb1fa2"


def test_records_carry_the_current_version():
    from services.commercial import lp_bindings as L
    from tests.fixtures.investment_case.edge_15min import build_edge_15min

    n = build_edge_15min()
    tariff = {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2030-01-01",
              "items": [{"id": "e", "kind": "energy", "unit": "per_kwh",
                         "periods": [{"name": "all", "rate": 0.1}]},
                        {"id": "d", "kind": "demand", "unit": "per_kw_month",
                         "periods": [{"name": "all", "rate": 10.0}]}]}
    applied = L.materialise_poc_prices(n, {"poc_link": "import", "import_tariff": tariff})
    assert getattr(n, L.DEMAND_SPEC_ATTR)["info"]["hash_version"] == H.HASH_VERSION
    applied.undo()
    applied.commit()
    assert n.meta[L.META_LINKS]["hash_version"] == H.HASH_VERSION


# The fields recipe 1 knew (P1 → 04c3c17), per model reachable from
# CommercialConfig. WP2.0 review round 2 R1: a field added to any of these
# models must be registered in `hashing.FIELDS_AFTER_V1` as (model, field) with
# its default, or every recipe-1 record holding that model flips to drift.
V1_FIELDS: dict[str, set[str]] = {
    "CommercialConfig": {"connection", "demand_items", "export_link", "export_price_ref",
                         "group_cap_mw", "group_contract", "group_members", "import_tariff",
                         "import_tariff_id", "initial_peak_lower_bound",
                         "meter_history_peaks_kw", "poc_link", "timezone"},
    "ConnectionAgreement": {"available_from", "capacity_fee",
                            "curtailment_compensation_eur_per_mwh", "curtailment_hours_per_year",
                            "envelope", "export_cap_mw", "group", "import_cap_mw", "kind"},
    "PriceSeriesRef": {"hash", "id", "provider", "source", "version", "vintage_year"},
    "Ratchet": {"lookback_months", "share"},
    "Tariff": {"dso_or_retailer", "id", "items", "jurisdiction", "name", "pack_hash",
               "valid_from", "valid_to"},
    "TariffItem": {"direction", "id", "kind", "measured_on", "periods", "ratchet", "settlement",
                   "tiers", "unit"},
    "TariffPeriod": {"end_hour", "months", "name", "rate", "start_hour", "weekdays"},
    "Tier": {"rate", "threshold"},
}


def _reachable():
    import typing

    import pydantic

    from models.commercial import CommercialConfig

    seen: dict[str, type] = {}

    def walk(m):
        if m.__name__ in seen:
            return
        seen[m.__name__] = m
        for fname, f in m.model_fields.items():
            if (m.__name__, fname) in H.FIELDS_AFTER_V1:
                # A field added after recipe 1 is absent from every recipe-1
                # record, so nothing below it is ever hashed with recipe 1
                # (e.g. `LibraryItemRef` under `import_tariff_ref`, WP2.4a).
                continue
            for t in [f.annotation, *typing.get_args(f.annotation)]:
                for tt in [t, *typing.get_args(t)]:
                    if isinstance(tt, type) and issubclass(tt, pydantic.BaseModel):
                        walk(tt)

    walk(CommercialConfig)
    return seen


def test_every_field_added_after_recipe_1_is_registered_with_its_default():
    for name, model in _reachable().items():
        for field, info in model.model_fields.items():
            if field in V1_FIELDS.get(name, set()):
                continue
            key = (name, field)
            assert key in H.FIELDS_AFTER_V1, (
                f"{name}.{field} was added after recipe 1: register {key} with its default in "
                "services/commercial/hashing.FIELDS_AFTER_V1")
            assert H.FIELDS_AFTER_V1[key] == info.get_default(call_default_factory=True), key


def test_no_recipe_1_field_disappeared():
    models = _reachable()
    for name, fields in V1_FIELDS.items():
        assert name in models, name
        assert fields <= set(models[name].model_fields), name
