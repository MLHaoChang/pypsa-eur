"""Dev helper: swapping any two gap checks in aggregate_gaps fails a test.

GK B (9) condition 4 for the S1 slice. For each pair of gap kinds, the helper
is wrapped so that, when both kinds are in its result, those two gaps trade
places and every other gap stays where it is: the effect of swapping the two
checks. The parity cases in test_p1_validity_aggregate must then fail. Run
from pypsa-gui/backend:

    python tests/assurance_acceptance/aggregate_gap_mutations.py
"""

import itertools
import sys

import pytest

sys.path.insert(0, ".")

KINDS = ("no load named", "no loads match", "loads have no profile",
         "do not share the network's snapshots", "not a number",
         "missing or non-finite", "no steps")

# Kinds that never appear together, so their relative order is not
# observable and swapping them is an equivalent mutant: "no loads match" only
# when there are no members and no unknown names, and "no steps" only for an
# empty frame, which has no missing step.
EXCLUSIVE = {(0, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (5, 6)}


def kind(gap):
    return next(i for i, k in enumerate(KINDS) if k in gap)


def mutant(real, a, b):
    def swapped(*args, **kwargs):
        gaps, frame, values = real(*args, **kwargs)
        kinds = [kind(g) for g in gaps]
        if a in kinds and b in kinds:
            i, j = kinds.index(a), kinds.index(b)
            gaps = list(gaps)
            gaps[i], gaps[j] = gaps[j], gaps[i]
        return gaps, frame, values
    return swapped


class Patch:
    def __init__(self, a, b):
        self.a, self.b = a, b

    def pytest_configure(self, config):
        from routers import network_profiles
        from services import assurance_records

        swapped = mutant(assurance_records.aggregate_gaps, self.a, self.b)
        assurance_records.aggregate_gaps = swapped
        network_profiles.aggregate_gaps = swapped


def main():
    survivors = []
    for a, b in itertools.combinations(range(len(KINDS)), 2):
        if (a, b) in EXCLUSIVE:
            print(f"swap {KINDS[a]!r} <-> {KINDS[b]!r}: equivalent (never together)",
                  file=sys.stderr)
            continue
        rc = pytest.main(
            ["-q", "-p", "no:cacheprovider", "-W", "ignore", "-x", "--no-header",
             "tests/assurance_acceptance/test_p1_validity_aggregate.py",
             "-k", "both_paths or no_steps"],
            plugins=[Patch(a, b)],
        )
        status = "killed" if rc != 0 else "SURVIVED"
        print(f"swap {KINDS[a]!r} <-> {KINDS[b]!r}: {status}", file=sys.stderr)
        if rc == 0:
            survivors.append((KINDS[a], KINDS[b]))
    print(f"{len(survivors)} surviving swaps: {survivors}", file=sys.stderr)
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
