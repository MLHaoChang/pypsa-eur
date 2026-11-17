"""Where each GC package's output reaches the report pack (GC-09).

``SOURCES`` maps a package id (``GC-03`` ...) to a provider: called with the
project, it returns the judged tables that package produced, or None while it
has produced nothing. GC-09 owns no engine code; each package registers one
line here as it lands (spec, "How they meet"), reading its output through
``services.campus_electrical_service``. A package with no provider, or whose
provider returns None, shows as pending on the tracker and in the pack.
"""

from __future__ import annotations

from typing import Callable

from models.report import Table

Provider = Callable[[object], "list[Table] | None"]

#: package id → provider. Empty until GC-01..GC-08 land.
SOURCES: dict[str, Provider] = {}


def tables(project, package: str) -> list[Table] | None:
    provider = SOURCES.get(package)
    return provider(project) if provider else None


def package_available(project, package: str) -> bool:
    return tables(project, package) is not None
