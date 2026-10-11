"""Checked-answer acceptance cases run by default."""

import pytest

from services import chat_service
from tests.assurance_acceptance.support import Harness
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401


@pytest.fixture(autouse=True)
def strict_server(monkeypatch):
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "strict")


@pytest.fixture
def harness(client, monkeypatch):
    value = Harness(client, monkeypatch)
    yield value
    for session_id in value.session_ids:
        chat_service.drop_session(session_id)
