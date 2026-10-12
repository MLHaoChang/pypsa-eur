"""Record quota/atomicity are enforced across independent application processes."""

import json
from pathlib import Path
import subprocess
import sys
import uuid


SCRIPT = """
import json, sys, time, uuid
from pathlib import Path
import app_paths
from services import assurance_records as records
from fastapi import HTTPException
root, owner, worker = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
app_paths.app_data_dir = lambda: root
records.MAX_RECORDS = 3
(root / ("ready" + worker)).touch()
deadline = time.monotonic() + 15
while len(list(root.glob("ready*"))) != 2:
    if time.monotonic() > deadline: raise TimeoutError("barrier")
    time.sleep(.01)
for number in range(2):
    oid = str(uuid.uuid5(uuid.UUID(owner), worker + str(number)))
    record = {"version": 1, "record_kind": "evidence", "owner_id": owner,
              "evidence_id": oid, "worker": worker, "expires_at": time.time() + 100}
    try: records.save(record, oid)
    except HTTPException as exc:
        if exc.detail.get("error_kind") != "assurance_storage_full": raise
"""


def test_cross_process_quota_and_atomic_envelopes(tmp_path):
    from services import assurance_records

    owner = str(uuid.uuid4())
    backend = Path(__file__).parents[1]
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", SCRIPT, str(tmp_path), owner, str(i)],
            cwd=backend,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for i in range(2)
    ]
    try:
        results = [p.communicate(timeout=40) for p in workers]
        for process, (_, error) in zip(workers, results):
            assert process.returncode == 0, error.decode()
    finally:
        for process in workers:
            if process.poll() is None:
                process.kill()
                process.wait()
    files = list((tmp_path / "assistant_assurance" / owner).glob("*.json"))
    assert len(files) == 3
    for path in files:
        envelope = json.loads(path.read_text())
        assert envelope["sha256"] == assurance_records.digest(envelope["record"])
    assert not list((tmp_path / "assistant_assurance" / owner).glob("*.tmp"))
