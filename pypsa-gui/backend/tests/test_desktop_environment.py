"""
Every environment that runs this code must resolve the same packages.

`pixi run -e desktop` is how the shell runs, `pixi run gui-tests` runs the
backend suite in `default`, and `.github/workflows/test.yaml` runs PyPSA-Eur's
own suites in `test`. If those disagree, the app that ships is not the app
anything validated — and the disagreement is invisible, because every suite
still passes in whichever environment it happens to use.

**How this was arrived at, so the dead ends are not retried.** `desktop` was
originally solved independently, and a fresh solve of a new environment does
not reproduce an old lock: it takes today's newest. `[exclude-newer]` in
`pixi.toml` deliberately lets pypsa, linopy and atlite float. So `desktop` came
up on pypsa 1.2.4 against a suite validated on 1.1.2. Three rounds then tried
to enumerate what mattered:

  1. pin `python`                     -> it was 1 of 112 differing packages
  2. pin a curated "core stack"       -> `anyio` was in the import closure
  3. pin that plus the measured
     import closure                   -> `pillow`, `websockets`, `libsqlite`,
                                         `openssl`, `polars-runtime-32` -- and
                                         win-64 on NETLIB reference BLAS with
                                         no OpenBLAS at all, invisible because
                                         numpy and scipy were pinned to
                                         identical versions

An enumerated list is only as good as the imagination of whoever wrote it. A
solve group makes the environments agree by construction instead, and this file
asserts the result rather than a list.

**A solve group is not free and the first attempt got it wrong**: it covers
`default`, `desktop`, `test` and `dev`. Grouping only `default` and `desktop`
re-solved those two and left `test` behind — and `test` is the environment both
CI gates run in, so the shipping environment ended up with no coverage and CI's
environment ended up 85-105 packages from it. Same defect, relocated.

`doc` is deliberately OUT: it is `no-default-feature = true` with its own pypsa
1.0.3, so it shares nothing and a group would only over-constrain it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_LOCK = Path(__file__).resolve().parents[3] / "pixi.lock"

# Every platform `pixi.toml` targets. A platform missing from the lock fails
# rather than skips — a skip here is the test quietly evaporating.
_PLATFORMS = ("osx-arm64", "osx-64", "win-64", "linux-64")

# Every environment in the solve group. `doc` is excluded by design.
_GROUPED = ("default", "desktop", "test", "dev")

# Packages that MUST be present for the comparison below to mean anything. A
# parity assertion is satisfied perfectly by an empty environment, and by a
# parser that matches nothing.
_MUST_BE_PRESENT = ("pypsa", "highspy", "linopy", "numpy", "scipy", "uvicorn",
                    "sqlalchemy", "python", "libblas")


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
                # version AND build string. Comparing versions alone let a
                # different scipy BINARY through: both environments resolved
                # 1.17.1 while `default` shipped `py313hc753a45_0` and
                # `desktop` `py313h52f5312_1` — a different sha256. The same
                # hole would have hidden a highspy rebuild, and highspy is the
                # LP solver whose build is the one thing that must not move.
                conda[parts[0]] = f"{parts[1]}={parts[2]}"
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
    assert re.match(r"^\d+\.\d+.*=", conda["pypsa"]), conda["pypsa"]


@pytest.mark.parametrize("platform", _PLATFORMS)
@pytest.mark.parametrize("other", ("desktop", "test", "dev"))
def test_every_shared_package_is_identical_across_the_solve_group(platform, other):
    """
    FULL parity — every package the two environments have in common, compared
    by version AND build string. Not a curated list.

    The curated version of this test is why it is now written this way. Three
    rounds enumerated what mattered: first `python`, then a "core stack", then
    the core stack plus the backend's measured import closure. Each round a
    reviewer found something the list had missed, and the last miss was the
    worst — `desktop` resolving NETLIB reference BLAS with no OpenBLAS at all
    on win-64, invisible precisely because numpy and scipy were pinned to
    identical versions. An enumerated list can only ever be as good as the
    imagination of whoever wrote it.

    The solve group makes this assertion cheap to satisfy and expensive to
    break, which is the right way round.
    """
    default, _ = _packages("default", platform)
    desktop, _ = _packages(other, platform)

    for name in _MUST_BE_PRESENT:
        assert name in default, f"{name} missing from `default` on {platform}"
        assert name in desktop, f"{name} missing from `{other}` on {platform}"

    differing = {
        name: (default[name], desktop[name])
        for name in default.keys() & desktop.keys()
        if default[name] != desktop[name]
    }
    assert not differing, (
        f"{len(differing)} package(s) differ on {platform}: "
        + ", ".join(f"{n} default={d} desktop={k}"
                    for n, (d, k) in sorted(differing.items())[:12])
    )

    missing = default.keys() - desktop.keys()
    assert not missing, (
        f"`{other}` is missing {len(missing)} package(s) `default` has on "
        f"{platform}: {sorted(missing)[:12]}"
    )


@pytest.mark.parametrize("platform", _PLATFORMS)
def test_the_optimisation_stack_is_pinned_not_floating(platform):
    """
    The solve group re-solves `default`, so the packages that decide
    optimisation RESULTS have to be held explicitly or a routine re-lock moves
    them. `[exclude-newer]` in `pixi.toml` deliberately lets pypsa, linopy and
    atlite float to the newest release, which is what made an independently
    solved environment land on pypsa 1.2.4 in the first place.
    """
    default, _ = _packages("default", platform)

    for name, version in (
        ("pypsa", "1.1.2"),
        ("highspy", "1.14.0"),   # the LP solver
        ("linopy", "0.8.0"),     # the modelling layer between them
        ("scip", "10.0.2"),      # the OTHER solver — missed on the first pass,
        ("pyscipopt", "6.2.0"),  # while claiming "both are pinned"
        ("atlite", "0.6.1"),     # builds renewable capacity factors
        ("tsam", "2.3.9"),       # temporal aggregation; took a MAJOR jump to
    ):                           # 3.4.1 on win-64 alone while unpinned
        assert default[name].startswith(f"{version}="), f"{name}={default[name]}"


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
