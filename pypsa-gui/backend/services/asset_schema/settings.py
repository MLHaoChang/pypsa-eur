"""
Catalogue entries for what a user sets outside an asset's own columns (spec expert-catalogue §3.1, §3.5,
issue 01a-2).

Two kinds of row:
- project-level settings, each under a settings `scope` naming its owner object (`connection`,
  `reliability`, `levers`, `load_growth`, `load_profile`, `grid_study`, `grid_code`, `demand_response`,
  `site`) and listing the write routes and body fields it covers (`writes`);
- the Energy Hub tag columns on buses and links (scope `Bus` / `Link`), whose `eh_kind` is the one source
  of `models.energy_hub.EH_CUSTOM_COLUMNS`.

Entries describe; they do not enforce. Range checks on writes arrive with 01a-3; until then each route
keeps its own checks unchanged (GK B2 (7) E1).

This module imports only `fields` and `functions`, so `models.energy_hub` can read it without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.asset_schema.fields import FieldSpec
from services.asset_schema.functions import Function as F

SOLVER_CONFIG = "PUT /api/simulation/solver_config"
PROJECT_SETTINGS = "PATCH /api/projects/{name}/settings"
SITE_ANCHOR = "PUT /api/projects/{name}/site-anchor"
EH_STUDY = "POST /api/results/eh_study"
LOAD_GENERATE = "POST /api/projects/{name}/profiles/loads/generate"
ISLAND_PUT = "PUT /api/campus-electrical/{name}/island"
ISLAND_PATCH = "PATCH /api/campus-electrical/{name}/island"
ISLAND_STUDY = "POST /api/campus-electrical/{name}/island/study"
CAMPUS_RUN = "POST /api/campus-electrical/{name}/run"

#: Write routes the settings coverage test walks (method + path prefix over the app's route table).
SETTINGS_ROUTE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("PUT", "/api/simulation/solver_config"),
    ("PATCH", "/api/projects/{name}/settings"),
    ("PUT", "/api/projects/{name}/site-anchor"),
    ("POST", "/api/results/eh_study"),
    ("PUT", "/api/results/business_case/inputs"),
    ("POST", "/api/projects/{name}/profiles/loads/generate"),
    ("PUT", "/api/campus-electrical/{name}/island"),
    ("PATCH", "/api/campus-electrical/{name}/island"),
    ("POST", "/api/campus-electrical/{name}/island"),
    ("POST", "/api/campus-electrical/{name}/run"),
    ("PUT", "/api/simulation/finance"),
    ("PUT", "/api/simulation/commercial/value_flows"),
    ("PUT", "/api/gridspine/{name}/config"),
    ("PATCH", "/api/projects/{name}/scenario"),
)

#: Link roles in the order of `models.energy_hub.EH_LINK_ROLES`, with their plain names (a test keeps
#: the two in step; this module cannot import `models.energy_hub`, which reads it).
EH_ROLE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("", "None"),
    ("grid_import", "Grid import"),
    ("eh_import", "Import"),
    ("import", "Import (other)"),
    ("eh_conversion", "Conversion"),
    ("conversion", "Conversion (other)"),
    ("electrolyser", "Electrolyser"),
    ("fuel_cell", "Fuel cell"),
    ("eh_n1_conversion", "Redundancy study copy"),
)

#: Island control modes and the words the frontend shows today (`frontend/src/plainNames.ts`).
CONTROL_MODES: tuple[tuple[str, str], ...] = (
    ("gfl_pq", "Grid-following, fixed power (P/Q)"),
    ("gfl_qu", "Grid-following, voltage-dependent reactive power (Q/U)"),
    ("gfl_pf", "Grid-following, fixed power factor"),
    ("gfl_fw", "Grid-following, frequency response"),
    ("gfm_droop", "Grid-forming, droop"),
    ("gfm_vsm", "Grid-forming, virtual synchronous machine"),
)

ISLAND_SCENARIOS: tuple[tuple[str, str], ...] = (
    ("ups_bridged", "UPS-bridged"),
    ("seamless", "Seamless"),
    ("planned", "Planned"),
)


@dataclass(frozen=True)
class Setting:
    scope: str
    spec: FieldSpec
    group: str  # one of the nine plain groups (entries.GROUP_IDS)
    functions: dict[str, str] = field(default_factory=dict, hash=False)
    writes: tuple[
        str, ...
    ] = ()  # "<METHOD> <path> <body field>"; "*" for an undeclared body
    default: Any = None  # the engine's own default, shown never written; None: none
    eh_kind: str = ""  # bool | float_nonneg | role: an EH_CUSTOM_COLUMNS column


def _s(
    key,
    plain_name,
    short_help,
    *,
    unit="",
    share=False,
    kind="number",
    options=(),
    options_plain=(),
    depth="basic",
    required=False,
    aliases=(),
) -> FieldSpec:
    # `group` and `label` are the legacy asset-form fields; settings rows never reach that form.
    return FieldSpec(
        key,
        "identity",
        plain_name,
        short_help,
        unit_display="%" if share else unit,
        unit_storage="" if share else unit,
        scale=100.0 if share else 1.0,
        kind=kind,
        options=tuple(options),
        options_plain=tuple(options_plain),
        depth=depth,
        required=required,
        plain_name=plain_name,
        short_help=short_help,
        aliases=tuple(aliases),
    )


def _opts(pairs):
    return {
        "options": tuple(k for k, _ in pairs),
        "options_plain": tuple(v for _, v in pairs),
    }


_ELEC = {F.LOAD_FLOW: "uses", F.SHORT_CIRCUIT: "uses", F.GRID_COMPLIANCE: "uses"}
_DISPATCH = {
    F.DISPATCH: "uses",
    F.EXPANSION: "uses",
    F.ADEQUACY: "uses",
    F.FINANCE: "uses",
}

SETTINGS: tuple[Setting, ...] = (
    # Energy Hub tags on buses and links (EH_CUSTOM_COLUMNS, in its order).
    Setting(
        "Bus",
        _s(
            "eh_poc",
            "Grid point of connection",
            "This bus is where the site connects to the public grid.",
            kind="bool",
            aliases=("PoC", "PCC"),
        ),
        "general",
        {F.ADEQUACY: "uses", F.GRID_COMPLIANCE: "uses"},
        default=False,
        eh_kind="bool",
    ),
    Setting(
        "Bus",
        _s(
            "eh_critical",
            "Critical bus",
            "Supply on this bus must not be lost. The Energy Hub studies read it to find the site side.",
            kind="bool",
        ),
        "reliability",
        {F.ADEQUACY: "uses"},
        default=False,
        eh_kind="bool",
    ),
    Setting(
        "Bus",
        _s(
            "eh_sk_mva",
            "Short-circuit level at the connection",
            "The grid's fault level at this bus. A weak grid has a low value.",
            unit="MVA",
            aliases=("fault level", "Sk"),
        ),
        "electrical",
        _ELEC,
        eh_kind="float_nonneg",
    ),
    Setting(
        "Bus",
        _s(
            "eh_ibr_mva",
            "Inverter-based capacity",
            "The inverter-connected capacity already behind this bus.",
            unit="MVA",
            aliases=("IBR",),
        ),
        "electrical",
        _ELEC,
        eh_kind="float_nonneg",
    ),
    Setting(
        "Link",
        _s(
            "eh_role",
            "Energy Hub role",
            "What this link does in the Energy Hub: a grid import, a conversion or neither.",
            kind="enum",
            **_opts(EH_ROLE_OPTIONS),
        ),
        "general",
        {F.ADEQUACY: "uses", F.DISPATCH: "uses"},
        default="",
        eh_kind="role",
    ),
    # The grid connection.
    Setting(
        "connection",
        _s(
            "poc_xr",
            "Grid X/R ratio at the connection",
            "The grid's reactance over resistance at the connection. Left unset, the scorecard assumes 4.",
            aliases=("X/R",),
        ),
        "electrical",
        {F.SHORT_CIRCUIT: "uses", F.GRID_COMPLIANCE: "uses"},
        writes=(f"{PROJECT_SETTINGS} poc_xr",),
    ),
    Setting(
        "connection",
        _s(
            "backup_autonomy_h",
            "Backup autonomy",
            "How long the backup supply must carry the critical load alone.",
            unit="h",
        ),
        "reliability",
        {F.ADEQUACY: "uses"},
        writes=(f"{PROJECT_SETTINGS} backup_autonomy_h",),
        default=4.0,  # models/schemas.py BACKUP_AUTONOMY_DEFAULT_H
    ),
    Setting(
        "site",
        _s(
            "site_anchor",
            "Site coordinates",
            "Where the site is on the map. The weather and the map views read it.",
            kind="location",
        ),
        "general",
        {F.DISPATCH: "uses"},
        writes=(f"{SITE_ANCHOR} *",),
    ),
    Setting(
        "site",
        _s(
            "timezone",
            "Site time zone",
            "The site's local time zone. Tariff periods and billing months follow its clock.",
            kind="text",
            depth="advanced",
        ),
        "general",
        {F.FINANCE: "uses"},
        writes=(f"{SOLVER_CONFIG} commercial.timezone",),
    ),
    # Reliability goal.
    Setting(
        "reliability",
        _s(
            "target_lole_h",
            "Allowed shortfall",
            "Hours per year the site may be short of supply.",
            unit="h/yr",
            aliases=("LOLE",),
        ),
        "reliability",
        {F.ADEQUACY: "needs"},
        writes=(
            f"{SOLVER_CONFIG} target_lole_h",
            f"{EH_STUDY} pack_overrides.target_lole_h",
        ),
    ),
    # Demand response.
    Setting(
        "demand_response",
        _s(
            "dsr_share_of_load",
            "Demand-response volume",
            "The share of each bus's peak load that may be shed on request.",
            share=True,
            aliases=("DSR", "demand response"),
        ),
        "operation",
        _DISPATCH,
        writes=(f"{SOLVER_CONFIG} dsr_share_of_load",),
        default=0.0,
    ),
    Setting(
        "demand_response",
        _s(
            "dsr_price_eur_per_mwh",
            "Demand-response price",
            "What each unit of load shed on request costs.",
            unit="€/MWh",
        ),
        "running_cost",
        _DISPATCH,
        writes=(f"{SOLVER_CONFIG} dsr_price_eur_per_mwh",),
        default=0.0,
    ),
    # Optimisation levers (models/energy_hub.py OptimizationLevers): per-run overrides of the archetype pack.
    Setting(
        "levers",
        _s(
            "sizing",
            "Size the assets",
            "The study sizes the assets rather than keeping today's sizes.",
            kind="bool",
            depth="advanced",
        ),
        "investment",
        {F.EXPANSION: "uses"},
        default=True,  # not a per-run override: the archetype pack sets it
    ),
    Setting(
        "levers",
        _s(
            "redundancy",
            "Redundancy",
            "The study may add spare units so one failure loses no supply.",
            kind="bool",
            depth="advanced",
        ),
        "investment",
        {F.EXPANSION: "uses", F.ADEQUACY: "uses"},
        writes=(f"{EH_STUDY} pack_overrides.levers.redundancy",),
        default=False,
    ),
    Setting(
        "levers",
        _s(
            "import_cap",
            "Import capacity",
            "The study may change how much the site may draw from the grid.",
            kind="bool",
            depth="advanced",
        ),
        "investment",
        {F.EXPANSION: "uses"},
        writes=(f"{EH_STUDY} pack_overrides.levers.import_cap",),
        default=False,
    ),
    Setting(
        "levers",
        _s(
            "storage_duration",
            "Battery duration",
            "The study may change how many hours the battery holds.",
            kind="bool",
            depth="advanced",
        ),
        "investment",
        {F.EXPANSION: "uses"},
        writes=(f"{EH_STUDY} pack_overrides.levers.storage_duration",),
        default=False,
    ),
    Setting(
        "levers",
        _s(
            "import_energy",
            "Import energy",
            "The study may change how much energy the site buys in a year.",
            kind="bool",
            depth="advanced",
        ),
        "investment",
        {F.EXPANSION: "uses"},
        writes=(f"{EH_STUDY} pack_overrides.levers.import_energy",),
        default=False,
    ),
    # Load growth (homer-parity 04a; the words of #1604 and services/solver/load_growth.py).
    Setting(
        "load_growth",
        _s(
            "load_growth_pct_per_year",
            "Load growth",
            "How fast each energy type's demand grows per year. It must be more than minus 100 %.",
            unit="%/yr",
            aliases=("demand growth",),
        ),
        "operation",
        _DISPATCH,
        writes=(f"{SOLVER_CONFIG} load_growth_pct_per_year",),
    ),
    Setting(
        "load_growth",
        _s(
            "load_growth_base_year",
            "Growth from year",
            "The year growth counts from. Required with a growth rate, and not after the first period.",
            kind="int",
            required=True,
        ),
        "operation",
        _DISPATCH,
        writes=(f"{SOLVER_CONFIG} load_growth_base_year",),
    ),
    Setting(
        "load_growth",
        _s(
            "load_scalers",
            "Load multiplier",
            "Scales every load in a period. 1 leaves it unchanged.",
            unit="×",
        ),
        "operation",
        _DISPATCH,
        writes=(f"{SOLVER_CONFIG} load_scalers",),
    ),
    Setting(
        "load_growth",
        _s(
            "load_scalers_by_carrier",
            "Load multiplier per energy type",
            "Scales one energy type's load in a period. 1 leaves it unchanged.",
            unit="×",
            depth="advanced",
        ),
        "operation",
        _DISPATCH,
        writes=(f"{SOLVER_CONFIG} load_scalers_by_carrier",),
    ),
    # Load profile generator (services/profile_gen/load_archetypes.py); the frontend's words (plainNames.ts).
    Setting(
        "load_profile",
        _s(
            "holidays",
            "Holiday dates",
            "Days the site runs as on a Sunday.",
            kind="list",
        ),
        "operation",
        {F.DISPATCH: "uses"},
        writes=(f"{LOAD_GENERATE} params.holidays",),
    ),
    Setting(
        "load_profile",
        _s(
            "timezone",
            "Time zone",
            "The clock the profile's hours follow.",
            kind="text",
            depth="advanced",
        ),
        "operation",
        {F.DISPATCH: "uses"},
        writes=(f"{LOAD_GENERATE} params.timezone",),
    ),
    Setting(
        "load_profile",
        _s(
            "seasonal_amplitude",
            "Seasonal swing",
            "How far demand moves between summer and winter.",
            share=True,
        ),
        "operation",
        {F.DISPATCH: "uses"},
        writes=(f"{LOAD_GENERATE} params.seasonal_amplitude",),
        default=0.0,
    ),
    Setting(
        "load_profile",
        _s(
            "noise",
            "Random variation",
            "Hour-to-hour randomness added to the profile.",
            share=True,
            depth="advanced",
        ),
        "operation",
        {F.DISPATCH: "uses"},
        writes=(f"{LOAD_GENERATE} params.noise",),
        default=0.0,
    ),
    Setting(
        "load_profile",
        _s(
            "seed",
            "Random seed",
            "Fixes the random variation so a rerun gives the same profile.",
            kind="int",
            depth="detail",
        ),
        "operation",
        {F.DISPATCH: "uses"},
        writes=(f"{LOAD_GENERATE} params.seed",),
    ),
    Setting(
        "load_profile",
        _s(
            "shifts",
            "Shifts per day",
            "How many shifts an industrial site runs: one, two, three or around the clock.",
            kind="enum",
            options=("1", "2", "3", "continuous"),
            options_plain=("One", "Two", "Three", "Around the clock"),
        ),
        "operation",
        {F.DISPATCH: "uses"},
        writes=(f"{LOAD_GENERATE} params.shifts",),
        default=2,
    ),
    Setting(
        "load_profile",
        _s(
            "base_share",
            "Always-on share",
            "The share of peak demand that runs outside shifts.",
            share=True,
        ),
        "operation",
        {F.DISPATCH: "uses"},
        writes=(f"{LOAD_GENERATE} params.base_share",),
        default=0.25,
    ),
    Setting(
        "load_profile",
        _s(
            "saturday_operation",
            "Runs on Saturdays",
            "The site works Saturdays as a weekday.",
            kind="bool",
        ),
        "operation",
        {F.DISPATCH: "uses"},
        writes=(f"{LOAD_GENERATE} params.saturday_operation",),
        default=False,
    ),
    Setting(
        "load_profile",
        _s(
            "shutdown_weeks",
            "Shutdown weeks",
            "Calendar weeks the site stands still.",
            kind="list",
        ),
        "operation",
        {F.DISPATCH: "uses"},
        writes=(f"{LOAD_GENERATE} params.shutdown_weeks",),
    ),
    # Grid study (island operation and the grid-code check).
    Setting(
        "grid_study",
        _s(
            "scenarios",
            "Island scenarios",
            "Which ways of leaving the grid the island study tests.",
            kind="enum_list",
            **_opts(ISLAND_SCENARIOS),
        ),
        "reliability",
        {F.GRID_COMPLIANCE: "uses", F.ADEQUACY: "uses"},
        writes=(f"{ISLAND_STUDY} scenarios",),
    ),
    Setting(
        "grid_study",
        _s(
            "control",
            "Control mode",
            "How each unit's inverter behaves when the site runs as an island.",
            kind="enum",
            depth="advanced",
            **_opts(CONTROL_MODES),
        ),
        "electrical",
        {F.GRID_COMPLIANCE: "uses"},
        # Held per unit in the island sidecar (`units.<unit>.control`) inside the free-form `config` /
        # `patch` bodies, which stay gap lines until the sidecar is typed.
    ),
    Setting(
        "grid_code",
        _s(
            "grid_code_profile",
            "Grid code",
            "The connection rules the site is checked against.",
            kind="text",
        ),
        "electrical",
        {F.GRID_COMPLIANCE: "needs"},
        writes=(f"{CAMPUS_RUN} profile",),
        default="eu_rfg_dcc_ce",
    ),
    Setting(
        "grid_code",
        _s(
            "grid_code_document",
            "Grid-code document",
            "An uploaded grid-code text the check reads its limits from.",
            kind="file",
            depth="advanced",
        ),
        "source",
        {F.GRID_COMPLIANCE: "uses"},
    ),
)

#: Body fields of the settings routes that are server-owned, each with its reason (spec §3.5).
NOT_OFFERED_SETTINGS: dict[str, str] = {
    f"{SOLVER_CONFIG} load_scalers_derived": "engine-internal: the server derives it from the growth rate; a sent value is dropped.",
}

#: FIELD_LABELS keys (frontend/src/utils/fieldLabels.ts) that name no single entry, with the reason.
FIELD_LABEL_KEYS_NOT_ENTRIES: dict[str, str] = {
    "connection_limit": "the grid-import link's size: the link capacity entry, under the connection card's word.",
    "business_case": "a whole Results section (the business case inputs), not one parameter.",
    "variants": "the project variants list, not a parameter.",
}


def eh_custom_columns() -> dict[str, dict[str, str]]:
    """`models.energy_hub.EH_CUSTOM_COLUMNS`: class → {column: kind}, in the rows' order."""
    out: dict[str, dict[str, str]] = {}
    for s in SETTINGS:
        if s.eh_kind:
            out.setdefault(s.scope, {})[s.spec.key] = s.eh_kind
    return out
