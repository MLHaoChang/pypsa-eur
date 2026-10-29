"""Dev helper: dump every fact list the A1.x suites seal or re-check.

GK B (10) asked for the S3 refactor (one shared fact builder) to leave the
facts of every kind byte-equal to master's. Load this file as a plugin while
the suites run, on both trees, then `cmp` the two files:

    FACT_DUMP=/path/out.jsonl python -m pytest -p no:cacheprovider \
        -p tests.assurance_acceptance.fact_record_dump \
        tests/assurance_acceptance/test_p1_evidence.py ...

Each line is one saved record's facts or one re-check's expected facts,
keyed by test id and scope kind. Without FACT_DUMP it does nothing.
"""

import json
import os

import pytest

from services import assurance_records


def _write(out, **line):
    with open(out, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, sort_keys=True, ensure_ascii=False) + "\n")


@pytest.fixture(autouse=True)
def _dump_facts(monkeypatch, request):
    out = os.environ.get("FACT_DUMP")
    if not out:
        yield
        return
    test = request.node.nodeid
    save = assurance_records.save
    require = assurance_records._require_current_facts

    def dumping_save(record, record_id):
        scope = record.get("scope") or {}
        _write(out, test=test, at="save", kind=scope.get("kind"), facts=record.get("facts"))
        return save(record, record_id)

    def dumping_require(facts, expected):
        _write(out, test=test, at="recheck", facts=expected)
        return require(facts, expected)

    monkeypatch.setattr(assurance_records, "save", dumping_save)
    monkeypatch.setattr(assurance_records, "_require_current_facts", dumping_require)
    yield
