"""Where each GC package's output reaches the report pack (GC-09).

``SOURCES`` maps a package id (``GC-03`` ...) to a provider: called with the
project, it returns the judged tables that package produced, or None while it
has produced nothing. GC-09 owns no engine code; each package registers one
line here as it lands (spec, "How they meet"), reading its output through
``services.campus_electrical_service``. A package with no provider, or whose
provider returns None, shows as pending on the tracker and in the pack; one
whose provider raises shows as unavailable, never as an error of the request.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from models.report import Table

log = logging.getLogger(__name__)

Provider = Callable[[object], "list[Table] | None"]

#: package id → provider. Empty until GC-01..GC-08 land.
SOURCES: dict[str, Provider] = {}


def read(project, package: str) -> tuple[str, list[Table] | None]:
    """``("present", tables)``, ``("pending", None)`` while the package has
    produced nothing, or ``("unavailable", None)`` when its provider raises."""
    provider = SOURCES.get(package)
    if provider is None:
        return "pending", None
    try:
        got = provider(project)
    except Exception:  # noqa: BLE001 — one package's failure must not fail the tracker or the pack
        log.exception("grid-compliance source %s could not be read", package)
        return "unavailable", None
    return ("pending", None) if got is None else ("present", got)


def package_available(project, package: str) -> bool:
    return read(project, package)[0] == "present"
