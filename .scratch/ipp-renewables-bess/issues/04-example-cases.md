# The example's cases (base + 8)

Status: needs-triage
Type: task
Blocked by: 03; creation needs #448 S5
Gated: told
Model tier: sonnet
Size: S

Read [the spec](../spec.md) §7 first.

## What to build

- `cases.json` beside the template: the 8 cases of spec §7. Each holds a name, a reason and 1–2
  declared changes `{path, value_type, old, new}`, validated with the experiment contract's path
  and value rules (`services/experiments._validate_change`).
- A test that every path resolves on the created project and every `old` equals the template's
  value.
- Until S5, section 8 lists them read-only (W7 / S5 own the rows). After S5, "Create the example's
  cases" calls S5's Duplicate-and-change for each.
