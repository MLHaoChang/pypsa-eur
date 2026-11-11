"""
Chart snapshots: the charts a user pins from Results for the study report
(ui-modern 02, slice 7).

A snapshot is the picture the user saw (a PNG the browser drew) plus the
`chart_snapshot@1` record that says what it shows: the view, the title, the
source line, and the table behind it. Files live in `<project>/reports/_charts/`
as `<id>.json` and `<id>.png`; `_charts` is not a report id, so the report
store never lists, copies or clears it. Only the server writes `id`,
`created_at` and `solve_fingerprint`; the client sends the rest.

Rules:

* Closed schema (`extra="forbid"`); cells are text up to 200 characters, a
  finite number or null; never a bool, NaN or infinity.
* The PNG is checked by `site_service.validate_thumbnail` (type and size) and
  by its IHDR (side and pixel caps).
* One lock (`_charts/.lock`, `harness.durable.file_lock`) covers the count and
  the write, so the cap of 12 holds under concurrency. Nothing else is taken
  inside it. It never makes the project directory and refuses while the
  project is closing; `store.reports_closed` takes it last (SH-07f, GK B K1).
* Reading never raises for a bad file: `report_figures` turns it into a gap.
* A snapshot is outside the evidence hash and never reaches a model prompt.
"""

from __future__ import annotations

import logging
import math
import pathlib
import re
import secrets
import struct
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

logger = logging.getLogger(__name__)

SCHEMA = "chart_snapshot@1"
CHARTS_DIRNAME = "_charts"
LOCK_FILE = ".lock"
MAX_CHARTS = 12
MAX_CELLS = 10_000
MAX_CELL_CHARS = 200
MAX_SIDE = 8000
MAX_PIXELS = 16_000_000
MAX_SPEC_BYTES = 3 * 1024 * 1024
LOCK_TIMEOUT_S = 5
ID_RE = re.compile(r"chart-[0-9a-f]{16}")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_MIN_PNG = 33  # magic + the IHDR chunk (length, type, 13 bytes, crc)


class ChartSnapshotError(ValueError):
    """Base of the refusals; the route maps each to its status."""


class ChartSnapshotInvalid(ChartSnapshotError):
    """422 `chart_snapshot_invalid`."""


class ChartPngTooLarge(ChartSnapshotError):
    """413 `chart_png_too_large`."""


class ChartLimitReached(ChartSnapshotError):
    """409 `chart_limit_reached`; `total` is the true number saved."""

    def __init__(self, total: int):
        super().__init__(
            f"the project already has {total} saved charts (limit {MAX_CHARTS})"
        )
        self.total = total


class ChartNotFound(ChartSnapshotError):
    """404, one answer for an unknown, malformed, ghost or foreign id."""


class ChartBusy(ChartSnapshotError):
    """409 `chart_busy`: the chart lock stayed held."""


Cell = StrictStr | StrictInt | StrictFloat | None
_Text = Annotated[StrictStr, Field(max_length=MAX_CELL_CHARS)]


class ChartSpec(BaseModel):
    """What the browser sends (and the server stores back unchanged)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    view: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    title: Annotated[StrictStr, Field(min_length=1, max_length=MAX_CELL_CHARS)]
    figure: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    unit: Annotated[StrictStr, Field(max_length=40)] | None = None
    source_line: _Text
    period: Annotated[StrictStr, Field(max_length=MAX_CELL_CHARS)] | None = None
    out_of_date_when_added: StrictBool
    columns: Annotated[list[_Text], Field(min_length=1, max_length=100)]
    rows: Annotated[list[list[Cell]], Field(max_length=MAX_CELLS)]
    rows_total: Annotated[StrictInt, Field(ge=0, le=10**9)]

    @field_validator("rows", mode="before")
    @classmethod
    def _closed_cells(cls, rows: Any) -> Any:
        # Strict unions already keep a bool out of a number; infinity and NaN
        # are the cases the types cannot see.
        if isinstance(rows, list):
            for row in rows:
                for cell in row if isinstance(row, list) else ():
                    if isinstance(cell, float) and not math.isfinite(cell):
                        raise ValueError("a cell must be a finite number")
        return rows

    @field_validator("rows")
    @classmethod
    def _cell_text(cls, rows: list[list[Cell]]) -> list[list[Cell]]:
        for row in rows:
            for cell in row:
                if isinstance(cell, str) and len(cell) > MAX_CELL_CHARS:
                    raise ValueError(
                        f"a text cell holds at most {MAX_CELL_CHARS} characters"
                    )
                if isinstance(cell, int) and abs(cell) >= 2**63:
                    raise ValueError("a number cell is out of range")
        return rows

    @model_validator(mode="after")
    def _shape(self) -> ChartSpec:
        width = len(self.columns)
        if any(len(row) != width for row in self.rows):
            raise ValueError("every row needs one cell per column")
        if len(self.rows) * width > MAX_CELLS:
            raise ValueError(f"a chart table holds at most {MAX_CELLS} cells")
        if self.rows_total < len(self.rows):
            raise ValueError("rows_total is below the rows sent")
        return self


class ChartSnapshot(ChartSpec):
    """The stored record: the spec plus what only the server knows."""

    schema_version: Literal["chart_snapshot@1"] = SCHEMA
    id: Annotated[StrictStr, Field(pattern=ID_RE.pattern)]
    created_at: StrictStr
    solve_fingerprint: StrictStr | None = None


@dataclass(frozen=True)
class ChartFigure:
    figure_id: str
    png: bytes | None
    caption: str
    gap: str | None = None
    earlier: bool = False


class ReportFigures(dict):
    """The figure PNGs a report is built from, with the readable and the
    unreadable charts beside them (`.charts`), so `_evidence_for` and
    `_render_figures` keep their `dict[str, bytes]` shape."""

    def __init__(self, base: Any = (), charts: list[ChartFigure] | None = None):
        super().__init__(base)
        self.charts: list[ChartFigure] = list(charts or [])


# ── files ────────────────────────────────────────────────────────────────


def charts_dir(project_dir: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(project_dir) / "reports" / CHARTS_DIRNAME


def _valid_id(chart_id: Any) -> str:
    if not isinstance(chart_id, str) or not ID_RE.fullmatch(chart_id):
        raise ChartNotFound("no such chart")
    return chart_id


def _file(project_dir: pathlib.Path, chart_id: str, suffix: str) -> pathlib.Path:
    # The name comes only from the fullmatch-checked id; the resolved parent must
    # be `<project>/reports/_charts` itself (not a link out of the project).
    path = charts_dir(project_dir) / f"{_valid_id(chart_id)}{suffix}"
    root = pathlib.Path(project_dir).resolve() / "reports" / CHARTS_DIRNAME
    if path.parent.resolve() != root:
        raise ChartNotFound("no such chart")
    return path


def _regular(path: pathlib.Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _ids(project_dir: pathlib.Path) -> list[str]:
    """Every id that has a record file, readable or not."""
    root = charts_dir(project_dir)
    try:
        names = [p.name for p in root.iterdir()]
    except OSError:
        return []
    return sorted(
        n[: -len(".json")]
        for n in names
        if n.endswith(".json") and ID_RE.fullmatch(n[: -len(".json")])
    )


@contextmanager
def _lock(
    project_dir: pathlib.Path,
    *,
    timeout: float = LOCK_TIMEOUT_S,
    closing_ok: bool = False,
):
    """The chart lock. Never makes the project directory (a missing one is
    `ReportProjectGone`, as `report_lock`), and refuses `ReportBusy` once the
    project is being deleted or moved (`reports/.closing`), checked under the
    lock; `reports_closed` takes it with `closing_ok`. `TimeoutError` when it
    stays held."""
    from harness.durable import file_lock
    from services.reports import store

    root = charts_dir(project_dir)
    try:
        root.parent.mkdir(exist_ok=True)
        root.mkdir(exist_ok=True)
    except FileNotFoundError as exc:
        raise store.ReportProjectGone("the project directory is gone") from exc
    with file_lock(root / LOCK_FILE, timeout=timeout, busy="chart store busy"):
        if not closing_ok and store.reports_closing(pathlib.Path(project_dir)):
            raise store.ReportBusy("the project is being deleted or moved")
        yield


def validate_png(data: bytes) -> None:
    """`ChartPngTooLarge` over the byte, side or pixel caps; `ChartSnapshotInvalid`
    when it is not a PNG with a readable IHDR."""
    from services import site_service

    try:
        site_service.validate_thumbnail(data)
    except site_service.ThumbnailTooLarge as exc:
        raise ChartPngTooLarge(f"the png part is too large: {exc}") from exc
    except site_service.ThumbnailInvalid as exc:
        raise ChartSnapshotInvalid(str(exc)) from exc
    if len(data) < _MIN_PNG:
        raise ChartSnapshotInvalid("the PNG is too short to hold its header")
    length, kind, width, height = struct.unpack(">I4sII", data[8:24])
    if length != 13 or kind != b"IHDR":
        raise ChartSnapshotInvalid("the PNG does not start with an IHDR chunk")
    if width < 1 or height < 1:
        raise ChartSnapshotInvalid("the PNG has an empty side")
    if width > MAX_SIDE or height > MAX_SIDE or width * height > MAX_PIXELS:
        raise ChartPngTooLarge(
            f"the PNG is over {MAX_SIDE} px a side or {MAX_PIXELS // 1_000_000} million pixels"
        )


def active_solve_fingerprint() -> str | None:
    """The input fingerprint of the active context's solve, None when there is none."""
    try:
        from services.pypsa_service import PyPSAService

        value = PyPSAService.get_active_context().network.meta.get(
            "assistant_solve_input_fingerprint"
        )
    except Exception:  # noqa: BLE001 -- no context or no solve: the chart just has no fingerprint
        return None
    return value if isinstance(value, str) else None


def add(
    project_dir: pathlib.Path,
    spec: ChartSpec,
    png: bytes,
    *,
    solve_fingerprint: str | None,
) -> ChartSnapshot:
    """Validate and store one snapshot; `ChartLimitReached` for the 13th."""
    from services.atomic_io import atomic_write_bytes, atomic_write_text

    validate_png(png)
    try:
        with _lock(project_dir):
            total = len(_ids(project_dir))
            if total >= MAX_CHARTS:
                raise ChartLimitReached(total)
            while (
                True
            ):  # a new id never names a file that exists (a figure id, a ghost png)
                chart_id = "chart-" + secrets.token_hex(8)
                if not any(
                    _file(project_dir, chart_id, s).exists() for s in (".json", ".png")
                ):
                    break
            snapshot = ChartSnapshot(
                **spec.model_dump(),
                id=chart_id,
                solve_fingerprint=solve_fingerprint,
                created_at=datetime.now(tz=UTC).isoformat(timespec="seconds"),
            )
            atomic_write_bytes(_file(project_dir, chart_id, ".png"), png)
            try:
                atomic_write_text(
                    _file(project_dir, chart_id, ".json"),
                    snapshot.model_dump_json(indent=2),
                )
            except BaseException:
                _file(project_dir, chart_id, ".png").unlink(missing_ok=True)
                raise
            return snapshot
    except TimeoutError as exc:
        raise ChartBusy(str(exc)) from exc


def _load(project_dir: pathlib.Path, chart_id: str) -> ChartSnapshot:
    path = _file(project_dir, chart_id, ".json")
    if not _regular(path):
        raise ChartNotFound("no such chart")
    snapshot = ChartSnapshot.model_validate_json(path.read_bytes())
    if snapshot.id != chart_id:
        raise ValueError("the record names another id")
    return snapshot


def list_snapshots(project_dir: pathlib.Path) -> list[ChartSnapshot]:
    """Every readable snapshot, oldest first."""
    out = []
    for chart_id in _ids(project_dir):
        try:
            out.append(_load(project_dir, chart_id))
        except (OSError, ValueError, ChartNotFound):
            continue
    return sorted(out, key=lambda s: (s.created_at, s.id))


def delete(project_dir: pathlib.Path, chart_id: str) -> None:
    """Remove a snapshot's files; `ChartNotFound` when it has no record file (a
    ghost png is as unknown as a missing id, and is left alone)."""
    record, png = (
        _file(project_dir, chart_id, ".json"),
        _file(project_dir, chart_id, ".png"),
    )
    if not charts_dir(project_dir).is_dir():
        raise ChartNotFound("no such chart")
    try:
        with _lock(project_dir):
            if not (record.exists() or record.is_symlink()):
                raise ChartNotFound("no such chart")
            for path in (record, png):
                path.unlink(missing_ok=True)
    except TimeoutError as exc:
        raise ChartBusy(str(exc)) from exc


def png_bytes(project_dir: pathlib.Path, chart_id: str) -> bytes:
    """The PNG of a snapshot that has both files; `ChartNotFound` otherwise."""
    record, png = (
        _file(project_dir, chart_id, ".json"),
        _file(project_dir, chart_id, ".png"),
    )
    if not (_regular(record) and _regular(png)):
        raise ChartNotFound("no such chart")
    try:
        return png.read_bytes()
    except OSError as exc:
        raise ChartNotFound("no such chart") from exc


def earlier_solve(snapshot: ChartSnapshot, current_fp: str | None) -> bool:
    """Whether the snapshot was taken from a solve other than the current one."""
    return snapshot.solve_fingerprint != current_fp


def _caption(snapshot: ChartSnapshot, earlier: bool) -> str:
    shown = (
        datetime.fromisoformat(snapshot.created_at)
        .astimezone(UTC)
        .strftime("%Y-%m-%d %H:%M")
    )
    text = f"{snapshot.title.rstrip('.')}. {snapshot.source_line.rstrip('.')}. As shown in Results on {shown} UTC."
    if snapshot.out_of_date_when_added:
        text += " Out of date when added."
    if earlier:
        text += " From an earlier solve."
    return text


def report_figures(
    project_dir: pathlib.Path, current_fp: str | None
) -> list[ChartFigure]:
    """One `ChartFigure` per saved chart, oldest first; an unreadable one
    becomes a gap (`png` None), never an exception."""
    good: list[tuple[ChartSnapshot, bytes]] = []
    gaps: list[ChartFigure] = []
    for chart_id in _ids(project_dir):
        try:
            snapshot = _load(project_dir, chart_id)
            png = png_bytes(project_dir, chart_id)
            validate_png(png)
        except (OSError, ValueError, ChartNotFound):
            gaps.append(
                ChartFigure(
                    chart_id,
                    None,
                    "",
                    f"A chart saved from Results ({chart_id}) could not be read and is left out of this report.",
                )
            )
            continue
        good.append((snapshot, png))
    good.sort(key=lambda item: (item[0].created_at, item[0].id))
    figures = []
    for snapshot, png in good:
        earlier = earlier_solve(snapshot, current_fp)
        figures.append(
            ChartFigure(snapshot.id, png, _caption(snapshot, earlier), None, earlier)
        )
    return figures + gaps
