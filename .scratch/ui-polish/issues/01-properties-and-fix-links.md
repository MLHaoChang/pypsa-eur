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
5. A card's Basis line says "the binding answer" twice. Cause: the dips and restart card (`CampusDipsRestartSection`) prints `Basis: {dip_label}; {restart_label}`, and gridspine's `ride_through.LABEL` and `restart_sequence.LABEL` both say "the binding answer is …". Fix pending a ruling (gridspine text or the section's wording).

## Acceptance

- Unit tests for 1, 2 and 3; before/after shots in Guided and Expert.

## Shots

`.scratch/ui-polish/shots/uip01-{guided,expert}-{1-before-read-only-and-year,2-after-fix-link-field,3-after-refused-save-one-toast}.png`, from `pypsa-gui/frontend/scripts/ui-polish-shots.mjs` (the real PropertiesPanel, FieldDrawer and ToasterHost; API routes mocked). "Before" is the read-only card the link used to open; the harness caught that the interceptor toast lingered until removed, so `saveFailedToast` now uses `toast.remove`.
