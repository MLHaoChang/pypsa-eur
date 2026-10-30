"""Dev helper: each run manifest MAC mutant fails a named test.

GK B (12) condition 4 for the run manifest MAC. Each mutant replaces one part
of the MAC; the named tests in test_p1_run_manifest_mac must then fail. Run
from pypsa-gui/backend:

    python tests/assurance_acceptance/run_manifest_mutations.py

Dropping project_id or run_id from the MAC's binding alone is an equivalent
mutant: the manifest body carries both. These mutants drop each field from
the whole MAC input, binding and body.
"""

import hashlib
import hmac
import sys

import pytest

sys.path.insert(0, ".")

FILE = "tests/assurance_acceptance/test_p1_run_manifest_mac.py"


def _without(field):
    def mac(manifest):
        from services import assurance_records as records
        from services import run_evidence

        body = {k: v for k, v in manifest.items() if k not in ("mac", field)}
        bound = {
            "domain": run_evidence.RUN_MAC_DOMAIN.decode(),
            "manifest": body,
            "project_id": body.get("project_id"),
            "run_id": body.get("run_id"),
        }
        message = records._canonical(bound)
        key = records.server_key(run_evidence.RUN_MAC_DOMAIN)
        return hmac.new(key, message, hashlib.sha256).hexdigest()

    return mac


MUTANTS = {
    "skip_verify": (
        lambda: setattr(_re(), "_seal_verifies", lambda manifest, mac: True),
        [
            "test_a_run_forged_without_the_key_is_not_checkable",
            "test_a_checked_run_whose_mac_no_longer_verifies_is_unavailable",
            "test_a_run_sealed_under_another_key_is_not_checkable",
            "test_an_unsealed_run_still_reads_as_unchecked_data",
        ],
    ),
    "drop_project_id": (
        lambda: setattr(_re(), "_manifest_mac", _without("project_id")),
        ["test_a_manifest_moved_to_another_project_is_refused"],
    ),
    "drop_run_id": (
        lambda: setattr(_re(), "_manifest_mac", _without("run_id")),
        ["test_a_mac_copied_from_another_run_is_refused"],
    ),
}


def _re():
    from services import run_evidence

    return run_evidence


class Mutate:
    def __init__(self, apply):
        self.apply = apply

    def pytest_configure(self, config):
        self.apply()


def main():
    survivors = []
    for name, (apply, tests) in MUTANTS.items():
        for test in tests:
            rc = pytest.main(
                [
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "-W",
                    "ignore",
                    "--no-header",
                    f"{FILE}::{test}",
                ],
                plugins=[Mutate(apply)],
            )
            print(
                f"{name} {test}: {'killed' if rc != 0 else 'SURVIVED'}", file=sys.stderr
            )
            if rc == 0:
                survivors.append((name, test))
    print(f"{len(survivors)} surviving: {survivors}", file=sys.stderr)
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
