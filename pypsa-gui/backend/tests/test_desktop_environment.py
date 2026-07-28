"""
The desktop environment must run the stack the suite tested (phase 2a, Task 0).

`pixi run -e desktop` is how the shell runs; the backend suite runs in
`default`. If the two resolve different packages, the app that ships is not the
app that was tested — and the divergence is invisible, because every test still
passes in `default`.

Not hypothetical. `desktop` was added solved freely, and a fresh solve of a new
environment does not reproduce an old lock: it takes today's newest.
`[exclude-newer] pypsa = "0d"` in `pixi.toml` deliberately lets pypsa float, so
`desktop` came up on **pypsa 1.2.4** against a suite validated on 1.1.2, plus
numpy, scipy and **highspy** — the LP solver PyPSA calls. `CLAUDE.md` documents
multi-port Link vintage workarounds scoped to PyPSA 1.1.2 that took five
re-solves to converge on.

Two fixes that did NOT work, recorded so they are not retried:

  * Pinning only `python`. The interpreter was 1 of 112 differing packages.
  * A pixi `solve-group`. It does make them agree — by re-solving `default` UP
    to pypsa 1.2.4 and highspy 1.15.1, i.e. upgrading the environment
    `snakemake -call` runs PyPSA-Eur in, across 228 lines. Measured, then
    reverted. The model environment is not this workstream's to modernise.

**What is enforced here is the CORE stack, not full parity.** ~100 further
transitive packages (bokeh, beautifulsoup4, asttokens, …) sit on newer patch
versions in `desktop`. None is imported by the backend — measured against its
actual import set — and closing that gap requires the solve-group above. That
residual is a deliberate, recorded trade-off rather than an oversight.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_LOCK = Path(__file__).resolve().parents[3] / "pixi.lock"

# Every platform `pixi.toml` targets. A platform missing from the lock fails
# rather than skips — a skip here is the test quietly evaporating.
_PLATFORMS = ("osx-arm64", "osx-64", "win-64", "linux-64")

# The packages whose version changes backend behaviour. Kept in step with the
# pins in `[feature.desktop.*]`; both sides are load-bearing and this list is
# what makes forgetting one a test failure instead of a silent regression.
_CORE = (
    "pypsa",            # the model itself
    "numpy", "scipy", "pandas", "xarray",
    "highspy", "linopy",        # the LP path
    "sqlalchemy", "alembic",    # persistence + migrations
    "fastapi", "starlette", "pydantic", "pydantic-settings",
    # uvicorn especially: the desktop shell's entire shutdown design was
    # verified by reading uvicorn 0.51.0's `Server.shutdown` and
    # `_wait_tasks_to_complete`. A different uvicorn is a different contract.
    "uvicorn",
    "netcdf4",
    "matplotlib", "matplotlib-base",   # MPLBACKEND=Agg is set against these
    "python",
)


def _packages(env: str, platform: str) -> tuple[dict[str, str], set[str]]:
    """
    `({conda name: version}, {pypi names})` for one environment/platform.

    Parsed by hand rather than with PyYAML: the lock is ~52 000 lines and only
    its `environments:` header block is needed.
    """
    conda: dict[str, str] = {}
    pypi: set[str] = set()
    in_environments = False
    cur_env: str | None = None
    in_packages = False
    cur_platform: str | None = None

    for line in _LOCK.read_text().splitlines():
        if line.startswith("environments:"):
            in_environments = True
            continue
        if in_environments and line and not line[0].isspace():
            break  # left the environments block
        if not in_environments:
            continue

        m = re.match(r"^  (\S+):$", line)
        if m:
            cur_env, in_packages, cur_platform = m.group(1), False, None
            continue
        if re.match(r"^    packages:$", line):
            in_packages = True
            continue
        if re.match(r"^    \S+:$", line):
            in_packages = False  # `channels:` and friends
            continue
        if not (in_packages and cur_env == env):
            continue

        m = re.match(r"^      (\S+):$", line)
        if m:
            cur_platform = m.group(1)
            continue
        if cur_platform != platform:
            continue

        m = re.match(r"^      - conda: (\S+)$", line)
        if m:
            stem = re.sub(r"\.(conda|tar\.bz2)$", "", m.group(1).rsplit("/", 1)[-1])
            parts = stem.rsplit("-", 2)
            if len(parts) == 3:
                conda[parts[0]] = parts[1]
            continue
        m = re.match(r"^      - pypi: (\S+)$", line)
        if m:
            name = m.group(1).rsplit("/", 1)[-1].split("-")[0].lower().replace("_", "-")
            pypi.add(name)
    return conda, pypi


def test_the_lockfile_parser_actually_finds_packages():
    """
    Guards the guard. Every assertion below has the shape "nothing differs",
    which a parser that silently matches nothing satisfies perfectly. A
    hand-run comparison during this task reported "0 differences" for exactly
    that reason — a `join` that matched almost no rows — and was nearly
    believed. So the parser proves it found real data first.
    """
    conda, _ = _packages("default", "osx-arm64")

    assert len(conda) > 400, f"parser found only {len(conda)} conda packages"
    for name in ("pypsa", "numpy", "uvicorn", "python"):
        assert name in conda, f"{name} missing — the parser is not reading the lock"
    assert re.match(r"^\d+\.\d+", conda["pypsa"]), conda["pypsa"]


@pytest.mark.parametrize("platform", _PLATFORMS)
def test_the_core_stack_is_identical_in_both_environments(platform):
    """
    The invariant the shell depends on. Runs against `pixi.lock` rather than
    the installed environment, so it holds for all four platforms — including
    the two nobody working on this can run.
    """
    default, _ = _packages("default", platform)
    desktop, _ = _packages("desktop", platform)

    assert default, f"no `default` packages parsed for {platform}"
    assert desktop, f"no `desktop` packages parsed for {platform}"

    differing = {
        name: (default[name], desktop.get(name))
        for name in _CORE
        if name in default and default[name] != desktop.get(name)
    }

    assert not differing, (
        f"{len(differing)} core package(s) differ on {platform} — the shell would "
        "run code the suite never tested. Pin them in `[feature.desktop.*]`: "
        + ", ".join(
            f"{n} default={d} desktop={k}" for n, (d, k) in sorted(differing.items())
        )
    )


@pytest.mark.parametrize("platform", _PLATFORMS)
def test_every_core_package_is_actually_present(platform):
    """
    The parity assertion above is vacuously true for a package absent from
    BOTH environments — including one renamed upstream, or misspelled in
    `_CORE`. Without this, a typo silently removes something from the guard.
    """
    default, _ = _packages("default", platform)

    missing = [name for name in _CORE if name not in default]

    assert not missing, (
        f"not in `default` on {platform}: {missing} — either the package was "
        "renamed upstream or `_CORE` has a typo; both make the parity test blind"
    )


@pytest.mark.parametrize("platform", _PLATFORMS)
def test_the_desktop_environment_carries_pywebview(platform):
    """
    Parity alone is satisfied by a `desktop` that is a byte-for-byte copy of
    `default`, which would not run the shell at all. This is the other half.

    Asserted on the pypi side because that is where pywebview lives on every
    platform; its conda-side extras differ (pyobjc on darwin, nothing on
    win-64, where pythonnet also arrives through pypi).
    """
    _, desktop_pypi = _packages("desktop", platform)
    _, default_pypi = _packages("default", platform)

    assert "pywebview" in desktop_pypi, (
        f"`desktop` has no pywebview on {platform}: {sorted(desktop_pypi)}"
    )
    assert "pywebview" not in default_pypi, (
        "pywebview leaked into `default` — the GUI toolkit must stay out of the "
        "environment `snakemake -call` solves PyPSA-Eur with"
    )
