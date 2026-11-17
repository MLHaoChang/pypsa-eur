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
