"""
Field descriptors: what every asset face (the Expert card, the creation form, the table columns, the guided
ledger, the chat tool's attribute docs) renders for one component class.

One `FieldSpec` per field, in the spec's fixed group order (uniform asset parameterisation §3.2). The vocabulary
is the decision study's `LedgerRow` (`label`, `technical_name`, `help`, `unit`), so the guided ledger (S2) is a
projection of these descriptors, not a translation of them.

Units: `unit_storage` is what the column holds; `unit_display` is what a person reads; `display = stored × scale`
(§3.4: money per kW, rates in %). `derived` fields are written by `derive` (or by PyPSA from the parts) and are
shown read-only, never typed: investment is typed only as overnight parts (plan §10).

S1a covers the classes S1b renders (Generator, StorageUnit); the other classes get their descriptors with their
cards (S1c).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any

from services.asset_schema.schema import part_columns

GROUPS = ("identity", "size", "investment", "fom", "variable", "performance", "replacement", "provenance")

GROUP_LABELS = {
    "identity": "Identity",
    "size": "Size",
    "investment": "Investment",
    "fom": "Fixed O&M",
    "variable": "Variable cost",
    "performance": "Technical performance",
    "replacement": "Replacement",
    "provenance": "Provenance",
}

_KW = 1e-3      # EUR/MW -> EUR/kW, EUR/MWh -> EUR/kWh
_PCT = 100.0    # fraction -> %


@dataclass(frozen=True)
class FieldSpec:
    key: str                      # the column it reads and writes
    group: str
    label: str
    help: str
    unit_display: str = ""
    unit_storage: str = ""
    scale: float = 1.0            # display = stored × scale
    kind: str = "number"          # number | int | bool | enum | text | bus | carrier
    options: tuple[str, ...] = ()
    required: bool = False
    key_param: bool = False       # the guided face lists it; the rest fold into "Defaults in use"
    derived: bool = False         # written by derive / the solver; read-only
    reveal_when: dict[str, Any] | None = field(default=None, hash=False)
    part: str | None = None       # the investment part it belongs to (composite classes)
    blank: str = "keep"           # what an emptied input saves: clear (null) | zero | keep (current value)

    @property
    def technical_name(self) -> str:
        return self.key

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["technical_name"] = self.key
        d["options"] = list(self.options)
        return d


def _money_per_mw(key: str, group: str, label: str, help: str, **kw) -> FieldSpec:
    return FieldSpec(key, group, label, help, "EUR/kW", "EUR/MW", _KW, **kw)


def _money_per_mw_yr(key: str, group: str, label: str, help: str, **kw) -> FieldSpec:
    return FieldSpec(key, group, label, help, "EUR/kW/yr", "EUR/MW/yr", _KW, **kw)


def _share(key: str, group: str, label: str, help: str, **kw) -> FieldSpec:
    return FieldSpec(key, group, label, help, "%", "fraction", _PCT, **kw)


_EXT = {"p_nom_extendable": True}
_UC = {"committable": True}

# ── shared rows ───────────────────────────────────────────────────────────────

def _identity(what: str, bus_help: str, carrier_help: str) -> tuple[FieldSpec, ...]:
    return (
        FieldSpec("name", "identity", "Name", f"Unique {what} identifier. Time-series profiles are matched to "
                  "this exact name.", kind="text", required=True),
        FieldSpec("carrier", "identity", "Carrier", carrier_help, kind="carrier"),
        FieldSpec("bus", "identity", "Bus", bus_help, kind="bus", required=True),
    )


def _size_bounds() -> tuple[FieldSpec, ...]:
    return (
        FieldSpec("p_nom_extendable", "size", "Extendable", "When ticked, the optimiser chooses the capacity "
                  "between the minimum and maximum and pays the annualised cost for every MW it adds.",
                  kind="bool", key_param=True),
        FieldSpec("p_nom_min", "size", "Minimum capacity", "Lower bound on the optimised capacity. Set it to the "
                  "existing capacity to forbid retirement.", "MW", "MW", reveal_when=_EXT),
        FieldSpec("p_nom_max", "size", "Maximum capacity", "Upper bound on the optimised capacity. Leave empty "
                  "for no limit.", "MW", "MW", reveal_when=_EXT, key_param=True),
    )


def _investment_common() -> tuple[FieldSpec, ...]:
    return (
        FieldSpec("build_year", "investment", "Build year", "First year the asset operates. With its lifetime, it "
                  "decides in which investment periods the asset exists.", "year", "year", kind="int"),
        _share("discount_rate", "investment", "Discount rate", "The rate this asset's overnight cost is annualised "
               "at. Leave empty to use the project's rate from Solver Settings."),
    )


def _outages(with_cf_flag: bool) -> tuple[FieldSpec, ...]:
    rows = (
        _share("outage_rate_value", "performance", "Forced-outage rate", "Share of time the asset is out on a "
               "forced outage, e.g. 5 %. Empty uses the carrier's library default. Read by the adequacy and FMEA "
               "analysis only; the dispatch is unaffected."),
        FieldSpec("outage_rate_basis", "performance", "Outage-rate basis", "What the rate is: FOR (service-hours "
                  "based, what NERC GADS class averages publish) or EFORd (demand based). The tool stores which one "
                  "you entered and never converts between them.", kind="enum", options=("FOR", "EFORd")),
        FieldSpec("mttr_hours", "performance", "Mean time to repair", "How long one forced outage lasts on "
                  "average. With the rate it implies an event frequency (8760 × rate / MTTR per year).", "h", "h"),
    )
    if with_cf_flag:
        rows += (
            FieldSpec("p_max_pu_includes_outages", "performance", "Availability includes outages", "Tick when the "
                      "availability profile already accounts for forced outages (a historical capacity-factor "
                      "table). The adequacy engines then apply no outage rate on top.", kind="bool"),
        )
    return rows


# ── Generator (single part) ──────────────────────────────────────────────────

GENERATOR: tuple[FieldSpec, ...] = (
    *_identity(
        "generator",
        "Bus the generator injects into.",
        "Technology carrier (solar, onwind, OCGT, …). Drives CO₂ accounting, grouping and canvas colours.",
    ),
    FieldSpec("p_nom", "size", "Capacity", "Installed active-power capacity. With extendable ticked, the existing "
              "capacity the optimiser starts from.", "MW", "MW", key_param=True),
    *_size_bounds(),
    _money_per_mw("overnight_cost", "investment", "Overnight cost", "Upfront cost per unit of capacity, as if built "
                  "at once without financing. Annualised over the lifetime at the discount rate.", key_param=True),
    FieldSpec("lifetime", "investment", "Lifetime", "Technical and economic lifetime. The asset retires after its "
              "build year plus lifetime.", "years", "years", key_param=True),
    *_investment_common(),
    _money_per_mw_yr("capital_cost", "investment", "Capital cost (annualised)", "Derived: overnight cost × annuity("
                     "discount rate, lifetime); fixed O&M is charged on top. The optimiser pays it on every MW it adds, "
                     "for the share of a year your snapshots represent.", derived=True),
    _money_per_mw_yr("fom_cost", "fom", "Fixed O&M", "Fixed operation and maintenance per installed unit per year, "
                     "paid on the whole capacity whatever the dispatch."),
    FieldSpec("marginal_cost", "variable", "Marginal cost", "Variable cost per MWh dispatched: fuel plus variable "
              "O&M.", "EUR/MWh", "EUR/MWh"),
    FieldSpec("curtailment_cost", "variable", "Curtailment cost", "Penalty per MWh of available but unused energy. "
              "Set above zero to discourage spilling wind or solar.", "EUR/MWh", "EUR/MWh"),
    FieldSpec("start_up_cost", "variable", "Start-up cost", "Cost of one start-up.", "EUR", "EUR", reveal_when=_UC),
    FieldSpec("shut_down_cost", "variable", "Shut-down cost", "Cost of one shut-down.", "EUR", "EUR",
              reveal_when=_UC),
    _share("efficiency", "performance", "Efficiency", "Conversion efficiency from primary energy to electricity. "
           "Used for CO₂ accounting (carrier emissions ÷ efficiency)."),
    _share("p_min_pu", "performance", "Minimum output", "Minimum dispatch as a share of capacity: a must-run floor."),
    _share("p_max_pu", "performance", "Availability", "Maximum dispatch as a share of capacity. For wind and solar, "
           "the time-varying capacity factor uploaded as a profile."),
    FieldSpec("committable", "performance", "Unit commitment", "Model on/off decisions with start-ups and minimum "
              "up and down times. Adds integer variables: slower, more realistic for thermal plants.", kind="bool"),
    _share("ramp_limit_up", "performance", "Ramp-up limit", "Largest hour-on-hour increase, as a share of capacity "
           "per hour.", reveal_when=_UC),
    _share("ramp_limit_down", "performance", "Ramp-down limit", "Largest hour-on-hour decrease, as a share of "
           "capacity per hour.", reveal_when=_UC),
    FieldSpec("min_up_time", "performance", "Minimum up time", "Hours the unit stays on after a start-up.", "h",
              "h", kind="int", reveal_when=_UC),
    FieldSpec("min_down_time", "performance", "Minimum down time", "Hours the unit stays off after a shut-down.",
              "h", "h", kind="int", reveal_when=_UC),
    FieldSpec("e_sum_min", "performance", "Minimum energy", "Least energy the generator must produce over the "
              "modelled horizon (Σ dispatch × weight). Empty for no lower bound.", "MWh", "MWh"),
    FieldSpec("e_sum_max", "performance", "Maximum energy", "Most energy the generator may produce over the "
              "modelled horizon, e.g. a fuel or water budget. Empty for no upper bound.", "MWh", "MWh"),
    FieldSpec("control", "performance", "AC control", "Control type in the AC power flow only: PQ fixes P and Q, "
              "PV holds the voltage, Slack absorbs the imbalance.", kind="enum", options=("PQ", "PV", "Slack")),
    *_outages(with_cf_flag=True),
)

# ── StorageUnit (composite: power part + energy part) ────────────────────────

_SOC_FREE = {"cyclic_state_of_charge": False}

STORAGE_UNIT: tuple[FieldSpec, ...] = (
    *_identity(
        "storage unit",
        "Bus the storage charges from and discharges to.",
        "Storage technology (battery, PHS, …). Used for grouping; does not change the energy balance.",
    ),
    FieldSpec("p_nom", "size", "Power", "Charge and discharge rating on the AC side.", "MW", "MW", key_param=True),
    FieldSpec("max_hours", "size", "Duration", "Hours of storage at rated power: energy = power × duration.", "h",
              "h", key_param=True),
    *_size_bounds(),
    FieldSpec("inv_power_overnight", "investment", "Power part: overnight cost", "Upfront cost of the power "
              "conversion (inverter, grid connection) per kW of power.", "EUR/kW", "EUR/MW", _KW, key_param=True,
              part="power"),
    FieldSpec("inv_power_lifetime", "investment", "Power part: lifetime", "Lifetime of the power part. When "
              "shorter than the energy part's, finance books a replacement.", "years", "years", part="power"),
    FieldSpec("inv_energy_overnight", "investment", "Energy part: overnight cost", "Upfront cost of the storage "
              "block per kWh of energy.", "EUR/kWh", "EUR/MWh", _KW, key_param=True, part="energy"),
    FieldSpec("inv_energy_lifetime", "investment", "Energy part: lifetime", "Lifetime of the storage block.",
              "years", "years", part="energy"),
    *_investment_common(),
    FieldSpec("lifetime", "investment", "Lifetime", "Derived: the longest part lifetime, the one the asset retires "
              "with.", "years", "years", derived=True),
    _money_per_mw_yr("capital_cost", "investment", "Capital cost (annualised)", "Derived from both parts: each "
                     "part's overnight cost annualised over its own lifetime, the energy part scaled by the "
                     "duration; fixed O&M is charged on top. Per kW of power.", derived=True),
    _share("inv_power_fom_share", "fom", "Power part: fixed O&M", "Fixed O&M of the power part, as a share of its "
           "overnight cost per year.", part="power"),
    _share("inv_energy_fom_share", "fom", "Energy part: fixed O&M", "Fixed O&M of the storage block, as a share "
           "of its overnight cost per year.", part="energy"),
    _money_per_mw_yr("fom_cost", "fom", "Fixed O&M (total)", "Derived: both parts' fixed O&M per kW of power per "
                     "year.", derived=True),
    FieldSpec("marginal_cost", "variable", "Marginal cost", "Cost per MWh dispatched, e.g. to model wear.",
              "EUR/MWh", "EUR/MWh"),
    _share("efficiency_store", "performance", "Charging efficiency", "Share of the electricity drawn that reaches "
           "the store."),
    _share("efficiency_dispatch", "performance", "Discharging efficiency", "Share of the stored energy delivered "
           "as electricity."),
    _share("standing_loss", "performance", "Standing loss", "Share of the state of charge lost each hour "
           "(self-discharge)."),
    FieldSpec("cyclic_state_of_charge", "performance", "Cyclic state of charge", "The state of charge at the end "
              "must equal the start, so the model cannot start with free energy.", kind="bool"),
    FieldSpec("state_of_charge_initial", "performance", "Initial state of charge", "Energy in the store at the "
              "first snapshot. Ignored when the state of charge is cyclic.", "MWh", "MWh", reveal_when=_SOC_FREE),
    FieldSpec("inflow", "performance", "Inflow", "Energy flowing into the store from outside, e.g. river inflow "
              "for hydro. Upload a profile on the Time Series page for hourly values.", "MW", "MW"),
    *_outages(with_cf_flag=False),
)

# What an emptied input saves (S1b). `clear` sends null: an Optional column, or a bound the write model turns
# into +inf ("no limit", "never retires"). Costs save 0, a deliberate "free". Everything else keeps its value:
# a blank capacity or efficiency is a slip, not a decision.
_ZERO = {"marginal_cost", "fom_cost", "curtailment_cost", "start_up_cost", "shut_down_cost"}
_CLEAR = {"p_nom_max", "lifetime", "overnight_cost", "discount_rate", "ramp_limit_up", "ramp_limit_down",
          "e_sum_min", "e_sum_max",
          "outage_rate_value", "outage_rate_basis", "mttr_hours", "p_max_pu_includes_outages"}


def _with_blank(component: str, specs: tuple[FieldSpec, ...]) -> tuple[FieldSpec, ...]:
    clear = _CLEAR | set(part_columns(component))
    return tuple(
        f if f.derived else replace(f, blank="zero" if f.key in _ZERO else "clear" if f.key in clear else "keep")
        for f in specs)


_BY_CLASS: dict[str, tuple[FieldSpec, ...]] = {
    "Generator": _with_blank("Generator", GENERATOR),
    "StorageUnit": _with_blank("StorageUnit", STORAGE_UNIT),
}


def known_components() -> list[str]:
    return sorted(_BY_CLASS)


def fields_for(component: str) -> tuple[FieldSpec, ...]:
    """The descriptors of one class, in group order. KeyError for a class S1 does not describe yet."""
    return _BY_CLASS[component]


def schema_payload(component: str) -> dict[str, Any]:
    """The route's body: the class's fields grouped, groups in the fixed order, empty groups left out."""
    specs = fields_for(component)
    groups = []
    for gid in GROUPS:
        rows = [f.as_dict() for f in specs if f.group == gid]
        if rows:
            groups.append({"id": gid, "label": GROUP_LABELS[gid], "fields": rows})
    return {"component": component, "groups": groups}
