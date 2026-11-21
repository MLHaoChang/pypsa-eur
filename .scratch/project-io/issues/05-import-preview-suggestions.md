<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 05 Assistant suggestions in the import preview (AI-IMPORT-1)

Status: needs-triage
Blocked by: 04
Owner decisions: AI-IMPORT-1, IMPORT-FLOW-1, RAW-NAMES-1
Source: gap review 2026-11-21; `project-io/spec.md` item 5, never filed.
Model: opus. Review: GK B + UX.

## Problem

`ImportResult.report.suggestions` (`gridspine/ingest/import_result.py`, class `Suggestion`) carries importer-side suggestions, for example a default for a missing thermal rating. The spec promises that the assistant proposes fixes in the preview. No route, tool or screen reads them, and with no API key the assistant is off.

## User-visible impact

After 04 a user sees what was not carried or was assumed, but gets no help closing those gaps before creating the project.

## Question for the owner

How far should 05 go?
1. **(Recommended) Importer suggestions only, no model.** The preview lists each `Suggestion` with Accept/Skip. An accepted one is applied in the same all-or-nothing import and recorded in `import_report.json`. This works with no API key and is testable without a model.
2. Importer suggestions plus "Ask the assistant" on the preview, when a key is set. It costs model calls per import and needs an evidence contract (assistant-assurance).
3. Defer: 04 ships the lists read-only, and 05 waits until imports have real users.

## Acceptance criteria (for option 1)

- [ ] The preview shows each suggestion in plain words with what it changes. Accept/Skip state goes with the import request and is re-checked at import.
- [ ] An accepted suggestion moves its row from "Assumed"/"Not carried" to "Applied (suggested)" in the stored import report. A skipped one stays where it was.
- [ ] End to end in the real app: import a fixture that yields at least one suggestion, accept it, create the project, and see the value in Expert and the report entry.

## Likely files

`pypsa-gui/backend/services/model_import_service.py`, `pypsa-gui/backend/routers/model_import.py` (both from 04), the preview component from 04 B.

## Comments
