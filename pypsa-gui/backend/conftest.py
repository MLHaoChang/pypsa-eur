"""
Rootdir conftest for the pypsa-gui backend suite.

Only registers plugins; the suite's fixtures live in tests/conftest.py. A
plugin that adds command-line options has to be loaded from here (or `-p`), not
from tests/conftest.py's `pytest_plugins`, which pytest refuses below rootdir.
"""

pytest_plugins = ["tests._shard"]
