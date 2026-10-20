# Site commercial UX: spec

Status: needs-triage
Owner input: 2026-10-20 (screenshots of a reference commercial site-planning tool, reviewed with the coordinator)

## Goal, in the owner's words

> "They are optimizing like tariffs. They have a strong database about tariffs, about asset configurations, technical constraints, prices… the capability to configure different owners of the site and the financing structure from different owners' perspective… generic templates, but the templates are nicely formatted within the user interface… especially the energy flow diagram."

Bring that commercial clarity, the library and the results presentation into pypsa-gui, on top of the network physics we already have.

## Owner decisions (2026-10-20)

- D1. Three packages in order: **A** results, then **B** design flow, then **C** commercial and library.
- D2. **Start A now**, as one Sonnet session. B and C are queued.
- D3. The connection ramp-rate limit and loss factors go into data-centre grid readiness (tickets 01/02), not here.
- D4. Draft/publish, per-scenario edits and tracking (actuals) are out of scope for now.

## Packages

### A. Results (Sonnet, not gated): ready
- A1. Energy-flow Sankey (Source → Via → Destination, MWh) plus a CSV energy-flows export. → `issues/01`
- A2. Load Shape Analysis tab. → `issues/02`

### B. Design flow (Sonnet + UX check): queued after LC-04 leaves the shell/dock area
- B1. Scenario rows showing the Physical / Commercial / Optimisation model.
- B2. Tiered asset picker: Standard (Load, Solar, Submeter) and Advanced (Battery, EV on-demand / fleet, Flexible load, Genset, Wind, External asset, Other project costs).
- B3. One asset edit-form pattern: General → Sizing (Manual / Auto-sized, min/max, i.e. `p_nom_extendable`, `p_nom_min`, `p_nom_max`) → collapsible Rates, Market, Advanced and Availability sections. Multi-value chips create variants through the existing sweep machinery.
- B4. A seven-step new-project wizard: Details → Location (map pin → lat/lon, country, timezone) → Template → Load → Connection → Tariffs → Review.

### C. Commercial and library (Opus, gated: finance): queued after DC readiness 03/06 and the gatekeeper's current queue
- C1. Library page: category cards plus a searchable table (name, scope, country, created). Library items gain a `scope` (common / workspace).
- C2. Market bundles per country (wholesale, ancillary, capacity, balancing), NO1–NO5 first. Data sourcing and licensing go to the owner when C opens.
- C3. Contract types: financial PPA (CfD), physical PPA (CfD with floor, cap and negative-price rule), swap, demand response.
- C4. Report variant selection by rule (top N by NPV, greatest IRR), per participant.
- C5. Later: the visual commercial-model board, and cross-scenario participant/stream columns with a "relative to baseline" view.

## What exists to build on

| Piece | Where |
|---|---|
| Money Sankey (participants ledger) | `pypsa-gui/frontend/src/pages/results/investment/ValueFlowsView.tsx`, `pypsa-gui/backend/services/results/value_flows.py` |
| Storage heatmap | `pypsa-gui/frontend/src/pages/results/StorageHeatmap.tsx` |
| Dispatch results, carrier filter | `pypsa-gui/frontend/src/pages/results/Dispatch.tsx`, `CarrierFilter.tsx` |
| Compare service | `pypsa-gui/backend/services/compare/` |
| Tariff engine, contracts, PPA kinds, library items | `pypsa-gui/backend/services/commercial/`, `models/commercial.py`, `services/library/` |
| Geocoding | `pypsa-gui/backend/services/geocode.py` |
