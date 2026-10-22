"""The report retains the LCOE's own inputs (checked-results S8b), so the
figure can be rebuilt from the stored payload alone."""

import math

import pytest

from tests.test_chat_tools_investment_case import _report


def rebuild(payload, real=False):
    inputs = payload["lcoe_inputs"]
    r = payload["cost_of_equity"]
    equity = payload["cash"]["equity_post_tax"]
    num = math.fsum(v / (1 + r) ** k for k, v in enumerate(inputs["value"])) - math.fsum(
        c / (1 + r) ** k for k, c in enumerate(equity))
    if real:
        r = (1 + r) / (1 + inputs["inflation"]) - 1
    return num / math.fsum(e / (1 + r) ** k for k, e in enumerate(inputs["energy_mwh"]))


@pytest.mark.parametrize("name", ["s1", "s2", "s3"])
def test_the_stored_inputs_rebuild_the_reported_lcoe(name):
    payload = _report(name)["sections"]["project"]["payload"]
    assert payload["lcoe_nominal_per_mwh"] is not None
    inputs = payload["lcoe_inputs"]
    assert len(inputs["energy_mwh"]) == len(inputs["value"]) == len(payload["years"])
    assert rebuild(payload) == pytest.approx(payload["lcoe_nominal_per_mwh"], rel=1e-12)
    if payload["price_basis"] == "nominal" and inputs["inflation"] is not None:
        assert rebuild(payload, real=True) == pytest.approx(payload["lcoe_real_per_mwh"], rel=1e-12)



def test_a_real_lcoe_with_no_discounted_generation_is_not_established():
    """Storage-dominated: net energy turns negative from 2034. Discounted at the cost
    of equity it is still positive; at the real rate it is not, so the real LCOE
    has no denominator and must not flip sign or divide by zero."""
    import dataclasses

    from services.finance.engine import run_case
    from tests.test_finance_engine import LAYER, _case

    base = _case()
    charging = dataclasses.replace(base.templates[0], first_year=2034, energy_mwh={"pv": -2.5})
    result = run_case(dataclasses.replace(base, templates=(base.templates[0], charging)), layers=LAYER)
    assert result.metrics["lcoe_nominal_per_mwh"] is not None
    assert result.metrics["lcoe_real_per_mwh"] is None
    assert "lcoe_real_not_established:no_discounted_generation" in result.flags
