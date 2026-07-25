"""
Marks `pypsa-gui/backend/tests` as a real package.

Without this file `tests` is not a package here, so `from tests.conftest import
build_network` (used across the chat suites) resolves through sys.path to
whatever else happens to expose a top-level `tests` module. That is not
hypothetical: cryptography 46.0.7 ships its own `tests/` directory into
site-packages, and the moment the environment picked that version up, every
`from tests.conftest import ...` hit cryptography's test helpers instead and
collection died with `ModuleNotFoundError: No module named
'cryptography_vectors'`.

conftest.py inserts the backend directory at sys.path[0], so with this file
present `tests` unambiguously resolves to THIS package regardless of what a
dependency drops into site-packages.

Intentionally empty otherwise — pytest still discovers `test_*.py` normally and
`pytest.ini`'s `python_files = test_*.py` keeps the standalone `qa_*.py`
scripts uncollected.
"""
