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
"start …" phrase. These two had one-line `_t("x", "Start …` schemas in
`harness/catalogue.py`, which the scan's regex never matched, so they were never
checked. Formatted into their fragments by S3 (#2086, #2096), the scan finds
them since S4 widened it to every fragment. They are listed in `PHRASE_GAPS`
there, which a test holds equal to the start-only tools without a phrase:

- `campus_new_grid_code_draft`
- `campus_start_milp`

Give each one a phrase in `ChatPanel`'s `guidedToolPhrase`, then empty
`PHRASE_GAPS`. That is a UI change, so the UX reviewer sees it.

## Lock gating of start_campaign is checked by name, not by wrapping

Found by GK B (75) on #2106; on master too. `start_campaign` is lock-gated by
the `_lock_gated` wrapper (`services/chat_tools.py`, `_lock_gated` /
`_check_foreign_lock`). Two mutants survive at master and on #2106 across
`test_write_surface_lock_policy`, `test_chat_tools_lock_gate_parity`,
`test_adequacy_campaign` and `test_lc36_d13_scope_campaign`: skipping the wrapper
for it, and skipping its `_check_foreign_lock`. The policy test checks the
lock-gated name set, not that each named tool's dispatcher is wrapped and
calls the check. Add a test that a foreign lock refuses `start_campaign` (and
the other routeless lock-gated tools) through `DISPATCHERS`.
