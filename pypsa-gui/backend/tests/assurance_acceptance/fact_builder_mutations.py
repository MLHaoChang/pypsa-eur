"""Dev helper: breaking the shared fact builder fails each kind's own suite.

GK B (10) condition for the S3 slice. Each mutant replaces `_fact`; the suite
of every kind that builds facts through it must then fail, without
test_p1_fact_builder's help. Unescaped tokens only differ for names holding
'~' or '/', which only the page suite uses. Run from pypsa-gui/backend:

    python tests/assurance_acceptance/fact_builder_mutations.py
"""

import sys

import pytest

sys.path.insert(0, ".")

A = "tests/assurance_acceptance/"
SUITES = {
    "static": [A + "test_p1_evidence.py", "-k", "not page"],
    "static_page": [A + "test_p1_evidence.py", "-k", "page"],
    "series": [A + "test_p1_timeseries.py"],
    "aggregate": [A + "test_p1_aggregate.py"],
    "run": [A + "test_p1_run_as_of.py"],
}


def swap_entity_and_metric(entity_id, metric, value, unit, *tokens):
    return REAL(metric, entity_id, value, unit, *tokens)


def drop_unit(entity_id, metric, value, unit, *tokens):
    fact = REAL(entity_id, metric, value, unit, *tokens)
    del fact["unit"]
    return fact


def unescaped(entity_id, metric, value, unit, *tokens):
    fact = REAL(entity_id, metric, value, unit, *tokens)
    fact["path"] = "".join("/" + str(t) for t in tokens)
    return fact


MUTANTS = {
    "swap entity_id and metric": (swap_entity_and_metric, SUITES),
    "drop unit": (drop_unit, SUITES),
    "unescaped path": (unescaped, {"static_page": SUITES["static_page"]}),
}


class Patch:
    def __init__(self, mutant):
        self.mutant = mutant

    def pytest_configure(self, config):
        from services import assurance_records

        assurance_records._fact = self.mutant

    def pytest_unconfigure(self, config):
        from services import assurance_records

        assurance_records._fact = REAL


def main():
    global REAL
    from services import assurance_records

    REAL = assurance_records._fact
    survivors = []
    for name, (mutant, suites) in MUTANTS.items():
        for kind, args in suites.items():
            rc = pytest.main(
                ["-q", "-p", "no:cacheprovider", "-W", "ignore", "-x", "--no-header",
                 *args],
                plugins=[Patch(mutant)],
            )
            status = "killed" if rc not in (0, 5) else "SURVIVED"
            print(f"{name} / {kind}: {status}", file=sys.stderr)
            if status == "SURVIVED":
                survivors.append((name, kind))
    print(f"{len(survivors)} surviving mutants: {survivors}", file=sys.stderr)
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
