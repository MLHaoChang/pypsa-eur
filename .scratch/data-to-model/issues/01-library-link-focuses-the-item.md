# "Update it there ›" focuses the pinned item in the Commercial library

Status: ready-for-agent
Blocked by: #586 merging (it owns `store/uiStore.ts` and `layout/flowNavigation.ts`)

## What

In the PDF values dialog (data-to-model slice 2, PR-C #599), a tariff or PPA contract pinned to
the Commercial library is refused with `library_pinned`. Its gap carries
`library: {kind, id, version}`, and the dialog's "Update it there ›" link closes the dialog and opens
the `library` panel. In Expert it uses `openFlowTarget`; in Guided it uses
`openFlowTargetInPlace`, which opens the field drawer. The page opens unfocused, and the row only
names the item and its version.

## To do

- Add a one-shot library focus `{kind, id, version}` to the UI store. It should follow the existing
  one-shots, such as `timeSeriesFocus` and `economicsTabRequest`.
- Set the focus from `LibraryLink` in `components/DocumentValuesPicker.tsx`.
- In `pages/results/investment/LibraryBrowser.tsx`, consume the focus: open that item at that
  version and scroll it into view.
- Tests: a vitest that clicking the link sets the focus, and a LibraryBrowser test that it opens the
  item. Cover both modes (UX reviewer condition, 2026-10-22).
