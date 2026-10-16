import pytest

from gridspine.static.parallel import WORKERS_ENV


@pytest.fixture(autouse=True)
def _serial_island_checks(monkeypatch):
    """The island checks run in this process (``parallel.ordered_map`` at
    one worker), so a test's monkeypatch reaches them; a test of the worker
    processes sets its own count."""
    monkeypatch.setenv(WORKERS_ENV, "1")
