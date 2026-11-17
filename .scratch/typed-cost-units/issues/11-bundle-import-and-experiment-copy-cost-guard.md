# Owned-cost guard: project-bundle import and the experiment preflight copy

Status: ready-for-agent
Type: bug
Found in: TCU 10 (#1555), follow-up candidates in `10-owned-cost-guard-gaps.md`.
Owner area: app backend (GK B reviews).
Parent: `10-owned-cost-guard-gaps.md`.

## What

TCU 10 made every network upload route check a priced row's investment parts and re-derive the costs they own.
Two writers were left:

1. **Project-bundle import** (`routers/projects.py` `import_bundle`) loads the bundle's `network.nc` as it is. A
   priced row with a stale `capital_cost`, or a part that cannot be derived (no lifetime, an infinite value),
   is opened unchecked. A bundle is an upload like the network imports, even though it restores a project.
2. **Experiment preflight copy** (`services/assistant_tools.py` `_candidate`) writes the case's value raw on a
   copy. A case that sets `capital_cost` on a priced row is re-derived away at staging, so it solves as the
   baseline while reported changed (the sweep already refuses this, TCU 09). A case that changes a priced
   scale (`max_hours`) leaves the copy's cost stale.

## Acceptance

- A bundle with a stale owned cost imports with the cost re-derived at the **bundle's own** discount rate (its
  `solver_config.json`, not the project open before), lists the re-derived rows in the response and the change
  log, and the imported project reads as unsaved (its file still holds the stale cost).
- A bundle with a priced part that cannot be derived is refused (422 `invalid_edit_value`, naming the row)
  before the project row or any file exists; the open network is unchanged.
- An experiment case setting a cost a priced row's parts own is refused (`derived_field`, the single edit's
  words, naming the row); an unpriced row's cost is still written.
- An experiment case changing a priced scale re-derives the copy's cost; the open network is untouched.

## Comments

- 2026-11-17, GK B (62) plan verdict: GO with conditions.
  - A1: the bundle rate is read through `_solver_config_from_dict`; a present but unreadable `solver_config.json`
    is a 422 (`import_unreadable`), only an absent one uses the default, and the response says so
    (`rederived_at_default_rate`).
  - A2: one load path. A clean bundle loads frame for frame as reset-and-load did; with nothing re-derived the
    response is unchanged and nothing is marked unsaved. Only rows whose owned cost moved are listed (a bundle
    saved with current costs lists none).
  - A3: on a 422, no project row or folder is left; covered for a bad row in a second class (a Line).
  - B1: one shared rule, `workflow_tools.cost_the_parts_own_problem`, for the sweep and the preflight copy, each
    with its own literal kind.
  - B2: the preflight allowlist reaches no discount rate and no part, so nothing can move the rate in the copy
    (pinned by a test).
  - B3: the refused set is `derive.owned_columns`; of it the allowlist reaches only `capital_cost` (pinned).
  - B4: the open network is read from the session's own context and pinned unchanged.
