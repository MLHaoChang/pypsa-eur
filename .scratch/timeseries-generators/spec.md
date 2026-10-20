# Generic time-series generators: spec

Status: needs-triage
Owner input: 2026-10-20

## Goal, in the owner's words

> "A module that can enable the customer to generate generic loads for different applications. Can be data center, can be industrial, can be residential… by different use cases that we can select, or the user should obviously be able to upload load profile… also time series to incorporate other generation time series… to model the maintenance, to model must runs… modules and front ends and back ends to enable a parametrization of these assets or these profiles within the GUI as part of the guided workflow. So very similar scope as the renewable generator currently, but then just include load and other time series that we can currently use in PyPSA."

## Owner decisions (2026-10-20)

- D1. Generalise the renewable profile generator (`services/profile_gen/`) into one framework. One engine, one preview/apply/QA path, one library and provenance model; families plug in through `register_source`.
- D2. The spec lands now. A fresh Opus session builds the backend after the profiles session hands off following RP-06, starting from that hand-off note. The UI waits for the approved project-workflow design (`.scratch/project-workflow/`).
- D4. Chat refinement (ticket 04) is in scope: the copilot proposes deterministic ops, never raw numbers, behind the old→new card (owner, 2026-10-20).
- D3. The workflow stays generic. Templates (data centre, energy hub, microgrid, H2, commodity/process) only pre-fill generator choices.

## Families

### 1. Load generator: archetype or upload → `issues/01`
- Archetypes:
  - data centre (wraps `DataCentreLoadSpec` and #421's envelope);
  - industrial (1/2/3-shift, continuous process, seasonal shutdowns);
  - commercial (office, retail, hospital);
  - residential (households, heating type, EV share);
  - EV charging;
  - heat demand from temperature.
  - The existing `study_library/load_profiles/` shapes become archetype presets.
- Parameters: annual MWh or peak, shape, seasonality, weekday/weekend, country holidays, temperature sensitivity, seed.
- Upload: CSV/xlsx with unit, timezone and resolution detection, resampling, gap filling, scaling to an annual total, and the `timeseries_qa` checks. An upload carries `provenance: measured`; a generated profile carries `synthetic/assumed`.

### 2. Generation availability → `issues/02`
- Planned maintenance: windows by date or rule (e.g. "N weeks in summer"), giving a reduced `p_max_pu`.
- Forced outages: stochastic from outage rate and repair time, with a seed. Kept consistent with the adequacy outage data, so the two never disagree.
- Must-run: a `p_min_pu` profile (heat-led CHP, seasonal minimum).
- Derating: temperature-dependent `p_max_pu`.

### 3. Other model series → `issues/03`
- Price series (reads package C2's market bundles when they exist; user series until then).
- Hydro inflow.
- Heat-pump COP from temperature; efficiency curves.
- Process schedules for grid-to-process (production targets, shutdowns).

### 4. Chat refinement of any series → `issues/04`
- Preview by exemplary week or date range. Refinement through a closed set of deterministic ops (scale peak, scale a time window, floor/cap, shift, smooth, rescale energy).
- The copilot turns a sentence into ops and shows the old→new card (LC-04) before applying. The same ops are available as controls without a key.
- The op list is kept in provenance, can be undone, and is replayed on regeneration.

## Shared contract
- Each series targets one component field (`p_set`, `p_max_pu`, `p_min_pu`, `marginal_cost`, `efficiency`, `inflow`) through the profile apply path, with the option C time-basis rule.
- Each series has a preview, provenance, save to library (scope as package C1) and copilot read/generate tools.
- Guided mode gets "Generate defaults for all", the same pattern as renewable profiles.
- UX placement follows the project workflow. A series is parametrised where its asset is edited (the load in Loads; maintenance and must-run in the asset's Availability section). The Time series section is the overview and QA of every series, not a second place to create them.

## Gating
Apply-path and adequacy consistency are gated: the engine gatekeeper reviews. UI PRs need the UX reviewer's OK once the workflow is approved.

## Builds on
`services/profile_gen/` (engine, presets, job, apply), `services/profile_shapes.py`, `services/user_timeseries.py`, `services/timeseries_qa.py`, `study_library/load_profiles/`, `models/flex_archetypes.py`, `services/adequacy/` outage data.
