# A null discount rate reads as 0.07 on load, but refuses a bundle import

Status: needs-triage
Type: bug
Found in: TCU 11 (#1628), GK B (63) ruling on a null bundle rate.
Owner area: app backend (GK B reviews).
Parent: `11-bundle-import-and-experiment-copy-cost-guard.md`.

## What

A `solver_config.json` with `"discount_rate": null` is handled two ways:

- **Bundle import** (`routers/projects.py` `_bundle_network_owning_its_costs`) refuses it as a 422
  (`import_unreadable`). GK B (63) ruled to keep that.
- **Project load** (`load_project`) keeps the null through `_solver_config_from_dict`, and
  `network_crud._global_discount_rate` then reads a missing rate as 0.07 without a word. Every re-derive on the
  open project (single edit, bulk edit, scale re-derive, network import) prices at that silent default.

The same file therefore opens as a project at 0.07 but is refused as a bundle.

## Fix

One shared "rate in force" helper, used by both `load_project` and `network_crud._global_discount_rate`, that
reads the stored rate under one rule (the annuity's rule, `periodized_costs.discount_rate_problem`, which the
bundle import already applies) and says what it does with a null: refuse it, or fall back to the default and
report that, as the bundle import reports `rederived_at_default_rate`.

## Acceptance

- A null rate is handled the same way on load and on bundle import (pinned by a test on each path).
- `_global_discount_rate` has no literal default of its own.
