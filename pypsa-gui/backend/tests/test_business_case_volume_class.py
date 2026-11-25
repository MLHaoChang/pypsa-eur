# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Business-case 18: a volume class the user states on a typed revenue or opex line.

`fixed` never scales, `volume` scales by the year's lease-up factor, unset refuses
`volume_factor_typed_line` (ruling 6). The digest reads the class only where it changes an
outcome, so a master-era case keeps its digest."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

import services.finance.business_case as bcm
from models.business_case import LineItem, VolumeFactorTypedLine
from services.results_workbook.business_case import write_business_case_sheet
from tests.test_business_case_engine import (
    _NH3_OUT,
    _OPS,
    _amount,
    _capex,
    _inputs,
    _lease_case,
    _line,
    _no_export_ic,
    _run,
)

_F = 0.6
_LEASE = {2032: _F}


def _service(**kw) -> LineItem:
    # 10 × 50 = 500 a year.
    return _line(name="service", category="service_fee", **kw)


def _staff(**kw) -> LineItem:
    return _line(name="staff", kind="opex", category="staff", quantity=2.0, price=30.0, **kw)


def _go(*lines, factor=_LEASE):
    return _run(_lease_case(factor, *lines), ic=_no_export_ic(), outputs=_NH3_OUT)[0]


# ── the outcome under a 60 % year ────────────────────────────────────────────


def test_volume_scales_by_the_years_factor_and_fixed_never_does():
    bc = _go(_service(volume_class="volume"), _staff(volume_class="fixed"))
    assert _amount(bc, "service", 2032) == 10.0 * _F * 50.0
    assert _amount(bc, "service", 2031) == 500.0
    assert _amount(bc, "staff", 2032) == -60.0
    bc = _go(_service(volume_class="fixed"), _staff(volume_class="volume"))
    assert _amount(bc, "service", 2032) == 500.0
    assert _amount(bc, "staff", 2032) == -2.0 * _F * 30.0
    assert _amount(bc, "staff", 2033) == -60.0


def test_volume_scales_a_per_year_quantity_by_each_years_own_factor():
    ln = _service(quantity=None, quantity_by_year={y: float(y - 2030) for y in _OPS},
                  volume_class="volume")
    bc = _go(ln, factor={2031: 0.5, 2033: 0.25})
    assert _amount(bc, "service", 2031) == 1.0 * 0.5 * 50.0
    assert _amount(bc, "service", 2032) == 2.0 * 50.0
    assert _amount(bc, "service", 2033) == 3.0 * 0.25 * 50.0


def test_unset_still_refuses_naming_the_class():
    with pytest.raises(VolumeFactorTypedLine) as e:
        _go(_service(volume_class="volume"), _staff())
    assert e.value.field == "lines[2].volume_class"
    found = _lease_case(_LEASE, _service(), _staff(volume_class="fixed")).refusals(
        horizon=(2031, 2035), base_year=2031
    )
    assert [(x.kind, x.field) for x in found] == [
        ("volume_factor_typed_line", "lines[1].volume_class")
    ]


def test_a_stated_class_clears_the_refusal_for_either_class():
    for cls in ("fixed", "volume"):
        found = _lease_case(_LEASE, _service(volume_class=cls)).refusals(
            horizon=(2031, 2035), base_year=2031
        )
        assert not found, cls


def test_the_engine_refuses_an_unset_typed_line_it_is_asked_to_scale():
    """Defence behind `require_runnable`: the engine never reads unset as either class."""
    with pytest.raises(VolumeFactorTypedLine):
        bcm._volume_line(_service())
    assert bcm._volume_line(_service(volume_class="volume")) is True
    assert bcm._volume_line(_service(volume_class="fixed")) is False


def test_a_share_on_a_volume_line_scales_once():
    share = _line(name="royalty", kind="opex", category="other", quantity=None,
                  quantity_source="share_of:revenue/service_fee", price=0.1)
    bc = _go(_service(volume_class="volume"), share)
    assert _amount(bc, "royalty", 2032) == pytest.approx(-0.1 * 10.0 * _F * 50.0)


# ── where the class may be stated ────────────────────────────────────────────


@pytest.mark.parametrize(
    "make",
    [
        lambda: _capex(volume_class="fixed"),
        lambda: _line(name="nh3", quantity=None, volume_class="volume",
                      quantity_source="linked:served_product.nh3@1"),
        lambda: _line(name="r", kind="opex", category="other", quantity=None,
                      quantity_source="share_of:revenue", volume_class="fixed"),
    ],
    ids=["capex", "linked", "share_of"],
)
def test_the_class_on_a_line_it_cannot_apply_to_is_a_parse_error(make):
    with pytest.raises(ValidationError, match="volume_class"):
        make()


def test_an_unknown_class_is_a_parse_error():
    with pytest.raises(ValidationError):
        _service(volume_class="sometimes")


def test_a_typed_opex_line_takes_the_class():
    assert _staff(volume_class="volume").volume_class == "volume"
    assert _service().volume_class is None


# ── the digest ───────────────────────────────────────────────────────────────

#: Master 24ea3f90's digests, before the field existed.
_MASTER_TYPED = "6da40d455842570f8f42d84e63d8222e"
_MASTER_LEASE = "f897c1522ac5f4714ef6a4bed7227e99"


def test_a_master_era_case_keeps_its_digest():
    ic = _no_export_ic()
    typed = _inputs(_service())
    assert _run(typed, ic=ic, outputs=_NH3_OUT)[0].hash == _MASTER_TYPED
    assert _go(factor=_LEASE).hash == _MASTER_LEASE


@pytest.mark.parametrize("cls", ["fixed", "volume"])
def test_a_class_that_changes_no_outcome_never_enters_the_digest(cls):
    ic = _no_export_ic()
    # At 100 % in every year.
    at_full = _inputs(_service(volume_class=cls))
    assert _run(at_full, ic=ic, outputs=_NH3_OUT)[0].hash == _MASTER_TYPED
    # Below 100 % only in a year outside the line's span.
    late = dict(start_year=2033)
    assert _go(_service(volume_class=cls, **late)).hash == _go(_service(**late)).hash


def test_the_class_enters_the_digest_where_it_scales_a_year():
    fixed = _go(_service(volume_class="fixed")).hash
    volume = _go(_service(volume_class="volume")).hash
    assert fixed != volume
    assert _MASTER_LEASE not in (fixed, volume)
    # The span bounds count: a line ending in the scaled year still reads it.
    a = _go(_service(volume_class="fixed", end_year=2032)).hash
    b = _go(_service(volume_class="volume", end_year=2032)).hash
    assert a != b


# ── report and workbook ──────────────────────────────────────────────────────


def test_the_report_names_each_stated_class_only_under_a_lease_up():
    case = _lease_case(_LEASE, _service(volume_class="volume"), _staff(volume_class="fixed"))
    shown = bcm.volume_factor_display(case, _OPS)
    assert shown["volume_class"] == {"service": "volume", "staff": "fixed"}
    full = _lease_case(None, _service(volume_class="volume"))
    assert bcm.volume_factor_display(full, _OPS) == {}


def _sheet(pnl):
    from openpyxl import Workbook

    wb = Workbook()
    report = {
        "basis": {"label": "b"},
        "pnl": pnl,
        "kpis": {},
    }
    ws = wb[write_business_case_sheet(wb, report, {"stale": False})]
    return [(r[0], r[1]) for r in ws.iter_rows(min_col=1, max_col=2, values_only=True)]


def test_the_workbook_shows_each_stated_class_under_a_lease_up():
    rows = _sheet(
        {
            "volume_factor": {"2032": _F},
            "volume_factor_note": bcm.VOLUME_FACTOR_NOTE,
            "volume_class": {"service": "volume", "staff": "fixed"},
        }
    )
    assert ("Volume class: service", "follows volume (scales with lease-up)") in rows
    assert ("Volume class: staff", "fixed (never scales)") in rows
    assert not [r for r in _sheet({}) if str(r[0]).startswith("Volume class")]
