"""gridspine is an installed distribution, not a directory that happens to be
on the path (increment-4 decision D5, taken in increment 5).

Before this, `import gridspine` worked only from the repository root — the
pypsa-gui backend runs from `pypsa-gui/backend` and reached the package
through a `sys.path` insert in its gridspine service, which the desktop build
could not reproduce. Now pixi installs the root `pyproject.toml` editable into
every environment, so the import is location-independent and the source tree
is still what runs. These tests hold both halves: the distribution resolves,
and it resolves to THIS checkout.
"""
import importlib.metadata
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import gridspine

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_gridspine_is_an_installed_distribution_at_the_pyproject_version():
    declared = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]
    assert declared["name"] == "gridspine"
    assert importlib.metadata.version("gridspine") == declared["version"]


def test_the_installed_package_is_this_checkout_not_a_copy():
    """Editable: the files under test are the files that run. A wheel copied
    into site-packages would pass the import test and silently test stale code."""
    assert Path(gridspine.__file__).resolve() == (REPO_ROOT / "gridspine" / "__init__.py").resolve()


def test_import_does_not_depend_on_the_working_directory(tmp_path):
    """The point of D5: run from a directory that is not the repo root, with
    no PYTHONPATH, and the import still resolves to this checkout."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    out = subprocess.run(
        [sys.executable, "-c", "import gridspine, sys; print(gridspine.__file__)"],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=True,
    )
    assert Path(out.stdout.strip()).resolve() == (REPO_ROOT / "gridspine" / "__init__.py").resolve()
