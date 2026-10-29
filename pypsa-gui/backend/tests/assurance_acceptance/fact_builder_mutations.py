"""Dev helper: breaking the shared fact builder at any one call site fails a test.

GK B (10) condition 3 for the S3 slice. Each mutant replaces `_fact` only for
calls from one site, so the static_page capture and its recheck are broken
apart; that site's suites must then fail. An unescaped path is an equivalent
mutant at the static, aggregate and run sites: their only token is a metric
name, which never holds '~' or '/' (INPUT_METRICS, "peak"/"mean",
run_evidence._METRIC). Run from pypsa-gui/backend:

    python tests/assurance_acceptance/fact_builder_mutations.py
"""

import sys

import pytest

sys.path.insert(0, ".")

A = "tests/assurance_acceptance/"
EVIDENCE = A + "test_p1_evidence.py"
BUILDER = A + "test_p1_fact_builder.py"
SITES = {
    "_raw_facts": [EVIDENCE, BUILDER, "-k", "not page and not pointer_characters"],
    "capture_input_page": [EVIDENCE, "-k", "page or pointer_characters"],
    "_page_facts": [EVIDENCE, "-k", "page or pointer_characters"],
    "_series_steps": [A + "test_p1_timeseries.py", BUILDER],
    "_aggregate_facts": [A + "test_p1_aggregate.py"],
    "_run_facts": [A + "test_p1_run_as_of.py"],
}
ESCAPING = ("capture_input_page", "_page_facts", "_series_steps")


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
    "swap entity_id and metric": (swap_entity_and_metric, tuple(SITES)),
    "drop unit": (drop_unit, tuple(SITES)),
    "unescaped path": (unescaped, ESCAPING),
}


def at_site(site, mutant):
    def fact(*args):
        caller = sys._getframe(1)
        # A comprehension is its own frame before Python 3.12.
        names = {caller.f_code.co_name, caller.f_back.f_code.co_name}
        return (mutant if site in names else REAL)(*args)

    return fact


class Patch:
    def __init__(self, fact):
        self.fact = fact

    def pytest_configure(self, config):
        from services import assurance_records

        assurance_records._fact = self.fact

    def pytest_unconfigure(self, config):
        from services import assurance_records

        assurance_records._fact = REAL


def main():
    global REAL
    from services import assurance_records

    REAL = assurance_records._fact
    survivors = []
    for name, (mutant, sites) in MUTANTS.items():
        for site in sites:
            rc = pytest.main(
                ["-q", "-p", "no:cacheprovider", "-W", "ignore", "-x", "--no-header",
                 *SITES[site]],
                plugins=[Patch(at_site(site, mutant))],
            )
            status = "killed" if rc not in (0, 5) else "SURVIVED"
            print(f"{name} @ {site}: {status}", file=sys.stderr)
            if status == "SURVIVED":
                survivors.append((name, site))
    print(f"{len(survivors)} surviving mutants: {survivors}", file=sys.stderr)
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
