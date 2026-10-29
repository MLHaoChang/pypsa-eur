"""Dev helper: dropping any one binding check of a series window fails a test.

GK B (10) condition 6 for the S4 slice. Each mutant removes one check from
`_series_facts` by rewriting its source; test_p1_series_window must then
fail, and the helper names the first test that does. Run from
pypsa-gui/backend:

    python tests/assurance_acceptance/series_window_mutations.py
"""

import inspect
import sys
import textwrap

import pytest

sys.path.insert(0, ".")

SUITE = "tests/assurance_acceptance/test_p1_series_window.py"

# name -> (text in _series_facts, its replacement)
MUTANTS = {
    "no index digest": (
        'if window["index_sha256"] != expected["index_sha256"]:',
        "if False:",
    ),
    "no values digest": (
        'if window["values_sha256"] != expected["values_sha256"]:',
        "if False:",
    ),
    "no rows check": ("or rows != len(steps)", "or False"),
    "no mask length check": ("if len(mask) != (rows + 7) // 8:", "if False:"),
    "no stray-bit check": (
        "if sum(certified) != sum(bin(octet).count(\"1\") for octet in mask) or not any(",
        "if not any(",
    ),
}


class Patch:
    def __init__(self, old, new):
        self.old, self.new = old, new

    def pytest_configure(self, config):
        from services import assurance_records

        source = textwrap.dedent(inspect.getsource(REAL))
        assert source.count(self.old) == 1, self.old
        namespace = {}
        exec(source.replace(self.old, self.new), assurance_records.__dict__, namespace)
        assurance_records._series_facts = namespace["_series_facts"]

    def pytest_unconfigure(self, config):
        from services import assurance_records

        assurance_records._series_facts = REAL


class FirstFailure:
    def __init__(self):
        self.name = None

    def pytest_runtest_logreport(self, report):
        if report.failed and self.name is None:
            self.name = report.nodeid.split("::")[-1]


def main():
    global REAL
    from services import assurance_records

    REAL = assurance_records._series_facts
    survivors = []
    for name, (old, new) in MUTANTS.items():
        first = FirstFailure()
        rc = pytest.main(
            ["-q", "-p", "no:cacheprovider", "-W", "ignore", "-x", "--no-header", SUITE],
            plugins=[Patch(old, new), first],
        )
        status = f"killed by {first.name}" if rc not in (0, 5) else "SURVIVED"
        print(f"{name}: {status}", file=sys.stderr)
        if rc in (0, 5):
            survivors.append(name)
    print(f"{len(survivors)} surviving mutants: {survivors}", file=sys.stderr)
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
