"""The 25 deliverables of the TRGC chain, as data (GC-09).

TRGC numbers and study titles follow the public Technical Requirements for
Grid Connection reports published with the Saudi Arabian Grid Code (SAGC) by
SE / NGSA (owner decision GC-TEXT-1). Nothing here names a project, customer,
consultant, EPC or OEM.

Each deliverable carries its phase (1–4), the deliverables it waits on, the
tool that runs it, the GC packages whose output feeds its pack, and the
generic data-list items the study needs before it can start.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: The status ladder, in order. A deliverable moves along it by hand; the
#: guard in ``tracker`` refuses a move that would submit work whose
#: prerequisites are not judged yet.
STATUSES: tuple[str, ...] = (
    "not_started",
    "data",
    "exported",
    "results_in",
    "judged",
    "submitted",
    "tso_comments",
    "accepted",
)
RANK = {s: i for i, s in enumerate(STATUSES)}
#: A prerequisite must be at least this far before a dependent is submitted.
PREREQUISITE_MIN = "judged"
#: A deliverable at or past this status counts as submitted.
SUBMITTED = "submitted"

PHASES: dict[int, str] = {
    1: "Preliminary design and model",
    2: "Compliance",
    3: "Grid impact",
    4: "Testing and field validation",
}

#: What each feeding package delivers, in words a report reader knows
#: (package ids stay out of user-facing text).
PACKAGE_LABELS: dict[str, str] = {
    "GC-01": "plant model set (detailed and aggregated)",
    "GC-02": "voltage-control strategy study",
    "GC-03": "planning-criteria and grid-strength screens",
    "GC-04": "PSS/E study results",
    "GC-05": "PowerFactory study results",
    "GC-06": "PSCAD study results",
    "GC-07": "judged dynamic results and benchmarks",
    "GC-08": "sub-synchronous interaction screens",
}


@dataclass(frozen=True)
class Deliverable:
    code: str
    title: str
    phase: int
    tool: str
    depends_on: tuple[str, ...] = ()
    packages: tuple[str, ...] = ()
    data_items: tuple[str, ...] = field(default=())


_PLANT = ("Single-line diagram", "Equipment ratings and impedances")
_CTRL = ("Plant controller settings", "Inverter / unit control parameters")
_GRID = ("PoC short-circuit levels (Sk min / max, X/R) from the TSO",)

DELIVERABLES: tuple[Deliverable, ...] = (
    Deliverable("01", "Detailed plant model", 1, "PSS/E, PSCAD", (),
                ("GC-01",), _PLANT + ("Cable and collector data",)),
    Deliverable("02", "Aggregated plant model", 1, "PSS/E, PSCAD", ("01",),
                ("GC-01",), _PLANT),
    Deliverable("03", "Dynamic performance and control tests", 1, "PSS/E, PSCAD",
                ("01", "02"), ("GC-04", "GC-06"), _CTRL + _GRID),
    Deliverable("04", "Dynamic model acceptance tests (RMS)", 1, "PSS/E",
                ("01", "02"), ("GC-04",), _CTRL + _GRID),
    Deliverable("05", "Dynamic model acceptance tests (EMT)", 1, "PSCAD",
                ("01", "02"), ("GC-06",), _CTRL + _GRID),
    Deliverable("06", "Grid-code compliance study (RMS)", 2, "PSS/E",
                ("04", "05"), ("GC-04", "GC-07"), _GRID),
    Deliverable("07", "Grid-code compliance study (EMT)", 2, "PSCAD",
                ("04", "05"), ("GC-06", "GC-07"), _GRID),
    Deliverable("08", "RMS vs EMT model benchmark", 2, "PSS/E + PSCAD",
                ("06", "07"), ("GC-07",), ()),
    Deliverable("09", "RMS model user guide", 1, "document", ("01", "02"),
                ("GC-01", "GC-04"), ()),
    Deliverable("10", "EMT model user guide", 1, "document", ("01", "02"),
                ("GC-01", "GC-06"), ()),
    Deliverable("11", "Plant datasheets", 1, "document", ("01",),
                ("GC-01",), _PLANT),
    Deliverable("12", "Voltage control strategy", 1, "load flow (in app)", (),
                ("GC-02",), _CTRL + ("TSO voltage set-point and droop requirement",)),
    Deliverable("13", "Power quality: field background measurement", 3,
                "PowerFactory", (), ("GC-05",),
                ("Background harmonic measurement at the PoC",)),
    Deliverable("14", "Power quality: network study", 3, "PowerFactory", ("13",),
                ("GC-05",), ("Harmonic current emission per unit", "Filter data")),
    Deliverable("15", "Steady state, N-1 / N-2", 3, "PSS/E", (),
                ("GC-03", "GC-04"), ("TSO contingency list", "TSO network case")),
    Deliverable("16", "Dynamic stability", 3, "PSS/E", ("04",),
                ("GC-04",), ("TSO dynamic case",)),
    Deliverable("17", "Auto-reclose", 3, "PSCAD", ("05",), ("GC-06",),
                ("Protection and reclose settings",)),
    Deliverable("18", "Small-signal stability", 3, "PowerFactory", ("04",),
                ("GC-05",), ()),
    Deliverable("19", "Sub-synchronous torsional interaction (SSTI) screening", 3,
                "PSCAD", (), ("GC-08",), ("Nearby synchronous machine shaft data",)),
    Deliverable("20", "SSR / SSCI", 3, "PSCAD", ("05",), ("GC-08",),
                ("Series compensation in the area",)),
    Deliverable("21", "Control interaction", 3, "PSCAD", ("05",), ("GC-06",), ()),
    Deliverable("22", "Insulation coordination", 3, "PSCAD", ("01",), ("GC-06",),
                ("Surge arrester data", "Insulation levels")),
    Deliverable("23", "Hardware-in-the-loop (HIL)", 4, "RSCAD", ("05",),
                ("GC-06",), ("Controller replica availability",)),
    Deliverable("24", "Field tests", 4, "PSS/E, PSCAD", ("06", "07"),
                ("GC-07",), ("Field test procedure", "Measurement recordings")),
    Deliverable("25", "Model validation against field tests", 4, "PSS/E, PSCAD",
                ("24",), ("GC-07",), ()),
)

BY_CODE: dict[str, Deliverable] = {d.code: d for d in DELIVERABLES}


def dependents(code: str) -> tuple[str, ...]:
    """The deliverables that list ``code`` as a prerequisite."""
    return tuple(d.code for d in DELIVERABLES if code in d.depends_on)
