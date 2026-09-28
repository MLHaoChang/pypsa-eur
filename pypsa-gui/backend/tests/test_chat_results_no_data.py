"""
`get_results` must never hand the model a `Response`.

Six of the 28 result kinds answer 204 on an unsolved or dispatch-stale
network, and several answer it on a perfectly good solve that simply produced
none of that kind (`lost_load` when nothing shed, `adequacy` when the run
carried no target). Before this, `json.dumps(result, default=str)` in
`_result_to_anthropic_content` turned each one into
"<starlette.responses.Response object at 0x…>" and handed it to the model as
data — the same defect the tools audit found in the five binary-export tools,
which survived here because that audit did not cover the results path.
"""
from __future__ import annotations

import json

import pypsa
import pytest

from services import chat_tools as T
from services.chat_tools_schema import RESULTS_ENUM
from tests.conftest import build_network


def _unsolved() -> pypsa.Network:
    n = pypsa.Network()
    n.add("Bus", "B1")
    n.add("Bus", "B2")
    n.add("Line", "L1", bus0="B1", bus1="B2", x=0.1, r=0.01, s_nom=100.0)
    return n


def test_no_result_kind_serialises_as_a_response_repr(install_network):
    """The regression that motivated the fix, across the whole enum."""
    install_network(_unsolved())
    leaked = []
    for kind in RESULTS_ENUM:
        body = json.dumps(T.get_results(kind), default=str)
        if "Response object" in body:
            leaked.append(kind)
    assert not leaked, f"result kinds leaked a Response repr: {leaked}"


@pytest.mark.parametrize("kind", [
    "line_duals", "prices", "emissions", "curtailment", "lost_load",
    "asset_economics",
])
def test_unsolved_network_reports_typed_no_data(kind, install_network):
    install_network(_unsolved())
    out = T.get_results(kind)
    assert isinstance(out, dict)
    assert out["status"] == "no_data"
    assert out["kind"] == kind
    # The message has to be actionable: it names the check to run next.
    assert "dispatch_status" in out["message"]


def test_no_data_message_covers_both_causes():
    """
    `lost_load` 204s on a solved run that shed nothing, so a message saying
    only "not solved" would be false.
    """
    msg = T._RESULTS_NO_DATA_MESSAGE
    assert "not been solved" in msg
    assert "produced none" in msg


def test_a_solved_network_still_returns_real_payloads(install_network):
    """The mapper must not swallow live results."""
    install_network(build_network(solve=True))
    out = T.get_results("cost_breakdown")
    assert isinstance(out, dict)
    assert out.get("status") != "no_data"
    assert "total" in out


def test_helper_passes_non_204_through_untouched():
    payload = {"rows": [1, 2, 3]}
    assert T._payload_or_no_data("prices", payload, "msg") is payload


def test_helper_only_maps_204_not_every_response():
    """A future handler returning 200-with-body must not become no_data."""
    from fastapi import Response

    ok = Response(status_code=200)
    assert T._payload_or_no_data("prices", ok, "msg") is ok
