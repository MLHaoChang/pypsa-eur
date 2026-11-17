"""The read-back stage as the backend sees it (increment 6, spec stage 6).

Three functions over a study's run directory, keyed by the bundle HOUR the
engineer worked on: ingest an upload, report what has been read back, and
hand out one comparison as data. The backend imports only `gridspine.drivers`;
the comparison itself lives in `readback/results.py`.
"""
from pathlib import Path

from gridspine.drivers.status import _bundles
from gridspine.readback.results import read_back, readback_figure, readback_summary
from gridspine.schema.contracts import ContractError


def _bundle_for(run_dir, hour: int) -> Path:
    bundles = _bundles(Path(run_dir))
    if int(hour) not in bundles:
        raise ContractError(
            f"no handoff bundle for hour {int(hour)}; bundles exist for {sorted(bundles)}"
        )
    return bundles[int(hour)]


def ingest_powerfactory_results(run_dir, hour: int, bus_csv, branch_csv=None) -> dict:
    """Compare the uploaded PowerFactory export with hour `hour`'s bundle and
    record the result in that bundle. Returns the summary."""
    return read_back(_bundle_for(run_dir, hour), bus_csv, branch_csv)


def readback_status(run_dir) -> dict:
    """{hour: summary} for every bundle that has a read-back."""
    out = {}
    for hour, bundle in _bundles(Path(run_dir)).items():
        summary = readback_summary(bundle)
        if summary is not None:
            out[hour] = summary
    return out


def result_figure(run_dir, hour: int, name: str) -> dict:
    return readback_figure(_bundle_for(run_dir, hour), name)


#: Where a pack judgment lands in the run directory (GC-07 part 5).
COMPLIANCE_DIR = "compliance"
JUDGMENT_JSON = "judgment.json"
JUDGMENT_CSV = "judgment.csv"
JUDGMENT_COLUMNS = (
    "case_id", "kind", "case_status", "check", "role", "status", "measured", "limit",
    "margin", "at_s", "unit", "quantity", "clause", "source", "detail", "data_hash",
    "contract_version",
)
#: Where the rated active power comes from when the caller gives none.
RATED_MW_SOURCE = "campus.yaml pcc.p_connection_mw (contracted connection capacity)"


def run_ratings(run_dir, rated_mva=None, mva_source="", rated_mw=None, mw_source=""):
    """
    The plant ratings for a pack judgment.

    ``rated_mw`` from the campus run (``pcc.p_connection_mw``) unless given
    with its source; ``rated_mva`` only when given with its source.
    """
    import yaml

    from gridspine.drivers.campus_study import CAMPUS_YAML
    from gridspine.readback.pack import Ratings

    if rated_mw is None:
        path = Path(run_dir) / CAMPUS_YAML
        if path.is_file():
            pcc = (yaml.safe_load(path.read_text()) or {}).get("pcc") or {}
            if pcc.get("p_connection_mw") is not None:
                rated_mw, mw_source = pcc["p_connection_mw"], RATED_MW_SOURCE
    return Ratings(rated_mw, mw_source, rated_mva, mva_source)


def judge_pack_results(run_dir, pack_dir, profile: dict, ratings) -> dict:
    """
    Judge a pack's results (``readback.pack.judge_pack``) into the run directory.

    Writes ``<run_dir>/compliance/judgment.json`` and ``judgment.csv``, each
    atomically; a re-run replaces both. Returns the judgment.
    """
    import csv
    import io
    import json

    from gridspine.drivers.campus_study import _write_text_atomic
    from gridspine.readback.pack import judge_pack

    judgment = judge_pack(pack_dir, profile, ratings)
    out = Path(run_dir) / COMPLIANCE_DIR
    out.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, JUDGMENT_COLUMNS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for case in judgment["cases"]:
        base = {
            "case_id": case["case_id"],
            "kind": case["kind"],
            "case_status": case["status"],
            "data_hash": case["data_hash"],
            "contract_version": case["contract_version"],
        }
        if not case["rows"]:
            w.writerow({**base, "status": case["status"], "detail": case["detail"]})
        for row in case["rows"]:
            w.writerow({**row, **base})
    _write_text_atomic(out / JUDGMENT_CSV, buf.getvalue())
    _write_text_atomic(
        out / JUDGMENT_JSON, json.dumps(judgment, indent=1, sort_keys=True, allow_nan=False) + "\n"
    )
    return judgment
