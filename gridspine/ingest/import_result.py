"""
The shared import contract: what every model importer returns, and the registry.

Every importer (Excel input workbook, PSS/E, PowerFactory, PLEXOS, ...) lives
in ``gridspine/ingest/<format>_import.py`` and provides::

    def read_<format>(paths: Sequence[Path], options: Mapping[str, Any]) -> ImportResult

plus a module-level ``IMPORTER = ImporterSpec(...)`` naming that reader. The
registry below finds those modules by their ``_import`` suffix, so adding an
importer never edits a shared list.

Rules every importer keeps:

- **Pure.** No app, database or web imports; it reads files and returns data.
- **Never drop a row silently.** A source item that does not become part of the
  network is listed in ``report.unmapped`` with the reason; a value the
  importer had to choose is listed in ``report.assumed`` with why.
- **Plain names.** Count keys, parameter names and reasons are what a user
  reads in the preview ("Lines", "Thermal rating"), not raw field codes.
- **Sidecars** hold everything the project stores outside ``network.nc``
  (time series, costs, dynamic data), keyed by the project-relative file name.

This module imports no engine; ``pypsa`` appears only as a type hint. The
``*_import.py`` importers are the one place in ``ingest/`` allowed to import
``pypsa``, because their output is a ``pypsa.Network``.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - type hint only, keeps the engine out
    import pypsa

DEFAULT_PACKAGE = "gridspine.ingest"
_FORMAT_ID = re.compile(r"^[a-z][a-z0-9_]*$")


class ImportFormatError(ValueError):
    """
    The files cannot be read as the format the importer handles.

    The message is shown to the user, so it names the file and what is wrong
    in plain words.
    """


def _require_text(value: Any, what: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} must be a non-empty string, got {value!r}")


@dataclass(frozen=True)
class Unmapped:
    """A source item that did not become part of the network, and why."""

    source_item: str
    reason: str

    def __post_init__(self) -> None:
        _require_text(self.source_item, "Unmapped.source_item")
        _require_text(self.reason, "Unmapped.reason")


@dataclass(frozen=True)
class Assumed:
    """A value the importer chose because the source did not give one."""

    asset: str
    parameter: str
    value: Any
    why: str

    def __post_init__(self) -> None:
        _require_text(self.asset, "Assumed.asset")
        _require_text(self.parameter, "Assumed.parameter")
        _require_text(self.why, "Assumed.why")


@dataclass
class ImportReport:
    """What an import read, made, skipped and assumed, for the preview."""

    source_format: str
    source_files: list[str]
    counts: dict[str, int] = field(default_factory=dict)
    unmapped: list[Unmapped] = field(default_factory=list)
    assumed: list[Assumed] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _require_text(self.source_format, "ImportReport.source_format")
        self.source_files = list(self.source_files)
        self.counts = dict(self.counts)
        self.unmapped = list(self.unmapped)
        self.assumed = list(self.assumed)
        self.warnings = list(self.warnings)
        for name in self.source_files:
            _require_text(name, "source file name")
        for name, n in self.counts.items():
            _require_text(name, "count name")
            if not isinstance(n, int) or isinstance(n, bool) or n < 0:
                raise ValueError(
                    f"count for {name!r} must be a non-negative int, got {n!r}"
                )
        for item in self.unmapped:
            if not isinstance(item, Unmapped):
                raise TypeError(f"unmapped entries must be Unmapped, got {item!r}")
        for item in self.assumed:
            if not isinstance(item, Assumed):
                raise TypeError(f"assumed entries must be Assumed, got {item!r}")
        for text in self.warnings:
            _require_text(text, "warning")

    def to_dict(self) -> dict[str, Any]:
        """A JSON-ready dict (``value`` in ``assumed`` must itself be JSON-ready)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ImportReport:
        return cls(
            source_format=data["source_format"],
            source_files=data.get("source_files", []),
            counts=data.get("counts", {}),
            unmapped=[Unmapped(**u) for u in data.get("unmapped", [])],
            assumed=[Assumed(**a) for a in data.get("assumed", [])],
            warnings=data.get("warnings", []),
        )


@dataclass
class ImportResult:
    """An importer's output: the network, the files stored beside it, the report."""

    network: pypsa.Network
    report: ImportReport
    sidecars: dict[str, Any] = field(default_factory=dict)


Reader = Callable[[Sequence[Path], Mapping[str, Any]], ImportResult]


@dataclass(frozen=True)
class ImporterSpec:
    """A registry entry: how the app offers and runs one importer."""

    format_id: str
    display_name: str
    extensions: tuple[str, ...]
    help: str
    reader: Reader

    def __post_init__(self) -> None:
        if not isinstance(self.format_id, str) or not _FORMAT_ID.match(self.format_id):
            raise ValueError(
                f"format_id must be lowercase letters, digits and _, got {self.format_id!r}"
            )
        _require_text(self.display_name, "display_name")
        _require_text(self.help, "help")
        if not callable(self.reader):
            raise ValueError(f"reader for {self.format_id!r} must be callable")
        exts = tuple(_normalise_ext(e) for e in self.extensions)
        if not exts:
            raise ValueError(
                f"{self.format_id!r} must name at least one file extension"
            )
        object.__setattr__(self, "extensions", exts)


def _normalise_ext(ext: str) -> str:
    _require_text(ext, "file extension")
    ext = ext.strip().lower()
    return ext if ext.startswith(".") else f".{ext}"


def importers(package: str = DEFAULT_PACKAGE) -> tuple[ImporterSpec, ...]:
    """
    Every registered importer, sorted by format id.

    Imports each ``*_import`` module of ``package`` and reads its ``IMPORTER``.
    """
    pkg = importlib.import_module(package)
    specs: dict[str, ImporterSpec] = {}
    for info in pkgutil.iter_modules(pkg.__path__):
        if not info.name.endswith("_import"):
            continue
        module = importlib.import_module(f"{package}.{info.name}")
        spec = getattr(module, "IMPORTER", None)
        if not isinstance(spec, ImporterSpec):
            raise TypeError(
                f"{module.__name__} must define IMPORTER = ImporterSpec(...)"
            )
        if spec.format_id in specs:
            raise ValueError(f"format id {spec.format_id!r} is registered twice")
        specs[spec.format_id] = spec
    return tuple(specs[k] for k in sorted(specs))


def get(format_id: str, package: str = DEFAULT_PACKAGE) -> ImporterSpec:
    """The importer for ``format_id``; KeyError names the known ids."""
    for spec in importers(package):
        if spec.format_id == format_id:
            return spec
    known = ", ".join(s.format_id for s in importers(package)) or "none"
    raise KeyError(f"no importer {format_id!r}; known: {known}")


def for_extension(ext: str, package: str = DEFAULT_PACKAGE) -> list[ImporterSpec]:
    """Importers that accept files with ``ext`` (case and leading dot ignored)."""
    wanted = _normalise_ext(ext)
    return [s for s in importers(package) if wanted in s.extensions]
