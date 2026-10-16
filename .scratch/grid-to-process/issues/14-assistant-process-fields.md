# Assistant: process-unit fields and the template parameters

Status: needs-triage
Type: task
Blocked by: 07
Gated: no (harness)
Model tier: sonnet
Size: S

Read `pypsa-gui/backend/harness/README.md` first; it is the contract.

## What to build

- The chat tools that create or edit Links accept the process-unit fields from ticket 02.
- `create_project_from_template` lists `eh_ammonia_staged` and `eh_concentrator_staged`.
- Regenerate the assistant inventory, as the harness README requires.

## Acceptance

- The inventory check passes.
- A chat smoke creates an HB-like unit.
