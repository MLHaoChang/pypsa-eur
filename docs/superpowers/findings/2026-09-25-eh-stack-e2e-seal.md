# EH reference-design stack seal — local e2e (post chat tools)

**Date:** 2026-09-25  
**Base:** `master` @ `ec233027` (EH chat tools #51 squash-merged)  
**Branch:** `cursor/eh-stack-e2e-seal-ef60` (findings / plan update)

## Merged stack on master

| PR | Slice |
|---|---|
| #48 | Complete-stack merge (P2–P5, P7–P9, FE panel/SCR) |
| #49 | P6(a) dedicated-bus multi-energy |
| #50 | P6(b) per-Load VOLL slacks / shared-bus attribution |
| #51 | Chat tools: `run_eh_study` + `eh_study` / `eh_reference_design` kinds + abort |

## Local verification

### Backend
```
cd pypsa-gui/backend
PYTHONPATH=<repo-root>:<backend> python -m pytest \
  tests/test_energy_hub_*.py \
  tests/test_adequacy_sweep.py \
  tests/test_chat_adequacy_tools.py \
  tests/test_adequacy_campaign.py \
  -q
```
Result: **247 passed** (167 energy_hub+sweep + 80 chat/campaign).

Plus HTTP surface:
```
python -m pytest tests/test_energy_hub_study_http.py -q
```
Result: **10 passed**.

Combined EH stack surface exercised: **257** tests, exit 0.

### Frontend
```
cd pypsa-gui/frontend
npm test -- --run \
  src/pages/results/EhReferenceDesignPanel.test.tsx \
  src/pages/results/adequacy.test.tsx \
  src/pages/results/McPanel.test.tsx \
  src/pages/results/AdequacyTab.test.tsx
```
Result: **4 files, 108 passed**.

## Coverage map (what this seals)

- Archetypes / packs, study runner + HTTP, report assemble
- Redundancy / levers / DtC / SCR gate / Class-C profiles / RAM v1 wire
- P6(a) dedicated-bus + P6(b) per-Load slacks
- Chat: start / poll / abort EH study; campaign charge on `budget_solves`
- Adequacy sweep regression companion

## Still deferred (not acceptance)

- Climate P8(b) (data procurement)
- Spare-lead severity modifier
- Planned-outage MC
- Class-C authoring UI
