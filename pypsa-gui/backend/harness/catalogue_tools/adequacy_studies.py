"""
Adequacy studies: the feature guide, the Energy Hub review and setup, the
six reliability study starters and their abort, the campaign budget and the
study report's schemas and routes.

Moved out of `harness/catalogue.py` (RETRO-FRAG-1); spliced back at its old
place, so it declares no ORDER.
Dispatchers: `services/chat_dispatchers/adequacy_studies.py`.

A schema fragment imports only `harness.catalogue_base`, so the constants
below are copies; `tests/test_tool_fragments.py` pins each to its source.
"""

from __future__ import annotations

from harness.catalogue_base import _DERIVED, _SERVICE_CALL, _empty, _t

# Copies of catalogue's enums (catalogue still uses them for its own tools and
# routes), each pinned equal to catalogue's, order included.
# The six kinds that run in a worker thread, i.e. the ones that can be aborted.
ADEQUACY_STUDY_ENUM = [
    "fmea_sweep",
    "frontier",
    "mc",
    "coupling_loop",
    "margin_loop",
    "eh_study",
]
# Where a reliability loop leaves the network when it finishes.
ADEQUACY_RESTORE_ENUM = ["base", "final"]
# Energy Hub archetype packs — mirrors models.energy_hub.EnergyHubArchetype.
EH_ARCHETYPE_ENUM = ["strong_grid", "weak_flexible", "off_grid"]
# EH pipeline stages — mirrors models.energy_hub.EH_PIPELINE_STAGES (order
# included). An explicit list must keep apply_pack + ens_solve.
EH_STAGE_ENUM = [
    "apply_pack",
    "ens_solve",
    "frontier",
    "front",
    "mc_certify",
    "fmea_top",
    "redundancy",
    "levers",
    "dtc_stress",
    "dtc_planning",
    "assemble",
]
# Engine limits the run_eh_study schema states, pinned to
# models.energy_hub.MAX_EH_BUDGET_SOLVES and services.adequacy.mc.MAX_DRAWS.
_MAX_EH_BUDGET_SOLVES = 120
_MC_MAX_DRAWS = 2000

TOOLS = [
    _t(
        "get_feature_guide",
        "The in-app Energy Hub / FMEA guide — the SAME wording the GUI's "
        "guided tours and hover tips show. Use it to explain what a field or "
        "control does and what to enter. No args: index of tours and fields; "
        "tour=eh_study|fmea|eh_tagging: its steps; field=<name>: one field's "
        "help (e.g. eh_poc, dtc_attribution, target_lole_h). Safety: read.",
        {"tour": {"type": "string"}, "field": {"type": "string"}},
        [],
    ),
    _empty(
        "review_eh_study",
        "Analyse the latest Energy Hub study: summary plus findings sorted "
        "by severity, each with the evidence it read, a recommendation and "
        "(where fully determined) `actions` — an existing tool and its exact "
        "args (run_eh_study re-runs, update_solver_config, ...). Present "
        "findings with their numbers; OFFER actions and run one only when "
        "the user agrees. Safety: read.",
    ),
    _t(
        "suggest_eh_setup",
        "Suggest the Energy Hub tags a network that is not tagged yet needs: "
        "the grid import Link (eh_role = grid_import), the point-of-connection "
        "bus (eh_poc) and the buses whose load must stay on (eh_critical), each "
        "with its reason and confidence, plus the units that lack outage data "
        "(a question for the user — no action). `actions` lists ready "
        "update_component / bulk_update_components calls; present them and run "
        "only the ones the user picks (in Guided mode each asks for "
        "confirmation). Applies nothing. `archetype` only words the reasons. Safety: read.",
        {
            "archetype": {
                "type": "string",
                "enum": ["strong_grid", "weak_flexible", "off_grid"],
            }
        },
        [],
    ),
    _t(
        "run_fmea_sweep",
        "Start the contingency sweep: class B (every single link outage) plus "
        "any class-C `scenarios` given (get them from get_stress_scenarios). "
        "Several LP solves, minutes; returns {status:'running'} immediately — "
        "poll get_adequacy_results('fmea_sweep') for rows. Requires VOLL > 0 "
        "in solver settings (422 without). 409 while another study or a "
        "foreground solve holds the network. The closing base re-solve leaves "
        "the network and the foreground results in base state. "
        "Safety: execution.",
        {"scenarios": {"type": "array", "items": {"type": "object"}}},
    ),
    _t(
        "run_frontier_study",
        "Start the cost-vs-availability (ε-constraint) study: ONE full "
        "capacity-expansion solve per reliability target, so the plan is "
        "re-optimised at every point — this is the curve for 'what would I "
        "BUILD for each standard'. `targets_permyriad` are ENS caps in ‱ and "
        "must be positive; omit for the engine's default spread. Requires "
        "VOLL > 0 (422 without). 409 while another study or a foreground "
        "solve is running. Returns {status:'running'} — poll "
        "get_adequacy_results('frontier') for points and the knee. "
        "Safety: execution.",
        {"targets_permyriad": {"type": "array", "items": {"type": "number"}}},
    ),
    _t(
        "run_mc_study",
        "Start the sequential Monte-Carlo adequacy study — LOLE / EUE, "
        "optionally an ELCC credit table for `elcc_assets` (names from "
        "get_adequacy_results('mc_elcc_candidates')) and/or the whole "
        "profile-bearing fleet as one portfolio via `elcc_portfolio`. Solves "
        "NOTHING and never mutates the network, so it needs no VOLL, but it "
        "is still mutually exclusive with the other studies (409). Minutes "
        "for an ELCC run; returns {status:'running'} — poll "
        "get_adequacy_results('mc'). Safety: execution.",
        {
            "draws": {"type": "integer"},
            "seed": {"type": "integer"},
            "cov_target": {"type": "number"},
            "elcc_assets": {"type": "array", "items": {"type": "object"}},
            "elcc_portfolio": {"type": "boolean"},
        },
    ),
    _t(
        "run_coupling_loop",
        "Start the reliability-targeted planning loop on the ENERGY lever: "
        "solve at an ENS cap, measure LOLE by Monte Carlo, adjust the cap, "
        "repeat until the plan meets `target_lole_h`. `target_lole_h` is "
        "HORIZON-basis hours, NOT h/yr — convert first on a multi-year "
        "horizon and state the basis when reporting. Omit `target_lole_h` / "
        "`draws` to use the project's reliability goal; the result's "
        "`goal_sources` says which values came from the goal (stored / "
        "template / default) and which you overrode. `restore` decides where "
        "the network is left: 'base' (default) or 'final'. Many solves, "
        "returns {status:'running'} — poll "
        "get_adequacy_results('coupling_loop') for iterations. 409 while "
        "another study or a foreground solve is running. Safety: execution.",
        {
            "target_lole_h": {"type": "number"},
            "draws": {"type": "integer"},
            "seed": {"type": "integer"},
            "eps0": {"type": "number"},
            "max_solves": {"type": "integer"},
            "restore": {"type": "string", "enum": ADEQUACY_RESTORE_ENUM},
        },
    ),
    _t(
        "run_margin_loop",
        "Start the reliability-targeted planning loop on the FIRM-CAPACITY "
        "lever: raise the planning reserve margin until the plan meets "
        "`target_lole_h` (horizon-basis hours, as run_coupling_loop; omit it "
        "and `draws` to use the project's reliability goal, named in "
        "`goal_sources`). There "
        "is deliberately NO starting-margin parameter — the start is measured "
        "by a probing solve. Many solves, returns {status:'running'} — poll "
        "get_adequacy_results('margin_loop'). 409 while another study or a "
        "foreground solve is running. Safety: execution.",
        {
            "target_lole_h": {"type": "number"},
            "draws": {"type": "integer"},
            "seed": {"type": "integer"},
            "max_solves": {"type": "integer"},
            "restore": {"type": "string", "enum": ADEQUACY_RESTORE_ENUM},
        },
    ),
    _t(
        "run_eh_study",
        "Start the Energy Hub reference-design study for one archetype pack "
        "(`strong_grid`, `weak_flexible`, or `off_grid`). Applies the pack, "
        "runs the EH pipeline — ENS-capped plan, ε-constraint frontier "
        "around the target, sequential-MC LOLE certification of the fixed "
        "plan on the hub side of the import Link (an import Link counts "
        "only when it carries its own outage data: it is then a two-state "
        "unit at its hourly planning cap, and the grid behind it is sampled "
        "as its own area when that side carries outage data too — one area "
        "per grid reached, grid storage dispatched grid-first, and a Link's "
        "opt-in `common_mode_rate` / `common_mode_mttr_hours` taking the "
        "Link and its grid area down together; `certification.import_model`, "
        "`import_firmness` and `fleet_scope.grid_areas` / "
        "`import_common_mode` say which applied), FMEA top-N residual modes "
        "(the Link-primary Class-B ranking, plus a `class_a` COPT screening "
        "of unit outages on the same plan), then any enabled redundancy / "
        "lever / DtC stages — and persists a ReferenceDesignReport whose TEA "
        "carries LCOE and, where the network "
        "has electrolyser Links, LCOH. Omit `archetype`, the LOLE / ENS "
        "targets in `pack_overrides` and `mc.draws` to use the project's "
        "reliability goal (site type, allowed shortfall, energy strictness, "
        "draws); a value you pass wins for this run, and the result's "
        "`goal_sources` names each one's source. Omit "
        "`stages` for the pack's default pipeline, or pass a non-empty list "
        "to override (it must include apply_pack and ens_solve). "
        "`frontier` sweeps cost vs ENS target around the pack target (the "
        "pack's `frontier_ladder` factors, default ×4 ×2 ×1 ×½ ×¼; default "
        "for strong_grid only, ≤~40% of the budget); `fmea_top` (default for every "
        "archetype) ranks the top "
        "5 Class-B Link failure modes on the ENS plan (needs Links with "
        "outage data; skipped rather than truncated if it does not fit the "
        "budget). "
        "`mc_certify` certifies the ENS plan on Monte Carlo LOLE against the "
        "pack's target_lole_h (h/yr): the report's `certified` is true only "
        "when the LOLE 95% CI upper bound is within target, false on "
        "fail/inconclusive even if the ENS target is met, null when there is "
        "no LOLE target or certification is not established (see the "
        "`certification` section note). The MC samples the hub side only: "
        "grid import counts as capacity only when the import Link carries "
        "its own outage data, so certification is conservative for a "
        "strong grid. It runs by default for weak_flexible / off_grid and "
        "costs no LP solve. "
        "weak_flexible sets each selected import Link's rating (`p_nom`, "
        "and a finite `p_nom_max`) to `import_p_nom_mw` divided by the "
        "number of import Links. Omitted, `import_p_nom_mw` is the network's "
        "connection limit (the import Links' summed capacity); where the "
        "network fixes none the study is refused (422 saying why), and a "
        "value that differs from it is refused naming both: change the limit "
        "on the network instead. The cap replaces each Link's own "
        "rating; when the two differ the `apply_pack` note and the report "
        "state, in plain words, the user's grid connection limit and the cap "
        "the study used. "
        "`budget_solves` (1–120, default 30) is a hard ceiling on LP solves "
        "inside the study — stages that would exceed it are skipped and "
        "reported not_established. Returns {status:'running'} — poll "
        "get_adequacy_results('eh_study') for status and "
        "get_adequacy_results('eh_reference_design') for the assembled "
        "report. 409 while another study or a foreground solve is running. "
        "Optional knobs (all validated before anything runs; a bad value is "
        "a 422 naming the field): `pack_overrides` changes the archetype "
        "pack (ENS target ‱, LOLE target h/yr, certification metric, import "
        "cap MW, stage defaults, levers); `dtc_config` names the critical "
        "buses/loads and islanding Links for DtC; `dsr_buses` opts buses "
        "into demand response (weak_flexible only); `mc` sets the "
        "certification draws/seed/cov_target. "
        "Safety: execution.",
        {
            "archetype": {"type": "string", "enum": EH_ARCHETYPE_ENUM},
            "stages": {
                "type": "array",
                "items": {"type": "string", "enum": EH_STAGE_ENUM},
            },
            "budget_solves": {
                "type": "integer",
                "minimum": 1,
                "maximum": _MAX_EH_BUDGET_SOLVES,
            },
            "pack_overrides": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "ens_cap_permyriad": {"type": "number", "exclusiveMinimum": 0},
                    "target_lole_h": {"type": "number", "minimum": 0},
                    "certification_metric": {
                        "type": "string",
                        "enum": ["mc_lole", "none"],
                    },
                    "import_p_nom_mw": {"type": "number", "exclusiveMinimum": 0},
                    "import_energy_mwh_per_year": {
                        "type": "number",
                        "minimum": 0,
                        "description": (
                            "weak_flexible only: annual energy import budget "
                            "at the hub (MWh/yr), metered as efficiency × p0 "
                            "on the grid→hub import Links, per year of each "
                            "investment period."
                        ),
                    },
                    "mc_certify_required": {"type": "boolean"},
                    "frontier_default": {"type": "boolean"},
                    "dtc_stress_default": {"type": "boolean"},
                    "dtc_planning_default": {"type": "boolean"},
                    "levers": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "redundancy": {"type": "boolean"},
                            "import_cap": {"type": "boolean"},
                            "storage_duration": {"type": "boolean"},
                            "import_energy": {"type": "boolean"},
                        },
                    },
                },
            },
            "dtc_config": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "critical_bus_ids": {"type": "array", "items": {"type": "string"}},
                    "critical_load_ids": {"type": "array", "items": {"type": "string"}},
                    "islanding_contingencies": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    "attribution": {
                        "type": "string",
                        "enum": ["bus_aggregate_not_per_load", "per_load"],
                        "description": (
                            "Default bus_aggregate_not_per_load. per_load "
                            "reports critical unserved by Load, ranking "
                            "non-critical Loads to shed first via a "
                            "disclosed 5% critical VOLL premium in the DtC "
                            "stress re-dispatch only. The ranking is exact "
                            "on loss-free paths; the result flags "
                            "priority_exact=false where lossy Links or line "
                            "losses can invert it."
                        ),
                    },
                },
                "required": ["islanding_contingencies"],
            },
            "dtc_attribution": {
                "type": "string",
                "enum": ["bus_aggregate_not_per_load", "per_load"],
                "description": (
                    "DtC attribution for the config the study derives from "
                    "eh_critical tags (or merged onto dtc_config; a "
                    "conflicting dtc_config.attribution is refused)."
                ),
            },
            "dsr_buses": {"type": "array", "items": {"type": "string"}},
            "mc": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "draws": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": _MC_MAX_DRAWS,
                    },
                    "seed": {"type": "integer", "minimum": 0},
                    "cov_target": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "maximum": 1,
                    },
                },
            },
        },
    ),
    _t(
        "build_study_report",
        "Assemble the client-facing reliability write-up from everything this "
        "session established — and everything it did not. Returns "
        "{objective, campaign, sections, required_disclosures, "
        "not_established, evidence_gaps, counts, writing_note}. Each section "
        "carries its own engine + fidelity, READ from the payload where the "
        "engine states them, so a screening convolution and a sampler never "
        "become two numbers in one table with no label between them. "
        "`required_disclosures` are sentences your prose MUST contain; "
        "`not_established` is what the study did not answer (say it — a "
        "report that omits what it did not measure reads as though it "
        "measured it); `evidence_gaps` are findings that undermine the whole "
        "document (a frozen demand profile, an island nothing can serve, a "
        "failure rate with no recorded source) and belong BEFORE the numbers. "
        "Narrate only from fields present in the payload. `project` names the "
        "asset-health ledger to fold in; it defaults to the project in the "
        "foreground. Safety: read.",
        {"project": {"type": "string"}},
    ),
    _t(
        "start_campaign",
        "Open a reliability CAMPAIGN: one solve budget across a whole chain of "
        "studies. Every engine already caps itself (frontier 12 targets, "
        "class-B sweep 20 contingencies, each loop 8 solves) — nothing caps "
        "chaining them, and a frontier plus two loops plus a sweep is ~50 full "
        "capacity-expansion solves on a shared solver. Start one before "
        "driving a multi-study question ('hit LOLE <= 3 h/yr at least cost'), "
        "state the objective in the user's terms, and the run_* study tools "
        "then charge against it and refuse what would overrun. "
        "`budget_solves` defaults to 30 — a frontier plus a loop, or a full "
        "sweep. run_mc_study is charged ZERO because it solves nothing. "
        "Safety: write.",
        {
            "objective": {"type": "string"},
            "budget_solves": {"type": "integer"},
        },
        ["objective"],
    ),
    _empty(
        "campaign_status",
        "The running campaign: {active, objective, budget_solves, "
        "spent_solves, remaining_solves, started_at, entries: [{study, "
        "solves_charged, at}]}, or {active: false}. Read it before choosing "
        "the next study — `entries` is the only record of what this campaign "
        "already ran, because each study surface holds ONLY its latest result "
        "and a second frontier overwrites the first. Safety: read.",
    ),
    _t(
        "end_campaign",
        "Close the campaign and return its final record (the log survives in "
        "the return value, not on the server). Do this when the objective is "
        "answered or the user redirects — a second start_campaign is refused "
        "while one is open, because it would silently discard this one's log. "
        "Safety: write.",
        {"note": {"type": "string"}},
    ),
    _t(
        "abort_adequacy_study",
        "Stop a running study at its next boundary. IDEMPOTENT and 200 even "
        "when the run has already finished; 404 only when that study never "
        "ran in this session. The closing base restore STILL runs, so the "
        "network is not left mid-contingency. Does NOT stop a foreground "
        "solve — that is abort_simulation. Safety: destructive.",
        {"study": {"type": "string", "enum": ADEQUACY_STUDY_ENUM}},
        ["study"],
    ),
]

ROUTES = {
    "get_feature_guide": [("GET", "/api/guides/{topic}")],
    "review_eh_study": [("GET", "/api/results/eh_review")],  # P24: one source
    "suggest_eh_setup": _SERVICE_CALL,  # P25: pure read of the live network
    "run_fmea_sweep": [("POST", "/api/results/fmea_sweep")],
    "run_frontier_study": [("POST", "/api/results/frontier")],
    "run_mc_study": [("POST", "/api/results/mc")],
    "run_coupling_loop": [("POST", "/api/results/coupling_loop")],
    "run_margin_loop": [("POST", "/api/results/margin_loop")],
    "run_eh_study": [("POST", "/api/results/eh_study")],
    # study report (1) — composite in-process fusion
    "build_study_report": _DERIVED,
    # campaign (3) — process-global study budget, no HTTP route of its own
    "start_campaign": _SERVICE_CALL,
    "campaign_status": _SERVICE_CALL,
    "end_campaign": _SERVICE_CALL,
    "abort_adequacy_study": [
        ("POST", f"/api/results/{s}/abort") for s in ADEQUACY_STUDY_ENUM
    ],
}

# `_SERVICE_CALL` writes that do NOT touch the live network (moved from
# catalogue's `_SERVICE_CALL_NON_NETWORK_WRITES`): the campaign record in
# the resident context's solver_state.
NON_NETWORK_WRITES = frozenset({"start_campaign", "end_campaign"})
