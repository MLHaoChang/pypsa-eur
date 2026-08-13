"""
Improvement #17 — create and delete many components in one call.

Building a 30-bus network meant 30 turns: 30 model round-trips, 30 audit
entries, 30 undo snapshots, and 30 chances for the turn's tool-call cap
(25) to cut the job in half. The cap is the real defect — a task that
needs more calls than a turn allows cannot be completed at all, only
resumed by a user who noticed.

Both tools refuse the WHOLE batch on any bad entry rather than applying
what they can. That follows the existing /_bulk rule, and the reason is
recoverability: a half-created network is not a state the agent can reason
about, and the undo stack unwinds one entry at a time.

What is deliberately not claimed is atomicity. Validation is exhaustive
before the first write, but a failure mid-apply is still partial — so the
error names exactly what landed instead of pretending nothing did.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest
from fastapi import HTTPException

from services import chat_tools


@pytest.fixture
def base(install_network):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2026-01-01", periods=2, freq="h"))
    n.add("Bus", "Hub", v_nom=380)
    install_network(n, name="BatchBase")
    return n


# ── batch create ────────────────────────────────────────────────────────────


def test_creates_every_component_in_one_call(base):
    out = chat_tools.batch_create_components("Bus", [
        {"name": "B1", "v_nom": 380},
        {"name": "B2", "v_nom": 220},
        {"name": "B3", "v_nom": 110},
    ])

    assert out["count"] == 3
    assert out["created"] == ["B1", "B2", "B3"]
    from services.pypsa_service import PyPSAService
    idx = PyPSAService.get_network().buses.index
    assert {"B1", "B2", "B3"}.issubset(set(idx))


def test_one_bad_payload_creates_nothing(base):
    """
    The recoverability rule. A batch that half-applied would leave the
    agent reasoning about a network it never described.
    """
    from services.pypsa_service import PyPSAService
    before = set(PyPSAService.get_network().buses.index)

    with pytest.raises(HTTPException) as exc:
        chat_tools.batch_create_components("Bus", [
            {"name": "Good1", "v_nom": 380},
            {"name": "Bad", "v_nom": "not-a-number"},
            {"name": "Good2", "v_nom": 220},
        ])

    assert exc.value.status_code == 400
    assert set(PyPSAService.get_network().buses.index) == before


def test_the_error_names_the_entry_that_failed(base):
    """An agent that is not told which entry to fix can only retry blind."""
    with pytest.raises(HTTPException) as exc:
        chat_tools.batch_create_components("Bus", [
            {"name": "Fine", "v_nom": 380},
            {"name": "Broken", "v_nom": "xyz"},
        ])

    detail = str(exc.value.detail)
    assert "Broken" in detail
    assert "1" in detail  # its index in the batch


def test_colliding_with_an_existing_name_refuses_the_batch(base):
    with pytest.raises(HTTPException) as exc:
        chat_tools.batch_create_components("Bus", [
            {"name": "New", "v_nom": 380},
            {"name": "Hub", "v_nom": 380},  # already in the network
        ])

    assert exc.value.status_code in (400, 409)
    assert "Hub" in str(exc.value.detail)
    from services.pypsa_service import PyPSAService
    assert "New" not in set(PyPSAService.get_network().buses.index)


def test_a_name_repeated_inside_the_batch_is_refused(base):
    """
    Caught before the first write rather than by the second create failing
    — otherwise entry 1 lands and entry 2 raises, which is the partial
    state this whole design exists to avoid.
    """
    with pytest.raises(HTTPException) as exc:
        chat_tools.batch_create_components("Bus", [
            {"name": "Twin", "v_nom": 380},
            {"name": "Twin", "v_nom": 220},
        ])

    assert exc.value.status_code == 400
    assert "Twin" in str(exc.value.detail)
    from services.pypsa_service import PyPSAService
    assert "Twin" not in set(PyPSAService.get_network().buses.index)


def test_an_entry_with_no_name_is_refused(base):
    with pytest.raises(HTTPException) as exc:
        chat_tools.batch_create_components("Bus", [{"v_nom": 380}])
    assert exc.value.status_code == 400


def test_an_empty_batch_is_refused(base):
    with pytest.raises(HTTPException) as exc:
        chat_tools.batch_create_components("Bus", [])
    assert exc.value.status_code == 400


def test_an_unknown_class_is_refused(base):
    with pytest.raises(HTTPException) as exc:
        chat_tools.batch_create_components("Reactor", [{"name": "R1"}])
    assert exc.value.status_code == 400


def test_an_oversized_batch_is_refused(base):
    """
    A bound exists so one tool call cannot wedge the event loop or blow the
    undo stack. Refusing is right here — unlike a read, silently doing part
    of a write is the failure mode.
    """
    too_many = [{"name": f"B{i}", "v_nom": 380}
                for i in range(chat_tools.MAX_BATCH_SIZE + 1)]
    with pytest.raises(HTTPException) as exc:
        chat_tools.batch_create_components("Bus", too_many)
    assert exc.value.status_code == 400
    assert str(chat_tools.MAX_BATCH_SIZE) in str(exc.value.detail)


def test_dedicated_create_logic_still_runs(base):
    """
    Routing through create_component rather than n.add keeps the per-class
    handlers — carrier auto-create, line haversine fill, transformer
    voltage validation — which a batch path writing rows directly would
    quietly skip.
    """
    chat_tools.batch_create_components("Bus", [
        {"name": "L1", "v_nom": 380, "x": 10.0, "y": 50.0},
        {"name": "L2", "v_nom": 380, "x": 11.0, "y": 51.0},
    ])
    out = chat_tools.batch_create_components("Line", [
        {"name": "Ln", "bus0": "L1", "bus1": "L2", "x": 0.1, "r": 0.01},
    ])

    assert out["count"] == 1
    from services.pypsa_service import PyPSAService
    # create_line auto-fills length from the bus coordinates.
    assert float(PyPSAService.get_network().lines.at["Ln", "length"]) > 0


# ── batch delete ────────────────────────────────────────────────────────────


def test_deletes_every_named_component(base):
    chat_tools.batch_create_components("Bus", [
        {"name": f"D{i}", "v_nom": 380} for i in range(3)
    ])

    out = chat_tools.batch_delete_components("Bus", ["D0", "D1", "D2"])

    assert out["count"] == 3
    from services.pypsa_service import PyPSAService
    idx = set(PyPSAService.get_network().buses.index)
    assert not {"D0", "D1", "D2"} & idx


def test_one_missing_name_deletes_nothing(base):
    chat_tools.batch_create_components("Bus", [{"name": "Keep", "v_nom": 380}])

    with pytest.raises(HTTPException) as exc:
        chat_tools.batch_delete_components("Bus", ["Keep", "Ghost"])

    assert exc.value.status_code == 404
    assert "Ghost" in str(exc.value.detail)
    from services.pypsa_service import PyPSAService
    assert "Keep" in set(PyPSAService.get_network().buses.index)


def test_an_empty_delete_batch_is_refused(base):
    with pytest.raises(HTTPException) as exc:
        chat_tools.batch_delete_components("Bus", [])
    assert exc.value.status_code == 400


# ── the confirmation gate (#19 hook) ────────────────────────────────────────


def test_batch_delete_is_destructive(base):
    from services import chat_service
    assert chat_service._safety_tier_for("batch_delete_components") == "destructive"
    assert chat_service._safety_tier_for("batch_create_components") == "write"


def test_a_doomed_batch_delete_never_reaches_the_confirmation_card(base):
    """
    The #19 rule applied to the widest-blast-radius tool in the set: do not
    make someone approve deleting thirty components when one of the names
    is wrong and the call will 404 either way.
    """
    chat_tools.batch_create_components("Bus", [{"name": "Real", "v_nom": 380}])

    session = chat_service_session()
    collected: list[dict] = []
    frames = list(_dispatch(session, collected, {
        "id": "tu-1", "name": "batch_delete_components",
        "input": {"component_class": "Bus", "names": ["Real", "Ghost"]},
    }))

    events = [e for e, _ in frames]
    assert "tool_pending_confirmation" not in events
    kinds = [p.get("error_kind") for e, p in frames if e == "tool_error"]
    assert kinds == ["invalid_tool_args"]
    assert "Ghost" in str(collected[0]["content"])


def chat_service_session():
    from services import chat_service
    return chat_service.ChatSession()


def _dispatch(session, collected, tu):
    from services import chat_service
    return chat_service._dispatch_real_tool_call(session, tu, collected)
