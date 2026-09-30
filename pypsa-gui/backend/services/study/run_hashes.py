"""
The ONE hash rule a decision study's readers share (plan S5 [B1], S7).

A reader of a finished run may only attribute its results to the ledger and
the option networks the run was built from. Three readers apply the rule:

* the case route (``routers/studies.py::_option_case``) answers 409
  ``ledger_changed_since_run`` / ``fork_changed_since_run``;
* the findings (``services/study/findings.py::load_inputs``) refuse a changed
  ledger and leave a changed fork out;
* the report (``services/study/report.py::stale_reasons``) marks itself
  ``stale`` and names why.

They call these functions, so a report is never fresh where a case would be
refused (gate S5 carry: "the stale flag and the case's 409 share one hash
rule"). Hashes are recorded at findings time (``Findings.hashes``): the
ledger's (``packs.ledger_hash``) and each OPTION fork's ``network.nc``
(sha256[:16], keyed by fork uuid). Tornado forks are not in that map — they
are deleted after the read — so they never make anything stale.
"""
from __future__ import annotations

import pathlib
from collections.abc import Mapping
from typing import Any

__all__ = ["file_hash", "fork_file_hash", "fork_matches", "ledger_matches",
           "recorded_ledger_hash"]


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
