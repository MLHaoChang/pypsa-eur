"""Implemented cases always run; only three pending artifact cases are opt-in."""

import os

import pytest

from services import chat_service
from tests.assurance_acceptance.support import Harness
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401


def pytest_collection_modifyitems(items):
    if os.environ.get("PYPSA_GUI_TEST_ASSURANCE_ACCEPTANCE") == "1":
        return
    marker = pytest.mark.skip(
        reason="Artifact assurance is not implemented; opt in to run its RED baseline."
    )
    pending = {
        "test_chart_binds_exact_input_data_units_render_and_image_bytes",
        "test_legacy_storage_delivery_rejects_tampered_chart_provenance",
        "test_delivery_preserves_the_chart_assurance_chain",
    }
    for item in items:
        if "/assurance_acceptance/" in str(item.path) and item.name in pending:
            item.add_marker(marker)


@pytest.fixture(autouse=True)
def strict_server(monkeypatch):
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "strict")


@pytest.fixture
def harness(client, monkeypatch):
    value = Harness(client, monkeypatch)
    yield value
    for session_id in value.session_ids:
        chat_service.drop_session(session_id)
