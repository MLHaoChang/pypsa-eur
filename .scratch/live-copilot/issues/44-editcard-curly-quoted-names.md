# LC-44 — Edit card names in curly quotes

Status: done (#1325)
Type: task
Raised by: UX (14) on #1295 (UX OK @e3d39420cb95c42432a5a59917cc1a66c8cf10cf), not blocking there

## What

`pypsa-gui/frontend/src/components/EditCard.tsx` `assetWords` (line 157 at e3d39420) wraps a
component's name in straight quotes: `Load 'it'`. Use curly quotes instead: `Load “it”`.

## Why out of #1295

`assetWords` is shared by every edit card and the words built from it (`editLinesOf`), so the
change touches every edit-card surface and their tests and shots, not only SC-07's task "Done:" line.

## Done when

- Every name `assetWords` quotes reads `“name”` in Guided and Expert.
- Tests and the copy guard (`src/uiCopyGuard.test.ts`) that pin the straight-quoted words are
  updated to the curly form, and nothing else in the card's wording changes.
