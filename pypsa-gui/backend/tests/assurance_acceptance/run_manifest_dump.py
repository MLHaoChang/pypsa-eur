"""Dev helper: what every ordinary run reader returns, for a byte compare.

GK B (12) condition 1 for the run manifest MAC: the MAC is invisible to every
reader but checked_run_manifest. This publishes fixed runs (fixed ids, times
and content) into a temporary project and prints, as one JSON document, what
publish_run, an identical republish, _load_run, get_run_evidence (with and
without a section), list_project_runs and compare_project_runs return. Run it
on master and on the PR tree from pypsa-gui/backend and `cmp` the outputs:

    python tests/assurance_acceptance/run_manifest_dump.py > out.json
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
os.environ["PYPSAGUI_APP_DATA_DIR"] = tempfile.mkdtemp()
os.environ.setdefault("SECRET_KEY", "test-secret-not-a-real-key")

from services import run_evidence  # noqa: E402

PROJECT = "11111111-2222-3333-4444-555555555555"


def publish(root, run_id, objective, started):
    return run_evidence.publish_run(
        root,
        PROJECT,
        run_id=run_id,
        kind="simulation",
        status="completed",
        started_at=started,
        finished_at=started.replace("10:00", "10:05"),
        mode="lopf",
        fingerprint="fp",
        job_id="job-" + run_id,
        actor_id="actor",
        metrics={
            "objective": {"value": objective, "unit": "EUR"},
            "served_energy": {"value": 42, "unit": "MWh"},
        },
        sections={"summary": {"objective": objective, "note": "a ~/ key"}},
        warnings=["w"],
    )


def main():
    root = Path(tempfile.mkdtemp())
    run_evidence._authority = lambda: (root, PROJECT, "actor")
    out = {
        "publish": publish(root, "run-a", 100, "2026-10-01T10:00:00+00:00"),
        "publish_b": publish(root, "run-b", 110, "2026-10-02T10:00:00+00:00"),
        "republish": publish(root, "run-a", 100, "2026-10-01T10:00:00+00:00"),
        "load_run": run_evidence._load_run(root, PROJECT, "run-a"),
        "get_run_evidence": run_evidence.get_run_evidence("run-a"),
        "get_run_evidence_section": run_evidence.get_run_evidence("run-a", "summary"),
        "list_project_runs": run_evidence.list_project_runs(),
        "compare_project_runs": run_evidence.compare_project_runs("run-b", "run-a"),
    }
    print(json.dumps(out, sort_keys=True, indent=1))


if __name__ == "__main__":
    main()
