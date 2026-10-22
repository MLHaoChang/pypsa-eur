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

