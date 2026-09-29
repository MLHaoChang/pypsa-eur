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

The `doc` environment needed its own bound. It sets `no-default-feature = true`,
so the root spec cannot reach it, and every platform of it was locked on pandas
3.0.3 — while `.readthedocs.yml` installs it and builds the docs in it on every
commit. `[feature.doc.dependencies]` now carries the same bound and that
environment resolved to 2.3.3. No environment is exempt.

Drop the bound when xarray's netCDF writer understands the pandas string dtype,
or when PyPSA casts before handing the dataset over. Re-check on every xarray
bump; the three lines above make that a seconds-long check.
"""

import pathlib
import re
import sys
import tomllib

REPO = pathlib.Path(__file__).resolve().parent.parent

#: Environments allowed to resolve pandas 3. EMPTY, and that is the point.
#:
#: `doc` used to be here, on the grounds that it sets `no-default-feature = true`
#: (so the root bound cannot reach it), does not list pandas at all, never exports
#: a network, and "is built by no CI job". That last clause was true and
#: misleading: `.readthedocs.yml` runs `pixi install -e default -e doc --frozen`
#: and then `pixi run build-docs` on EVERY commit, in exactly that environment,
#: and the build imports `scripts/` through mkdocstrings and reads tables through
#: pandas. Every platform of it was locked on pandas 3.0.3.
#:
#: So it is bounded explicitly in `[feature.doc.dependencies]` instead, and that
#: environment re-resolved to 2.3.3 on all four platforms. The exemption
#: mechanism is kept — `_exempt` — because the next environment that genuinely
#: cannot be bounded should be recorded here by name rather than by deleting a
#: test; its behaviour is covered with a synthetic name below.
UNBOUNDED_ENVIRONMENTS: set[str] = set()


#: `<3` followed by anything but another digit. A substring test for "<3" — which
#: is what this started as — also accepts `<30` and `<3000`, both of which permit
#: pandas 3 and neither of which looks wrong at a glance.
_BOUNDED_BELOW_3 = re.compile(r"<\s*=?\s*3(?!\d)")


def _excludes_pandas_3(spec: str) -> bool:
    """True when `spec` cannot resolve to a pandas 3.x release.

    `<=3` is deliberately NOT accepted: it admits 3.0.0 itself.
    """
    compact = spec.replace(" ", "")
    if "<=3" in compact or "<=3." in compact:
        return False
    return bool(_BOUNDED_BELOW_3.search(compact))


def _declared_pandas_specs() -> dict[str, str]:
    """`{where: spec}` for every pandas constraint in `pixi.toml`.

    Not just `[dependencies]`: a spec in a feature table is what actually
    constrains a feature-built environment, and `[feature.doc.dependencies]` is
    precisely where pandas 3 enters this workspace. A check that reads one table
    passes happily while a `[feature.*]` entry next door pins pandas 3.
    """
    manifest = tomllib.loads((REPO / "pixi.toml").read_text(encoding="utf-8"))
    found = {}
    for table in ("dependencies", "pypi-dependencies"):
        if "pandas" in manifest.get(table, {}):
            found[table] = manifest[table]["pandas"]
    for feature, body in manifest.get("feature", {}).items():
        for table in ("dependencies", "pypi-dependencies"):
            if "pandas" in body.get(table, {}):
                found[f"feature.{feature}.{table}"] = body[table]["pandas"]
    return found


def test_the_pandas_dependency_declares_an_upper_bound_below_3():
    specs = _declared_pandas_specs()
    assert "dependencies" in specs, "pixi.toml no longer declares pandas at all"
    for where, spec in specs.items():
        if where.startswith("feature.") and where.split(".")[1] in UNBOUNDED_ENVIRONMENTS:
            continue
        assert isinstance(spec, str), f"{where}: pandas spec is not a plain string: {spec!r}"
        assert _excludes_pandas_3(spec), (
            f"{where} declares pandas as {spec!r}, which permits pandas 3. "
            "xarray's netCDF writer cannot handle pandas 3's default string dtype, so "
            "such a spec lets a resolver produce an environment in which the GUI "
            "cannot save a project. See this module's docstring."
        )


def test_the_bound_check_is_not_a_substring_test():
    """`"<3" in spec` — the first version of this — accepts every one of the
    third group below, and each of them resolves pandas 3."""
    for good in (">=2.1,<3", ">=2.1, <3", ">=2.1,<3.0", ">=2.1,<3.0.0", "<3"):
        assert _excludes_pandas_3(good), good
    for bad in (">=2.1", "*", ">=2.1,<4", ">=3"):
        assert not _excludes_pandas_3(bad), bad
    for looks_bounded in (">=2.1,<30", ">=2.1,<3000", ">=2.1,<=3", ">=2.1,<=3.0"):
        assert not _excludes_pandas_3(looks_bounded), looks_bounded


#: An environment name at the `environments:` block's first level.
_ENVIRONMENT = re.compile(r"^  ([A-Za-z][\w-]*):$")
#: A platform key under `packages:` inside one environment. `\w+-\d+` — what this
#: was — cannot match `osx-arm64`, because `-` is not a word character and `arm64`
#: does not start with a digit. That platform's package lines were therefore
#: attributed to whichever platform came before it (osx-64, alphabetically), so a
#: pandas 3 entry on osx-arm64 was reported against osx-64 — and an environment
#: that listed osx-arm64 FIRST, or alone, would have had its pandas dropped from
#: the scan entirely while the "parsed no pandas entries" assertion still passed.
_PLATFORM = re.compile(r"^      ([a-z][a-z0-9]*(?:-[a-z0-9]+)+):$")
_PANDAS_BUILD = re.compile(r"/pandas-(\d[^-]*)-")


def _parse_locked(text: str) -> dict[tuple[str, str], set[str]]:
    """`{(environment, platform): {version, ...}}` from the text of a `pixi.lock`.

    Pure, so the parser itself can be tested — it was the untested half of this
    guard, and the half that had a bug. Read out of the `environments:` block
    rather than the package list, because that list is shared across environments
    and says nothing about which environment uses a given build. The block is cut
    at the next top-level key: slicing to EOF left the package list in scope,
    where `  depends:` and `  purls:` match the environment-name shape.
    """
    start = text.index("\nenvironments:") + 1
    rest = text[start + len("environments:"):]
    end = re.search(r"\n(?=[A-Za-z]\w*:)", rest)
    block = rest[: end.start()] if end else rest

    found: dict[tuple[str, str], set[str]] = {}
    environment = platform = None
    for line in block.splitlines():
        named = _ENVIRONMENT.match(line)
        if named:
            environment, platform = named.group(1), None
            continue
        on_platform = _PLATFORM.match(line)
        if on_platform:
            platform = on_platform.group(1)
            continue
        version = _PANDAS_BUILD.search(line)
        if version and environment and platform:
            found.setdefault((environment, platform), set()).add(version.group(1))
    return found


def _locked_pandas_versions() -> dict[tuple[str, str], set[str]]:
    return _parse_locked((REPO / "pixi.lock").read_text(encoding="utf-8"))


SYNTHETIC_LOCK = """version: 6
environments:
  default:
    channels:
    - url: https://conda.anaconda.org/conda-forge/
    packages:
      linux-64:
      - conda: https://conda.anaconda.org/conda-forge/linux-64/pandas-2.3.3-py313h.conda
      osx-arm64:
      - conda: https://conda.anaconda.org/conda-forge/osx-arm64/pandas-3.0.3-py313h.conda
      win-64:
      - conda: https://conda.anaconda.org/conda-forge/win-64/pandas-2.3.3-py313h.conda
packages:
- conda: https://conda.anaconda.org/conda-forge/win-64/pandas-9.9.9-py313h.conda
  depends:
  - numpy
  purls: []
- conda: https://conda.anaconda.org/conda-forge/linux-64/pandas-2.3.3-py313h.conda
  depends:
  - numpy
  purls: []
"""
#: The 9.9.9 build is the discriminator, and it is FIRST in the trailing
#: `packages:` list on purpose. It belongs to no environment, so it must not
#: appear in the parse — and slicing the block to EOF (which is what this did)
#: leaves the last platform key in scope and attributes it to win-64. It has to
#: come before the first `  depends:` line to show that: two spaces and a name is
#: also the shape of an environment header, so `depends:` silently becomes the
#: "current environment" and hides every package entry after it.


def test_the_lockfile_parser_sees_every_platform_including_osx_arm64():
    """The parser had no test, and a regex bug in it. `osx-arm64` is in this
    workspace's `pixi.lock` four times over, and it was invisible."""
    parsed = _parse_locked(SYNTHETIC_LOCK)
    assert parsed == {
        ("default", "linux-64"): {"2.3.3"},
        ("default", "osx-arm64"): {"3.0.3"},
        ("default", "win-64"): {"2.3.3"},
    }
    # The `packages:` list after the block must not be read as an environment —
    # its `depends:` and `purls:` lines have the same shape as an environment name.
    assert all(environment == "default" for environment, _platform in parsed)


def test_the_real_lockfile_is_parsed_per_platform_for_every_environment():
    """The regression this closes, stated against the real file: four platforms
    per environment, not three."""
    locked = _locked_pandas_versions()
    platforms = {platform for _environment, platform in locked}
    assert {"linux-64", "osx-64", "osx-arm64", "win-64"} <= platforms, sorted(platforms)


def _exempt(environment: str) -> bool:
    """Whether `environment` is allowed to resolve pandas 3. One place, so the
    policy is visible and the mechanism stays tested if the set ever empties."""
    return environment in UNBOUNDED_ENVIRONMENTS


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
        if not _exempt(environment)
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
    # NOTHING is exempt today, `doc` included — it is bounded in its own feature
    # table now rather than waved through here.
    assert _pandas_3_offenders({("doc", "linux-64"): {"3.0.3"}}) == {
        ("doc", "linux-64"): ["3.0.3"]
    }


def test_the_exemption_mechanism_still_works_for_whatever_needs_it_next(monkeypatch):
    """`UNBOUNDED_ENVIRONMENTS` is empty, so without this the exemption path is
    dead code that would be discovered broken the day something needs it."""
    monkeypatch.setattr(sys.modules[__name__], "UNBOUNDED_ENVIRONMENTS", {"doc"})
    assert _pandas_3_offenders({("doc", "linux-64"): {"3.0.3"}}) == {}
    assert _pandas_3_offenders({("default", "linux-64"): {"3.0.3"}}) == {
        ("default", "linux-64"): ["3.0.3"]
    }


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
