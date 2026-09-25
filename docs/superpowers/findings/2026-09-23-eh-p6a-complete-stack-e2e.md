# EH P6(a) + stack seal — local e2e

**Date:** 2026-09-23  
**Branch:** `cursor/eh-p6-multi-energy-ef60`  
**PR:** #49 → `master`

## Scope

P6(a) dedicated-bus multi-energy ENS on top of the merged complete stack (#48).
Also clears three **pre-existing** GUI-backend CI failures on `master` (unrelated
to P6 code paths) so the PR can merge green:

1. `dtc.py` — use `VOLL_SLACK_PREFIX` / `involuntary_slack_mask` (no inline `__voll_` f-string)
2. `test_golden_coverage` — declare `get_eh_{reference_design,redundancy,levers,dtc,dtc_planning}`
3. `scr_gate.py` — guard `gridspine` import (frozen app has no pip pin)

## Local verification

### Backend
```
cd pypsa-gui/backend
PYTHONPATH=<repo-root>:<backend> python -m pytest \
  tests/test_energy_hub_*.py tests/test_adequacy_sweep.py -q
```
Plus the three previously-red CI pins above.

### Frontend
```
cd pypsa-gui/frontend
npm test -- --run \
  src/pages/results/EhReferenceDesignPanel.test.tsx \
  src/pages/results/adequacy.test.tsx \
  src/pages/results/McPanel.test.tsx \
  src/pages/results/AdequacyTab.test.tsx
```

## Still deferred

- P6(b) multi-slack / shared-bus attribution
- Climate P8(b), spare-lead, planned-outage MC, Class-C authoring UI
  (chat tools: landed on `cursor/eh-chat-tools-ef60`)

## Independent e2e QA

- Verdict: **GO** (no binding conditions)
- Backend: **161** passed (`test_energy_hub_*.py` + `test_adequacy_sweep.py`)
- Frontend: **107** passed (4 EH/adequacy panel files)
- CI hygiene pins: slack / golden EH routes / guarded gridspine — green
