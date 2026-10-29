"""Dev helper: load() without its mark comparison fails every replay red test.

GK B (9) condition 6 for the REPLAY slice. The mutant drops the comparison:
load() accepts any mutable record it can read and unseal. Each red test in test_p1_replay must then fail on its own. Run from
pypsa-gui/backend:

    python tests/assurance_acceptance/replay_mutation.py
"""

import sys

import pytest

sys.path.insert(0, ".")

RED = (
    "test_older_sealed_copy_put_back_is_unavailable",
    "test_whole_store_restored_from_an_older_copy_is_unavailable",
    "test_a_mark_rolled_back_below_the_file_is_unavailable",
    "test_a_copy_from_another_epoch_is_unavailable",
    "test_a_record_with_no_mark_is_unavailable",
    "test_a_crash_after_the_mark_commit_never_accepts_the_old_file",
    "test_a_pruned_mark_starts_a_fresh_chain",
)


class SkipComparison:
    def pytest_configure(self, config):
        from services import assurance_records as records

        def unchecked(path, owner, record_id, kind):
            record = records._read(path, record_id)
            if record.get("record_kind") != kind:
                raise records.HTTPException(404, "Record not found")
            return {k: v for k, v in record.items() if k != records.MARK_KEY}

        records._read_marked = unchecked


def main():
    survivors = []
    for name in RED:
        for kind in ("conversation_context", "operation", "ui_action"):
            node = f"tests/assurance_acceptance/test_p1_replay.py::{name}[{kind}]"
            rc = pytest.main(["-q", "-p", "no:cacheprovider", "-W", "ignore", "--no-header", node],
                             plugins=[SkipComparison()])
            print(f"{node}: {'killed' if rc != 0 else 'SURVIVED'}", file=sys.stderr)
            if rc == 0:
                survivors.append(node)
    print(f"{len(survivors)} surviving: {survivors}", file=sys.stderr)
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
