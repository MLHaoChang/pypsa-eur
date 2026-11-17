"""
The shared import contract: what every model importer returns, and the registry.

Every importer (Excel input workbook, PSS/E, PowerFactory, PLEXOS) is declared
once in ``DECLARED`` below and lives in ``gridspine/ingest/<id>_import.py``,
providing::

    def read_<id>(paths: Sequence[Path], options: Mapping[str, Any]) -> ImportResult

The registry never imports an importer to list it: ``importers()`` only checks
that the module exists, and the reader is imported when it runs. So listing the
formats does not import ``pypsa``, and the four importers can land in any order
without editing a shared list.

Rules every importer keeps:

- **Pure.** No app, database or web imports; it reads files and returns data.
- **Never drop a row silently.** A source item that does not become part of the
  network is listed in ``report.unmapped`` with the reason; a value the
  importer had to choose is listed in ``report.assumed`` with why. Both stay
  full lists, never summarised as counts.
- **Plain names.** Count keys, parameter names and reasons are what a user
  reads in the preview ("Lines", "Thermal rating"), not raw field codes.
- **JSON-ready values.** ``Assumed.value`` and a suggestion's values must
  survive ``json.dumps(..., allow_nan=False)``: no NaN, no infinity.
- **Sidecars** hold everything the project stores outside ``network.nc``,
  keyed by the project file name they become (``user_ts.json``,
  ``metadata.json``, ...), with JSON-ready content or ``bytes``. Names are
  plain file names (no folders, no ``..``) and never ``RESERVED_SIDECARS``.
- **Errors.** Unreadable files, an unknown format and an importer that cannot
  load all raise ``ImportFormatError`` with a reason the user can read.
- **Hints.** An importer that suspects a value is wrong ("likely kV, not V")
  adds a ``Suggestion`` with ``by="importer"``; the assistant adds its own
  with ``by="assistant"``. Suggestions are proposals the user accepts or
  rejects in the preview: an importer never applies its own suggestion.

Engine cage (owner ruling, 2026-11-17): ``gridspine/ingest/*_import.py`` are
the only modules outside ``producers/`` allowed to import ``pypsa``, because
their output is a ``pypsa.Network``. This module imports no engine; ``pypsa``
appears only as a type hint.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - type hint only, keeps the engine out
    import pypsa

DEFAULT_PACKAGE = "gridspine.ingest"
CONFIDENCE = ("high", "medium", "low")
SUGGESTED_BY = ("importer", "assistant")
# Files the project writes itself; an importer's sidecar never replaces them.
RESERVED_SIDECARS = frozenset({"network.nc", "results_state.pkl"})
_WINDOWS_DEVICES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)
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


def _require_json(value: Any, what: str) -> None:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{what} must be JSON-ready and finite, got {value!r}"
        ) from exc


@dataclass(frozen=True)
class Unmapped:
    """A source item that did not become part of the network, and why."""

    source_item: str
    reason: str

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
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
        self.validate()

    def validate(self) -> None:
        _require_text(self.asset, "Assumed.asset")
        _require_text(self.parameter, "Assumed.parameter")
        _require_json(self.value, "Assumed.value")
        _require_text(self.why, "Assumed.why")


@dataclass(frozen=True)
class Suggestion:
    """
    A proposed change the user accepts or rejects in the preview.

    ``target`` is an asset or, for an unmapped item, the source item;
    ``current`` is ``None`` when the value is missing. ``source`` says where
    the proposed value comes from ("catalogue default", "typical for 110 kV
    lines", "sheet Lines, row 12").
    """

    target: str
    parameter: str
    current: Any
    proposed: Any
    why: str
    source: str
    confidence: str
    by: str = "importer"

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _require_text(self.target, "Suggestion.target")
        _require_text(self.parameter, "Suggestion.parameter")
        _require_json(self.current, "Suggestion.current")
        _require_json(self.proposed, "Suggestion.proposed")
        _require_text(self.why, "Suggestion.why")
        _require_text(self.source, "Suggestion.source")
        if self.confidence not in CONFIDENCE:
            raise ValueError(f"Suggestion.confidence must be one of {CONFIDENCE}")
        if self.by not in SUGGESTED_BY:
            raise ValueError(f"Suggestion.by must be one of {SUGGESTED_BY}")


@dataclass
class ImportReport:
    """What an import read, made, skipped and assumed, for the preview."""

    source_format: str
    source_files: list[str]
    counts: dict[str, int] = field(default_factory=dict)
    unmapped: list[Unmapped] = field(default_factory=list)
    assumed: list[Assumed] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)

    def __post_init__(self) -> None:
        _require_text(self.source_format, "ImportReport.source_format")
        self.source_files = list(self.source_files)
        self.counts = dict(self.counts)
        self.unmapped = list(self.unmapped)
        self.assumed = list(self.assumed)
        self.warnings = list(self.warnings)
        self.suggestions = list(self.suggestions)
        self.validate()

    def validate(self) -> None:
        """Check every field; run again before output, since lists stay mutable."""
        _require_text(self.source_format, "ImportReport.source_format")
        for name in self.source_files:
            _require_text(name, "source file name")
        for name, n in self.counts.items():
            _require_text(name, "count name")
            if not isinstance(n, int) or isinstance(n, bool) or n < 0:
                raise ValueError(
                    f"count for {name!r} must be a non-negative int, got {n!r}"
                )
        for items, kind in (
            (self.unmapped, Unmapped),
            (self.assumed, Assumed),
            (self.suggestions, Suggestion),
        ):
            for item in items:
                if not isinstance(item, kind):
                    raise TypeError(f"{kind.__name__} entry expected, got {item!r}")
                item.validate()
        for text in self.warnings:
            _require_text(text, "warning")

    def to_dict(self) -> dict[str, Any]:
        """A JSON-ready dict; ``json.dumps(..., allow_nan=False)`` accepts it."""
        self.validate()
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
            suggestions=[Suggestion(**s) for s in data.get("suggestions", [])],
        )


@dataclass
class ImportResult:
    """An importer's output: the network, the files stored beside it, the report."""

    network: pypsa.Network
    report: ImportReport
    sidecars: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.sidecars = dict(self.sidecars)
        self.validate()

    def validate(self) -> None:
        """Check the report and every sidecar; ``read`` runs it again."""
        if not isinstance(self.report, ImportReport):
            raise TypeError(f"report must be an ImportReport, got {self.report!r}")
        self.report.validate()
        for name, content in self.sidecars.items():
            _require_sidecar_name(name)
            if not isinstance(content, bytes):
                _require_json(content, f"sidecar {name!r}")


def _require_sidecar_name(name: Any) -> None:
    """
    A plain file name in the project folder: no folders, no reserved files.

    Compared the way Windows and macOS do (case-insensitive, trailing dots and
    spaces dropped), so "Network.nc" cannot stand in for network.nc there.
    """
    _require_text(name, "sidecar file name")
    if (
        any(ord(c) < 32 or ord(c) == 127 for c in name)
        or "/" in name
        or "\\" in name
        or name.strip() != name
        or name in (".", "..")
        or ":" in name  # a Windows drive ("C:x.json") or alternate stream
    ):
        raise ValueError(f"sidecar file name must be a plain file name, got {name!r}")
    folded = name.casefold().rstrip(". ")
    if folded in {r.casefold() for r in RESERVED_SIDECARS}:
        raise ValueError(
            f"sidecar file name {name!r} is reserved for the project itself"
        )
    if folded.split(".")[0] in _WINDOWS_DEVICES:
        raise ValueError(
            f"sidecar file name {name!r} is a reserved device name on Windows"
        )


Reader = Callable[[Sequence[Path], Mapping[str, Any]], ImportResult]


@dataclass(frozen=True)
class ImporterSpec:
    """
    A registry entry: how the app offers one importer.

    The reader is ``read_<format_id>`` in ``<package>.<format_id>_import``,
    imported only by ``load_reader``.
    """

    format_id: str
    display_name: str
    extensions: tuple[str, ...]
    help: str
    package: str = DEFAULT_PACKAGE

    def __post_init__(self) -> None:
        if not isinstance(self.format_id, str) or not _FORMAT_ID.match(self.format_id):
            raise ValueError(
                f"format_id must be lowercase letters, digits and _, got {self.format_id!r}"
            )
        _require_text(self.display_name, "display_name")
        _require_text(self.help, "help")
        exts = tuple(_normalise_ext(e) for e in self.extensions)
        if not exts:
            raise ValueError(
                f"{self.format_id!r} must name at least one file extension"
            )
        object.__setattr__(self, "extensions", exts)

    @property
    def module(self) -> str:
        return f"{self.package}.{self.format_id}_import"

    def is_available(self) -> bool:
        """Whether the importer module is present, checked without importing it."""
        return importlib.util.find_spec(self.module) is not None

    def load_reader(self) -> Reader:
        """
        Import the reader; ImportFormatError when it cannot load.

        Only ImportError is translated (a missing module or dependency); any
        other error raised while importing the module is a bug and propagates.
        """
        try:
            module = importlib.import_module(self.module)
        except ImportError as exc:
            missing = exc.name or self.module
            raise ImportFormatError(
                f"The {self.display_name} importer cannot run: it needs the module "
                f"{missing!r}, which this installation does not have"
            ) from exc
        reader = getattr(module, f"read_{self.format_id}", None)
        if not callable(reader):
            raise ImportFormatError(
                f"The {self.display_name} importer is incomplete: {self.module} "
                f"has no read_{self.format_id}(paths, options)"
            )
        return reader


def _normalise_ext(ext: str) -> str:
    _require_text(ext, "file extension")
    ext = ext.strip().lower()
    return ext if ext.startswith(".") else f".{ext}"


DECLARED: tuple[ImporterSpec, ...] = (
    ImporterSpec(
        format_id="excel",
        display_name="Excel input workbook",
        extensions=(".xlsx",),
        help="A workbook with one sheet per asset type, as the app's own template exports it.",
    ),
    ImporterSpec(
        format_id="psse",
        display_name="PSS/E case",
        extensions=(".raw", ".dyr"),
        help="A PSS/E .raw network file, optionally with a .dyr dynamic data file.",
    ),
    ImporterSpec(
        format_id="dgs",
        display_name="PowerFactory export (DGS)",
        extensions=(".dgs",),
        help="A DIgSILENT PowerFactory network exported as a DGS file.",
    ),
    ImporterSpec(
        format_id="plexos",
        display_name="PLEXOS model",
        extensions=(".xml", ".xlsx"),
        help="A PLEXOS model exported as XML, or its input workbook.",
    ),
)


def importers(declared: Sequence[ImporterSpec] = DECLARED) -> tuple[ImporterSpec, ...]:
    """The declared importers present in this build, sorted by format id."""
    seen: set[str] = set()
    for spec in declared:
        if spec.format_id in seen:
            raise ValueError(f"format id {spec.format_id!r} is declared twice")
        seen.add(spec.format_id)
    return tuple(
        sorted((s for s in declared if s.is_available()), key=lambda s: s.format_id)
    )


def get(format_id: str, declared: Sequence[ImporterSpec] = DECLARED) -> ImporterSpec:
    """The importer for ``format_id``; ImportFormatError names the ones available."""
    available = importers(declared)
    for spec in available:
        if spec.format_id == format_id:
            return spec
    known = ", ".join(s.format_id for s in available) or "none"
    raise ImportFormatError(
        f"No importer {format_id!r} in this build; available: {known}"
    )


def for_extension(
    ext: str, declared: Sequence[ImporterSpec] = DECLARED
) -> list[ImporterSpec]:
    """
    Importers that accept files with ``ext`` (case and leading dot ignored).

    More than one can match (``.xlsx``): the user picks on screen, the app
    never picks silently.
    """
    wanted = _normalise_ext(ext)
    return [s for s in importers(declared) if wanted in s.extensions]


def read(
    format_id: str,
    paths: Sequence[Path],
    options: Mapping[str, Any] | None = None,
    declared: Sequence[ImporterSpec] = DECLARED,
) -> ImportResult:
    """Run one importer and check it kept the contract."""
    spec = get(format_id, declared)
    result = spec.load_reader()([Path(p) for p in paths], dict(options or {}))
    if not isinstance(result, ImportResult):
        raise TypeError(
            f"{spec.module} returned {type(result).__name__}, not ImportResult"
        )
    if result.report.source_format != format_id:
        raise ValueError(
            f"{spec.module} reported source_format {result.report.source_format!r}, "
            f"expected {format_id!r}"
        )
    result.validate()
    return result
