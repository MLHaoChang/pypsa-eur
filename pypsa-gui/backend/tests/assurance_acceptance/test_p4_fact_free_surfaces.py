"""AA-05 S5b B4/B5 (owner card 2026-11-08 "Pin them as fact-free", AA05-A46-SCOPE-1).

Only the answer and the checked chart carry a checked fact. Table exports,
reports and download headers never present a value as checked:

- B4: each download kind sends an exact set of header names (not a word grep:
  a chart legitimately says "evidence re-verified now"), and no header value
  carries a number from the evidence record.
- B5: a delivery zip item for a table carries no facts, metrics, evidence id
  or evidence status; only `assurance`, dataset_sha256 and render_spec_sha256,
  as before.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import io
import json
import re
import zipfile

from services import assurance_records as records
from services import chat_tools
from tests.assurance_acceptance.fact_profiles import *  # noqa: F401,F403
from tests.assurance_acceptance.fact_profiles import owner
from tests.test_checked_charts import GENERATOR, ask

# Every response of the app carries these (content and middleware headers).
BASE = {"content-disposition", "content-length", "content-type", "x-pypsa-replica", "vary"}
HEADERS = {
    "table": BASE,
    "chart": BASE | {"x-checked-chart"},
}
# Keys that would present a value as checked, anywhere in a delivery item.
CHECKED_KEYS = {"facts", "metrics", "warnings", "blocks", "evidence_id", "evidence_ids",
                "evidence_status", "evidence_note", "result_slots", "slots"}
TABLE_ITEM = {"archive_path", "assurance", "dataset_sha256", "file_id", "filename", "mime",
              "provenance", "render_spec_sha256", "sha256", "size"}
TABLE_ASSURANCE = {"artifact_sha256", "dataset", "dataset_sha256", "project_id", "render_spec",
                   "render_spec_sha256", "schema_version", "source_kind", "validity"}
NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:e[-+]?\d+)?(?![\w.])")


def checked_numbers(record):
    """Each fact value as text, as a header could carry it."""
    out = set()
    for fact in record["facts"]:
        value = fact["value"]
        out |= {repr(value), str(value)}
        if isinstance(value, float) and value.is_integer():
            out.add(str(int(value)))
    return out


def numbers_in(headers):
    return {n for value in headers.values() for n in NUMBER.findall(value)}


def keys_in(value):
    if isinstance(value, dict):
        return set(value) | {k for child in value.values() for k in keys_in(child)}
    if isinstance(value, list):
        return {k for child in value for k in keys_in(child)}
    return set()


def _checked(solved, checked):
    receipt = ask(checked, solved, "generator-dispatch/1", "G1", GENERATOR, ["p"])
    record = records.evidence(owner(checked), receipt["evidence_ids"][0])
    return receipt, record


def _table_of(record):
    """A table export whose cells are the checked numbers themselves."""
    rows = [[f["metric"], f["value"]] for f in record["facts"]]
    return chat_tools.export_to_csv(rows, ["metric", "value"], "facts.csv")


def test_a_chart_download_sends_only_its_header_names_and_no_checked_number(solved, checked):
    receipt, record = _checked(solved, checked)
    response = checked.client.get(receipt["chart"]["download_url"])
    assert response.status_code == 200
    assert set(response.headers) == HEADERS["chart"]
    assert response.headers["x-checked-chart"] == "evidence re-verified now"
    assert not numbers_in(response.headers) & checked_numbers(record)


def test_a_table_download_sends_no_checked_header_or_number(solved, checked):
    _receipt, record = _checked(solved, checked)
    table = _table_of(record)
    response = checked.client.get(table["download_url"])
    assert response.status_code == 200
    assert set(response.headers) == HEADERS["table"]
    assert not numbers_in(response.headers) & checked_numbers(record)
    # The export's own reply names no evidence either.
    assert not set(table) & CHECKED_KEYS


def test_a_delivered_table_carries_no_checked_fact(solved, checked):
    receipt, record = _checked(solved, checked)
    table = _table_of(record)
    bundle = chat_tools.build_delivery([table["file_id"], receipt["chart"]["file_id"]])
    response = checked.client.get(bundle["download_url"])
    assert response.status_code == 200
    assert not numbers_in(response.headers) & checked_numbers(record)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        items = {i["file_id"]: i for i in json.loads(archive.read("manifest.json"))["files"]}
    item = items[table["file_id"]]
    assert set(item) == TABLE_ITEM
    assert set(item["assurance"]) == TABLE_ASSURANCE
    assert item["assurance"]["source_kind"] == "table_export"
    assert not keys_in(item) & CHECKED_KEYS
    assert record["evidence_id"] not in json.dumps(item)
    # The chart in the same zip is the one item that names its evidence.
    chart = items[receipt["chart"]["file_id"]]
    assert chart["assurance"]["evidence_id"] == record["evidence_id"]
    assert chart["assurance"]["dataset"]["metrics"]
