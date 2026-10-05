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
body arrives through the `use_skill` tool. `grill` is the first skill.
_Avoid_: prompt template, macro, plugin

**Choice card**:
The card the panel renders when the assistant calls `ask_user`: a title, a
question, options with one marked recommended, and a free-text field. A pick
is sent as the next user message; the turn does not block on it. Distinct
from the Confirmation card, which gates a write or run and does block.
_Avoid_: prompt, dialog, confirmation (that is the other card)

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
