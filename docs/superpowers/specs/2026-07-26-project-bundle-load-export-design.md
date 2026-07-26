# Design: single-file project bundle (load + export)

**Status:** approved  
**Date:** 2026-07-26

## Goal

One file carries the full project (network + solve results + GUI sidecars). Load that file into a project by default.

## Format

**`.pypsaproj.zip`** (existing). Contents:

| Member | Role |
|--------|------|
| `network.nc` | Topology + LP dispatch / prices (`*_t`) |
| `results_state.pkl` | Side-results (LP/PF toggle, AC-PF, lost-load) |
| `solver_config.json` | Solver settings |
| `layout.json` | Schematic layout |
| `metadata.json` | Objective, `has_results`, condition, … |
| `user_ts.json` | User time series |
| `uploads/` | Chatbot uploads |

Not inventing XML — PyPSA has no native XML network format.

## UX

1. **Save / Export** — Bundle remains the default export. Copy states explicitly: network + **results** + config + layout.
2. **Open** — Browse accepts `.pypsaproj.zip` / `.zip`; import restores results and marks solve completed when present.
3. **New Project → From file** — Prefer `.pypsaproj.zip` (full project). Raw `.nc` is still accepted: import then wrap as a new project (network-only; no GUI side-results). Drop misleading `.h5` accept until supported.
4. Network-only formats (`.nc` / Excel / MATPOWER) stay under Import/Export for interoperability; clearly labeled network-only.

## Out of scope

- Custom XML schema
- Changing on-disk project directory layout
- Autosave downloading a zip (autosave stays server-only)
