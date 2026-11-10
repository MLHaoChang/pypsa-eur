"""DC business case slice 02: the inputs model and its pack defaults.

Spec .scratch/dc-business-case/spec.md §4, §5, §9 (DCBC-SCOPE-1); GK B BC7 (typed refusals,
real units) and BC9 (defaults pack: previous rows byte-equal, defaults sourced and assumed).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from models import business_case as B
from services.library.defaults_pack import loader

PREV, NEW = "2026-11-07", "2026-11-10"
VERSIONS = Path(loader.__file__).parent / "versions"


def _full() -> B.BusinessCaseInputs:
    return B.BusinessCaseInputs(
        revenue_model="gpu_hour",
        gpu_hour=B.GpuHourRevenue(
            price_per_gpu_hour=2.5,
            reserved_share=0.6,
            reserved_price_per_gpu_hour=2.0,
            kw_per_gpu=1.28,
            sold_share=0.9,
            indexation=-0.05,
        ),
        colocation=B.ColocationRevenue(
            price_per_kw_month=150.0,
            indexation=0.02,
            lease_up_by_phase=[[0.5, 0.8, 1.0], [1.0]],
        ),
        pass_through=B.PowerPassThrough(enabled=False, share=1.0, margin=0.05),
        facility=B.Facility(capex_per_mw_it=9.0e6, life_years=20),
        it_hardware=B.ItHardware(
            capex_basis="per_gpu", capex=30_000.0, life_years=5, refresh_years=5
        ),
        opex=B.NonEnergyOpex(
            staff_per_mw_year=40_000.0,
            maintenance_share=0.015,
            insurance_share=0.005,
            property_tax_share=0.01,
            network_per_mw_year=10_000.0,
            sga_share=0.15,
        ),
        receivable_days=45,
    )


# ---------------------------------------------------------------- round trip


def test_round_trip_json():
    full = _full()
    again = B.BusinessCaseInputs.model_validate_json(full.model_dump_json())
    assert again == full
    assert (
        B.BusinessCaseInputs.model_validate(json.loads(full.model_dump_json())) == full
    )


def test_round_trip_with_assumed_defaults():
    filled = B.BusinessCaseInputs().with_cost_defaults(
        loader.load_defaults_pack(NEW), currency="EUR"
    )
    assert filled.assumed
    assert B.BusinessCaseInputs.model_validate_json(filled.model_dump_json()) == filled


def test_empty_inputs_are_valid_and_not_established():
    """A partial case is saved: every price and cost is None, never 0."""
    empty = B.BusinessCaseInputs()
    assert empty.revenue_model is None
    assert empty.gpu_hour.price_per_gpu_hour is None
    assert empty.colocation.price_per_kw_month is None
    assert empty.facility.capex_per_mw_it is None
    assert empty.it_hardware.capex is None
    assert all(v is None for v in empty.opex.model_dump().values())
    assert empty.receivable_days is None


# ---------------------------------------------------------------- validation names its field


@pytest.mark.parametrize(
    "payload, field",
    [
        ({"gpu_hour": {"reserved_share": 1.2}}, "reserved_share"),
        ({"gpu_hour": {"sold_share": -0.1}}, "sold_share"),
        ({"gpu_hour": {"price_per_gpu_hour": -1}}, "price_per_gpu_hour"),
        ({"gpu_hour": {"kw_per_gpu": 0}}, "kw_per_gpu"),
        ({"gpu_hour": {"indexation": -1.0}}, "indexation"),
        ({"colocation": {"price_per_kw_month": -5}}, "price_per_kw_month"),
        ({"colocation": {"lease_up_by_phase": [[0.5, 1.5]]}}, "lease_up_by_phase[0]"),
        ({"colocation": {"lease_up_by_phase": [[]]}}, "lease_up_by_phase[0]"),
        ({"pass_through": {"enabled": True}}, "share"),
        ({"pass_through": {"share": 1.1}}, "share"),
        ({"pass_through": {"margin": -0.1}}, "margin"),
        ({"facility": {"life_years": 0.5}}, "life_years"),
        ({"facility": {"capex_per_mw_it": -1}}, "capex_per_mw_it"),
        ({"it_hardware": {"life_years": 0}}, "life_years"),
        ({"it_hardware": {"refresh_years": 0}}, "refresh_years"),
        ({"it_hardware": {"capex_basis": "per_rack"}}, "capex_basis"),
        ({"opex": {"maintenance_share": 1.5}}, "maintenance_share"),
        ({"opex": {"insurance_share": -0.1}}, "insurance_share"),
        ({"opex": {"property_tax_share": 2}}, "property_tax_share"),
        ({"opex": {"sga_share": 1.01}}, "sga_share"),
        ({"opex": {"staff_per_mw_year": -1}}, "staff_per_mw_year"),
        ({"opex": {"network_per_mw_year": -1}}, "network_per_mw_year"),
        ({"receivable_days": -1}, "receivable_days"),
        ({"receivable_days": 400}, "receivable_days"),
        ({"revenue_model": "services"}, "revenue_model"),
        (
            {"gpu_hour": {"price_per_gpu_hours": 2}},
            "price_per_gpu_hours",
        ),  # a typo is refused
    ],
)
def test_every_invalid_field_is_named(payload, field):
    with pytest.raises(ValidationError) as exc:
        B.BusinessCaseInputs.model_validate(payload)
    err = exc.value.errors()[0]
    assert field in ".".join(str(p) for p in err["loc"]) + " " + err["msg"]


def test_assumed_must_match_its_field():
    filled = B.BusinessCaseInputs().with_cost_defaults(
        loader.load_defaults_pack(NEW), currency="EUR"
    )
    data = filled.model_dump()
    data["facility"]["life_years"] = 25  # edited, entry kept: refused
    with pytest.raises(ValidationError, match="facility.life_years"):
        B.BusinessCaseInputs.model_validate(data)
    del data["assumed"][
        "facility.life_years"
    ]  # edited, entry dropped: the user's figure
    assert B.BusinessCaseInputs.model_validate(data).facility.life_years == 25


def test_assumed_names_only_cost_fields():
    with pytest.raises(
        ValidationError,
        match=r"\['gpu_hour.price_per_gpu_hour'\], which are not defaultable",
    ):
        B.BusinessCaseInputs.model_validate(
            {
                "assumed": {
                    "gpu_hour.price_per_gpu_hour": {
                        "key": "k",
                        "value": 1,
                        "unit": "u",
                        "source": "s",
                        "source_year": 2026,
                        "pack": "p",
                    }
                }
            }
        )


# ---------------------------------------------------------------- typed refusals (BC7)


def _runnable_colocation() -> B.BusinessCaseInputs:
    return B.BusinessCaseInputs(
        revenue_model="colocation",
        colocation=B.ColocationRevenue(price_per_kw_month=150.0),
        pass_through=B.PowerPassThrough(enabled=True, share=1.0, margin=0.05),
        facility=B.Facility(capex_per_mw_it=9.0e6, life_years=20),
    )


def _drop(inputs: B.BusinessCaseInputs, path: str) -> B.BusinessCaseInputs:
    data = inputs.model_dump()
    group, name = path.split(".")
    data[group][name] = None
    return B.BusinessCaseInputs.model_validate(data)


def test_runnable_cases_pass():
    _full().require_runnable()
    _runnable_colocation().require_runnable()


@pytest.mark.parametrize(
    "inputs, cls, field",
    [
        (B.BusinessCaseInputs(), B.RevenueModelMissing, "revenue_model"),
        (
            _drop(_full(), "gpu_hour.price_per_gpu_hour"),
            B.PriceNotEstablished,
            "gpu_hour.price_per_gpu_hour",
        ),
        (
            _drop(_full(), "gpu_hour.reserved_price_per_gpu_hour"),
            B.PriceNotEstablished,
            "gpu_hour.reserved_price_per_gpu_hour",
        ),
        (
            _drop(_full(), "gpu_hour.kw_per_gpu"),
            B.PriceNotEstablished,
            "gpu_hour.kw_per_gpu",
        ),
        (_drop(_full(), "it_hardware.capex"), B.ItCapexMissing, "it_hardware.capex"),
        (
            _drop(_full(), "it_hardware.life_years"),
            B.LifeMissing,
            "it_hardware.life_years",
        ),
        (
            _drop(_full(), "facility.capex_per_mw_it"),
            B.FacilityCapexMissing,
            "facility.capex_per_mw_it",
        ),
        (_drop(_full(), "facility.life_years"), B.LifeMissing, "facility.life_years"),
        (
            _drop(_runnable_colocation(), "colocation.price_per_kw_month"),
            B.PriceNotEstablished,
            "colocation.price_per_kw_month",
        ),
        (
            _drop(_runnable_colocation(), "facility.capex_per_mw_it"),
            B.FacilityCapexMissing,
            "facility.capex_per_mw_it",
        ),
    ],
)
def test_each_refusal_is_typed_and_names_its_field(inputs, cls, field):
    """Fail-closed: the exact subclass (a base-class mutant fails), the field and the code."""
    with pytest.raises(B.BusinessCaseRefused) as exc:
        inputs.require_runnable()
    assert type(exc.value) is cls
    assert exc.value.field == field
    assert exc.value.code == f"business_case_refused:{cls.kind}"
    assert field in str(exc.value)


def test_kinds_are_distinct():
    kinds = [
        c.kind
        for c in (
            B.RevenueModelMissing,
            B.PriceNotEstablished,
            B.ItCapexMissing,
            B.FacilityCapexMissing,
            B.LifeMissing,
            B.ItCapexNotOwned,
            B.PassThroughNotColocation,
        )
    ]
    assert len(set(kinds)) == len(kinds) and B.BusinessCaseRefused.kind not in kinds


def test_reserved_price_needed_only_with_a_reserved_share():
    data = _full().model_dump()
    data["gpu_hour"].update(reserved_share=0.0, reserved_price_per_gpu_hour=None)
    B.BusinessCaseInputs.model_validate(data).require_runnable()


def test_colocation_never_needs_it_hardware():
    """R2: the tenant owns the IT; no IT capex, no refusal."""
    inputs = _runnable_colocation()
    assert inputs.it_hardware.capex is None
    inputs.require_runnable()


def test_defaults_never_make_a_case_runnable_without_a_price():
    """Cost defaults fill costs only (DCBC-SCOPE-1 Q4): the price is still refused."""
    filled = B.BusinessCaseInputs(revenue_model="colocation").with_cost_defaults(
        loader.load_defaults_pack(NEW), currency="EUR"
    )
    with pytest.raises(B.PriceNotEstablished):
        filled.require_runnable()


# ---------------------------------------------------------------- real units (BC7)


def test_kw_month_is_twelve_times_a_year():
    """USD 181 per kW-month ≈ USD 2.2 M per MW-year (research §3, derived)."""
    rent = B.ColocationRevenue(price_per_kw_month=181.0).rent_per_kw_year()
    assert rent == pytest.approx(2172.0)
    assert rent * 1000 == pytest.approx(2.172e6)


def test_gpu_count_and_gpu_hours_in_kw_per_gpu():
    """1 MW of IT at 1.28 kW per GPU (H100 DGX all-in, research §5) is 781.25 GPUs; one MWh of
    served IT energy is 781.25 GPU-hours, times the sold share (never a second u(t))."""
    gpu = B.GpuHourRevenue(kw_per_gpu=1.28, sold_share=0.8)
    assert gpu.gpu_count(1.0) == pytest.approx(781.25)
    assert gpu.gpu_hours(1.0) == pytest.approx(781.25 * 0.8)
    assert gpu.gpu_hours(8760.0) == pytest.approx(
        gpu.gpu_count(1.0) * B.HOURS_PER_YEAR * 0.8
    )


def test_it_capex_per_gpu_and_per_mw():
    gpu = B.GpuHourRevenue(kw_per_gpu=1.25)
    per_gpu = B.ItHardware(capex_basis="per_gpu", capex=40_000.0)
    per_mw = B.ItHardware(capex_basis="per_mw_it", capex=32.0e6)
    assert per_gpu.capex_total(2.0, gpu) == pytest.approx(1600 * 40_000.0)
    assert per_mw.capex_total(2.0) == pytest.approx(64.0e6)
    with pytest.raises(B.PriceNotEstablished):
        per_gpu.capex_total(2.0)  # per GPU without kW per GPU
    with pytest.raises(B.ItCapexMissing):
        B.ItHardware().capex_total(2.0)


def test_unit_helpers_refuse_without_their_input():
    with pytest.raises(B.PriceNotEstablished):
        B.ColocationRevenue().rent_per_kw_year()
    for call in (lambda g: g.gpu_hours(1.0), lambda g: g.gpu_count(1.0)):
        with pytest.raises(B.PriceNotEstablished) as exc:
            call(B.GpuHourRevenue())
        assert exc.value.field == "gpu_hour.kw_per_gpu"
    with pytest.raises(B.PriceNotEstablished) as exc:  # per GPU, kW per GPU unset
        B.ItHardware(capex_basis="per_gpu", capex=1.0).capex_total(
            1.0, B.GpuHourRevenue()
        )
    assert exc.value.field == "gpu_hour.kw_per_gpu"


# ---------------------------------------------------------------- pack defaults (BC9)


def test_defaults_carry_source_year_and_assumed():
    pack = loader.load_defaults_pack(NEW)
    filled = B.BusinessCaseInputs().with_cost_defaults(pack, currency="EUR")
    assert set(filled.assumed) == set(B.COST_DEFAULT_KEYS)
    for path, d in filled.assumed.items():
        row = pack.value(B.COST_DEFAULT_KEYS[path])
        assert d.status == "assumed" and row.illustrative is True
        assert d.source == row.source and d.source.startswith("assumed")
        assert d.source_year == 2026 and d.pack == pack.stamp
        assert row.range is not None and row.range.low <= d.value <= row.range.high
        assert B._get(filled, path) == d.value
    assert filled.assumed["facility.capex_per_mw_it"].currency == "EUR"
    assert filled.assumed["facility.capex_per_mw_it"].money_year == 2025
    assert filled.it_hardware.life_years == 5
    life = pack.value("dc-business-case.it_hardware.lifetime").range
    assert (life.low, life.high) == (4, 6)
    # Percent rows arrive as fractions (the loader's %/year -> share/year, % -> per unit).
    assert filled.opex.maintenance_share == pytest.approx(0.015)
    assert filled.opex.insurance_share == pytest.approx(0.005)
    assert filled.opex.property_tax_share == pytest.approx(0.01)
    assert filled.opex.sga_share == pytest.approx(0.15)
    assert filled.facility.life_years == 20


def test_defaults_never_fill_a_revenue_price_or_an_unsourced_line():
    filled = B.BusinessCaseInputs().with_cost_defaults(
        loader.load_defaults_pack(NEW), currency="EUR"
    )
    assert filled.gpu_hour.price_per_gpu_hour is None
    assert filled.gpu_hour.kw_per_gpu is None
    assert filled.colocation.price_per_kw_month is None
    assert filled.it_hardware.capex is None  # no primary price list
    assert filled.opex.staff_per_mw_year is None  # no primary per-MW source
    assert filled.opex.network_per_mw_year is None
    assert filled.receivable_days is None


def test_defaults_never_overwrite_a_stated_value():
    stated = B.BusinessCaseInputs(
        facility=B.Facility(capex_per_mw_it=1.0, life_years=30),
        opex=B.NonEnergyOpex(sga_share=0.0),
    )
    filled = stated.with_cost_defaults(loader.load_defaults_pack(NEW), currency="EUR")
    assert filled.facility.capex_per_mw_it == 1.0 and filled.facility.life_years == 30
    assert filled.opex.sga_share == 0.0  # a stated 0 is the user's, kept
    assert not {
        "facility.capex_per_mw_it",
        "facility.life_years",
        "opex.sga_share",
    } & set(filled.assumed)
    assert (
        stated.assumed == {} and stated.facility.capex_per_mw_it == 1.0
    )  # input untouched


def test_a_money_default_in_another_currency_is_skipped_not_converted():
    filled = B.BusinessCaseInputs().with_cost_defaults(
        loader.load_defaults_pack(NEW), currency="USD"
    )
    assert filled.facility.capex_per_mw_it is None
    assert "facility.capex_per_mw_it" not in filled.assumed
    assert filled.facility.life_years == 20  # a life has no currency


def test_an_older_pack_gives_no_defaults():
    filled = B.BusinessCaseInputs().with_cost_defaults(
        loader.load_defaults_pack(PREV), currency="EUR"
    )
    assert filled.assumed == {} and filled == B.BusinessCaseInputs()


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
    assert {v.key for v in rows} == set(B.COST_DEFAULT_KEYS.values())
    assert all(v.part is None for v in rows)
    assert pack.cost_parts("dc-business-case") == []
    assert not any("price" in v.key or "revenue" in v.key for v in rows)
    prev = loader.load_defaults_pack(PREV)
    assert [
        v for v in pack.cost_values if v.technology != "dc-business-case"
    ] == prev.cost_values
    assert pack.finance == prev.finance and pack.tariffs == prev.tariffs


def test_facility_capex_default_is_the_converted_jll_figure():
    """USD 10.7 M/MW (JLL, 2025) at 1.13 USD/EUR (ECB 2025 average) ≈ EUR 9.47 M/MW."""
    row = loader.load_defaults_pack(NEW).value("dc-business-case.facility.overnight")
    assert row.unit == "EUR/MW" and row.value == pytest.approx(10.7e6 / 1.13, rel=1e-4)
    assert row.range.low == pytest.approx(8.9e6 / 1.13, rel=1e-4)
    assert row.range.high == pytest.approx(23.3e6 / 1.13, rel=1e-4)


def test_a_pack_row_without_a_value_is_skipped():
    """A `not_available` row (value None) fills nothing, never a None default."""
    real = loader.load_defaults_pack(NEW)
    blank = real.value("dc-business-case.sga_share").model_copy(
        update={"value": None, "original_value": None, "status": "not_available"}
    )

    class _Pack:
        stamp = real.stamp

        def value(self, key):
            return blank if key == blank.key else real.value(key)

    filled = B.BusinessCaseInputs().with_cost_defaults(_Pack(), currency="EUR")
    assert filled.opex.sga_share is None and "opex.sga_share" not in filled.assumed
    assert "opex.insurance_share" in filled.assumed


def test_lease_up_bounds_are_inclusive():
    ramp = B.ColocationRevenue(lease_up_by_phase=[[0.0, 0.5, 1.0]])
    assert ramp.lease_up_by_phase == [[0.0, 0.5, 1.0]]


def test_a_pack_error_that_is_not_a_missing_row_propagates():
    """Only a missing row (a LookupError) means "no default"; any other pack error is raised."""
    real = loader.load_defaults_pack(NEW)

    class _Broken:
        stamp = real.stamp

        def value(self, key):
            raise loader.DefaultsPackError("corrupt pack")

    with pytest.raises(loader.DefaultsPackError, match="corrupt pack"):
        B.BusinessCaseInputs().with_cost_defaults(_Broken(), currency="EUR")


def test_a_pack_value_outside_its_field_range_is_refused():
    real = loader.load_defaults_pack(NEW)
    bad = real.value("dc-business-case.sga_share").model_copy(update={"value": 1.5})

    class _Pack:
        stamp = real.stamp

        def value(self, key):
            return bad if key == bad.key else real.value(key)

    with pytest.raises(ValidationError, match="sga_share"):
        B.BusinessCaseInputs().with_cost_defaults(_Pack(), currency="EUR")


# ---------------------------------------------------------------- GK B #1320 F1, F2, ruling (2)

#: Every float field of the inputs (dotted path in a payload), unbounded or not.
FLOAT_FIELDS = [
    ("gpu_hour", "price_per_gpu_hour"),
    ("gpu_hour", "reserved_share"),
    ("gpu_hour", "reserved_price_per_gpu_hour"),
    ("gpu_hour", "kw_per_gpu"),
    ("gpu_hour", "sold_share"),
    ("gpu_hour", "indexation"),
    ("colocation", "price_per_kw_month"),
    ("colocation", "indexation"),
    ("pass_through", "share"),
    ("pass_through", "margin"),
    ("facility", "capex_per_mw_it"),
    ("facility", "life_years"),
    ("it_hardware", "capex"),
    ("it_hardware", "life_years"),
    ("opex", "staff_per_mw_year"),
    ("opex", "maintenance_share"),
    ("opex", "insurance_share"),
    ("opex", "property_tax_share"),
    ("opex", "network_per_mw_year"),
    ("opex", "sga_share"),
]


def test_float_field_list_is_complete():
    """A new float field must join FLOAT_FIELDS (the lease-up list is tested on its own)."""
    found = {
        (g, n)
        for g, sub in B.BusinessCaseInputs.model_fields.items()
        if hasattr(sub.annotation, "model_fields")
        for n, f in sub.annotation.model_fields.items()
        if "float" in str(f.annotation) and "list" not in str(f.annotation)
    }
    assert found == set(FLOAT_FIELDS)


@pytest.mark.parametrize("group, name", FLOAT_FIELDS)
@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_every_float_field_refuses_non_finite(group, name, bad):
    with pytest.raises(ValidationError) as exc:
        B.BusinessCaseInputs.model_validate({group: {name: bad}})
    assert exc.value.errors()[0]["loc"] == (group, name)


@pytest.mark.parametrize("bad", [float("inf"), float("nan")])
def test_receivable_days_and_lease_up_refuse_non_finite(bad):
    with pytest.raises(ValidationError, match="receivable_days"):
        B.BusinessCaseInputs.model_validate({"receivable_days": bad})
    with pytest.raises(ValidationError, match="lease_up_by_phase"):
        B.BusinessCaseInputs.model_validate(
            {"colocation": {"lease_up_by_phase": [[bad]]}}
        )


def test_a_json_infinity_is_refused():
    with pytest.raises(ValidationError, match="price_per_kw_month"):
        B.BusinessCaseInputs.model_validate_json(
            '{"colocation": {"price_per_kw_month": Infinity}}'
        )


def test_a_cost_default_refuses_non_finite():
    with pytest.raises(ValidationError, match="value"):
        B.CostDefault(
            key="k",
            value=float("inf"),
            unit="u",
            source="s",
            source_year=2026,
            pack="p",
        )


def test_colocation_with_owned_it_capex_is_refused():
    """F2 (B5): the tenant owns the IT under colocation; stated IT capex is a typed refusal."""
    data = _runnable_colocation().model_dump()
    data["it_hardware"]["capex"] = 30.0e6
    with pytest.raises(B.BusinessCaseRefused) as exc:
        B.BusinessCaseInputs.model_validate(data).require_runnable()
    assert type(exc.value) is B.ItCapexNotOwned
    assert exc.value.field == "it_hardware.capex"
    assert exc.value.code == "business_case_refused:it_capex_not_owned"


def test_colocation_keeps_an_it_life_without_capex():
    """Only stated capex is refused: a defaulted IT life alone prices nothing."""
    filled = _runnable_colocation().with_cost_defaults(
        loader.load_defaults_pack(NEW), currency="EUR"
    )
    assert filled.it_hardware.life_years == 5 and filled.it_hardware.capex is None
    filled.require_runnable()


def test_pass_through_under_gpu_hour_is_refused():
    """Ruling (2): R3 is a colocation recovery; under GPU-hour it is a typed refusal."""
    data = _full().model_dump()
    data["pass_through"]["enabled"] = True
    with pytest.raises(B.BusinessCaseRefused) as exc:
        B.BusinessCaseInputs.model_validate(data).require_runnable()
    assert type(exc.value) is B.PassThroughNotColocation
    assert exc.value.field == "pass_through.enabled"
    assert exc.value.code == "business_case_refused:pass_through_not_colocation"


def test_pass_through_under_colocation_runs():
    inputs = _runnable_colocation()
    assert inputs.pass_through.enabled
    inputs.require_runnable()
