<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 09 Network Excel export fails on an hourly year (30 s client timeout) and blocks the next request

Status: done
Blocked by: none
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), walk W2 (shot `s17-Excel`).
Model: sonnet. Review: GK B.

## Problem

On the IPP template (8,760 h), Export › Excel shows a generic "Export failed" toast at 30.2 s. Through the API the same export succeeds (200, 3.5 MB, 42.8 s). `frontend/src/api/client.ts:20` sets `timeout: 30000` for every call. `routers/io.py` `export_excel` runs synchronously, so the next export (CSV) waited more than 120 s behind it. The results workbook export takes 72 s with no progress shown.

## User-visible impact

Hourly projects (most templates) cannot be exported to Excel from the UI, and the failure has no reason.

## Acceptance criteria

- [ ] Large exports (Excel network, results workbook) run as a job with progress, or at least with a per-call timeout sized to the export. The UI shows "Preparing… <elapsed>", and the file downloads when ready.
- [ ] A slow export does not block other requests: it runs off the request thread, tested with a concurrent CSV export.
- [ ] A real failure shows the server's reason, not "Export failed".
- [ ] Exported cells keep the EXPORT-INJECT-1 escaping (the existing test still passes).
- [ ] End to end in the real app: IPP template › run › Export › Excel downloads the .xlsx, which opens with its sheets. A CSV export started meanwhile finishes in under 10 s.

## Likely files

`pypsa-gui/frontend/src/api/client.ts`, `pypsa-gui/frontend/src/api/io.ts`, `pypsa-gui/frontend/src/layout/Sidebar.tsx` (export toast), `pypsa-gui/backend/routers/io.py` (`export_excel`, `_export_excel_bytes`).

## Comments
