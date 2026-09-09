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
