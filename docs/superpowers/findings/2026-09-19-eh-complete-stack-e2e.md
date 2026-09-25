# EH reference-design complete stack — local e2e

**Date:** 2026-09-19  
**Branch:** `cursor/eh-reference-design-complete-ef60`  
**PR:** #48 → `master`

## Merged sources

P2 #41, P3a docs #46, P7 BE #43, P7 FE #47, P8a #44, P9 #42, FE panel #40, SCR strip #45 — on plan base #39.

## Local verification

### Backend
```
cd pypsa-gui/backend
PYTHONPATH=<repo-root>:<backend> python -m pytest \
  tests/test_energy_hub_*.py tests/test_adequacy_sweep.py -q
```
Result: **exit 0** (gridspine requires repo root on `PYTHONPATH`).

### Frontend
```
cd pypsa-gui/frontend
npm test -- --run \
  src/pages/results/EhReferenceDesignPanel.test.tsx \
  src/pages/results/adequacy.test.tsx \
  src/pages/results/McPanel.test.tsx \
  src/pages/results/AdequacyTab.test.tsx
```
Result: **4 files, 102 passed**.

## Still open / blocked

- P6 multi-energy — BLOCKED (slack redesign)
- Deferred: climate P8(b), spare-lead, planned-outage MC
  (chat tools: landed on `cursor/eh-chat-tools-ef60`)

## Independent e2e QA

- Assessor: [bc-fd2cf311-65ca-513f-a391-9532c3fd9088](bc-fd2cf311-65ca-513f-a391-9532c3fd9088)
- Verdict: **GO** (no binding conditions)
- Re-run: backend **153** passed; frontend **102** passed
