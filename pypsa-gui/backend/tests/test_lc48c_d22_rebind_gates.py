"""LC-48c, LC-D22.rebind: a write or a delivery racing a switch of the active project changes nothing.

The seam is the copilot's real turn loop (``chat_service.run_turn`` with a scripted
provider, the real dispatcher and the real ``update_component``) and the real
``build_delivery`` tool. The switch is the real projects route: the person's other tab
saves the open network as ``Elsewhere`` with ``rebind``, which moves the active project
under the running turn.

Hand oracle, the baseline project (B1 110 kV, G1 20 MW at 10 EUR/MWh, L1 5 MW, two
snapshots). ``lc43_gates.state`` is read right after the switch and again after the
refusal; it must be equal, and the baseline project's own uploads must be as before the
turn:

- the model asks for G1 marginal cost 10 -> 50; the switch lands while the model is
  answering; the write is refused ``project_switched_mid_turn`` (a tool error and the
  turn's error), ``update_component`` writes nothing: G1 is 10 in the open network
  (``Elsewhere``) and 10 in the baseline project's saved file;
- ``build_delivery`` of an exported network.nc, with the switch landing while the zip is
  built, is 409 ``project_changed``; no zip is stored in either project (each holds only
  the exported network.nc, which saving as ``Elsewhere`` copied).
"""

# ruff: noqa: F811 -- shared public harness fixtures
import pypsa

from services import assurance_artifacts, chat_service, chat_tools, project_registry
from services.pypsa_service import PyPSAService
from tests.lc43_gates import refused, state
from tests.test_assistant_capabilities import session  # noqa: F401
from tests.test_chat_e2e import (
    FakeAnthropicClient,
    _FakeFinalMessage,
    _FakeUsage,
    _text_block,
    _tool_use_block,
)
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401


def switch_to_elsewhere(client) -> dict:
    """The person's other tab: save the open network as ``Elsewhere`` and make it active.

    Returns ``lc43_gates.state`` of the new active project, read right after the switch.
    """
    response = client.post(
        "/api/projects/Elsewhere", params={"force": True, "rebind": True}
    )
    assert response.status_code == 200, response.text
    assert PyPSAService.get_active_context().loaded_project == "Elsewhere"
    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, "Elsewhere")
        root = project_registry.project_dir(project)
    return {"root": root, "state": state(root)}


def saved_g1_cost(project_dir) -> float:
    files = sorted(p for p in project_dir.rglob("*.nc") if "uploads" not in p.parts)
    assert files, f"no saved network under {project_dir}"
    return float(pypsa.Network(files[0]).generators.at["G1", "marginal_cost"])


class _SwitchingFakeClient(FakeAnthropicClient):
    """A scripted provider whose first answer arrives after the active project switched."""

    def __init__(self, turns, on_first_call):
        super().__init__(turns)
        self._on_first_call = on_first_call

    def _next_turn(self, **kwargs):
        if self._on_first_call is not None:
            hook, self._on_first_call = self._on_first_call, None
            hook()
        return super()._next_turn(**kwargs)


def test_a_write_after_the_active_project_switched_mid_turn_writes_nothing(
    baseline, client, monkeypatch
):
    _ctx, _project_id, project_dir = baseline
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-EXAMPLE")
    home_uploads = state(project_dir)["uploads"]
    assert saved_g1_cost(project_dir) == 10.0
    switched = {}
    args = {
        "component_class": "Generator",
        "name": "G1",
        "attrs": {"marginal_cost": 50},
    }
    provider = _SwitchingFakeClient(
        [
            (
                [],
                _FakeFinalMessage(
                    content=[_tool_use_block("tu-g1", "update_component", args)],
                    usage=_FakeUsage(),
                ),
            ),
            ([], _FakeFinalMessage(content=[_text_block("ok")], usage=_FakeUsage())),
        ],
        on_first_call=lambda: switched.update(switch_to_elsewhere(client)),
    )

    frames = list(
        chat_service.run_turn(
            chat_service.ChatSession(), "set G1 cost to 50", client=provider
        )
    )

    tool_errors = [p for e, p in frames if e == "tool_error"]
    assert [(p["tool_use_id"], p["error_kind"]) for p in tool_errors] == [
        ("tu-g1", "project_switched_mid_turn")
    ], frames
    assert [p.get("error_kind") for e, p in frames if e == "error"] == [
        "project_switched_mid_turn"
    ], frames
    assert not [p for e, p in frames if e == "tool_result"], frames
    assert state(switched["root"]) == switched["state"]
    assert PyPSAService.get_network().generators.at["G1", "marginal_cost"] == 10.0
    assert saved_g1_cost(project_dir) == 10.0
    assert state(project_dir)["uploads"] == home_uploads


def test_a_delivery_built_while_the_active_project_switched_is_refused(
    baseline, session, client, monkeypatch
):
    _ctx, _project_id, project_dir = baseline
    export = chat_tools.export_network_nc()
    home_uploads = state(project_dir)["uploads"]
    assert export["file_id"] in home_uploads
    switched = {}
    verify = assurance_artifacts.verify_chart

    def switch_while_zipping(*a, **k):
        if not switched:
            switched.update(switch_to_elsewhere(client))
        return verify(*a, **k)

    monkeypatch.setattr(assurance_artifacts, "verify_chart", switch_while_zipping)

    refused(
        lambda: chat_tools.build_delivery([export["file_id"]]), 409, "project_changed"
    )

    assert switched, "the switch never landed inside build_delivery"
    assert state(switched["root"]) == switched["state"]
    # Saving as ``Elsewhere`` copied the project with its one upload, the export; no zip joined it.
    assert switched["state"]["uploads"] == [export["file_id"]]
    assert state(project_dir)["uploads"] == home_uploads
