"""
The functions a study runs on a model (spec expert-catalogue §1, EXPERT-CAT-1).

A function needs some catalogue entries and ignores others: an entry's
`functions` maps a function id to "needs" (its run is blocked while the entry
is missing) or "uses" (it improves the run). The long text is in
`guide/function.<id>.md`.
"""

from __future__ import annotations

from enum import Enum


class Function(str, Enum):
    DISPATCH = "dispatch"
    EXPANSION = "expansion"
    LOAD_FLOW = "load_flow"
    SHORT_CIRCUIT = "short_circuit"
    GRID_COMPLIANCE = "grid_compliance"
    ADEQUACY = "adequacy"
    FINANCE = "finance"


#: Plain names, in the fixed display order.
FUNCTION_NAMES: dict[Function, str] = {
    Function.DISPATCH: "Dispatch",
    Function.EXPANSION: "Capacity expansion",
    Function.LOAD_FLOW: "Load flow",
    Function.SHORT_CIRCUIT: "Short circuit",
    Function.GRID_COMPLIANCE: "Grid compliance",
    Function.ADEQUACY: "Adequacy",
    Function.FINANCE: "Finance",
}

#: One sentence each, shown in search results and the guide head.
FUNCTION_HELP: dict[Function, str] = {
    Function.DISPATCH: "Runs the site hour by hour at least cost with the sizes you set.",
    Function.EXPANSION: "Lets the optimiser choose sizes for the assets you open to sizing.",
    Function.LOAD_FLOW: "Checks voltages and flows on the electrical network for each hour.",
    Function.SHORT_CIRCUIT: "Works out fault currents at each connection point.",
    Function.GRID_COMPLIANCE: "Checks the site against the grid operator's connection rules.",
    Function.ADEQUACY: "Tests whether supply covers demand when equipment fails.",
    Function.FINANCE: "Turns costs and revenues into a business case over the project life.",
}

FUNCTION_IDS: tuple[str, ...] = tuple(f.value for f in Function)

USE_LEVELS = ("needs", "uses")
