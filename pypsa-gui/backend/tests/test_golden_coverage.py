"""
Exhaustive-by-default: every fixture class, on every surface, is either
COVERED or EXCLUDED with a written reason.

Opt-in was rejected because it reproduces the exact failure being fixed.
Forgetting to opt Links into asset_economics produces SILENCE, and silence is
how the missing Link block shipped. Inverting the default turns an absence into
a decision with a name on it.
"""
from __future__ import annotations

from tests.golden import coverage as cov


def test_every_class_on_every_surface_is_covered_or_excluded():
    holes = []
    for surface in cov.SURFACES:
        covered = cov.COVERAGE.get(surface, set())
        for cls in sorted(cov.FIXTURE_CLASSES):
            if cls in covered:
                continue
            if (surface, cls) in cov.EXCLUSIONS:
                continue
            holes.append(f"{surface} x {cls}")
    assert not holes, (
        "Undeclared surface/class pairs. Either the surface reports this class "
        "(add it to COVERAGE) or it deliberately does not (add it to EXCLUSIONS "
        "with a reason):\n  " + "\n  ".join(holes)
    )


def test_exclusion_reasons_are_real_sentences():
    # A reason of "n/a" is an absence with extra steps.
    weak = [
        f"{s} x {c}: {why!r}"
        for (s, c), why in cov.EXCLUSIONS.items()
        if len(why.strip()) < 25
    ]
    assert not weak, "Exclusion reasons must explain WHY:\n  " + "\n  ".join(weak)


def test_no_exclusion_contradicts_its_coverage_entry():
    both = [
        f"{s} x {c}"
        for (s, c) in cov.EXCLUSIONS
        if c in cov.COVERAGE.get(s, set())
    ]
    assert not both, "Listed as both covered and excluded:\n  " + "\n  ".join(both)


def test_every_surface_has_a_coverage_entry():
    missing = [s for s in cov.SURFACES if s not in cov.COVERAGE]
    assert not missing, f"surfaces with no COVERAGE entry: {missing}"
