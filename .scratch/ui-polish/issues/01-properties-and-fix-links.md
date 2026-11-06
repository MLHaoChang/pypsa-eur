# Properties and fix-link polish (UIP-01)

Status: in progress
Type: task
Blocked by: none
Gated: no

Frontend only. Defects the UX reviewers logged while reviewing other PRs.

## What to build

1. A fix link that targets a Link field (a FlowTarget with a field) opens the editable form scrolled to that field, focused and highlighted, in Guided and Expert. It used to open the read-only Properties panel at the top.
2. Properties never groups a year ("2027", not "2,027") and drops the doubled unit word ("Build year 2027", not "2,027 year").
3. A refused Properties save shows one toast, top-centre, so it never covers Save (in Guided the bottom-right toast covered it).
4. "No value" is one character everywhere: "—" (U+2014), the existing majority (290 uses against 13 "–"); `review-ux.md` names none. Status glyphs and numeric ranges keep "–".
5. A card's Basis line says "the binding answer" twice. Cause: the backend `_VS_SCREENING` sentence in `grid_readiness.py` repeats gridspine's `SCREENING_BASIS`. Needs a backend change, raised with the coordinator.

## Acceptance

- Unit tests for 1, 2 and 3; before/after shots in Guided and Expert.
