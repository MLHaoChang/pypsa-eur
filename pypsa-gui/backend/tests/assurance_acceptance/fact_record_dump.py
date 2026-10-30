"""Dev helper: dump the facts every fact-building site returns, and every
record saved, while the A1.x suites run.

GK B (10) condition 2 for the S3 slice (one shared fact builder): the facts
of all six sites stay byte-equal to master's. Load this file as a plugin on
both trees, then `cmp` the two files:

    FACT_DUMP=/path/out.jsonl python -m pytest -p no:cacheprovider \
        -p tests.assurance_acceptance.fact_record_dump \
        tests/assurance_acceptance/test_p1_evidence.py ...

Each line is keyed by test id and site. S4 adds the facts evidence() and
capture_series return, so a series record that stores only a row mask can be
compared with S3's, which stored its facts. The static_page capture site builds
its facts inside capture_input_page, so its facts are the saved record's
(site "save", kind "static_page"). Without FACT_DUMP it does nothing.
"""

import json
import os

import pytest

from services import assurance_records

# The fact-building functions of the static, static_page recheck, series,
# aggregate and run sites.
SITES = ("_raw_facts", "_page_facts", "_series_steps", "_aggregate_facts", "_run_facts")


def _write(out, **line):
    with open(out, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, sort_keys=True, ensure_ascii=False, default=str) + "\n")


def _dumping(out, test, site, real):
    def wrapped(*args, **kwargs):
        result = real(*args, **kwargs)
        _write(out, test=test, site=site, result=result)
        return result

    return wrapped


def _record_facts(out, test, site, real):
    """S4: the facts a record is read or captured with, whatever it stores."""

    def wrapped(*args, **kwargs):
        record = real(*args, **kwargs)
        if isinstance(record, dict):
            kind = (record.get("scope") or {}).get("kind")
            _write(out, test=test, site=site, kind=kind, facts=record.get("facts"))
        return record

    return wrapped


@pytest.fixture(autouse=True)
def _dump_facts(monkeypatch, request):
    out = os.environ.get("FACT_DUMP")
    if not out:
        yield
        return
    test = request.node.nodeid
    for site in SITES:
        real = getattr(assurance_records, site)
        monkeypatch.setattr(assurance_records, site, _dumping(out, test, site, real))
    for site in ("evidence", "capture_series"):
        real = getattr(assurance_records, site)
        monkeypatch.setattr(assurance_records, site, _record_facts(out, test, site, real))
    save = assurance_records.save

    def dumping_save(record, record_id):
        scope = record.get("scope") or {}
        _write(out, test=test, site="save", kind=scope.get("kind"), facts=record.get("facts"))
        return save(record, record_id)

    monkeypatch.setattr(assurance_records, "save", dumping_save)
    yield
