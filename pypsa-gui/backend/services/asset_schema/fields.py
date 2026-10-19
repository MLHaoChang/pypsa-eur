"""
Field descriptors: what every asset face (the Expert card, the creation form, the table columns, the guided
ledger, the chat tool's attribute docs) renders for one component class.

One `FieldSpec` per field, in the spec's fixed group order (uniform asset parameterisation §3.2). The vocabulary
is the decision study's `LedgerRow` (`label`, `technical_name`, `help`, `unit`), so the guided ledger (S2) is a
projection of these descriptors, not a translation of them.

Units: `unit_storage` is what the column holds; `unit_display` is what a person reads; `display = stored × scale`
(§3.4: money per kW, rates in %). `derived` fields are written by `derive` (or by PyPSA from the parts) and are
shown read-only, never typed: investment is typed only as overnight parts (plan §10).

S1a/S1b cover Generator and StorageUnit; S1c adds Store, Load and Link (single-part classes on PyPSA's own
columns), then Line (one part priced per MVA and km, scaled by its length) and Transformer.
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
    blank: str = "keep"           # what an emptied input saves: clear (null) | zero | empty ("") | keep (current)

    @property
    def technical_name(self) -> str:
        return self.key

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["technical_name"] = self.key
        d["options"] = list(self.options)
        if self.reveal_when:
            # A rule naming several values is a tuple here and a list on the wire.
            d["reveal_when"] = {k: list(v) if isinstance(v, tuple) else v for k, v in self.reveal_when.items()}
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
                  "based, what NERC GADS class averages publish) or EFORd (demand based). They differ materially for "
                  "peakers with reserve-shutdown hours; the tool stores which one you entered and never converts "
                  "between them.", kind="enum", options=("FOR", "EFORd")),
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


# ── unit commitment (Generator and Link share these rows) ────────────────────

def _uc_cost_rows(*, shut_down: bool, stand_by: bool, start_up_readonly: bool = False) -> tuple[FieldSpec, ...]:
    """The "variable" rows of the unit-commitment block, shown behind the `committable` gate."""
    if start_up_readonly:
        start_up = FieldSpec(
            "start_up_cost", "variable", "Start-up cost", "Read-only. PyPSA does not weight a start-up cost by the "
            "snapshot or period weighting, so a process unit prices a restart as lost production instead (see "
            "grid-to-process ticket 03).", "EUR", "EUR", reveal_when=_UC, derived=True)
    else:
        start_up = FieldSpec("start_up_cost", "variable", "Start-up cost", "Cost of one start-up.", "EUR", "EUR",
                             reveal_when=_UC)
    rows = (start_up,)
    if shut_down:
        rows += (FieldSpec("shut_down_cost", "variable", "Shut-down cost", "Cost of one shut-down.", "EUR", "EUR",
                           reveal_when=_UC),)
    if stand_by:
        rows += (FieldSpec("stand_by_cost", "variable", "Stand-by cost", "Cost per hour while the unit is on, at "
                           "any output, including zero.", "EUR/h", "EUR/h", reveal_when=_UC),)
    return rows


def _uc_rows() -> tuple[FieldSpec, ...]:
    """The "performance" rows of the unit-commitment block: the `committable` switch and what it reveals."""
    return (
        FieldSpec("committable", "performance", "Unit commitment", "Model on/off decisions with start-ups and "
                  "minimum up and down times. Adds integer variables: slower, more realistic for thermal plants.",
                  kind="bool"),
        _share("ramp_limit_up", "performance", "Ramp-up limit", "Largest hour-on-hour increase, as a share of "
               "capacity per hour.", reveal_when=_UC),
        _share("ramp_limit_down", "performance", "Ramp-down limit", "Largest hour-on-hour decrease, as a share of "
               "capacity per hour.", reveal_when=_UC),
        FieldSpec("min_up_time", "performance", "Minimum up time", "Hours the unit stays on after a start-up.", "h",
                  "h", kind="int", reveal_when=_UC),
        FieldSpec("min_down_time", "performance", "Minimum down time", "Hours the unit stays off after a "
                  "shut-down.", "h", "h", kind="int", reveal_when=_UC),
    )


# ── Generator (single part) ──────────────────────────────────────────────────

GENERATOR: tuple[FieldSpec, ...] = (
    *_identity(
        "generator",
        "Bus the generator injects into.",
        "Technology carrier (solar, onwind, OCGT, …). Drives CO₂ accounting, grouping and canvas colours.",
    ),
    FieldSpec("process_role", "identity", "Process role", "For a generator on a product bus: buy_in is a real "
              "purchase of the product (booked as an expense), shortfall is product not delivered, priced at its "
              "lost-production value (booked as lost revenue, never as an expense). Empty: an ordinary generator.",
              kind="enum", options=("buy_in", "shortfall")),
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
    *_uc_cost_rows(shut_down=True, stand_by=False),
    _share("efficiency", "performance", "Efficiency", "Conversion efficiency from primary energy to electricity. "
           "Used for CO₂ accounting (carrier emissions ÷ efficiency)."),
    _share("p_min_pu", "performance", "Minimum output", "Minimum dispatch as a share of capacity: a must-run floor."),
    _share("p_max_pu", "performance", "Availability", "Maximum dispatch as a share of capacity. For wind and solar, "
           "the time-varying capacity factor uploaded as a profile."),
    *_uc_rows(),
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

# ── Store (single part, sized in energy) ────────────────────────────────────

_E_EXT = {"e_nom_extendable": True}
_E_FREE = {"e_cyclic": False}

STORE: tuple[FieldSpec, ...] = (
    *_identity(
        "store",
        "Bus the store exchanges energy with. It has no power limit of its own: the Links and other components "
        "on that bus set the charge and discharge rate.",
        "Energy carrier held (H2, heat, battery, …). Used for grouping; does not change the energy balance.",
    ),
    FieldSpec("e_nom", "size", "Energy capacity", "Energy the store can hold. With extendable ticked, the existing "
              "capacity the optimiser starts from.", "MWh", "MWh", key_param=True),
    FieldSpec("e_nom_extendable", "size", "Extendable", "When ticked, the optimiser chooses the energy capacity "
              "between the minimum and maximum and pays the annualised cost for every MWh it adds.",
              kind="bool", key_param=True),
    FieldSpec("e_nom_min", "size", "Minimum capacity", "Lower bound on the optimised energy capacity.", "MWh", "MWh",
              reveal_when=_E_EXT),
    FieldSpec("e_nom_max", "size", "Maximum capacity", "Upper bound on the optimised energy capacity. Leave empty "
              "for no limit.", "MWh", "MWh", reveal_when=_E_EXT, key_param=True),
    FieldSpec("overnight_cost", "investment", "Overnight cost", "Upfront cost per kWh of storage capacity, as if "
              "built at once without financing. Annualised over the lifetime at the discount rate.", "EUR/kWh",
              "EUR/MWh", _KW, key_param=True),
    FieldSpec("lifetime", "investment", "Lifetime", "Technical and economic lifetime. The store retires after its "
              "build year plus lifetime.", "years", "years", key_param=True),
    *_investment_common(),
    FieldSpec("capital_cost", "investment", "Capital cost (annualised)", "Derived: overnight cost × annuity("
              "discount rate, lifetime); fixed O&M is charged on top. Per kWh of capacity.", "EUR/kWh/yr",
              "EUR/MWh/yr", _KW, derived=True),
    FieldSpec("fom_cost", "fom", "Fixed O&M", "Fixed operation and maintenance per kWh of capacity per year.",
              "EUR/kWh/yr", "EUR/MWh/yr", _KW),
    FieldSpec("marginal_cost", "variable", "Marginal cost", "Cost per MWh discharged.", "EUR/MWh", "EUR/MWh"),
    _share("e_min_pu", "performance", "Minimum state of energy", "Lowest allowed fill level, as a share of the "
           "energy capacity."),
    _share("e_max_pu", "performance", "Maximum state of energy", "Highest allowed fill level, as a share of the "
           "energy capacity."),
    FieldSpec("e_cyclic", "performance", "Cyclic state of energy", "The fill level at the end must equal the start, "
              "so the model cannot start with free energy.", kind="bool"),
    FieldSpec("e_initial", "performance", "Initial energy", "Energy in the store at the first snapshot. Ignored "
              "when the state of energy is cyclic.", "MWh", "MWh", reveal_when=_E_FREE),
    _share("standing_loss", "performance", "Standing loss", "Share of the stored energy lost each hour "
           "(self-discharge, boil-off)."),
    *_outages(with_cf_flag=False),
)

# ── Load (no investment) ─────────────────────────────────────────────────────

LOAD: tuple[FieldSpec, ...] = (
    *_identity(
        "load",
        "Bus the demand is drawn from.",
        "Energy carrier of the demand (AC, heat, H2, …). Used for grouping.",
    ),
    FieldSpec("p_set", "size", "Demand", "Active-power demand. A profile uploaded on the Time Series page replaces "
              "this static value hour by hour.", "MW", "MW", key_param=True),
    FieldSpec("q_set", "size", "Reactive demand", "Reactive-power demand, used by the AC power flow only.", "MVAr",
              "MVAr"),
    FieldSpec("sign", "performance", "Sign", "Power sign convention: −1 for a consumer (the default), +1 to make the "
              "load inject power.", "", ""),
)

# ── Link (single part, priced per MW of its input, bus0) ─────────────────────

LINK: tuple[FieldSpec, ...] = (
    FieldSpec("name", "identity", "Name", "Unique link identifier. Time-series profiles are matched to this exact "
              "name.", kind="text", required=True),
    FieldSpec("carrier", "identity", "Carrier", "Technology carrier (electrolysis, fuel cell, DC, heat pump, …). "
              "Drives grouping and canvas colours.", kind="carrier"),
    FieldSpec("bus0", "identity", "From bus", "Input side: the bus the link draws from. Its capacity and cost are "
              "per MW of this input.", kind="bus", required=True),
    FieldSpec("bus1", "identity", "To bus", "Output side: the bus that receives the input × efficiency.",
              kind="bus", required=True),
    *(FieldSpec(f"bus{i}", "identity", f"Port {i} bus", f"Optional extra port {i}: with a positive efficiency{i} "
                f"the bus receives energy (CHP heat); with a negative one the link draws from it (a process "
                f"feed). Leave empty for no port {i}.", kind="bus") for i in (2, 3, 4)),
    FieldSpec("eh_process", "identity", "Process unit", "Tick when this link is a production step: bus0 is the "
              "main input, bus1 the product, the other ports further inputs. A process unit is a fixed client "
              "input, not a decision of the plan.", kind="bool"),
    FieldSpec("criticality_tier", "identity", "Criticality tier", "How this unit's electrical supply is ranked when "
              "it must be cut: safety (priced at VoLL), process_critical (a stop costs a restart), "
              "process_flexible (it can stop and catch up from inventory) or deferrable (it shifts within the "
              "day).", kind="enum", options=("safety", "process_critical", "process_flexible", "deferrable"),
              reveal_when={"eh_process": True}),
    FieldSpec("p_nom", "size", "Capacity", "Rating on the input side (bus0). With extendable ticked, the existing "
              "capacity the optimiser starts from.", "MW", "MW", key_param=True),
    *_size_bounds(),
    _money_per_mw("overnight_cost", "investment", "Overnight cost", "Upfront cost per kW of input capacity, as if "
                  "built at once without financing. Annualised over the lifetime at the discount rate.",
                  key_param=True),
    FieldSpec("lifetime", "investment", "Lifetime", "Technical and economic lifetime. The link retires after its "
              "build year plus lifetime.", "years", "years", key_param=True),
    *_investment_common(),
    _money_per_mw_yr("capital_cost", "investment", "Capital cost (annualised)", "Derived: overnight cost × annuity("
                     "discount rate, lifetime); fixed O&M is charged on top. Per kW of input.", derived=True),
    _money_per_mw_yr("fom_cost", "fom", "Fixed O&M", "Fixed operation and maintenance per kW of input per year."),
    FieldSpec("marginal_cost", "variable", "Marginal cost", "Cost per MWh drawn from the input bus.", "EUR/MWh",
              "EUR/MWh"),
    *_uc_cost_rows(shut_down=False, stand_by=True, start_up_readonly=True),
    _share("efficiency", "performance", "Efficiency", "Output per unit of input: MWh delivered to bus1 per MWh drawn "
           "from bus0 (the COP for a heat pump, above 100 %)."),
    *(FieldSpec(f"efficiency{i}", "performance", f"Port {i} efficiency", f"Flow on port {i} per unit drawn from "
                f"bus0. Positive: the port bus receives it (CHP heat, 0.4 = 0.4 MWh per MWh of fuel). Negative: "
                f"the link draws it from the port bus (a feed, −4.6 = 4.6 per unit of bus0). Read only when bus{i} "
                f"is set.", "", "") for i in (2, 3, 4)),
    _share("p_min_pu", "performance", "Minimum flow", "Lowest flow as a share of capacity. Negative lets the link "
           "run backwards (−100 % for a bidirectional DC line)."),
    _share("p_max_pu", "performance", "Maximum flow", "Highest flow as a share of capacity. A profile uploaded on "
           "the Time Series page replaces this value hour by hour."),
    *_uc_rows(),
    FieldSpec("throughput_unit", "performance", "Throughput unit", "Display only: the unit of the product the unit "
              "makes per hour, for example t/h. The solve reads the efficiency, not this.", kind="text",
              derived=True, reveal_when={"eh_process": True}),
    FieldSpec("specific_energy_kwh_per_t", "performance", "Specific energy", "Display only, derived: electricity "
              "per tonne of product, from the efficiency. The solve reads the efficiency, not this.",
              "kWh/t", "kWh/t", derived=True, reveal_when={"eh_process": True}),
    *_outages(with_cf_flag=False),
)

# ── Line (one part priced per MVA and km, S1c-2) ─────────────────────────────

_S_EXT = {"s_nom_extendable": True}


def _branch_identity(what: str, carrier_help: str | None, from_label: str, to_label: str) -> tuple[FieldSpec, ...]:
    """`carrier_help` None: the class has no carrier column (PyPSA's Transformer)."""
    carrier = (FieldSpec("carrier", "identity", "Carrier", carrier_help, kind="carrier"),) if carrier_help else ()
    return (
        FieldSpec("name", "identity", "Name", f"Unique {what} identifier.", kind="text", required=True),
        *carrier,
        FieldSpec("bus0", "identity", from_label, "The bus at one end. A positive flow in the results runs from "
                  "this bus to the other.", kind="bus", required=True),
        FieldSpec("bus1", "identity", to_label, "The bus at the other end.", kind="bus", required=True),
    )


def _s_nom_bounds(what: str) -> tuple[FieldSpec, ...]:
    return (
        FieldSpec("s_nom", "size", "Capacity", f"Apparent-power rating of the {what}. With extendable ticked, the "
                  "existing capacity the optimiser starts from.", "MVA", "MVA", key_param=True),
        FieldSpec("s_nom_extendable", "size", "Extendable", "When ticked, the optimiser chooses the capacity "
                  "between the minimum and maximum and pays the annualised cost for every MVA it adds.",
                  kind="bool", key_param=True),
        FieldSpec("s_nom_min", "size", "Minimum capacity", "Lower bound on the optimised capacity.", "MVA", "MVA",
                  reveal_when=_S_EXT),
        FieldSpec("s_nom_max", "size", "Maximum capacity", "Upper bound on the optimised capacity. Leave empty "
                  "for no limit.", "MVA", "MVA", reveal_when=_S_EXT, key_param=True),
    )


def _lifetime(what: str) -> FieldSpec:
    return FieldSpec("lifetime", "investment", "Lifetime", f"Technical and economic lifetime. The {what} retires "
                     "after its build year plus lifetime; leave empty for never.", "years", "years", key_param=True)


LINE: tuple[FieldSpec, ...] = (
    *_branch_identity("line", "AC for an AC line. Used for grouping and canvas colours. A line joins buses of "
                      "one carrier; join buses of different carriers with a Link.", "From bus", "To bus"),
    *_s_nom_bounds("line"),
    FieldSpec("length", "size", "Length", "Route length. The impedances and the investment are entered per km "
              "and scale with it.", "km", "km", key_param=True),
    FieldSpec("inv_per_km_overnight", "investment", "Overnight cost per km", "Upfront cost per MVA of capacity "
              "and km of route, as catalogues publish it (e.g. HVAC overhead). Times the length, annualised over "
              "the lifetime at the discount rate.", "EUR/MVA/km", "EUR/MVA/km", key_param=True),
    _lifetime("line"),
    *_investment_common(),
    FieldSpec("capital_cost", "investment", "Capital cost (annualised)", "Derived: overnight cost per km × length "
              "× annuity(discount rate, lifetime); fixed O&M is charged on top. Per MVA of capacity.",
              "EUR/MVA/yr", "EUR/MVA/yr", derived=True),
    _share("inv_per_km_fom_share", "fom", "Fixed O&M", "Fixed operation and maintenance per year, as a share of "
           "the overnight cost."),
    FieldSpec("fom_cost", "fom", "Fixed O&M (total)", "Derived: the overnight cost per km × length × the fixed "
              "O&M share, per MVA of capacity per year.", "EUR/MVA/yr", "EUR/MVA/yr", derived=True),
    *_outages(with_cf_flag=False),
)

# ── Transformer (single part, on PyPSA's own columns) ────────────────────────

TRANSFORMER: tuple[FieldSpec, ...] = (
    *_branch_identity("transformer", None, "HV bus", "LV bus"),
    *_s_nom_bounds("transformer"),
    FieldSpec("overnight_cost", "investment", "Overnight cost", "Upfront cost per MVA of rating, as if built at "
              "once without financing. Annualised over the lifetime at the discount rate.", "EUR/MVA", "EUR/MVA",
              key_param=True),
    _lifetime("transformer"),
    *_investment_common(),
    FieldSpec("capital_cost", "investment", "Capital cost (annualised)", "Derived: overnight cost × annuity("
              "discount rate, lifetime); fixed O&M is charged on top. Per MVA of rating.", "EUR/MVA/yr",
              "EUR/MVA/yr", derived=True),
    FieldSpec("fom_cost", "fom", "Fixed O&M", "Fixed operation and maintenance per MVA of rating per year.",
              "EUR/MVA/yr", "EUR/MVA/yr"),
    FieldSpec("v_nom_0", "performance", "Declared HV voltage", "Nominal voltage of the HV winding. Checked against "
              "the HV bus.", "kV", "kV"),
    FieldSpec("v_nom_1", "performance", "Declared LV voltage", "Nominal voltage of the LV winding. Checked against "
              "the LV bus.", "kV", "kV"),
    FieldSpec("r", "performance", "Resistance", "Series resistance in per unit of the rating.", "pu", "pu"),
    FieldSpec("x", "performance", "Reactance", "Series reactance in per unit of the rating.", "pu", "pu"),
    FieldSpec("tap_ratio", "performance", "Tap ratio", "Off-nominal turns ratio (1 = nominal)."),
    FieldSpec("phase_shift", "performance", "Phase shift", "Phase shift between the windings.", "°", "°"),
)

# ── Bus, Carrier, GlobalConstraint, ShuntImpedance (M5: the non-asset components) ──
# No investment: identity and technical fields only. Their help folds in the Bus card's old propertyDocs tips
# (plan 2026-10-14-m5-non-asset-forms.md).

BUS: tuple[FieldSpec, ...] = (
    FieldSpec("name", "identity", "Name", "Unique bus identifier. Every component attached to the bus names it, so a "
              "rename re-points them all.", kind="text", required=True),
    FieldSpec("carrier", "identity", "Carrier", "Energy carrier modelled at this bus (AC, DC, H2, heat, gas, …). It "
              "decides which assets can attach and which energy balance the bus enters.", kind="carrier"),
    FieldSpec("control", "identity", "Control", "Power-flow control for the non-linear AC power flow: PQ (a load), PV "
              "(regulated voltage) or Slack (the reference). For the linear optimisation only the slack matters.",
              kind="enum", options=("PQ", "PV", "Slack")),
    FieldSpec("country", "identity", "Country", "ISO country code, for grouping, country-level statistics and "
              "policy constraints.", kind="text"),
    FieldSpec("unit", "identity", "Unit", "What the bus carries, for a product bus: MWh for an energy product (H2, "
              "NH3) or t for a material (ore, concentrate). Empty for an electrical bus, which reads as MW.",
              kind="text"),
    FieldSpec("mwh_per_t", "identity", "Energy per tonne", "For an energy product in MWh: its lower heating value, "
              "which results use to show tonnes (NH3: 5.166 MWh/t). Empty: tonnes are Unavailable.", "MWh/t",
              "MWh/t", reveal_when={"unit": "MWh"}),
    FieldSpec("product_value", "identity", "Product value", "What one unit of this product is worth to the client, "
              "per the bus's unit (EUR/MWh or EUR/t), with its source in the ledger. Results value a process unit's "
              "output at it. Empty: that value is Unavailable, never 0.", "EUR/unit", "EUR/unit",
              reveal_when={"unit": ("MWh", "t")}),
    FieldSpec("sub_network", "identity", "Sub-network", "Manual region label for the 'region' clustering mode: buses "
              "sharing it collapse to one node. Empty: determined from the electrical topology.", kind="text"),
    FieldSpec("x", "identity", "Longitude", "Geographic longitude, east positive. Places the bus on the map and "
              "sets the length of a line recalculated from coordinates; the optimisation does not read it.",
              "°E", "°E"),
    FieldSpec("y", "identity", "Latitude", "Geographic latitude, north positive. Places the bus on the map and sets "
              "the length of a line recalculated from coordinates; the optimisation does not read it.", "°N", "°N"),
    FieldSpec("v_nom", "performance", "Nominal voltage", "The bus's nominal voltage. Lines are coloured by voltage "
              "class; a transformer's declared voltages are checked against it.", "kV", "kV", key_param=True),
)

CARRIER: tuple[FieldSpec, ...] = (
    FieldSpec("name", "identity", "Name", "Unique carrier name. Buses and assets name it; a rename re-points them.",
              kind="text", required=True),
    FieldSpec("nice_name", "identity", "Display name", "How the carrier is shown in charts and tables. Empty: its "
              "name.", kind="text"),
    FieldSpec("color", "identity", "Colour", "Its colour on the map and in charts, as a CSS colour (e.g. #1f77b4).",
              kind="text"),
    FieldSpec("unit", "identity", "Unit", "Unit of the energy it carries, for display. Empty: MWh.", kind="text"),
    FieldSpec("co2_emissions", "performance", "CO₂ emissions", "Emissions per MWh of primary energy burnt. A "
              "generator's emissions are this divided by its efficiency; a CO₂ cap sums them.", "t/MWh", "t/MWh",
              key_param=True),
)

GLOBAL_CONSTRAINT_TYPES = ("primary_energy", "tech_capacity_expansion_limit", "operational_limit",
                           "transmission_volume_expansion_limit", "transmission_expansion_cost_limit")

GLOBAL_CONSTRAINT: tuple[FieldSpec, ...] = (
    FieldSpec("name", "identity", "Name", "Unique constraint name.", kind="text", required=True),
    FieldSpec("type", "identity", "Type", "What the constraint caps: primary energy (a CO₂ cap), new capacity of a "
              "carrier, a carrier's dispatch, or the volume or cost of new transmission.", kind="enum",
              options=GLOBAL_CONSTRAINT_TYPES, key_param=True),
    FieldSpec("sense", "identity", "Sense", "How the sum compares with the constant.", kind="enum",
              options=("<=", "==", ">="), key_param=True),
    FieldSpec("constant", "identity", "Constant", "The bound, in the type's unit: t for a CO₂ cap, MW for capacity, "
              "MWh for dispatch, MW·km or € for transmission.", required=True, key_param=True),
    FieldSpec("carrier_attribute", "identity", "Carrier attribute", "For primary energy: the carrier column summed, "
              "e.g. co2_emissions.", kind="text", reveal_when={"type": "primary_energy"}),
    FieldSpec("carrier", "identity", "Carrier", "For a capacity or operational limit: the carrier it applies to.",
              kind="carrier", reveal_when={"type": ("tech_capacity_expansion_limit", "operational_limit")}),
    FieldSpec("investment_period", "identity", "Investment period", "The period it binds in. Empty: the whole "
              "horizon.", "", "", kind="int"),
)

SHUNT_IMPEDANCE: tuple[FieldSpec, ...] = (
    FieldSpec("name", "identity", "Name", "Unique shunt identifier.", kind="text", required=True),
    FieldSpec("bus", "identity", "Bus", "Bus the shunt connects to ground.", kind="bus", required=True),
    FieldSpec("g", "performance", "Conductance", "Shunt conductance; absorbs active power in the AC power flow.",
              "S", "S"),
    FieldSpec("b", "performance", "Susceptance", "Shunt susceptance; positive injects reactive power (a capacitor), "
              "negative absorbs it (a reactor).", "S", "S"),
)

# What an emptied input saves (S1b). `clear` sends null: an Optional column, or a bound the write model turns
# into +inf ("no limit", "never retires"). Costs save 0, a deliberate "free". Everything else keeps its value:
# a blank capacity or efficiency is a slip, not a decision.
_ZERO = {"marginal_cost", "fom_cost", "curtailment_cost", "start_up_cost", "shut_down_cost", "stand_by_cost"}
_CLEAR = {"p_nom_max", "e_nom_max", "s_nom_max", "v_nom_0", "v_nom_1", "lifetime", "overnight_cost", "discount_rate", "ramp_limit_up",
          "ramp_limit_down", "e_sum_min", "e_sum_max",
          "outage_rate_value", "outage_rate_basis", "mttr_hours", "p_max_pu_includes_outages"}


# M5: per class. `empty` sends "" (a text column the write model types `str = ""`: an emptied country or display
# name is a decision, and null would be refused); GlobalConstraint's optional columns clear to null.
_EMPTY_BY_CLASS = {"Bus": {"country", "sub_network", "unit"}, "Carrier": {"nice_name", "color", "unit"},
                   "Link": {"bus2", "bus3", "bus4", "criticality_tier"}, "Generator": {"process_role"}}
_CLEAR_BY_CLASS = {"GlobalConstraint": {"carrier_attribute", "carrier", "investment_period"},
                   "Bus": {"mwh_per_t", "product_value"}}


def _with_blank(component: str, specs: tuple[FieldSpec, ...]) -> tuple[FieldSpec, ...]:
    clear = _CLEAR | set(part_columns(component)) | _CLEAR_BY_CLASS.get(component, set())
    empty = _EMPTY_BY_CLASS.get(component, set())

    def policy(key: str) -> str:
        return "empty" if key in empty else "zero" if key in _ZERO else "clear" if key in clear else "keep"

    return tuple(f if f.derived else replace(f, blank=policy(f.key)) for f in specs)


_BY_CLASS: dict[str, tuple[FieldSpec, ...]] = {
    "Generator": _with_blank("Generator", GENERATOR),
    "StorageUnit": _with_blank("StorageUnit", STORAGE_UNIT),
    "Store": _with_blank("Store", STORE),
    "Load": _with_blank("Load", LOAD),
    "Link": _with_blank("Link", LINK),
    "Line": _with_blank("Line", LINE),
    "Transformer": _with_blank("Transformer", TRANSFORMER),
    "Bus": _with_blank("Bus", BUS),
    "Carrier": _with_blank("Carrier", CARRIER),
    "GlobalConstraint": _with_blank("GlobalConstraint", GLOBAL_CONSTRAINT),
    "ShuntImpedance": _with_blank("ShuntImpedance", SHUNT_IMPEDANCE),
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
