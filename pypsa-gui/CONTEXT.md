# pypsa-gui

The React + FastAPI application layered over PyPSA. It owns a vocabulary that
is *not* PyPSA's, and the two overlap on several words — this file is where
that overlap is settled so a spec, a plan, or a reviewer does not have to
re-derive it.

Every term below was checked against the code at the cited location. Extend
this file with `domain-modeling` when a spec needs a term it does not define;
do not add general programming concepts.

## Language

**Project**:
An org-scoped row in the `projects` table owning a storage directory. Name and
`storage_path` are each unique per org (`backend/db/models.py:41`).
_Avoid_: workspace, model, case

**Scenario**:
A Project derived from another Project via `POST /api/projects/{base}/scenarios`,
which copies the base's chat history into the new directory
(`backend/routers/projects.py:2300`). A Scenario *is* a Project row — not a
separate entity — so anything true of Projects is true of Scenarios.
_Avoid_: variant, branch, copy

**Context** (`ctx`):
The Project currently bound to a session, and the thing most routes resolve
before doing anything (`backend/services/active_project.py`). Route handlers
reject when the active ctx is not the project named in the request.
_Avoid_: current project, session project, active model

**Snapshot**:
Overloaded across three unrelated concepts. In prose, a spec, or a plan, always
qualify it; unqualified "snapshot" is the defect, not any one of the meanings.

- **Saved snapshot** — a project state persisted to disk under the project's
  snapshot directory, addressed by `snapshot_id` (`backend/routers/snapshots.py`).
  This is the one users see.
- **Time step** — PyPSA's own meaning: one entry on the network's time index,
  carrying `snapshot_weightings`. Upstream API, not renameable.
- **State capture** — an in-memory copy taken for undo or for the dispatch-fix
  path (`_state_snapshot`, `undo_snapshot_middleware`).

**Component**:
PyPSA's term for a network element class — Bus, Generator, Link, Line, Store.
Correct in backend code and anywhere the PyPSA API is in view.

**Asset**:
The user-facing word for the same objects on the map canvas and in the
parameter table. Correct in UI copy and user-facing endpoints
(`backend/routers/asset_results.py`); prefer **Component** in backend prose so
the PyPSA mapping stays visible.

**Asset write**:
Any frontend update of a Component through `PUT /api/network/{class}/{name}`.
The backend's remove+add cycle resets every omitted field to schema defaults,
so an Asset write MUST spread the full current row under the patch — and the
module that owns that idiom (fetch → spread → PUT → invalidate) is
`frontend/src/utils/assetWrite.ts`. A hand-rolled read-spread copy at a call
site is the defect this term exists to name (B1/B2 corruption class; the
chat-staleness silent revert).
_Avoid_: mutation (overloaded with React Query's `useMutation`), save (that's
project persistence)

**Decision study**:
A guided investment question answered for one base Project ("Do I need a
battery at my site?"): intake answers, assumptions ledger, options, findings
and report, stored as the sidecar `studies/<study_id>.json` inside that
Project's storage directory (`backend/services/study/store.py`) and served under
`/api/projects/{name}/studies` (`backend/routers/studies.py`). Its option
Projects are study-owned forks. Unqualified "study" already names two other
things in this codebase, so always write **decision study** (ids and keys
`decision_study`).
_Avoid_: study, investment study, the gridspine study (the planning → dynamics
pipeline, `backend/routers/gridspine.py`), the adequacy studies (frontier, mc,
fmea_sweep, margin_loop, coupling_loop, eh_study; `STUDY_KEYS` in
`backend/services/project_context.py`)

**Vintage**:
A per-investment-period capacity bound on a component
(`backend/routers/vintage.py`). Multi-period work expands these transiently
rather than storing one row per period.

**Solve**:
One optimisation run over the network — `n.optimize()`, driven through
`solver_service.run_simulation` and queued by `backend/routers/solve_queue.py`.
Always off the request thread; it blocks.
_Avoid_: simulation, optimisation run, job

**Unavailable**:
The state of a figure the backend could not resolve, as distinct from a figure
that resolved to zero. Zero is a legitimate result in an energy-system model,
so the two must never share a representation — see
[ADR-0001](docs/adr/0001-unresolvable-figures-ship-as-null.md). A payload says
which one it means by carrying an explicit flag beside the value; a bare `0.0`
asserts a real zero.
_Avoid_: missing, empty, N/A, no data, zero

**Site**:
A user-drawn boundary on the map around one or more member Buses, drawn in
3D by the site view (`frontend/src/pages/SiteCanvas.tsx`). Sites and the
user's placements of objects inside them live in the project's `sites.json`
sidecar (`GET/PUT /api/projects/{name}/sites`), never in the network, so
moving an object is not an Asset write and does not touch results. A
**campus study** (gridspine's electrical analysis of a solved hub project,
`backend/routers/campus_electrical.py`) *runs on* a Site's member Buses; it
holds no geometry of its own, and a Site holds no electrical result. Neither
word replaces the other: Site for the drawn plot and placements, campus study
for the transformer, compensation, short-circuit and grid-code analysis.
_Avoid_: campus (unqualified), plant, zone (a zone is the packer's placement band)

**Placement rule**:
A library entry's statement of where its type stands relative to the others
on a Site — anchor (in its yard, between its two buses' yards, beyond the far
yard), neighbours it sits next to, clearance, keep-out distances, which way
it faces (`frontend/src/site3d/assetLibrary.ts`, `placement`). The packer
honours it; the **placement check** (`site3d/placementCheck.ts`) reports
where an arrangement breaks one as a **layout finding** — advisory text in
the site overlay and the Issues panel and a red or amber outline, never a
block on saving. A placement the user made always wins over the rule.
_Avoid_: constraint (as if it blocked), validation error, zoning

**Site results**:
The 3D site view's per-object values at the selected time step — output,
state of charge, loading and flow direction — as one map per time step,
`"Class:name"` → state (`frontend/src/site3d/useSiteResults.ts`). They show
only while the dispatch is **fresh** for the network as it is now: the
solver status says `dispatch: 'fresh'` for this solve (objective and solve
time), the component lists the site reads were refetched for that solve and
still hold the same data (an edit ends it at once), and every result chunk
shown was fetched after that solve and not invalidated since
(`site3d/useDispatchFresh.ts`). Otherwise the site shows no results rather
than stale ones.
_Avoid_: overlay (the canvases' per-bus aggregation), playback

**Coordinate profile**:
A capacity-factor time series (values in [0, 1] over one weather year) that
the renewable profile generator computes for a named lon/lat point
(`backend/services/profile_gen/engine.py`). Generating one is a queued
`profile_gen` job whose artifact waits under
`<storage>/profiles_pending/<job_id>.nc` until the user **applies** it onto a
generator's `p_max_pu` (`backend/services/profile_gen/apply.py`) — generation
never touches the network by itself. The point id doubles as the generator
name at apply time. The engine's `resolution` argument aggregates at
generation: `hourly` is the native axis, `daily`/`weekly` are means over the
hour, upsampled back onto the network's hourly snapshots at apply (weekly
applies to its own calendar year only and never tiles investment periods).
The chat assistant drives the same tools through the `renewable-profile`
guided workflow, and its preview/generate tools accept `from_generators`
(generator names — the bus coordinates seed the sites) instead of raw
coordinates.
_Avoid_: weather series, yield curve, profile upload (that's the xlsx path in
`routers/network_profiles.py`)

**Profile source**:
Which engine produced a coordinate profile — `synthetic` (deterministic
shapes from `services/profile_gen/synthetic.py`, no download, used for
testing and the estimate preview) or `atlite` (ERA5 reanalysis through
atlite, `services/profile_gen/atlite_source.py`). The estimate preview is
always synthetic even when the queued generation is atlite; say which one a
figure came from.
_Avoid_: real vs fake, live data, historical mode

**Cutout cache**:
The on-disk store of atlite ERA5 cutouts keyed by bounding box + weather
year (`backend/services/profile_gen/cutout_cache.py`), so a second
generation over the same region does not re-download. Filling it needs CDS
API credentials; without them the backend refuses with a 422 that names the
setup (`cds_preflight` → `CdsCredentialError`).
_Avoid_: weather cache, ERA5 database, download folder

**Profile preset**:
A named bundle of technology parameters — a wind turbine or solar
panel/orientation — that the generator resolves into engine params
(`backend/services/profile_gen/presets.py`, `GET /api/profiles/presets`).
`default` presets carry empty params (synthetic source only); the real ones
(`vestas-v90-3mw`, `cdte-horizontal-tracking`, …) feed atlite. Expert-mode
overrides merge on top via `resolve_params`.
_Avoid_: template (that's network profiles), turbine type, config

**AI-training load profile**:
The opt-in hourly load of a data-center template built from a
`DataCentreLoadSpec` with an `AiWorkloadSpec`: IT MW × hourly mean
utilisation × PUE(ambient), split by `critical_share` over `it_load` and
`cooling` (`backend/services/dc_load_profile.py`). Chosen with a create body
`{"dc_load": {...}}`; without it the template keeps its load.
_Avoid_: GPU profile generator (that's `profile_gen`, renewables)

**Dynamic envelope**:
The sub-hourly behaviour of an AI-training load that the LP never sees, per
COD phase: swing (MW), max ΔP/Δt (MW/s), checkpoint dip (MW and MW/s) and the
oscillation band (peak amplitude in MW, period in s). IT side at the POC,
before any UPS/BESS smoothing; every value assumed. Stored under `dc_load` in
the project's `eh_template.json`, read with `read_envelope`.
_Avoid_: ramp profile, transient study

## Assistant language

The in-app assistant's own vocabulary. The code lives in
`pypsa-gui/backend/harness/` (its README is the contract); the spec is
`.scratch/harness/spec.md`.

**Harness**:
Everything the assistant needs that is not a language model: the provider
seam (`harness/protocol.py`), the tool catalogue (`harness/catalogue.py`),
the frame vocabulary, the workflows, the skills, and (as the loop moves) the
session, confirmation, budget and history machinery. A provider plugs in
underneath it. The word comes from the provider-seam spec of 2026-08-05; the
folder makes the tree match the word.
_Avoid_: agent layer, chatbot core, orchestrator

**Provider** / **Wire**:
A Provider is one implementation of the seam (Anthropic, OpenAI-compatible,
the fake); the Wire is its message format (`anthropic` or `openai`), named
only inside the provider layer and in the profile store.
_Avoid_: backend (overloaded), vendor, model (that is the id a profile names)

**Workflow**:
A step-by-step flow the assistant leads, defined as one Markdown file in
`harness/workflows/` with front matter (where it is offered, its steps and
their completion criteria) and one body section per step. Runtime state is
the pair (workflow id, step) on the chat session; the current step's body
travels as per-turn user content, never in the system prompt. The hub-design
Guided flow is the first workflow.
_Avoid_: playbook, recipe, flow (unqualified), wizard (that is a dialog)

**Start menu**:
The list of workflows offered when a chat opens in a given context (no
project, Expert, Guided). Served by the harness; the panel renders it as
chips. Replaces the hardcoded "Try asking" prompt arrays.
_Avoid_: starter prompts, suggestions, quick actions

**Skill**:
A reusable procedure the assistant loads on demand: `harness/skills/<name>/SKILL.md`
with `name` and `description` front matter, the same layout as the developer
agents' `.claude/skills/`. The prompt carries only the catalogue line; the
body arrives through the `use_skill` tool. `grill` is the first skill. A
**user skill** has the same layout under `<app data>/skills/`; it ranks after
the package skills and is refused, never used, when a package skill has its
name. Its provenance (`package` or `user`) is shown in the panel's listing,
never in the prompt.
_Avoid_: prompt template, macro, plugin

**Event log**:
A session's append-only record of everything the model saw (`harness/eventlog.py`):
each message as it entered the provider history, the per-turn context, and the
trims, rewinds and resets of that history, so the history can be rebuilt
exactly, forked at a turn and replayed on another provider. Kept per user and
chat under the app-data directory. It is the conversation's source of truth:
what the model is sent is its projection, and `chat.jsonl` (which still serves
`/history`) is written from it.
_Avoid_: transcript (that is chat.jsonl), audit log

**Goal**:
What the assistant keeps working toward over several turns without being
asked each time (`harness/goals.py`): an objective and a round budget
(default 5, at most 20). After a turn, the next round starts on its own until
the assistant completes or pauses the goal, the rounds are used up, or a
turn needs the user (a card, a question, an error, a usage limit). Every
change still needs its card. Only the user resumes a paused or blocked goal,
from the goal strip.
_Avoid_: task (that is a durable assistant task), plan

**External tool**:
A tool from an MCP server an install's super-admin listed in
`<app data>/mcp-servers.json` (`harness/mcp_client.py`), offered to the
assistant as `mcp__<server>__<tool>` beside the built-in catalogue, for every
provider. Its list is fixed for the chat session. Unless the server marks it
read-only, it gets the confirmation card in Expert as well as Guided, and its
results are fenced like any tool result. There are none until an admin lists a
server.
_Avoid_: plugin, connector, integration

**External agent**:
An agent on the same machine (Claude Code, Codex, a Kimi agent) that drives
the running app through `/api/mcp` (`harness/mcp_server.py`), local mode only,
with the token in `<app data>/mcp-endpoint.json`. It gets the catalogue
without the assistant's own control tools, in a chat session of its own
(`mcp-…`); every call it makes that writes waits for a person to approve its
card in the chat panel.
_Avoid_: bot, integration, remote client

**Choice card**:
The card the panel renders when the assistant calls `ask_user`: a title, a
question, options with one marked recommended, and a free-text field. A pick
is sent as the next user message; the turn does not block on it. Distinct
from the Confirmation card, which gates a write or run and does block.
It may carry a **detail** (Markdown under the question), allow several
picks (**multi-select**: one Send button, the picks joined by "; "), or be a
**plan review**: the detail is a plan and the card takes one verdict.
_Avoid_: prompt, dialog, confirmation (that is the other card)

**Run evidence**:
The immutable record of one foreground solve, queued solve, adequacy study,
or report, stored under `<project>/run_evidence/<run_id>/`
(`backend/services/run_evidence.py`). Section files are hashed; the terminal
`manifest.json` is written last and left unchanged. A missing or altered
artifact is Unavailable for that run id.
_Avoid_: the newest results, the live network, a saved snapshot

**Discussed run**:
The run id the current chat episode is talking about. `discuss_project_run`
stores it on the session and, when a durable parent thread is bound, on
`refs.discussed`. Unless called with `open_ui=false`, it opens the Results
chrome and selects that run by identifier. Resuming the parent copies the same identifiers onto the new
episode (`durable_conversation.resume_episode`). The binding leaves the live
network's inputs in place.
_Avoid_: the active solve, checked scope (the assurance binding)

**Experiment plan**:
A typed list of cases saved under `<project>/experiments/` by
`start_experiment` (`backend/services/experiments.py`). Each case is 1–12
typed changes. The record stays `planned` with `executed: false` until a
later slice runs the cases through ordinary harness tools.
_Avoid_: scenario (a Scenario is a Project), sensitivity sweep (that tool
queues solves), task (a durable sequence of ordinary tool calls)

## Investment language

Shared by the expert workbench and the guided investment path, which run on one
engine ([ADR-0004](docs/adr/0004-one-investment-engine-guided-mode-compiles-onto-it.md)).

**Expert mode** / **Guided mode**:
The two ways one Project is worked on. Expert mode exposes every parameter;
Guided mode asks only for Key parameters, fills the rest with Generic defaults
and lets the chat assistant lead each step. Both read and write the same
project state; neither holds a copy.
_Avoid_: face, simple UI, novice view, pro view

**Decision study**:
A question-led evaluation above a Project ("Do I need a battery at my site?"):
it owns a Decision question, an Assumptions ledger, a Baseline, a few Options
and a Verdict. Always qualify it; bare "study" already names the running
analyses on a Project (the Energy Hub study, adequacy studies).
_Avoid_: study (unqualified), investment study, scenario

**Decision question**:
The template a Decision study starts from; it fixes which Options are offered
and which Key parameters are asked.
_Avoid_: question pack, use case

**Baseline**:
The "do nothing" Option of a Decision study, against which every headline
figure is a delta.
_Avoid_: reference case, counterfactual (that word is the engine's own,
narrower: the bill the owner would pay without its assets)

**Option**:
One candidate design in a Decision study, solved on its own fork of the
Project.
_Avoid_: alternative, variant (a variant is a throw-away fork for one
sensitivity bound)

**Verdict**:
The Decision study's answer for its question: recommended, marginal or not
recommended, with the drivers that could flip it.
_Avoid_: result, recommendation (as a noun for the whole answer)

**Key parameter**:
An input Guided mode asks the user for because it moves the Verdict. Every
other input is a Generic default.
_Avoid_: main input, basic setting

**Driver**:
A Key parameter whose range is tested for robustness; a Verdict is marginal
when one driver's bound flips its sign.
_Avoid_: sensitivity, lever

**Generic default**:
An illustrative value from the Library (value, unit, range, source, year) used
when the user has not supplied one. It is always listed in the report, and it
is never an engine default: the engine still treats an unsupplied value as
Unavailable.
_Avoid_: default (unqualified), assumption (that is any ledger row), preset

**Assumptions ledger**:
The list of every input a Decision study uses, each with value, unit, range,
provenance (library, user, imported, measured) and whether the user changed it.
Always say "assumptions ledger"; the engine's own ledger is the Value-flow
ledger.
_Avoid_: ledger (unqualified), parameter list, inputs table

**Value-flow ledger**:
The engine's per-participant cash-flow lines (who pays whom, for which value
stream and tariff item).
_Avoid_: ledger (unqualified), cashflow table

**Investment case**:
The financial evaluation of one solved Project for one owner over its life:
cash flows, NPV, IRR, payback and debt metrics, with every figure that could
not be established marked Unavailable.
_Avoid_: pro forma, business case, finance run

**Bill**:
What a site is charged at its point of connection for one billing period,
rated item by item from its Tariff.
_Avoid_: energy cost, invoice

**Library**:
The org-scoped store of reusable inputs: tariffs, contracts, connection
agreements, price and meter series, and the Generic defaults.
_Avoid_: catalogue, database, study library

**Pack**:
Overloaded; always qualify it.

- **Archetype pack**: an Energy Hub site archetype's settings (strong grid,
  weak flexible, off grid).
- **Jurisdiction pack**: dated, cited tax and incentive rules for one
  jurisdiction (DE, NL, US federal, CA federal).
- **Generic defaults pack**: the versioned set of Generic defaults in the
  Library.

**Overnight cost**:
The upfront investment per unit of capacity, as if built overnight.
_Avoid_: CAPEX (ambiguous between this and Capital cost), investment cost

**Capital cost**:
PyPSA's periodic cost per unit of capacity, charged once per modelled horizon
(a year when the time steps span one year), derived from the Overnight cost
parts, their lifetimes and the discount rate. When an asset carries an Overnight
cost, PyPSA uses it and ignores any Capital cost. Always labelled with its
period.
_Avoid_: CC (unlabelled), annual CAPEX

## Process language

Grid-to-process plans a client's electrical system and its production as one
problem; the objective is the client's margin, not the cheapest bill for a fixed
load. Until the tickets that build each term land, its citation is the design
(`.scratch/grid-to-process/design.md`); the ticket that builds the code replaces
it with file and line.

**Process unit**:
One production step that turns inputs into a product at a rate in t/h: a
Haber-Bosch loop, a crusher, a grinding mill. It is a Link with `eh_process`
set: bus0 is the main input, the other inputs sit on `bus2..` and the product on
bus1. Its electricity follows from throughput × specific energy, and it may be
committable (on/off with a minimum load). Its size is a fixed client input,
never a decision of the plan (design §1.2, §2.3).
_Avoid_: load (a Load is fixed demand; a process unit decides its rate), machine,
plant (a plant is several process units)

**Product**:
A material a process unit makes or uses, carried on its own Bus: NH3, ore,
concentrate. Energy products (H2, NH3) are counted in MWh (LHV); material
products in tonnes. The Bus's `unit` says which (design §1.2, §3.A).
_Avoid_: commodity (traded energy in the commercial code), carrier (the PyPSA
attribute a product Bus has, not the product)

**Inventory**:
A Store of one Product between two process units, or between a process unit and
delivery: an H2 tank, an NH3 tank, an ore stockpile, a concentrate shed. Its
level is in the Product's unit (design §1.2, §2.4).
_Avoid_: buffer, storage (that is the electrical StorageUnit)

**Production target**:
What a period must deliver of a Product, as a delivery Load on its Bus. A
shortfall is supplied by a priced slack, so the target is not a hard constraint
(design §1.2, §2.5).
_Avoid_: quota, demand (unqualified, that is the electrical demand)

**Lost-production value**:
The cost of one unit of a Product not delivered: the client's margin per tonne,
or a contract's shortfall penalty. It is not a purchase; where the Product can be
bought in, the buy-in has its own price and the lost-production value must exceed
it. It is entered per Product with its source; when missing it is Unavailable
([ADR-0001](docs/adr/0001-unresolvable-figures-ship-as-null.md)), never 0
(design §1.2, §2.1).
_Avoid_: VoLL (per MWh of electricity; it stays the price of unserved electrical
load), penalty (unqualified)

**Criticality tier**:
How a process unit's electrical supply is ranked when it must be cut: `safety`
(priced at VoLL), `process_critical` (a stop costs a restart),
`process_flexible` (it can stop and catch up from Inventory) or `deferrable` (it
shifts within the day). It extends today's two ranks, the `eh_critical` Bus and
demand-side response as a priced resource (design §1.2).
_Avoid_: priority, criticality (unqualified), critical load (that is the
`eh_critical` Bus only)

**Restart time**:
The hours a process unit stays down after a stop before it produces again. Its
cost is the lost tonnes, priced by the Lost-production value, not a start-up
cost (design §1.2, §2.3).
_Avoid_: start-up cost (PyPSA's own attribute, unweighted by the time-step
weighting)
