"""te-24 (owner decision LCOE-CAVEAT-1): an "LCOE (real)" above EUR 300/MWh keeps its
value and gains one sentence that names the bound. The sentence states the backup
cause only when the site has a must-stay-on share and genset capacity in the design."""

import pandas as pd
import pytest

from services.results.lcoe_caveat import (
    LCOE_CAVEAT_BOUND_EUR_PER_MWH,
    backup_mw,
    critical_share,
    lcoe_caveat,
)

NBSP = " "
HOSPITAL = {"id": "eh_site", "site_type": "hospital", "params": {"critical_share": 0.5}}
CAUSE = "the backup kept for the critical load is costed but rarely runs"


class _Net:
    def __init__(self, rows):
        self.generators = pd.DataFrame(rows, columns=["carrier", "p_nom", "p_nom_opt"])


GENSETS = _Net([["diesel", 2.0, 2.0], ["grid", 50.0, 50.0]])
NO_GENSETS = _Net([["solar", 1.0, 1.0], ["grid", 50.0, 50.0]])


def test_the_bound_is_the_owners_and_is_named_in_money_style():
    assert LCOE_CAVEAT_BOUND_EUR_PER_MWH == 300.0
    words = lcoe_caveat(413.0, "EUR/MWh", template=HOSPITAL, n=GENSETS)
    assert words == f"Above €{NBSP}300/MWh, high for a hospital: {CAUSE}."


@pytest.mark.parametrize("value", [300.0, 299.9, 120.0, None])
def test_no_caveat_at_or_below_the_bound(value):
    assert lcoe_caveat(value, "EUR/MWh", template=HOSPITAL, n=GENSETS) is None


def test_another_currency_is_not_compared_with_a_euro_bound():
    assert lcoe_caveat(413.0, "USD/MWh", template=HOSPITAL, n=GENSETS) is None


@pytest.mark.parametrize(
    "template, n",
    [
        (HOSPITAL, NO_GENSETS),  # no backup capacity in the design
        ({**HOSPITAL, "params": {"critical_share": 0}}, GENSETS),  # no share
        ({"id": "eh_datacenter"}, GENSETS),  # not a site template
        (None, GENSETS),
    ],
)
def test_the_cause_is_stated_only_when_it_is_true(template, n):
    words = lcoe_caveat(413.0, "EUR/MWh", template=template, n=n)
    assert words is not None and words.startswith(f"Above €{NBSP}300/MWh, high for ")
    assert CAUSE not in words


def test_the_neutral_line_names_the_site_type_or_none():
    assert lcoe_caveat(413.0, "EUR/MWh", template=HOSPITAL, n=NO_GENSETS) == (
        f"Above €{NBSP}300/MWh, high for a hospital."
    )
    assert lcoe_caveat(413.0, "EUR/MWh", template=None, n=GENSETS) == (
        f"Above €{NBSP}300/MWh, high for a site like this."
    )


def test_backup_reads_the_optimised_capacity_and_falls_back_to_the_entered_one():
    built_none = _Net([["gas", 2.0, 0.0]])
    assert backup_mw(built_none) == 0.0
    unsolved = _Net([["gas", 2.0, float("nan")]])
    assert backup_mw(unsolved) == 2.0
    assert backup_mw(_Net([])) == 0.0


def test_critical_share_needs_a_site_type_and_a_positive_share():
    assert critical_share(HOSPITAL) == 0.5
    assert critical_share({"params": {"critical_share": 0.5}}) is None
    assert critical_share({"site_type": "hospital", "params": {}}) is None
