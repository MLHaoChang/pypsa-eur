"""What a study directory says about itself — read from the artifacts, only.

Increment 4, task 2. The backend asks three questions between runs: how far
did this get, which hours did it pick, what did it assume. All three are
answered here from files on disk, with no reference to the job that wrote
them, because the process that ran the study is usually gone by the time
someone asks — a restarted backend, a recycled container, a study opened a
week later. The spec makes it a contract rather than a nicety: a stage failure
is a typed artifact in the study directory, and "runs resume from the last
valid artifact".

The state machine is deliberately dull. Each stage has ONE completion signal
on disk; a stage is `done` when its signal is there, `running` when the stage
before it is done and its own signal is not, and `pending` otherwise — so a
directory missing an early artifact never claims a late stage. An
`error_<stage>.json` artifact overrides the stage it names (`failed`, or
`aborted` when the cause is a `StudyAborted`) and leaves everything after it
`pending`.

Only the standard library, pandas, and the schema layer: this module is on the
boundary the backend calls, and it must not import an engine to answer "how
far did this get".
"""
import json
from pathlib import Path

import pandas as pd

from gridspine.drivers.year_study import REASON_SEP, STAGES
from gridspine.schema.contracts import ContractError

#: The five states a stage can be in, in the order they appear in a run.
STAGE_STATES = ("pending", "running", "done", "failed", "aborted")


def _error_artifacts(outdir: Path) -> dict:
    """stage -> the parsed error artifact, for every `error_<stage>.json` present."""
    out = {}
    for path in sorted(outdir.glob("error_*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        stage = data.get("stage")
        if stage:
            out[stage] = data
    return out


def _manifest(outdir: Path) -> dict:
    path = outdir / "manifest.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _n_selected(outdir: Path):
    path = outdir / "selected.csv"
    if not path.is_file():
        return None
    try:
        return int(len(pd.read_csv(path)))
    except (OSError, ValueError):
        return None


def stage_status(outdir) -> dict:
    """Per-stage state, the selection, and the error artifact if there is one.

    Returns ``{"status", "resumable", "error", "selected_hours",
    "converged_hours", "bundles", "stages": {stage: {"state", "done",
    "total"}}}``. `status` is the run's own: `not started`, `running`,
    `completed`, `failed` or `aborted`.
    """
    outdir = Path(outdir)
    manifest = _manifest(outdir)
    errors = _error_artifacts(outdir)
    n = _n_selected(outdir)
    screen = manifest.get("screen")

    lf_done = len(list(outdir.glob("lf_*_bus.csv")))
    screen_done = len(list(outdir.glob("screening_h*.csv")))
    bundle_done = len([p for p in outdir.glob("bundle_h*") if (p / "manifest.json").is_file()])
    has_manifest = bool(manifest)

    # (complete?, done, total) per stage — one signal each, all from disk.
    signals = {
        "ingest": ((outdir / "loads.csv").is_file(), None, None),
        "dispatch": ((outdir / "dispatch.csv").is_file(), None, None),
        "ranking": ((outdir / "metrics.csv").is_file() and (outdir / "selected.csv").is_file(), None, None),
        "loadflow": (n is not None and lf_done >= n, lf_done, n),
        "screening": (
            (screen is False) or (n is not None and screen_done >= n),
            screen_done, n,
        ),
        "handoff": (
            has_manifest and (screen is False or (n is not None and bundle_done >= n)),
            bundle_done, n,
        ),
    }

    # A directory with nothing in it has not started; one with any artifact has.
    # Without this the first stage would read `running` for a study that was
    # only ever created — the difference the UI shows as a queued job.
    started = outdir.is_dir() and any(outdir.iterdir())

    stages, blocked = {}, False
    for stage in STAGES:
        complete, done, total = signals[stage]
        if stage in errors:
            cause = str(errors[stage].get("cause", ""))
            state = "aborted" if "studyaborted" in cause.lower().replace(" ", "") else "failed"
            blocked = True
        elif blocked:
            state = "pending"
        elif complete:
            state = "done"
        else:
            state = "running" if _previous_done(stages, stage, started) else "pending"
            blocked = True
        if total is None:
            done, total = (1, 1) if state == "done" else (0, 1)
        stages[stage] = {"state": state, "done": int(done or 0), "total": int(total or 0)}

    failed = next((s for s in STAGES if stages[s]["state"] in ("failed", "aborted")), None)
    if failed is not None:
        status = stages[failed]["state"]
    elif all(stages[s]["state"] == "done" for s in STAGES):
        status = manifest.get("status", "completed")
    elif any(stages[s]["state"] != "pending" for s in STAGES):
        status = "running"
    else:
        status = "not started"

    return {
        "status": status,
        "resumable": (outdir / "dispatch.csv").is_file() and (outdir / "loads.csv").is_file(),
        "error": errors.get(failed) if failed else None,
        "selected_hours": [int(h) for h in manifest.get("selected_hours", _selected_hours(outdir))],
        "converged_hours": [int(h) for h in manifest.get("converged_hours", [])],
        "bundles": {str(h): p.name for h, p in _bundles(outdir).items()},
        "stages": stages,
        # Spec stage 6 (increment 6): per bundle hour, whether the engineer's
        # PowerFactory export has been read back and how it did — the short
        # form; `drivers.readback.readback_status` has the whole summary.
        "readback": _readback_short(outdir),
    }


def _readback_short(outdir: Path) -> dict:
    out = {}
    for hour, bundle in _bundles(outdir).items():
        path = bundle / "readback.json"
        if not path.is_file():
            continue
        try:
            summary = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        branches = summary.get("branches")
        out[str(hour)] = {
            "pass": bool(summary.get("pass")),
            "bus": {k: summary["bus"][k] for k in ("n", "n_ok")} if summary.get("bus") else None,
            "branches": {k: branches[k] for k in ("n", "n_ok")} if branches else None,
        }
    return out


def _previous_done(stages: dict, stage: str, started: bool) -> bool:
    i = STAGES.index(stage)
    if i == 0:
        return started
    return stages[STAGES[i - 1]]["state"] == "done"


def _selected_hours(outdir: Path) -> list:
    path = outdir / "selected.csv"
    if not path.is_file():
        return []
    try:
        return [int(h) for h in pd.read_csv(path)["hour"]]
    except (OSError, KeyError, ValueError):
        return []


def _bundles(outdir: Path) -> dict:
    out = {}
    for p in sorted(outdir.glob("bundle_h*")):
        if (p / "manifest.json").is_file():
            try:
                out[int(p.name[len("bundle_h"):])] = p
            except ValueError:
                continue
    return out


def ranked_snapshots(outdir) -> pd.DataFrame:
    """The selection joined to every ranking metric — one row per selected hour.

    `reasons` comes back as a list (the CSV joins it on `REASON_SEP`), which is
    the shape the action layer and the copilot want; the metric columns are
    whatever `metrics.csv` holds, so a new criterion appears here for free.
    """
    outdir = Path(outdir)
    selected, metrics = outdir / "selected.csv", outdir / "metrics.csv"
    for path in (selected, metrics):
        if not path.is_file():
            raise ContractError(f"no ranked snapshots in {outdir}: {path.name} is missing")
    sel = pd.read_csv(selected)
    sel["hour"] = sel["hour"].astype(int)
    sel["reasons"] = [
        [] if not isinstance(r, str) or not r else r.split(REASON_SEP) for r in sel["reasons"]
    ]
    met = pd.read_csv(metrics)
    met["hour"] = met["hour"].astype(int)
    return sel.merge(met, on="hour", how="left", validate="one_to_one")


def ledger_entries(outdir) -> dict:
    """The assumptions ledger AS DATA, from the newest bundle or the manifest.

    A bundle's `ledger.json` is the richer answer (it carries the provenance
    counts and the measurements the study established); before any bundle
    exists the manifest's `ledger` list is all there is.
    """
    outdir = Path(outdir)
    bundles = _bundles(outdir)
    if bundles:
        path = bundles[max(bundles)] / "ledger.json"
        if path.is_file():
            return json.loads(path.read_text())
    manifest = _manifest(outdir)
    if "ledger" in manifest:
        return {"entries": list(manifest["ledger"]), "provenance_counts": {},
                "measurements": {}, "hour": None}
    raise ContractError(f"no ledger in {outdir}: neither a bundle nor a manifest carries one")
