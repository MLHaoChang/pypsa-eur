"""
The ONE hash rule a decision study's readers share (plan S5 [B1], S7).

A reader of a finished run may only attribute its results to the ledger and
the option networks the run was built from. Three readers apply the rule:

* the case route (``routers/studies.py::_option_case``) answers 409
  ``ledger_changed_since_run`` / ``intake_changed_since_run`` /
  ``fork_changed_since_run``;
* the findings (``services/study/findings.py::load_inputs``) refuse a changed
  ledger or intake and leave a changed fork out;
* the report (``services/study/report.py::stale_reasons``) marks itself
  ``stale`` and names why.

They call these functions, so a report is never fresh where a case would be
refused (gate S5 carry: "the stale flag and the case's 409 share one hash
rule"). Hashes are recorded at findings time (``Findings.hashes``): the
ledger's (``packs.ledger_hash``), the intake's (:func:`intake_hash`, gate S7)
and each OPTION fork's ``network.nc``
(sha256[:16], keyed by fork uuid). Tornado forks are not in that map — they
are deleted after the read — so they never make anything stale.

**The engine inputs (U2 WP8, plan §2 C10).** The run also records what it
wrote into each option fork's ``solver_config.json`` for the Investment Case
engine — the ``commercial`` and ``finance`` blocks — as one digest per fork
(:func:`engine_inputs_digest`, keyed by fork uuid) and the run's
:func:`compiled_hash` (the run's commercial digest and each option's finance
digest). :func:`compiled_matches` is the rule: a run that recorded none (a
run before WP8, whose forks may predate the engine's LP or case) or a fork
whose blocks were edited since (an Expert edit of its solver config) never
matches, so the case and the findings refuse 409
``engine_inputs_changed_since_run`` and the report reads stale.
"""
from __future__ import annotations

import pathlib
from collections.abc import Mapping
from typing import Any

__all__ = ["compiled_hash", "compiled_matches", "engine_inputs_digest", "engine_recorded",
           "file_hash", "fork_engine_digest", "fork_file_hash", "fork_matches", "intake_hash",
           "intake_matches", "ledger_matches", "recorded_ledger_hash"]

#: The solver-config blocks that are the engine's inputs (plan §2 C10).
ENGINE_BLOCKS = ("commercial", "finance")
#: The refusal (409) every reader answers when the engine inputs moved or were
#: never recorded; the frontend offers a re-run (`ERROR_COPY`).
ENGINE_STALE = "engine_inputs_changed_since_run"
#: The plain reasons, for a run before WP8 and for an edited fork.
EARLIER_VERSION = (
    "This study was run by an earlier version of the app, which priced its options and "
    "valued their investment cases differently, so its results are out of date and are "
    "not shown. Run the study again to bring them up to date.")


def edited_since_run(options) -> str:
    """The plain reason for options whose tariff or finance settings were edited."""
    names = ", ".join(sorted(options))
    return (f"The tariff or finance settings of option(s) {names} were edited after the run "
            "(for example in the Expert view), so the run's results no longer describe "
            "them; re-run the study to value them as they are now.")


def file_hash(path: pathlib.Path) -> str | None:
    """sha256[:16] of a file, None when it cannot be read (matches nothing)."""
    from services.study import runner

    return runner._network_hash(pathlib.Path(path))


def fork_file_hash(row) -> str | None:
    """The hash of a fork's ``network.nc`` as it is on disk now."""
    from services import project_registry

    return file_hash(project_registry.project_dir(row) / "network.nc")


def recorded_ledger_hash(hashes: Mapping[str, Any] | Any) -> str | None:
    if hashes is None:
        return None
    if isinstance(hashes, Mapping):
        return hashes.get("ledger_hash")
    return getattr(hashes, "ledger_hash", None)


def _canonical(value: Any) -> Any:
    """
    One spelling per number: an integral float is its int, because a browser's
    JSON drops the `.0` and an unchanged intake re-saved from the UI must hash
    the same (gate S7 re-verification [S-R1]). Booleans stay booleans.
    """
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def intake_hash(intake: Mapping[str, Any] | None) -> str:
    """A stable hash of the study's intake (its canonical JSON)."""
    import hashlib
    import json

    text = json.dumps(_canonical(dict(intake or {})), sort_keys=True, default=str,
                      separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def intake_matches(intake: Mapping[str, Any] | None, hashes: Mapping[str, Any] | Any) -> bool:
    """
    True when ``intake`` is the one the run's forks were built from (gate S7
    [S4]: a load or site edited after the run changes what the options
    mean). A run record without an intake hash never matches.
    """
    recorded = (hashes.get("intake_hash") if isinstance(hashes, Mapping)
                else getattr(hashes, "intake_hash", None))
    return recorded is not None and intake_hash(intake) == recorded


def ledger_matches(ledger, hashes: Mapping[str, Any] | Any) -> bool:
    """True when ``ledger`` is the one the run was built from."""
    from services.study import packs

    recorded = recorded_ledger_hash(hashes)
    return recorded is not None and packs.ledger_hash(ledger) == recorded


def fork_matches(recorded: str | None, current: str | None) -> bool:
    """
    True when a fork's network is the one the run solved. A run record
    without a hash, or a file that cannot be read (``None``), never matches.
    """
    return recorded is not None and current is not None and current == recorded


# ── the engine inputs (U2 WP8, plan §2 C10) ──────────────────────────────

def _field(hashes: Mapping[str, Any] | Any, key: str, default=None):
    if hashes is None:
        return default
    if isinstance(hashes, Mapping):
        return hashes.get(key, default)
    return getattr(hashes, key, default)


def _sha16(payload: Any) -> str:
    import hashlib
    import json

    text = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def engine_inputs_digest(solver_config: Mapping[str, Any]) -> str:
    """
    The digest of a solver config's engine inputs: its ``commercial`` and
    ``finance`` blocks (canonical JSON), nothing else — a solver option or
    the base's name does not move it.
    """
    return _sha16({k: solver_config.get(k) for k in ENGINE_BLOCKS})


def fork_engine_digest(row) -> str | None:
    """
    The engine-input digest of a fork's ``solver_config.json`` as it is on
    disk now (the file the queue solved from and saved); None when it cannot
    be read, which matches nothing.
    """
    import json

    from services import project_registry

    path = project_registry.project_dir(row) / "solver_config.json"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return engine_inputs_digest(cfg) if isinstance(cfg, Mapping) else None


def compiled_hash(commercial_digest: str | None,
                  finance_digests: Mapping[str, str | None]) -> str:
    """
    The run's compiled hash: the compiled commercial config's digest (the
    run's ``commercial_digest``) and each option's compiled finance digest.
    """
    return _sha16({"commercial": commercial_digest,
                   "finance": {str(k): v for k, v in finance_digests.items()}})


def engine_recorded(hashes: Mapping[str, Any] | Any, run: Mapping[str, Any] | None = None
                    ) -> bool:
    """
    Whether the run recorded its engine inputs: a compiled hash in the
    findings and, when the run record is given, the study's export series
    the forks were bound to (a run before WP6 has neither).
    """
    if not _field(hashes, "compiled_hash"):
        return False
    return run is None or bool(run.get("export_series"))


def compiled_matches(hashes: Mapping[str, Any] | Any, fork_uuid: str,
                     current: str | None) -> bool:
    """
    True when the fork's engine inputs on disk (``current``, from
    :func:`fork_engine_digest`) are the ones the run wrote. A run without a
    compiled hash, a fork it recorded no digest for, or an unreadable config
    never matches.
    """
    if not _field(hashes, "compiled_hash"):
        return False
    recorded = (_field(hashes, "option_compiled_hashes", None) or {}).get(str(fork_uuid))
    return recorded is not None and current is not None and current == recorded
