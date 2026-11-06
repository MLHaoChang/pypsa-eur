# A dc_load create form in the New project wizard

Status: needs-triage
Type: task
Blocked by: 05
Size: M

## What to build

The New project wizard sends no `dc_load` body (`api/projects.ts`: only `eh_site` takes create params), so a data-centre project created in the UI has no `dc_load` spec. The Backup (03) and Power architecture (05) sections show only on a project that has one, which today comes from chat's `create_project_from_template` params or the API. Add a `dc_load` step to the wizard for the two data-centre templates. It needs the architecture select with its hints as visible text (UX 11, plan point 6), the backup mode and the PUE at design; refusals (`dc_load_invalid`) are shown beside their field.

## Done when

A data-centre project created in the wizard carries a `dc_load` spec with the chosen architecture, and the Backup and Power architecture sections show on it.

## Comments
