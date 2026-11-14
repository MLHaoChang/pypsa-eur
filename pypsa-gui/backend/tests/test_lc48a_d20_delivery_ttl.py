"""LC-48a, LC-D20.delivery_ttl: a delivery older than 7 days is refused and nothing changes.

Owner decision LC48A-TTL-1: a copilot delivery (an ``agent_export`` file and its download
link) lives 7 days from when it was made. After that, ``get_file_delivery``,
``build_delivery`` and the authenticated download refuse it with 410 ``delivery_expired``;
the file stays on disk, and exporting the same content again makes a fresh delivery.

The seam is the copilot's delivery tools and the download route, on the baseline project
(B1 110 kV, G1 20 MW at 10 EUR/MWh, L1 5 MW, two snapshots). Age is set by writing the
delivery's recorded creation time back, as a file made that long ago would read.

Hand oracle (7 days = 604,800 s):
- made 604,800 - 60 s ago: delivered, built into a zip and downloaded (200, same sha256);
- made 604,800 + 60 s ago: each of the three is 410 delivery_expired, and
  ``lc43_gates.state`` and the file's ``meta.json`` bytes are as before;
- the person's own upload made 30 days ago is not a delivery and never expires: its
  download is 200;
- exporting the same network again returns the same file id, now 200 everywhere.
"""

# ruff: noqa: F811 -- shared public harness fixtures
import hashlib
import json
import time

from services import chat_tools, upload_service
from tests.lc43_gates import refused, state
from tests.test_assistant_capabilities import session  # noqa: F401
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401

WEEK = 7 * 24 * 3600


def _meta_path(project_dir, file_id):
    return (
        upload_service.get_upload_path(
            "Tool Baseline", file_id, project_dir=project_dir
        ).parent
        / "meta.json"
    )


def made_ago(project_dir, file_id, seconds: float) -> None:
    """Record the file as made ``seconds`` ago."""
    path = _meta_path(project_dir, file_id)
    meta = json.loads(path.read_text())
    path.write_text(json.dumps({**meta, "uploaded_at": time.time() - seconds}))


def test_a_delivery_inside_seven_days_is_served(baseline, session, client):
    _ctx, _project_id, project_dir = baseline
    delivery = chat_tools.export_network_nc()
    made_ago(project_dir, delivery["file_id"], WEEK - 60)

    assert (
        chat_tools.get_file_delivery(delivery["file_id"])["sha256"]
        == delivery["sha256"]
    )
    assert chat_tools.build_delivery([delivery["file_id"]])["files_total"] == 1
    response = client.get(delivery["download_url"])
    assert response.status_code == 200, response.text
    assert hashlib.sha256(response.content).hexdigest() == delivery["sha256"]


def test_a_delivery_older_than_seven_days_is_refused_and_nothing_changes(
    baseline, session, client
):
    _ctx, _project_id, project_dir = baseline
    delivery = chat_tools.export_network_nc()
    made_ago(project_dir, delivery["file_id"], WEEK + 60)
    meta_bytes = _meta_path(project_dir, delivery["file_id"]).read_bytes()
    before = state(project_dir)

    refused(
        lambda: chat_tools.get_file_delivery(delivery["file_id"]),
        410,
        "delivery_expired",
    )
    refused(
        lambda: chat_tools.build_delivery([delivery["file_id"]]),
        410,
        "delivery_expired",
    )
    response = client.get(delivery["download_url"])
    assert response.status_code == 410, response.text
    assert response.json()["detail"]["error_kind"] == "delivery_expired"
    assert delivery["sha256"].encode() not in response.content

    assert state(project_dir) == before
    assert _meta_path(project_dir, delivery["file_id"]).read_bytes() == meta_bytes


def test_the_persons_own_upload_never_expires(baseline, client):
    _ctx, project_id, project_dir = baseline
    meta = upload_service.add_upload(
        "Tool Baseline", b"bus\nB9\n", "mine.csv", "text/csv", project_dir=project_dir
    )
    made_ago(project_dir, meta.file_id, 30 * 24 * 3600)
    response = client.get(f"/api/projects/{project_id}/uploads/{meta.file_id}/blob")
    assert response.status_code == 200, response.text
    assert response.content == b"bus\nB9\n"


def test_exporting_the_same_content_again_makes_a_fresh_delivery(
    baseline, session, client
):
    _ctx, _project_id, project_dir = baseline
    first = chat_tools.export_network_nc()
    made_ago(project_dir, first["file_id"], WEEK + 60)
    refused(
        lambda: chat_tools.get_file_delivery(first["file_id"]), 410, "delivery_expired"
    )

    again = chat_tools.export_network_nc()

    assert again["file_id"] == first["file_id"]
    assert chat_tools.get_file_delivery(again["file_id"])["sha256"] == first["sha256"]
    assert client.get(again["download_url"]).status_code == 200
