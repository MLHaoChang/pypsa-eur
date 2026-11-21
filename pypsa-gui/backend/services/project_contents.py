"""What a project is: the one table of the files and folders a project folder
holds, and which operations carry each one (project-io issue 01, PROJ-SELF-1).

Every copy path reads this table: the bundle export and import
(`routers/projects.py`), Save-As / Save-a-Copy (`_carry_sidecars_on_move`), a
scenario fork (`create_scenario`, `services/study/forks.py`), a snapshot
(`routers/snapshots.py`) and a template instantiation. The lists those modules
used to keep by hand (`_BUNDLE_FILES`, `_BUNDLE_DIRS`, ...) are derived from it
in `routers/projects.py`, so a new sidecar is added here once.

Names are literals, not the services' constants: importing every owning service
here would pull half the app into the router at import time. The guard in
`tests/test_project_contents.py` walks every module-level `SIDECAR_NAME` and
`*_FILE` constant and fails when one is neither in this table nor in
`NOT_CARRIED`, and fails when a table entry has no writer.

`experiments/` is deliberately absent: its plans name other projects' ids, so a
copy would hold duplicate or dangling plans (`test_bundle_provenance` pins it).
"""

from __future__ import annotations

import os as _os
import stat as _stat
import unicodedata as _unicodedata
import zipfile as _zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath as _Posix

#: The operations a project's contents travel through.
BUNDLE = "bundle"  # export to .pypsaproj.zip and import from it
SAVE_AS = "save_as"  # Save-As / Save-a-Copy into another project
FORK = "fork"  # a scenario fork and the study option forks
SNAPSHOT = "snapshot"  # snapshot create, and its restore
TEMPLATE = "template"  # copied from a template folder into a new project
OPS = (BUNDLE, SAVE_AS, FORK, SNAPSHOT, TEMPLATE)

FILE, DIR = "file", "dir"

#: Groups, which the derived lists in `routers/projects.py` split on.
#: SIDECAR: a top-level file. CAMPUS: a file under `campus_electrical/`.
#: FOLDER: working-state folder. HISTORY: append-only records (SR-1, F10).
SIDECAR, CAMPUS, FOLDER, HISTORY = "sidecar", "campus", "folder", "history"

_ALL = frozenset({BUNDLE, SAVE_AS, FORK, SNAPSHOT})


@dataclass(frozen=True)
class Entry:
    path: str
    kind: str
    group: str
    carried_by: frozenset
    why: str
    #: op -> sub-paths of a DIR entry that op leaves out (and, on a snapshot
    #: restore, leaves in place on the live side).
    skip: dict = field(default_factory=dict)
    #: True for a name whose writer is still in review; the guard accepts a
    #: table entry without a writer only when this is set.
    pending_writer: str = ""


def _f(path, ops, why, group=SIDECAR, **kw):
    return Entry(path, FILE, group, frozenset(ops), why, **kw)


def _importer(path, writer):
    return _f(
        path,
        _ALL,
        "an importer's record of the source model it read",
        pending_writer=writer,
    )


def _marker_entry() -> Entry:
    """The template marker's row. Named here only to say which operations copy
    it; every read goes through `services.template_marker` (its test exempts
    this function, as it does `solve_capture.sidecars`)."""
    return _f(
        "eh_template.json",
        _ALL - {FORK} | {TEMPLATE},
        "the template marker (UM07-KIND-1)",
    )


TABLE: tuple[Entry, ...] = (
    _f("network.nc", _ALL, "the network"),
    _f("user_ts.json", _ALL, "uploaded time series"),
    _f("solver_config.json", _ALL | {TEMPLATE}, "solver settings"),
    _f(
        "metadata.json",
        _ALL,
        "name, scenario labels, results flags and the project kind",
    ),
    _f("layout.json", _ALL, "diagram layout"),
    _f("results_state.pkl", _ALL, "side results of the last solve"),
    _f("adequacy_worksheet.json", _ALL, "the adequacy worksheet"),
    _f("adequacy_stress_scenarios.json", _ALL | {TEMPLATE}, "Class-C stress scenarios"),
    _f("asset_health.json", _ALL, "outage-rate provenance ledger (review S7)"),
    _f("sites.json", _ALL, "3D site view: sites, boundaries, placements"),
    _f("site_anchor.json", _ALL, "the confirmed site anchor (SC-01a)"),
    _f("map_layout.json", _ALL, "map view: routed branches, dragged bubbles"),
    _f("island_config.json", _ALL | {TEMPLATE}, "island control modes and dynamics"),
    _f("island_cuts.json", _ALL, "frequency cuts the user applied"),
    _f("library_refs.json", _ALL, "Library series pins (WP1.1c)"),
    _f("flow_acks.json", _ALL, "acknowledged project-flow warnings"),
    _f("flow_scope.json", _ALL, "the Run's work cards (W4a)"),
    _f("grid_readiness_limits.json", _ALL, "the user's grid-readiness limits"),
    _f(
        "sensitivity_case.json",
        _ALL - {FORK},
        "a case's baseline record; a fork of a case is not that case",
    ),
    # Dropped by every copy path until project-io 01. A fork does not take
    # them: a sensitivity case reads its base's marker (template_marker), and a
    # copy of its own would cut that link.
    _marker_entry(),
    _f(
        "cases.json",
        _ALL - {FORK} | {TEMPLATE},
        "a template's pre-built sensitivity cases",
    ),
    _importer("psse_case.json", "PSS/E importer"),
    _importer("psse_dynamics.json", "PSS/E importer"),
    _importer("plexos_areas.json", "PLEXOS importer"),
    _importer("plexos_fuel_prices.json", "PLEXOS importer"),
    _importer("import_report.json", "shared import contract (#1600)"),
    # The campus electrical study (plan C9): bundle only. The grid-code
    # documents and drafts stay with their licensee.
    *(
        _f(f"campus_electrical/{p}", {BUNDLE}, "campus electrical study", group=CAMPUS)
        for p in (
            "campus_input.yaml",
            "campus_assets.yaml",
            "run/campus_assets_used.yaml",
            "run/campus_investment.csv",
            "run/campus_cost.csv",
            "run/campus_compliance_invested.csv",
            "run/campus_invest_scope.json",
            "island_study.json",
        )
    ),
    Entry("uploads", DIR, FOLDER, _ALL, "chat uploads"),
    Entry("reports", DIR, FOLDER, _ALL, "study reports"),
    Entry("studies", DIR, FOLDER, _ALL, "decision-study sidecars"),
    Entry("sites", DIR, FOLDER, _ALL, "3D site context"),
    Entry(
        "run_evidence",
        DIR,
        HISTORY,
        frozenset({BUNDLE, SAVE_AS}),
        "run history; a restore must not erase later runs, a fork did not run them",
    ),
    # The planning -> dynamics study. Its `run/` is a result: a snapshot leaves
    # it out (and a restore leaves the live one), so a restored study reads
    # stale through the normal checks. Not forked: a fork does not take the
    # project kind, and a capacity-expansion project holds no gridspine/.
    Entry(
        "gridspine",
        DIR,
        FOLDER,
        frozenset({BUNDLE, SAVE_AS, SNAPSHOT}),
        "the planning -> dynamics study",
        skip={SNAPSHOT: ("run",)},
    ),
)

#: Module-level `SIDECAR_NAME` / `*_FILE` names that are NOT table entries, and why.
NOT_CARRIED: dict[str, str] = {
    ".eh_template.lock": "a lock file",
    ".lock": "a lock file",
    ".uploads.lock": "a lock file",
    "install.json": "the app install record, not a project file",
    "chat.jsonl": "a per-conversation thread; forks copy it themselves",
    # Inside a folder the table carries whole.
    "comments.json": "inside reports/",
    "context.json": "inside sites/",
    "generation.json": "inside sites/",
    "thumb.png": "inside sites/",
    # Campus electrical files the bundle does not carry (plan C9).
    "campus_cost_basis.json": "campus: derived on every run",
    "campus_milp_basis.json": "campus: derived on every run",
    "campus_milp_summary.json": "campus: derived on every run",
    "campus_profile_used.json": "campus: derived on every run",
    "draft_notes.json": "campus: a grid-code draft stays with its licensee",
    "settings.json": "campus: per-install settings",
    "gfm_applied.json": "campus island tuning: derived from island_config.json",
}


def entries(
    op: str, *, kind: str | None = None, group: str | None = None
) -> tuple[Entry, ...]:
    """The table entries `op` carries, in table order."""
    if op not in OPS:
        raise ValueError(f"unknown operation: {op}")
    return tuple(
        e
        for e in TABLE
        if op in e.carried_by
        and (kind is None or e.kind == kind)
        and (group is None or e.group == group)
    )


def names(
    op: str, *, kind: str | None = None, group: str | None = None
) -> tuple[str, ...]:
    return tuple(e.path for e in entries(op, kind=kind, group=group))


def entry(path: str) -> Entry | None:
    return next((e for e in TABLE if e.path == path), None)


def skipped(dname: str, op: str) -> tuple[str, ...]:
    """The sub-paths of folder `dname` that `op` leaves out."""
    e = entry(dname)
    return tuple(e.skip.get(op, ())) if e else ()


def allows_member(member: str) -> bool:
    """True when a bundle zip member is a path the table lets an import write."""
    if member in names(BUNDLE, kind=FILE):
        return True
    top = member.split("/", 1)[0]
    return member != top and top in names(BUNDLE, kind=DIR)


#: A bundle's own record of its members (issue 02). Only `bundle_manifest`
#: writes and reads it; it is never unpacked into a project.
MANIFEST = "manifest.json"


# ── Safe bundle staging (issue 01, GK B I2) ──────────────────────────────────

#: The most a bundle may unpack to. The upload itself is capped by
#: `services.upload_guard`; this caps what its members inflate to.
MAX_UNPACKED_BYTES = int(
    _os.environ.get("PYPSA_GUI_MAX_BUNDLE_UNPACKED_BYTES", str(4 * 1024**3))
)


class BundleRefused(ValueError):
    """A bundle that may not be imported: `detail` is the 400's body, an
    `error_kind` and a message a user can act on."""

    def __init__(self, detail: dict):
        super().__init__(detail["message"])
        self.detail = detail
        self.kind = detail["error_kind"]
        self.message = detail["message"]


def _folded(member: str) -> str:
    # NFC as well: macOS stores names decomposed, so "é" and "e\u0301" are one file there.
    return "/".join(
        _unicodedata.normalize("NFC", p.rstrip(". ")).casefold()
        for p in member.split("/")
    )


def screen_members(
    zf: _zipfile.ZipFile, *, max_unpacked: int | None = None
) -> tuple[list[str], list[str]]:
    """`(allowed, ignored)` member names of a bundle zip, or `BundleRefused`.

    Refused: an absolute path, a `..` part, a drive letter or backslash, a
    symlink, two members that are one file on a case-folding file system
    (case, trailing dot or space), and members that unpack to more than
    `MAX_UNPACKED_BYTES`. Ignored (reported, never written): a member the
    table does not list. Directory entries and `MANIFEST` are neither.
    """
    cap = MAX_UNPACKED_BYTES if max_unpacked is None else max_unpacked
    allowed, ignored, seen, total = [], [], {}, 0
    for info in zf.infolist():
        name = info.filename
        if info.is_dir():
            continue
        parts = _Posix(name).parts
        if (
            "\\" in name
            or name.startswith("/")
            or ".." in parts
            or not parts
            or (len(parts[0]) >= 2 and parts[0][1] == ":")
        ):
            raise BundleRefused(
                {
                    "error_kind": "bundle_unsafe_path",
                    "message": f"The bundle holds '{name}', a path outside the project. Nothing was imported.",
                }
            )
        if _stat.S_ISLNK(info.external_attr >> 16):
            raise BundleRefused(
                {
                    "error_kind": "bundle_unsafe_path",
                    "message": f"The bundle holds '{name}' as a link. Nothing was imported.",
                }
            )
        folded = _folded(name)
        if folded in seen:
            raise BundleRefused(
                {
                    "error_kind": "bundle_name_clash",
                    "message": f"The bundle holds both '{seen[folded]}' and '{name}', which are one file "
                    "on Windows and macOS. Nothing was imported.",
                }
            )
        seen[folded] = name
        total += info.file_size
        if total > cap:
            raise BundleRefused(
                {
                    "error_kind": "bundle_too_large",
                    "message": f"The bundle unpacks to more than {cap // 1024**2} MB. Nothing was imported.",
                }
            )
        if name != MANIFEST:
            (allowed if allows_member(name) else ignored).append(name)
    for folded, name in seen.items():
        parts = folded.split("/")
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i])
            if parent in seen:
                raise BundleRefused(
                    {
                        "error_kind": "bundle_unsafe_path",
                        "message": f"The bundle holds '{seen[parent]}' as a file and '{name}' inside it. "
                        "Nothing was imported.",
                    }
                )
    return allowed, ignored
