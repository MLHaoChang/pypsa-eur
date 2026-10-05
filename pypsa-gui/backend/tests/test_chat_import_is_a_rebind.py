"""
A raw import (.nc / .csv / .xlsx / .m) is a switch to an UNBOUND project.

The UI has always treated it as one: `ImportExport.tsx` clears the active
project after a raw import "so the 5-min autosave ... can't CLAIM and overwrite
the previously-active project's folder with this freshly-imported network".
Two surfaces did not:

* **Chat.** The import tools were not in `PROJECT_REBINDING_TOOLS`, so the
  import switched the binding the turn was pinned to and the VERY NEXT tool was
  refused as `project_switched_mid_turn`; no `project_rebound` frame reached the
  panel. The panel kept the old project name, and its autosave sent
  `expect=<old>` to a backend that was now unbound — which the save guard lets
  through, because its identity check only fires against a BOUND backend. The
  imported network was then written over the old project's folder. Measured in
  local mode: one bus imported over a three-bus project, the guard passed.

* **HTTP, in server mode.** The import routes never moved the session's
  active-project pointer. `reset_network` put the imported network in the
  session's scratch slot, but each request re-resolves from the pointer, so the
  very next request got the OLD project back: a 200 describing a network nobody
  could see. Measured: import 1 bus over 3, GET /network/buses, 3 buses.
"""
from __future__ import annotations

import base64
import io
import pathlib
import tempfile

import pypsa
import pytest

from services import chat_service, chat_tools
from services.pypsa_service import PyPSAService
from tests.test_chat_e2e import (
    FakeAnthropicClient, _FakeFinalMessage, _FakeUsage, _text_block,
    _text_event, _tool_use_block, _tool_use_event,
)


def _nc_bytes(*buses: str) -> bytes:
    n = pypsa.Network()
    for b in buses:
        n.add("Bus", b)
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "x.nc"
        n.export_to_netcdf(p)
        return p.read_bytes()


def _three_bus():
    n = pypsa.Network()
    for b in ("X1", "X2", "X3"):
        n.add("Bus", b)
    return n


# ── Chat: the turn follows the import, and says so ───────────────────────────


@pytest.fixture
def auto_approve(monkeypatch):
    monkeypatch.setattr(chat_service, "AUTO_APPROVE_TIERS",
                        frozenset({"write", "destructive"}))


def test_a_chat_import_rebinds_the_turn_instead_of_stopping_it(
    tmp_projects_dir, install_network, auto_approve, monkeypatch,
):
    install_network(_three_bus(), name="X")
    seen: list[list[str]] = []
    real_list = chat_tools.DISPATCHERS["list_components"]

    def _list(**kwargs):
        seen.append(list(PyPSAService.get_network().buses.index))
        return real_list(**kwargs)

    monkeypatch.setitem(chat_tools.DISPATCHERS, "list_components", _list)
    imp = {"bytes_b64": base64.b64encode(_nc_bytes("IMP0")).decode(),
           "filename": "other.nc"}
    lst = {"component_class": "Bus"}
    client = FakeAnthropicClient([
        ([_tool_use_event("tu-i", "import_network_nc", imp),
          _tool_use_event("tu-l", "list_components", lst)],
         _FakeFinalMessage(content=[
             _tool_use_block("tu-i", "import_network_nc", imp),
             _tool_use_block("tu-l", "list_components", lst)],
             usage=_FakeUsage())),
        ([_text_event("ok.")],
         _FakeFinalMessage(content=[_text_block("ok.")], usage=_FakeUsage())),
    ])

    events = list(chat_service.run_turn(
        chat_service.ChatSession(), "import it", client=client))

    kinds = [p.get("error_kind") for ev, p in events if ev in ("error", "tool_error")]
    assert "project_switched_mid_turn" not in kinds, (
        "the agent's own import was treated as an external project switch and "
        "the rest of the turn was refused"
    )
    rebounds = [p for ev, p in events if ev == "project_rebound"]
    assert rebounds == [{"from": "X", "to": None, "via_tool": "import_network_nc"}], (
        "without this frame the panel keeps 'X' and its autosave writes the "
        "imported network over X's folder"
    )
    assert seen == [["IMP0"]], "the next tool in the turn must see the import"


def test_every_raw_import_tool_is_a_rebinding_tool():
    """The four raw imports, the bundle and the template all switch the
    binding; a tool that does so without being listed stops the turn."""
    expected = {"import_network_nc", "import_csv_bundle", "import_excel",
                "import_matpower", "import_project_bundle",
                "create_project_from_template"}
    missing = expected - set(chat_service.PROJECT_REBINDING_TOOLS)
    assert not missing, f"not treated as rebinds: {sorted(missing)}"


# ── HTTP, server mode: the import is what the next request sees ──────────────


def _import(client, data: bytes, name: str = "other.nc"):
    return client.post(
        "/api/io/import/netcdf",
        files={"file": (name, io.BytesIO(data), "application/octet-stream")},
    )


def _buses(client) -> list[str]:
    return [b["name"] for b in client.get("/api/network/buses").json()]


@pytest.fixture
def on_project_x(client, tmp_projects_dir, install_network):
    install_network(_three_bus(), name=None)
    assert client.post("/api/projects/X").status_code == 200
    assert client.post("/api/projects/X/activate").status_code == 200
    assert _buses(client) == ["X1", "X2", "X3"]
    return client


def test_an_http_import_is_what_the_next_request_sees(on_project_x):
    client = on_project_x
    r = _import(client, _nc_bytes("IMP0"))
    assert r.status_code == 200
    assert r.json()["buses"] == 1

    assert _buses(client) == ["IMP0"], (
        "the import reported 1 bus and the next request served the previous "
        "project's 3: the session pointer still named it"
    )
    assert client.get("/api/network/meta").json()["loaded_project"] is None


def test_a_refused_import_leaves_the_session_on_its_project(on_project_x):
    """Un-pointing happens on SUCCESS only. A refusal raises after the reset —
    which landed in the scratch slot — so the project is still resident and
    still what the session sees."""
    client = on_project_x
    r = _import(client, _nc_bytes("ic:reserved"))
    assert r.status_code == 422

    assert _buses(client) == ["X1", "X2", "X3"]
    assert client.get("/api/network/meta").json()["loaded_project"] == "X"


def test_the_chat_import_tool_moves_the_acting_sessions_pointer(
    on_project_x, _auth_db, seeded_identity,
):
    """The chat wrapper calls the async route directly, so it must supply the
    acting db and session itself — otherwise the route gets `Depends`
    sentinels, or nothing, and the pointer stays put."""
    import uuid

    from db.models import Session as SessionRow
    from services import active_project

    client = on_project_x
    _engine, session_local = _auth_db
    with session_local() as db:
        row = (db.query(SessionRow)
               .filter(SessionRow.user_id == uuid.UUID(str(seeded_identity["user_id"])))
               .filter(SessionRow.active_project_id.isnot(None))
               .one())
        # Bound exactly as `deps.bind_active_project` binds a request, which is
        # what a chat turn's tools run inside.
        ctx, slot = active_project.resolve_for_session(db, row)
        tokens = (
            PyPSAService.bind_request_context(ctx),
            PyPSAService._request_slot.set(slot),
            PyPSAService._request_scratch.set(active_project.scratch_key(row)),
        )
        session_id = row.id

    chat_tools.set_acting_user(seeded_identity["user_id"])
    chat_tools.set_acting_session(session_id)
    try:
        chat_tools.import_network_nc(
            base64.b64encode(_nc_bytes("IMP0")).decode(), "other.nc")
    finally:
        chat_tools.set_acting_user(None)
        chat_tools.set_acting_session(None)
        PyPSAService._request_scratch.reset(tokens[2])
        PyPSAService._request_slot.reset(tokens[1])
        PyPSAService.reset_request_context(tokens[0])

    assert _buses(client) == ["IMP0"]
