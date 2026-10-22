# Bulk upload: an in-place receipt

Status: needs-triage
Type: task
Blocked by: ts-05 (#586)
Gated: no; UX reviewer for the UI
Model tier: sonnet

Follow-up noted in the reviews of #586.

## Gap

Time Series' whole-tab upload (one file, many components) is the bulk counterpart of the editors' "Measured file" tab (UX reviewer, #586), but its success and its refusals end in a toast only.

## What to build

An in-place receipt under the tab: the components matched (each with old → new and "Open its editor ›" where one exists), the columns not matched and why, and a refusal's reason with the template as the fix.

## Done when

Uploading a file with two matching columns and one unknown one shows all three in place, with no toast as the only record.
