# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""
pandas 3 breaks this stack, so the constraint and the lockfile must both say so.

The failure is NOT in this repository. pandas 3 makes `StringDtype(na_value=nan)`
the default string dtype, and xarray 2025.6.1's netCDF writer rejects it at three
separate points:

    xarray/conventions.py:306       np.issubdtype(dtype, np.datetime64) -> TypeError
    xarray/backends/netCDF4_.py:172 _nc4_dtype: "unsupported dtype ...: str"
    xarray/coding/strings.py:34     check_vlen_dtype: StringDtype has no .metadata

Every `Network.export_to_netcdf` goes through that path, which is how one root
cause produced `484 failed, 3803 passed, 296 errors` on a full `pixi update`.
Neither PyPSA 1.1.2 nor linopy 0.8.0 declares a pandas upper bound, so a
resolver is free to pair them with pandas 3 — and did: before the bound, every
`win-64` environment in `pixi.lock` was on pandas 3.0.3 with that xarray, while
linux-64 and osx-64 were on 2.3.3. CI runs only linux and macos, so nothing
caught it.

These tests exist so that whoever next relaxes the constraint gets a sentence
instead of 484 mystery failures. The lockfile one is the one that matters: the
spec can be loosened indirectly — a transitive bump, a new feature that re-pins
pandas — without anyone editing the line the manifest test reads. The third test
is its discrimination half, and earns its place: pixi rejects a hand-edited
lockfile before pytest runs, so the lockfile check could not otherwise be shown
to fail on a bad lock, and a guard that has never failed is not a guard.

To reproduce the incompatibility WITHOUT installing pandas 3:

    import pandas as pd
    pd.options.future.infer_string = True   # pandas >= 2.1
    # ... then any Network.export_to_netcdf

Drop the bound when xarray's netCDF writer understands the pandas string dtype,
or when PyPSA casts before handing the dataset over. Re-check on every xarray
bump; the three lines above make that a seconds-long check.
"""

import pathlib
import re
import tomllib

REPO = pathlib.Path(__file__).resolve().parent.parent

#: `doc` sets `no-default-feature = true`, does not list pandas at all (it
#: arrives transitively), never exports a network, and is built by no CI job.
#: It is deliberately left unbounded rather than silently swept in, because
#: bounding it would force a re-resolve of its exact pins for no stated benefit.
UNBOUNDED_ENVIRONMENTS = {"doc"}


def test_the_pandas_dependency_declares_an_upper_bound_below_3():
    manifest = tomllib.loads((REPO / "pixi.toml").read_text(encoding="utf-8"))
    spec = manifest["dependencies"]["pandas"]
    assert isinstance(spec, str), f"pandas spec is no longer a plain string: {spec!r}"
    assert "<3" in spec.replace(" ", ""), (
        f"pandas is declared as {spec!r} with no upper bound below 3. "
        "xarray's netCDF writer cannot handle pandas 3's default string dtype, so "
        "an unbounded spec lets a resolver produce an environment in which the GUI "
        "cannot save a project. See this module's docstring."
    )


def _locked_pandas_versions() -> dict[tuple[str, str], set[str]]:
    """`{(environment, platform): {version, ...}}` from `pixi.lock`.

    Parsed out of the `environments:` block rather than the package list, because
    the package list is shared across environments and says nothing about which
    environment actually uses a given build.
    """
    text = (REPO / "pixi.lock").read_text(encoding="utf-8")
    block = text[text.index("\nenvironments:"):]
    found: dict[tuple[str, str], set[str]] = {}
    environment = platform = None
    for line in block.splitlines():
        named = re.match(r"^  (\w[\w-]*):$", line)
        if named:
            environment, platform = named.group(1), None
            continue
        on_platform = re.match(r"^      (\w+-\d+):$", line)
        if on_platform:
            platform = on_platform.group(1)
            continue
        version = re.search(r"/pandas-(\d[^-]*)-", line)
        if version and environment and platform:
            found.setdefault((environment, platform), set()).add(version.group(1))
    return found


def _pandas_3_offenders(locked: dict[tuple[str, str], set[str]]) -> dict:
    """The environment/platform pairs on pandas 3, `UNBOUNDED_ENVIRONMENTS` aside.

    Pulled out as a pure function so the assertion below can be shown to FAIL on
    a lockfile that carries pandas 3 — see
    `test_the_guard_would_actually_catch_a_pandas_3_lockfile`. Editing the real
    lockfile to prove it is not an option: pixi validates that every referenced
    build exists in the package list, so a hand-edited lock is rejected before
    any test runs, and the guard would look effective without being tested.
    """
    return {
        (environment, platform): sorted(versions)
        for (environment, platform), versions in locked.items()
        if environment not in UNBOUNDED_ENVIRONMENTS
        and any(v.split(".")[0] == "3" for v in versions)
    }


def test_the_guard_would_actually_catch_a_pandas_3_lockfile():
    """The discrimination half. Without it the test below passes because the
    lockfile is clean, which is indistinguishable from passing because the check
    is broken."""
    assert _pandas_3_offenders({("default", "win-64"): {"3.0.3"}}) == {
        ("default", "win-64"): ["3.0.3"]
    }
    # Two-digit majors must not read as 3, and 2.x must not be flagged.
    assert _pandas_3_offenders({("test", "linux-64"): {"2.3.3"}}) == {}
    assert _pandas_3_offenders({("test", "linux-64"): {"30.1.0"}}) == {}
    # `doc` is exempt by name, and only `doc`.
    assert _pandas_3_offenders({("doc", "linux-64"): {"3.0.3"}}) == {}


def test_no_shipped_environment_resolves_pandas_3_on_any_platform():
    """The guard that survives an indirect loosening.

    Per environment AND per platform, because the divergence this catches was
    exactly per-platform: win-64 on pandas 3 while the two platforms CI runs were
    on pandas 2.
    """
    locked = _locked_pandas_versions()
    assert locked, "parsed no pandas entries out of pixi.lock — has its format changed?"

    offenders = _pandas_3_offenders(locked)
    assert not offenders, (
        "these environment/platform pairs resolve pandas 3, in which "
        "Network.export_to_netcdf raises and the GUI cannot save a project:\n  "
        + "\n  ".join(f"{e} / {p}: {v}" for (e, p), v in sorted(offenders.items()))
        + "\nSee this module's docstring for the three xarray call sites."
    )
