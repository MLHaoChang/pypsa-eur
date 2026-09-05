"""Stage 2: the short-circuit-ratio pre-check at every inverter bus.

    SCR = S_k'' at the connection bus / installed inverter MVA at that bus

Plain SCR only. WSCR, ESCR, the impedance screen and the RoCoF -> EMT flag
are spec phase 4; a pre-check that quietly becomes a grid-strength study is
scope creep with a compliance-shaped tail.

Two rulings, both ledgered:

* The denominator is INSTALLED capacity — the templates' inverter ``mbase_mva``,
  which Task 10 records as the RES_LEDGER installed MW read as MVA — never the
  dispatched hour. SCR is a property of the network's strength at the bus;
  dividing by a curtailed output would report a weak bus as strong.
* The bands are REPORTED thresholds, not pass/fail gates. This stage has no
  standing to fail a bus; it tells the reader which buses to look at.

The numerator is whichever 60909 case the caller supplies; the table must be a
single case and the case is carried in the output. Weak-grid screening
conventionally uses the MINIMUM fault level, but that choice belongs to the
driver, not here.

Pure pandas — no engine import, though ``static/`` would allow one.
"""
import math

import pandas as pd

from gridspine.schema.contingency import validate_fault_levels
from gridspine.schema.contracts import ContractError
from gridspine.templates.unit_params import UnitTemplates

#: Ascending band edges; a value ON an edge belongs to the upper band.
SCR_BANDS = (2.0, 3.0, 5.0)
BAND_NAMES = ("very_weak", "weak", "moderate", "strong")

SCR_LEDGER = (
    "SCR = S_k'' at the connection bus / INSTALLED inverter MVA at that bus; "
    "capacity is the template mbase_mva (installed MW read as MVA), never the "
    "dispatched output — dividing by a curtailed hour would report a weak bus "
    "as strong (assumed scope)",
    "SCR bands <2 very_weak, 2-3 weak, 3-5 moderate, >=5 strong are conventional "
    "reporting thresholds, not pass/fail gates; plain SCR only, WSCR/ESCR and the "
    "impedance screen are phase 4",
    "the 60909 case feeding the numerator is the caller's choice and is carried "
    "in the table; weak-grid screening conventionally uses the minimum case",
)

OUTPUT_COLUMNS = ("bus", "case", "ibr_mva", "sk_mva", "scr", "band")


def band(value: float) -> str:
    for edge, name in zip(SCR_BANDS, BAND_NAMES):
        if value < edge:
            return name
    return BAND_NAMES[-1]


def scr(fault_levels: pd.DataFrame, registry: pd.DataFrame, templates: UnitTemplates) -> pd.DataFrame:
    fl = validate_fault_levels(fault_levels)
    cases = sorted(set(fl["case"]))
    if len(cases) != 1:
        raise ContractError(
            f"fault_levels must hold a single 60909 case, got {cases}; SCR at one "
            "bus from two cases is two different numbers"
        )
    case = cases[0]
    by_bus = fl.set_index("bus")

    res = registry[registry["kind"] == "res"]
    no_template = [u for u in res.index if u not in templates.units.index]
    if no_template:
        raise ContractError(f"res unit(s) with no template row: {no_template}")
    not_inverter = [
        u for u in res.index if templates.units.at[u, "model"] != "inverter"
    ]
    if not_inverter:
        raise ContractError(
            f"res unit(s) whose template model is not 'inverter': {not_inverter}"
        )
    capacity = pd.Series(
        [float(templates.units.at[u, "mbase_mva"]) for u in res.index], index=res.index
    )
    ibr_mva = capacity.groupby(res["bus"].values).sum()

    no_fault_level = sorted(b for b in ibr_mva.index if b not in by_bus.index)
    if no_fault_level:
        raise ContractError(f"no fault level for RES bus(es): {no_fault_level}")

    rows = []
    for bus in sorted(ibr_mva.index):
        sk = float(by_bus.at[bus, "sk_mva"])
        cap = float(ibr_mva[bus])
        value = sk / cap
        if not math.isfinite(value):
            raise ContractError(f"non-finite SCR at {bus}: sk_mva={sk}, ibr_mva={cap}")
        rows.append({
            "bus": bus, "case": case, "ibr_mva": cap, "sk_mva": sk,
            "scr": value, "band": band(value),
        })
    return pd.DataFrame(rows, columns=list(OUTPUT_COLUMNS))
