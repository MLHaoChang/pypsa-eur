"""Business case slice 02: the line-item model, its validation and its pack defaults.

Spec .scratch/business-case/spec.md §3 (G1, G5–G9, G-C1, G-C2) and §4 (BC-ALL-1,
BCA-SCOPE-1); GK B plan GO for slice 02 (Q1–Q5, P1–P5), #1320 F1/F2, and BC9 (defaults
pack: previous rows byte-equal, defaults sourced and assumed).
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from models import business_case as B
from models.finance import ESCALATION_CLASSES
from services.library.defaults_pack import loader

PREV, NEW = "2026-11-07", "2026-11-10"
VERSIONS = Path(loader.__file__).parent / "versions"
H = (2027, 2046)  # the analysis horizon: COD .. analysis end
BASE = 2026  # the case's base year

#: The 2026-11-10 pack rows (no consumer until the DC preset, slice 07).
DC_KEYS = {
    "facility_capex": "dc-business-case.facility.overnight",
    "facility_life": "dc-business-case.facility.lifetime",
    "it_life": "dc-business-case.it_hardware.lifetime",
    "maintenance": "dc-business-case.facility.maintenance_share",
    "insurance": "dc-business-case.insurance_share",
    "property_tax": "dc-business-case.property_tax_share",
    "sga": "dc-business-case.sga_share",
}

#: Each DC key's pack unit; a test key ("k") reads "x" (GK B BC 07 Q1: a default names its unit).
KEY_UNITS = {
    DC_KEYS["facility_capex"]: "EUR/MW",
    DC_KEYS["facility_life"]: "years",
    DC_KEYS["it_life"]: "years",
    DC_KEYS["maintenance"]: "share/year",
    DC_KEYS["insurance"]: "share/year",
    DC_KEYS["property_tax"]: "share/year",
    DC_KEYS["sga"]: "per unit",
}


def _units(kw: dict) -> dict:
    """`kw` with `default_units` beside its `default_keys`, unless stated."""
    if "default_keys" in kw and "default_units" not in kw:
        keys = kw["default_keys"]
        kw = {
            **kw,
            "default_units": {c: KEY_UNITS.get(k, "x") for c, k in keys.items()},
        }
    return kw


def _rev(name: str = "compute", **kw: Any) -> dict:
    return {
        "name": name,
        "kind": "revenue",
        "category": "service_fee",
        "unit": "GPU_h",
        "quantity": 1.0e6,
        "price": 2.5,
        "escalation": "contract",
        "indexation": -0.05,
        **_units(kw),
    }


def _opex(name: str = "staff", **kw: Any) -> dict:
    return {
        "name": name,
        "kind": "opex",
        "category": "staff",
        "unit": "unit",
        "quantity": 10.0,
        "price": 40_000.0,
        "escalation": "opex",
        **_units(kw),
    }


def _capex(name: str = "facility", **kw: Any) -> dict:
    return {
        "name": name,
        "kind": "capex",
        "category": "facility",
        "unit": "lump",
        "quantity": 1.0,
        "price": 9.0e7,
        "escalation": "capex",
        "life_years": 20,
        **_units(kw),
    }


def _share(name: str = "maintenance", base: str = "capex/facility", **kw: Any) -> dict:
    return {
        "name": name,
        "kind": "opex",
        "category": "maintenance",
        "unit": "share",
        "quantity_source": f"share_of:{base}",
        "quantity": 0.015,
        "escalation": "opex",
        **_units(kw),
    }


def _case(*lines: dict, **kw: Any) -> B.BusinessCaseInputs:
    return B.BusinessCaseInputs(lines=list(lines), **kw)


def _refusals(case: B.BusinessCaseInputs) -> list[tuple[str, str]]:
    return [(r.kind, r.field) for r in case.refusals(horizon=H, base_year=BASE)]


def _full() -> B.BusinessCaseInputs:
    """A case that fills every column of every model at least once."""
    default = B.CostDefault(
        key=DC_KEYS["facility_life"],
        value=20.0,
        unit="years",
        source="assumed: test",
        source_year=2026,
        currency=None,
        money_year=None,
        pack="generic@2026-11-10#abc",
    )
    return _case(
        _rev(
            quantity_by_year={2027: 5.0e5},
            price_by_year={2027: 3.0},
            money_year=2025,
            start_year=2027,
            end_year=2046,
            note="term sheet",
            hint={
                "low": 2.0,
                "high": 6.0,
                "unit": "USD/GPU_h",
                "source": "research [16]",
                "year": 2026,
            },
            preset_rule={
                "requires": ["facility"],
                "excludes": ["it_hardware"],
                "price_includes_energy": True,
            },
        ),
        _capex(
            depreciation="declining_balance",
            rate=0.1,
            refresh_years=10,
            life_years=20.0,
            assumed={"life_years": default},
            default_keys={"life_years": DC_KEYS["facility_life"]},
        ),
        _share(),
        _rev(
            "h2",
            quantity_source="linked:h2.served@2026-11-10",
            quantity=None,
            unit="kg",
            price=4.0,
            linked_share=0.8,
            linked_share_by_year={2027: 0.5},
        ),
        preset="eh_datacenter@1",
        receivable_days=45,
    )


# ---------------------------------------------------------------- round trip, closed lists


def test_round_trip_every_column_json():
    """GK B P2: JSON turns the grids' int keys into strings; they come back as ints."""
    full = _full()
    js = full.model_dump_json()
    back = B.BusinessCaseInputs.model_validate_json(js)
    assert back == full
    assert back.lines[0].price_by_year == {2027: 3.0}
    assert back.model_dump_json() == js
    # Every LineItem column is set to a non-default somewhere in _full().
    set_cols = {k for ln in full.lines for k in ln.model_fields_set}
    assert set_cols == set(B.LineItem.model_fields)
    assert set(B.PresetRule.model_fields) <= set(
        full.lines[0].preset_rule.model_fields_set
    )


@pytest.mark.parametrize("key", ["2030.5", "x", "", "1e3"])
def test_a_non_integer_grid_year_is_refused(key):
    js = json.dumps({"lines": [_rev(price_by_year={key: 1.0})]})
    with pytest.raises(ValidationError) as e:
        B.BusinessCaseInputs.model_validate_json(js)
    assert e.value.errors()[0]["loc"][:3] == ("lines", 0, "price_by_year")


@pytest.mark.parametrize(
    "model, payload",
    [
        (B.BusinessCaseInputs, {"revenue_model": "gpu_hour"}),
        (B.LineItem, {**_rev(), "tax": "exempt"}),  # G-C2: no tax column
        (B.PresetRule, {"requires": [], "owns": []}),
        (
            B.CostDefault,
            {
                "key": "k",
                "value": 1,
                "unit": "u",
                "source": "s",
                "source_year": 2026,
                "pack": "p",
                "extra": 1,
            },
        ),
    ],
)
def test_extra_columns_are_refused(model, payload):
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        model.model_validate(payload)


def test_closed_lists_match_the_spec():
    assert get_args(B.Escalation) == (*ESCALATION_CLASSES, "contract")
    assert get_args(B.Unit) == (
        "MWh",
        "MWh_H2",
        "kg",
        "t",
        "GPU_h",
        "kW_month",
        "kW_year",
        "MW",
        "MW_year",
        "unit",
        "unit_month",
        "lump",
        "share",
    )
    assert get_args(B.Depreciation) == ("straight_line", "declining_balance")
    assert "land" not in B.CATEGORIES["capex"]
    for bad in (
        {"kind": "asset"},
        {"unit": "kW"},  # MW is a unit since BC 07; kW is not
        {"escalation": "cpi"},
        {"category": "compute_revenue"},
    ):
        with pytest.raises(ValidationError):
            B.LineItem.model_validate({**_rev(), **bad})


def test_category_stream_is_spec_table_and_complete():
    """GK B P5: pinned literally to spec §3's table, and complete both ways."""
    assert B.CATEGORY_STREAM == {
        ("revenue", "product_sale"): ("product_revenue", "contract"),
        ("revenue", "by_product"): ("byproduct_revenue", "contract"),
        ("revenue", "service_fee"): ("service_revenue", "contract"),
        ("revenue", "lease"): ("lease_revenue", "contract"),
        ("revenue", "capacity_payment"): ("capacity_revenue", "contract"),
        ("revenue", "tariff"): ("tariff_revenue", "contract"),
        ("revenue", "cost_recovery"): ("power_passthrough", "contract"),
        ("revenue", "incentive"): ("premium_revenue", "contract"),
        ("revenue", "other"): ("other_revenue", "contract"),
        ("opex", "staff"): ("bc_staff", "opex"),
        ("opex", "maintenance"): ("bc_maintenance", "opex"),
        ("opex", "insurance"): ("bc_insurance", "opex"),
        ("opex", "property_tax"): ("bc_property_tax", "opex"),
        ("opex", "feedstock"): ("bc_feedstock", "opex"),
        ("opex", "consumables"): ("bc_consumables", "opex"),
        ("opex", "network"): ("bc_network", "opex"),
        ("opex", "land"): ("bc_land", "opex"),
        ("opex", "sga"): ("bc_sga", "opex"),
        ("opex", "other"): ("bc_other", "opex"),
        ("capex", "facility"): (None, "capex"),
        ("capex", "equipment"): (None, "capex"),
        ("capex", "it_hardware"): (None, "capex"),
        ("capex", "other"): (None, "capex"),
    }
    pairs = {(k, c) for k in get_args(B.Kind) for c in B.CATEGORIES[k]}
    assert set(B.CATEGORY_STREAM) == pairs
    assert set(get_args(B.Category)) == {c for _, c in pairs}


def test_capex_depreciation_defaults_to_straight_line():
    assert B.LineItem(**_capex()).depreciation == "straight_line"
    assert B.LineItem(**_opex()).depreciation is None


def test_provenance_reads_assumed():
    assert B.LineItem(**_opex()).provenance == "stated"
    assert _full().lines[1].provenance == "assumed"


# ---------------------------------------------------------------- F1: Finite on every float

#: (payload builder, column) for every float column; grid cells as `<grid>[]`.
FLOAT_COLUMNS = [
    (_rev, "quantity"),
    (_rev, "quantity_by_year[]"),
    (_rev, "price"),
    (_rev, "price_by_year[]"),
    (_rev, "indexation"),
    (_capex, "life_years"),
    (lambda: _capex(depreciation="declining_balance"), "rate"),
    (_capex, "refresh_years"),
    (lambda: _rev(quantity_source="linked:x@1", quantity=None), "linked_share"),
    (
        lambda: _rev(quantity_source="linked:x@1", quantity=None),
        "linked_share_by_year[]",
    ),
]


def _float_fields(model) -> set[str]:
    return {
        n
        for n, f in model.model_fields.items()
        if "float" in str(f.annotation) or "Finite" in str(f.annotation)
    }


def test_float_column_list_is_complete():
    assert {c.removesuffix("[]") for _, c in FLOAT_COLUMNS} == _float_fields(B.LineItem)
    assert _float_fields(B.BusinessCaseInputs) == {"receivable_days"}
    assert _float_fields(B.CostDefault) == {"value", "range_low", "range_high"}
    assert _float_fields(B.Hint) == {"low", "high"}


@pytest.mark.parametrize("bad", [math.inf, -math.inf, math.nan])
@pytest.mark.parametrize("build, column", FLOAT_COLUMNS)
def test_every_float_column_refuses_non_finite(build, column, bad):
    payload = build()
    if column.endswith("[]"):
        payload[column[:-2]] = {2030: bad}
    else:
        payload[column] = bad
    with pytest.raises(ValidationError) as e:
        B.LineItem.model_validate(payload)
    assert e.value.errors()[0]["loc"][0] == column.removesuffix("[]")


@pytest.mark.parametrize("bad", ["Infinity", "NaN", "-Infinity"])
def test_a_json_infinity_is_refused_in_a_grid_cell(bad):
    js = (
        '{"lines": ['
        + json.dumps(_rev())[:-1]
        + ', "price_by_year": {"2030": '
        + bad
        + "}}]}"
    )
    with pytest.raises(ValidationError):
        B.BusinessCaseInputs.model_validate_json(js)


@pytest.mark.parametrize("bad", [math.inf, math.nan])
def test_receivable_days_and_a_cost_default_refuse_non_finite(bad):
    with pytest.raises(ValidationError):
        B.BusinessCaseInputs(receivable_days=bad)
    with pytest.raises(ValidationError):
        B.CostDefault(
            key="k", value=bad, unit="u", source="s", source_year=2026, pack="p"
        )


# ---------------------------------------------------------------- shape faults (GK B Q3)


@pytest.mark.parametrize(
    "payload, loc",
    [
        (_rev(life_years=10), "life_years"),
        (_opex(depreciation="straight_line"), "depreciation"),
        (_opex(refresh_years=5), "refresh_years"),
        (_capex(rate=0.2), "rate"),  # straight_line takes no rate
        (_opex(indexation=0.02), "indexation"),
        (_rev(quantity_source="linked:h2.served@1"), "quantity"),
        (
            _rev(
                quantity_source="linked:h2.served@1",
                quantity=None,
                quantity_by_year={2030: 1.0},
            ),
            "quantity_by_year",
        ),
        (_share(price=1.0), "price"),
        (_share(price_by_year={2030: 1.0}), "price_by_year"),
        (_opex(preset_rule={}), "preset_rule"),
        (_rev(default_keys={"price": "k"}), "default_keys"),
        (_opex(default_keys={"life_years": "k"}), "default_keys"),
        (_capex(default_keys={"rate": "k"}), "default_keys"),
        (_share(default_keys={"price": "k"}), "default_keys"),
        (
            _opex(
                quantity_source="linked:x@1",
                quantity=None,
                default_keys={"quantity": "k"},
            ),
            "default_keys",
        ),
        (_capex(rate=0.0, depreciation="declining_balance"), "rate"),
        (_capex(rate=1.01, depreciation="declining_balance"), "rate"),
        (_capex(life_years=0.5), "life_years"),
        (_capex(refresh_years=0.5), "refresh_years"),
        (_rev(name=""), "name"),
        (_rev(name="x" * 81), "name"),
        (_rev(quantity_source="linked:h2"), "quantity_source"),  # no catalogue version
        (_rev(quantity_source="share_of:assets"), "quantity_source"),
        (_rev(quantity_source="typed "), "quantity_source"),
    ],
)
def test_each_shape_fault_names_its_column(payload, loc):
    with pytest.raises(ValidationError) as e:
        B.BusinessCaseInputs(lines=[payload])
    assert e.value.errors()[0]["loc"][:3] == ("lines", 0, loc)


def test_boundary_shapes_are_accepted():
    B.LineItem(**_opex(default_keys={"price": "k", "quantity": "k"}))
    B.LineItem(
        **_capex(
            depreciation="declining_balance",
            rate=0.1,
            default_keys={"rate": "k", "life_years": "k"},
        )
    )
    B.LineItem(**_share(default_keys={"quantity": "k"}))
    B.LineItem(
        **_opex(
            quantity_source="linked:x@1", quantity=None, default_keys={"price": "k"}
        )
    )
    B.LineItem(
        **_capex(
            depreciation="declining_balance", rate=1.0, life_years=1, refresh_years=1
        )
    )
    B.LineItem(**_rev(name="x" * 80))
    B.LineItem(**_rev(quantity_source="linked:a@b", quantity=None))
    B.LineItem(**_share(quantity_source="share_of:revenue"))


def test_assumed_must_hold_its_column():
    d = B.CostDefault(
        key="k", value=20.0, unit="years", source="s", source_year=2026, pack="p"
    )
    with pytest.raises(ValidationError, match=r"assumed\[life_years\] holds 20.0"):
        B.LineItem(**_capex(life_years=25, assumed={"life_years": d}))


def test_a_preset_cannot_require_and_exclude_one_category():
    with pytest.raises(ValidationError, match="both required and excluded"):
        B.PresetRule(requires=["facility"], excludes=["facility"])


@pytest.mark.parametrize("bad", ["eh_datacenter", "@1", "a@b@c", "a b@1"])
def test_preset_is_id_at_version(bad):
    with pytest.raises(ValidationError):
        B.BusinessCaseInputs(preset=bad)
    assert B.BusinessCaseInputs(preset="eh_datacenter@2026-11-10").preset


# ---------------------------------------------------------------- refusals (G9)


def test_a_complete_case_has_no_refusal():
    case = _case(_rev(), _opex(), _capex(), _share())
    assert _refusals(case) == []
    case.require_runnable(horizon=H, base_year=BASE)


@pytest.mark.parametrize(
    "lines, kind, field",
    [
        ([_rev(), _rev()], "name_duplicate", "lines[1].name"),
        ([_rev(category="staff")], "category_not_in_kind", "lines[0].category"),
        ([_capex(category="land")], "category_not_in_kind", "lines[0].category"),
        ([_rev(unit="share")], "unit_not_allowed", "lines[0].unit"),
        ([_rev(unit="lump")], "unit_not_allowed", "lines[0].unit"),
        ([_opex(unit="lump")], "unit_not_allowed", "lines[0].unit"),
        (
            [_rev(quantity_source="linked:x@1", quantity=None, unit="share")],
            "unit_not_allowed",
            "lines[0].unit",
        ),
        ([_capex(), _share(unit="unit")], "unit_not_allowed", "lines[1].unit"),
        ([_rev(quantity=None)], "quantity_missing", "lines[0].quantity"),
        ([_capex(), _share(quantity=None)], "quantity_missing", "lines[1].quantity"),
        (
            [_capex(), _share(quantity=1.0001)],
            "share_out_of_range",
            "lines[1].quantity",
        ),
        (
            [_capex(), _share(quantity=-0.0001)],
            "share_out_of_range",
            "lines[1].quantity",
        ),
        (
            [_capex(), _share(quantity_by_year={2030: 1.5})],
            "share_out_of_range",
            "lines[1].quantity_by_year[2030]",
        ),
        ([_share(base="opex")], "share_cycle", "lines[0].quantity_source"),
        (
            # b (sga) reads a (maintenance), a share: not itself, still a cycle
            [
                _capex(),
                _share("a"),
                _share("b", base="opex/maintenance", category="sga"),
            ],
            "share_cycle",
            "lines[2].quantity_source",
        ),
        ([_share(base="opex/staff")], "share_base_empty", "lines[0].quantity_source"),
        (
            [_rev(), _share(base="capex")],
            "share_base_empty",
            "lines[1].quantity_source",
        ),
        (
            [_share(base="capex/staff")],
            "category_not_in_kind",
            "lines[0].quantity_source",
        ),
        (
            [_rev(start_year=2030, end_year=2029)],
            "end_before_start",
            "lines[0].end_year",
        ),
        ([_rev(start_year=2026)], "outside_horizon", "lines[0].start_year"),
        ([_rev(end_year=2047)], "outside_horizon", "lines[0].end_year"),
        (
            [_rev(price_by_year={2026: 1.0})],
            "year_out_of_range",
            "lines[0].price_by_year[2026]",
        ),
        (
            [_rev(end_year=2030, quantity_by_year={2031: 1.0})],
            "year_out_of_range",
            "lines[0].quantity_by_year[2031]",
        ),
        ([_rev(money_year=BASE + 6)], "year_out_of_range", "lines[0].money_year"),
        ([_rev(money_year=1989)], "year_out_of_range", "lines[0].money_year"),
        ([_rev(indexation=None)], "indexation_missing", "lines[0].indexation"),
        ([_capex(life_years=None)], "life_missing", "lines[0].life_years"),
        ([_capex(depreciation="declining_balance")], "rate_missing", "lines[0].rate"),
        ([_rev(price=None)], "price_not_established", "lines[0].price"),
        (
            [_rev(price=None, quantity_source="linked:x@1", quantity=None)],
            "price_not_established",
            "lines[0].price",
        ),
        ([_opex(price=None)], "cost_not_established", "lines[0].price"),
        ([_capex(price=None)], "cost_not_established", "lines[0].price"),
        # G7
        (
            [_rev(preset_rule={"requires": ["it_hardware"]})],
            "it_capex_missing",
            "lines[0].preset_rule.requires[it_hardware]",
        ),
        (
            [_rev(preset_rule={"requires": ["facility"]})],
            "facility_capex_missing",
            "lines[0].preset_rule.requires[facility]",
        ),
        (
            [_rev(preset_rule={"requires": ["equipment"]})],
            "capex_missing",
            "lines[0].preset_rule.requires[equipment]",
        ),
        # #1320 F2: an excluded capex, the category in the field path
        (
            [
                _rev(preset_rule={"excludes": ["it_hardware"]}),
                _capex("gpus", category="it_hardware"),
            ],
            "capex_not_owned",
            "lines[1].category",
        ),
        (
            [
                _rev(preset_rule={"price_includes_energy": True}),
                _rev("power", category="cost_recovery", unit="MWh"),
            ],
            "pass_through_price_includes_energy",
            "lines[1].category",
        ),
    ],
)
def test_each_refusal_names_its_kind_and_field(lines, kind, field):
    case = _case(*lines)
    assert (kind, field) in _refusals(case)
    found = {r.kind for r in case.refusals(horizon=H, base_year=BASE)}
    assert found <= set(B.REFUSAL_KINDS)
    with pytest.raises(B.BusinessCaseRefused) as e:
        case.require_runnable(horizon=H, base_year=BASE)
    assert e.value.code.startswith("business_case_refused:")


@pytest.mark.parametrize(
    "lines",
    [
        [_capex(), _share(quantity=0.0)],
        [_capex(), _share(quantity=1.0)],
        [
            _capex(),
            _share(
                quantity=None, quantity_by_year={y: 0.5 for y in range(H[0], H[1] + 1)}
            ),
        ],
        [_rev(start_year=2030, end_year=2030)],
        [_rev(start_year=H[0], end_year=H[1])],
        [_rev(start_year=H[1])],
        [_rev(end_year=H[0])],
        [_capex(), _share(quantity_by_year={2030: 0.0, 2031: 1.0})],
        [_rev(quantity_by_year={2030: 5.0})],
        [_rev(money_year=BASE + 5)],
        [_rev(money_year=1990)],
        [_rev(price_by_year={H[0]: 1.0, H[1]: 2.0})],
        [_rev(price=None, end_year=2028, price_by_year={2027: 1.0, 2028: 1.1})],
        [_opex(price=None, price_by_year={y: 1.0 for y in range(H[0], H[1] + 1)})],
        [_rev(quantity=None, quantity_by_year={y: 1.0 for y in range(H[0], H[1] + 1)})],
        [_capex(depreciation="declining_balance", rate=0.2)],
        [_rev(escalation="tariff", indexation=None)],
        [
            _rev(preset_rule={"requires": ["it_hardware", "facility", "equipment"]}),
            _capex(),
            _capex("gpus", category="it_hardware"),
            _capex("racks", category="equipment"),
        ],
        [_rev(preset_rule={"excludes": ["it_hardware"]}), _capex()],
        [
            _rev(preset_rule={"price_includes_energy": False}),
            _rev("power", category="cost_recovery", unit="MWh"),
        ],
        [_rev(), _share("sga", base="revenue", category="sga")],
        [_capex(), _capex("lump2", unit="lump")],
    ],
)
def test_the_other_side_of_each_boundary_runs(lines):
    assert _refusals(_case(*lines)) == []


@pytest.mark.parametrize(
    "lines, expected",
    [
        # A linked line takes its quantity from 04, never quantity_missing.
        ([_rev(quantity_source="linked:x@1", quantity=None)], []),
        # The rule's owner and any non-pass-through revenue line run.
        (
            [
                _rev(preset_rule={"price_includes_energy": True}),
                _rev("lease", category="lease"),
            ],
            [],
        ),
        # An excluded capex category names only capex lines.
        (
            [
                _rev(preset_rule={"excludes": ["other"]}),
                _rev("misc", category="other"),
                _opex("misc2", category="other"),
            ],
            [],
        ),
        # `cost_recovery` on an opex line is a category fault, not a pass-through.
        (
            [
                _rev(preset_rule={"price_includes_energy": True}),
                _opex(category="cost_recovery"),
            ],
            [("category_not_in_kind", "lines[1].category")],
        ),
        # An empty span is end_before_start and still not established: never a covered grid.
        (
            [_rev(start_year=2030, end_year=2029, price=None)],
            [
                ("end_before_start", "lines[0].end_year"),
                ("price_not_established", "lines[0].price"),
            ],
        ),
    ],
)
def test_refusal_scope(lines, expected):
    assert _refusals(_case(*lines)) == expected


def test_share_base_with_one_line_runs_and_empty_refuses():
    """GK B Q5: an SG&A share with no revenue line is named, never a 0."""
    sga = _share("sga", base="revenue", category="sga")
    assert _refusals(_case(sga)) == [("share_base_empty", "lines[0].quantity_source")]
    assert _refusals(_case(_rev(), sga)) == []


def test_a_partial_price_grid_is_not_established():
    case = _case(_rev(price=None, price_by_year={2027: 1.0}))
    assert _refusals(case) == [("price_not_established", "lines[0].price")]


def test_order_is_line_then_column_and_not_established_last():
    """GK B P4."""
    case = _case(
        _rev("a", price=None, money_year=1900, unit="lump"),
        _rev("a", indexation=None),
    )
    got = _refusals(case)
    assert got == [
        ("unit_not_allowed", "lines[0].unit"),
        ("year_out_of_range", "lines[0].money_year"),
        ("price_not_established", "lines[0].price"),
        ("name_duplicate", "lines[1].name"),
        ("indexation_missing", "lines[1].indexation"),
    ]
    with pytest.raises(B.UnitNotAllowed) as e:
        case.require_runnable(horizon=H, base_year=BASE)
    assert (e.value.kind, e.value.field) == got[0]
    assert _refusals(case) == got  # deterministic


def test_kinds_are_distinct_subclasses_and_the_documented_list():
    """GK B P3: a kind cannot be added silently."""
    subs = B.BusinessCaseRefused.__subclasses__()
    kinds = [c.kind for c in subs]
    assert len(kinds) == len(set(kinds))
    assert set(kinds) == set(B.REFUSAL_KINDS)
    assert len(B.REFUSAL_KINDS) == len(set(B.REFUSAL_KINDS))
    err = B.CapexNotOwned("lines[3].category")
    assert err.code == "business_case_refused:capex_not_owned"
    assert err.field == "lines[3].category" and isinstance(err, ValueError)


def test_no_retired_dc_kind_survives():
    """GK B ruling 2026-11-10: one kind per refusal, no aliases."""
    src = Path(B.__file__).read_text()
    for retired in (
        "it_capex_not_owned",
        "pass_through_not_colocation",
        "revenue_model_missing",
    ):
        assert retired not in src


# ---------------------------------------------------------------- per-year override


def test_a_grid_cell_is_that_years_value_unescalated_in_money_year():
    """GK B 2026-11-10: the cell is that year's value in the line's money_year money, the
    row's escalation does not apply to that year, and every cell is Finite."""
    ln = B.LineItem(
        **_rev(
            price=2.5,
            money_year=2025,
            price_by_year={2030: 3.0},
            quantity_by_year={2031: 7.0},
        )
    )
    assert ln.cell("price", 2030) == B.Cell(3.0, 2025, False)
    assert ln.cell("price", 2031) == B.Cell(2.5, 2025, True)
    assert ln.cell("quantity", 2031) == B.Cell(7.0, 2025, False)
    assert ln.cell("quantity", 2030) == B.Cell(1.0e6, 2025, True)
    assert B.LineItem(**_rev(price=None)).cell("price", 2030) == B.Cell(
        None, None, True
    )
    with pytest.raises(ValidationError):
        B.LineItem(**_rev(price_by_year={2030: math.inf}))


# ---------------------------------------------------------------- defaults (BC9, G-C1, P1)


def _dc_lines() -> list[dict]:
    """The DC defaults as cost lines with their pack keys (07 will ship these rows)."""
    return [
        _rev(preset_rule={"requires": ["facility"]}),
        _capex(
            price=None,
            life_years=None,
            default_keys={
                "price": DC_KEYS["facility_capex"],
                "life_years": DC_KEYS["facility_life"],
            },
        ),
        _capex(
            "gpus",
            category="it_hardware",
            unit="unit",
            quantity=1000,
            price=30_000.0,
            life_years=None,
            default_keys={"life_years": DC_KEYS["it_life"]},
        ),
        _share(
            "maint", quantity=None, default_keys={"quantity": DC_KEYS["maintenance"]}
        ),
        _share(
            "ins",
            base="capex",
            category="insurance",
            quantity=None,
            default_keys={"quantity": DC_KEYS["insurance"]},
        ),
        _share(
            "ptax",
            base="capex",
            category="property_tax",
            quantity=None,
            default_keys={"quantity": DC_KEYS["property_tax"]},
        ),
        _share(
            "sga",
            base="revenue",
            category="sga",
            quantity=None,
            default_keys={"quantity": DC_KEYS["sga"]},
        ),
    ]


def test_defaults_carry_source_year_and_assumed():
    pack = loader.load_defaults_pack(NEW)
    filled = _case(*_dc_lines()).with_cost_defaults(pack, currency="EUR")
    got = {(ln.name, col): d for ln in filled.lines for col, d in ln.assumed.items()}
    assert {d.key for d in got.values()} == set(DC_KEYS.values())
    for (name, col), d in got.items():
        row = pack.value(d.key)
        ln = next(x for x in filled.lines if x.name == name)
        assert d.status == "assumed" and row.illustrative is True
        assert d.source == row.source and d.source.startswith("assumed")
        assert d.source_year == 2026 and d.pack == pack.stamp
        assert row.range is not None and row.range.low <= d.value <= row.range.high
        assert getattr(ln, col) == d.value and ln.provenance == "assumed"
    fac = filled.lines[1]
    assert got[("facility", "price")].currency == "EUR"
    assert fac.life_years == 20 and filled.lines[2].life_years == 5
    life = pack.value(DC_KEYS["it_life"]).range
    assert (life.low, life.high) == (4, 6)
    # Percent rows arrive as fractions (the loader's %/year -> share/year, % -> per unit).
    assert [ln.quantity for ln in filled.lines[3:]] == pytest.approx(
        [0.015, 0.005, 0.01, 0.15]
    )
    assert _refusals(filled) == []


def test_a_preset_defaults_money_year_survives_into_the_line():
    """G-C1: the facility default is 2025 money, not the base year."""
    filled = _case(*_dc_lines()).with_cost_defaults(
        loader.load_defaults_pack(NEW), currency="EUR"
    )
    fac = filled.lines[1]
    assert fac.money_year == 2025 != BASE
    assert fac.assumed["price"].money_year == 2025
    assert fac.cell("price", 2030).money_year == 2025
    back = B.BusinessCaseInputs.model_validate_json(filled.model_dump_json())
    assert back.lines[1].money_year == 2025
    # A life default carries no money year onto a line that has none.
    assert filled.lines[2].money_year is None


def test_defaults_never_fill_a_revenue_line_or_overwrite_a_stated_value():
    lines = _dc_lines()
    lines[0]["price"] = None
    lines[1] |= {"price": 1.0, "life_years": 30}
    lines[6]["quantity"] = 0.0  # a stated 0 is the user's, kept
    case = _case(*lines)
    filled = case.with_cost_defaults(loader.load_defaults_pack(NEW), currency="EUR")
    assert filled.lines[0].price is None and filled.lines[0].assumed == {}
    assert filled.lines[1].price == 1.0 and filled.lines[1].life_years == 30
    assert filled.lines[1].assumed == {} and filled.lines[1].money_year is None
    assert filled.lines[6].quantity == 0.0 and filled.lines[6].assumed == {}
    assert case.lines[1].assumed == {}  # input untouched
    assert ("price_not_established", "lines[0].price") in _refusals(filled)


# P1: each skip path ends in cost_not_established, never a 0.


def _facility_after(pack, *, currency="EUR", **kw) -> list[tuple[str, str]]:
    case = _case(
        _rev(),
        _capex(price=None, default_keys={"price": DC_KEYS["facility_capex"]}, **kw),
    )
    filled = case.with_cost_defaults(pack, currency=currency)
    assert filled.lines[1].price is None and "price" not in filled.lines[1].assumed
    return _refusals(filled)


NOT_ESTABLISHED = [("cost_not_established", "lines[1].price")]


def test_no_default_key_is_cost_not_established():
    case = _case(_rev(), _capex(price=None))
    filled = case.with_cost_defaults(loader.load_defaults_pack(NEW), currency="EUR")
    assert _refusals(filled) == NOT_ESTABLISHED


def test_an_older_pack_is_cost_not_established():
    assert _facility_after(loader.load_defaults_pack(PREV)) == NOT_ESTABLISHED


def test_another_currency_is_skipped_not_converted():
    assert (
        _facility_after(loader.load_defaults_pack(NEW), currency="USD")
        == NOT_ESTABLISHED
    )


def test_a_stated_different_money_year_is_skipped_not_converted():
    """GK B Q4."""
    assert (
        _facility_after(loader.load_defaults_pack(NEW), money_year=BASE)
        == NOT_ESTABLISHED
    )
    case = _case(
        _capex(
            price=None,
            money_year=2025,
            default_keys={"price": DC_KEYS["facility_capex"]},
        )
    )
    filled = case.with_cost_defaults(loader.load_defaults_pack(NEW), currency="EUR")
    assert filled.lines[0].price == pytest.approx(10.7e6 / 1.13, rel=1e-4)


def test_typed_price_cells_are_never_relabelled_by_a_default():
    """GK B F1 on #1320 @15efa912: cells typed in base-year money keep the base year; the
    default is skipped and the run refuses the gap."""
    pack = loader.load_defaults_pack(NEW)
    line = _capex(
        price=None,
        price_by_year={2027: 50_000.0},
        default_keys={"price": DC_KEYS["facility_capex"]},
    )
    filled = _case(_rev(), line).with_cost_defaults(pack, currency="EUR")
    ln = filled.lines[1]
    assert ln.price is None and "price" not in ln.assumed and ln.money_year is None
    assert ln.cell("price", 2027) == B.Cell(50_000.0, None, False)
    assert _refusals(filled) == NOT_ESTABLISHED
    # A stated money year equal to the row's still takes the default beside the cells.
    stated = _case(_rev(), {**line, "money_year": 2025}).with_cost_defaults(
        pack, currency="EUR"
    )
    assert stated.lines[1].price == pytest.approx(10.7e6 / 1.13, rel=1e-4)
    assert stated.lines[1].cell("price", 2027) == B.Cell(50_000.0, 2025, False)


def _patched(update: dict) -> Any:
    real = loader.load_defaults_pack(NEW)
    bad = real.value(DC_KEYS["facility_capex"]).model_copy(update=update)

    class _Pack:
        stamp = real.stamp

        def value(self, key):
            return bad if key == bad.key else real.value(key)

    return _Pack()


def test_only_a_price_default_moves_the_money_year():
    """G-C1: a life row with a money year leaves the line's money year alone; a price row
    without one fills a line whose money year is stated."""
    real = loader.load_defaults_pack(NEW)
    life = real.value(DC_KEYS["facility_life"]).model_copy(
        update={"currency_year": 2020}
    )
    capex = real.value(DC_KEYS["facility_capex"]).model_copy(
        update={"currency_year": None}
    )

    class _Pack:
        stamp = real.stamp

        def value(self, key):
            return {life.key: life, capex.key: capex}.get(key) or real.value(key)

    line = _capex(
        price=None,
        life_years=None,
        money_year=BASE,
        default_keys={
            "price": DC_KEYS["facility_capex"],
            "life_years": DC_KEYS["facility_life"],
        },
    )
    filled = _case(line).with_cost_defaults(_Pack(), currency="EUR").lines[0]
    assert filled.price == capex.value and filled.life_years == 20
    assert filled.money_year == BASE
    line2 = _capex(
        life_years=None, default_keys={"life_years": DC_KEYS["facility_life"]}
    )
    assert (
        _case(line2).with_cost_defaults(_Pack(), currency="EUR").lines[0].money_year
        is None
    )


def test_a_pack_row_without_a_value_is_cost_not_established():
    pack = _patched({"value": None, "original_value": None, "status": "not_available"})
    assert _facility_after(pack) == NOT_ESTABLISHED


def test_a_pack_error_that_is_not_a_missing_row_propagates():
    """Only a missing row (a LookupError) means "no default"; any other pack error is raised."""
    real = loader.load_defaults_pack(NEW)

    class _Broken:
        stamp = real.stamp

        def value(self, key):
            raise loader.DefaultsPackError("corrupt pack")

    with pytest.raises(loader.DefaultsPackError, match="corrupt pack"):
        _case(*_dc_lines()).with_cost_defaults(_Broken(), currency="EUR")


def test_a_pack_value_outside_its_column_range_is_refused():
    real = loader.load_defaults_pack(NEW)
    bad = real.value(DC_KEYS["facility_life"]).model_copy(update={"value": 0.5})

    class _Pack:
        stamp = real.stamp

        def value(self, key):
            return bad if key == bad.key else real.value(key)

    with pytest.raises(ValidationError, match="life_years"):
        _case(*_dc_lines()).with_cost_defaults(_Pack(), currency="EUR")


# ---------------------------------------------------------------- the pack (BC9)


def test_new_pack_keeps_every_previous_row_byte_equal():
    """AS-02 / BC9: 2026-11-10 = 2026-11-07 + appended rows; every other file byte-identical;
    both hashes pinned and the old one unchanged."""
    old, new = VERSIONS / PREV, VERSIONS / NEW
    old_csv, new_csv = (
        (old / "values.csv").read_bytes(),
        (new / "values.csv").read_bytes(),
    )
    assert new_csv.startswith(old_csv)
    added = new_csv[len(old_csv) :].decode().splitlines()
    assert added and all(line.startswith("dc-business-case.") for line in added)
    for f in sorted(p.relative_to(old) for p in old.rglob("*") if p.is_file()):
        if f.name not in ("values.csv", "manifest.json"):
            assert (new / f).read_bytes() == (old / f).read_bytes(), f
    m_old = json.loads((old / "manifest.json").read_text())
    m_new = json.loads((new / "manifest.json").read_text())
    assert {
        k: v for k, v in m_new.items() if k not in ("version", "dc_business_case_costs")
    } == {k: v for k, v in m_old.items() if k != "version"}
    pins = json.loads(
        (Path(__file__).parent / "fixtures/defaults_pack/pack_hashes.json").read_text()
    )["generic_defaults"]
    assert loader.load_defaults_pack(PREV).hash == pins[PREV]
    assert loader.load_defaults_pack(NEW).hash == pins[NEW]
    assert loader.load_defaults_pack().version == NEW


def test_new_rows_price_no_part_and_no_revenue():
    """No `CostPart` (so no template, study or pricing path sees a priceable technology) and
    no revenue price row (DCBC-SCOPE-1 Q4)."""
    pack = loader.load_defaults_pack(NEW)
    rows = [v for v in pack.cost_values if v.technology == "dc-business-case"]
    assert {v.key for v in rows} == set(DC_KEYS.values())
    assert all(v.part is None for v in rows)
    assert pack.cost_parts("dc-business-case") == []
    assert not any(re.search(r"price|revenue", v.key) for v in rows)
    prev = loader.load_defaults_pack(PREV)
    assert [
        v for v in pack.cost_values if v.technology != "dc-business-case"
    ] == prev.cost_values
    assert pack.finance == prev.finance and pack.tariffs == prev.tariffs


def test_facility_capex_default_is_the_converted_jll_figure():
    """USD 10.7 M/MW (JLL, 2025) at 1.13 USD/EUR (ECB 2025 average) ≈ EUR 9.47 M/MW."""
    row = loader.load_defaults_pack(NEW).value(DC_KEYS["facility_capex"])
    assert row.unit == "EUR/MW" and row.value == pytest.approx(10.7e6 / 1.13, rel=1e-4)
    assert row.range.low == pytest.approx(8.9e6 / 1.13, rel=1e-4)
    assert row.range.high == pytest.approx(23.3e6 / 1.13, rel=1e-4)
