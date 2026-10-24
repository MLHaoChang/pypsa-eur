# E1 follow-up. The template ledger in the reports' assumptions appendix

Status: ready-for-human — appendix draft implemented after GK plan OK; blocked on brief amendment for report_jobs.py metadata-only admission guard (Outsourced task L)
Type: task
Blocked by: E1a merged (`claude/e1a-planning-years-staged-dc`)
Gated: yes, gatekeeper (a new report seam). Send the plan first.
Model tier: sonnet
Size: M

Read [workspace reconciliation](../workspace-reconciliation.md) §3.3 (free planning years, E1) and the E1a PR first.

## Goal

A project's template ledger (`eh_template.json` `ledger`) is read into the assumptions appendix of the adequacy (EH study), grid-readiness and decision-study reports. Each row shows its label, value, unit, source and note. The growth placeholders of an example built for entered planning years are listed by name: `growth_inputs` rows with `set: false`, plus their `placeholder <kind> <year>` ledger rows.

## Why

Gatekeeper condition 3 on E1 (2026-10-21) asks that placeholder growth never be silent. E1a covers the places where a user acts on a placeholder:
- the flow's assumed-row warnings;
- `unknown` in the peak check and the scorecard;
- the investment case's `growth_placeholder:<kind>_<year>` flags and notes.

No report reads the template ledger today. The study report's assumptions section is the decision-study `AssumptionsLedger`, a different store. So the report appendix was split out of E1a with the gatekeeper's OK.

## Acceptance

- The adequacy, grid-readiness and study reports each carry an assumptions appendix listing the project's template ledger rows (assumed, synthetic, sourced, entered), or say "no template ledger" for a project without one.
- The staged DC created with entered years and no `dc_load` lists one appendix row per placeholder, named by its label (for example "IT load 2038 (placeholder)").
- A project created without `planning` shows the same appendix as today plus the ledger rows. No placeholder rows appear.
- The docx render round-trips the appendix (`services/reports/roundtrip.py`).

## Comments
