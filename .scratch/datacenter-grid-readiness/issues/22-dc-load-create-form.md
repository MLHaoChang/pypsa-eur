# A dc_load create form in the New project wizard

Status: ready-for-agent
Type: task
Blocked by: 05
Size: M

## What to build

The New project wizard sends no `dc_load` body (`api/projects.ts`: only `eh_site` takes create params), so a data-centre project created in the UI has no `dc_load` spec. The Backup (03) and Power architecture (05) sections show only on a project that has one, which today comes from chat's `create_project_from_template` params or the API. Add a `dc_load` step to the wizard for the two data-centre templates. It needs the architecture select with its hints as visible text (UX 11, plan point 6), the backup mode and the PUE at design; refusals (`dc_load_invalid`) are shown beside their field.

## Done when

A data-centre project created in the wizard carries a `dc_load` spec with the chosen architecture, and the Backup and Power architecture sections show on it.

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Not buildable until answered.
- Question (UX (12)): UX (12): where does the `dc_load` step go in the New project wizard (own step after the template pick, or a section inside the template step), and does the architecture select sit with it? Builder needs the placement before the form can be built; then M, sonnet, GK A + UX (12). DC 05 (#1199) is merged.
- Blocked by: the answer.

### 2026-11-07: needs-info answered (coordinator)

UX (12): inside the template step, not its own wizard step. Follow the eh_site precedent (StartCard tile → GenericSiteForm): the eh_datacenter and eh_datacenter_staged tiles open a DC form under the tile with the dc_load fields (IT load, PUE, backup mode). The architecture select sits in that form; each option shows its hint as visible text under the select, not a tooltip. Prefill every field with the template defaults so Create works without edits. Use the Backup and Power architecture sections' own labels, with a parity test. Same in Guided and Expert. Shots: tile closed, form open, each architecture's hint, and a refusal opening with the field label. Reviewers: UX (12) + GK A.
