Status: needs-triage

# Tool fragments: the tools left in the monolith, and two missing chat phrases

Follow-up to `02-tool-registry-fragments.md`.

## Tools that could not be spliced as they stand

A fragment is spliced back at one place in each of TOOLS, TOOL_ROUTES and
DISPATCHERS, and the move must leave `smoke/tool_registry_digest.py` equal to
master. These tools are not contiguous with their family in at least one table,
so moving them needs a plan for the order first (GK B):

- `get_adequacy_results`, `get_fmea_worksheet`, `get_stress_scenarios`,
  `put_stress_scenarios`, `get_eh_template`: contiguous in TOOLS and TOOL_ROUTES,
  but in DISPATCHERS they are interleaved with `get_asset_health`,
  `record_asset_health`, `get_project_flow` and `get_reliability_goal` (S4, #2106).

## Start-only tools without a "start …" chat phrase

`ChatPanel.sendRequest.test.tsx` (P29 gate B1-1) scans the schemas for
descriptions that begin "Start …" and requires each tool to have its own
"start …" phrase. The scan read `harness/catalogue.py` alone, so the tools S3
moved into fragments (#2086, #2096) went unchecked. S4 widened the scan to every
fragment. Two tools fail it and are listed in `PHRASE_GAPS` there:

- `campus_new_grid_code_draft`
- `campus_start_milp`

Give each one a phrase in `ChatPanel`'s `guidedToolPhrase`, then empty
`PHRASE_GAPS`. That is a UI change, so the UX reviewer sees it.
