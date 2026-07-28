# Accessible dialog primitive + DOM test capability — design

**Date:** 2026-07-28
**Branch:** `feature/modal-a11y-primitive`, worktree `pypsa-eur-modal-a11y-primitive`
**Recon:** `.superpowers/pipeline/modal-a11y-primitive/recon.md` (panel-reviewed, 2/3 cleared)

## Goal

Give `pypsa-gui`'s frontend one accessible dialog primitive, the DOM-testing
capability needed to prove it works, and migrate the existing dialog call sites
onto it — without touching any file the concurrent workstream-H session is
editing.

## Why now

The desktop-app roadmap is workstreams A–L (A–G landed, H in flight, I–L
planned). None of them covers frontend accessibility or code health. This is
that gap, and it is parallel-safe by construction.

The behaviour matters more in a desktop shell than in a browser. Fourteen
dialog instances exist across eleven files; exactly one carries `role="dialog"`
and `aria-modal`, and **nothing anywhere in the frontend traps focus**. Tab
walks out of an open dialog into the page behind it. In a browser the user can
orient by the address bar or tab strip; in the chromeless pywebview window
workstream H is building, there is nothing to orient against.

## Constraints

**From the concurrent session.** These files are being edited on
`feature/local-app-impl` right now and are out of scope entirely:
`backend/desktop/*`, `backend/main.py`, `backend/services/shutdown.py`,
`main.py`, `pixi.toml`, `frontend/src/utils/download.ts`, and the download call
sites — `utils/projectActions.ts`, `layout/Sidebar.tsx`,
`pages/TimeSeriesManager.tsx`, `pages/results/shared.tsx`,
`pages/LoadProfileManager.tsx`, `pages/ImportExport.tsx`,
`pages/ModelHorizon.tsx`, `pages/OverviewPanel.tsx`, `components/ChatPanel.tsx`.

`Sidebar.tsx` (3 dialog instances) and `ChatPanel.tsx` (1) are therefore barred
despite having real dialogs. Ten actionable instances remain across nine files.

**From `CLAUDE.md`.**

- Destructive actions use immediate-action-plus-undo-toast, not confirm
  dialogs. `utils/toasts.tsx`'s `confirmToast` is the existing expression of
  this, used in 7 files.
- Node and npm come from the pixi environment. Never hardcode an interpreter
  path; use `pixi run`. `node_modules` carries per-platform native binaries and
  is never copied between machines.
- The repo is developed on both Windows and macOS arm64. Anything added must
  work on both.
- More than one agent session works in the main worktree; commit path-limited
  (`git commit <path>`), never `git add -A`.
- Existing z-index landscape: slide panels `z-[300]` with backdrop `z-[299]`;
  Leaflet controls occupy `z-800`, so floating UI over the map needs `z-[900]`.

## Decisions

Settled with the human:

| Decision | Choice | Why |
|---|---|---|
| The two destructive confirms (`SnapshotsPanel` restore, `TopologyCanvas` reset) | Migrate as dialogs; record the house-rule tension | Keeps the change purely additive — accessibility only, no behaviour change. Converting them to undo-toasts is a separate decision about whether those actions are genuinely undoable. |
| Portal vs in-place | In-place, single canonical z-index | Matches all 14 existing sites; introduces no new rendering pattern. Accepted risk: an ancestor `overflow`/`transform` can still clip a dialog. |
| `ShortcutsHelp` | Migrate last, as its own commit | It is the only site whose migration reaches outside its own file — its Escape is special-cased in `App.tsx`'s shared global keydown effect. Isolating it confines the cross-file risk to one revertable commit. |

Decided here, from recon's findings:

| Decision | Choice | Why |
|---|---|---|
| File location | New `components/Dialog.tsx`, not inside `PageKit.tsx` | Every one of `PageKit.tsx`'s 12 exports is a pure synchronous render function with no `useEffect`, refs, or portals. A dialog needs all three. It shares PageKit's visual language by convention, not by co-location. |
| Name | `Dialog` | Matches the ARIA role it implements. `Modal` describes a behaviour (blocking) rather than the thing. |
| Prop shape | Controlled `{open, onClose}`, `{...props}` spread | All 14 existing call sites are already `{open, onClose}`-shaped, and no `PageKit` component owns its own open/closed state. The spread follows `Btn`, the one existing component that passes native attributes through — needed here for `aria-labelledby`. |
| Backdrop click closes | Default on, opt-out via prop | Nine of the ten actionable instances already dismiss on backdrop click. `TopologyCanvas`'s "Reset diagram?" deliberately does not, which is plausibly intentional for a destructive action, and the opt-out preserves it. |
| Canonical z-index | `z-[9999]`, caller-overridable | The largest band already in use, held by four existing dialogs. Clears the slide-panel band (`300`) and Leaflet (`800`). |
| Scroll locking | Out of scope | No precedent anywhere in the frontend, and it is new behaviour rather than an accessibility fix. Recorded as a follow-up. |

## What the primitive does

`Dialog` owns exactly the behaviours that are missing today and nothing else:

- `role="dialog"`, `aria-modal="true"`, and an accessible name — supplied
  either by a `title` prop the primitive renders and wires to `aria-labelledby`,
  or by a caller-supplied `aria-label`/`aria-labelledby` arriving through the
  `{...props}` spread. A `Dialog` with neither is a defect, since a dialog with
  no accessible name is announced as just "dialog".
- **Focus trap** — Tab and Shift+Tab cycle within the dialog's focusable
  descendants.
- **Initial focus** — moves into the dialog on open.
- **Focus restoration** — returns focus to the element that had it before the
  dialog opened, on close.
- **Self-contained Escape** — the dialog closes itself, rather than depending on
  a global handler.

It does not own data fetching. Several existing sites read stores and fire
queries from inside the dialog component (`SnapshotsPanel` reads `useUIStore`
and fires two `useQuery` calls from within the dialog itself). Those keep that
responsibility in a wrapper around `Dialog`, not inside it.

## Success criteria

Each is observable and independently checkable.

1. `npx vitest run` executes at least one `*.test.tsx` file — the suite can test
   a React component, which it cannot today.
2. Opening a `Dialog` moves focus into it, and Tab from its last focusable
   element returns to its first rather than reaching the page behind.
3. Closing a `Dialog` returns focus to the element focused before it opened.
4. Escape closes a `Dialog` with no global key handler involved.
5. Every migrated call site renders `role="dialog"` and `aria-modal="true"`.
6. Backdrop click closes a `Dialog` by default and does not close
   `TopologyCanvas`'s reset confirm.
7. `npx tsc --noEmit -p tsconfig.json` reports no new errors.
8. The full existing suite still passes, and no pre-existing test file is
   removed or skipped. Baseline to compare against is recorded by running the
   suite before Task 1 changes `vite.config.ts`, not quoted from this document.
9. No file listed under Constraints as barred appears in the branch diff.

## Out of scope

- Scroll locking (no precedent; new behaviour).
- Converting the two destructive confirms to undo-toasts (separate decision).
- The four barred dialog instances in `Sidebar.tsx` and `ChatPanel.tsx`.
- The 486 `title=` tooltips — whether native tooltips render in pywebview
  cannot be answered until workstream H's shell exists.
- Decomposing the oversized components (`TopologyCanvas` 3,677 lines,
  `results/Dispatch` 3,612, `CompareView` 2,928).
- `confirmToast` — it stays as the answer for destructive actions. This
  primitive covers forms, wizards, informational panels, and the command
  palette.

## Known follow-ups

Carried from the recon's parked and deferred findings, plus this design:

- `recon.md` states `PageKit.tsx` is 369 lines; it is 368.
- Scroll locking.
- Whether `SnapshotsPanel`'s restore and `TopologyCanvas`'s reset should become
  undo-toasts per the house rule.
- Whether the primitive should later portal, if an ancestor `overflow` or
  `transform` is found to clip a migrated dialog.

**Added at close-out (Task 8), from the shipped implementation:**

- Resolved dependency versions from Task 1: `jsdom@29.1.1`,
  `@testing-library/react@16.3.2`, `@testing-library/user-event@14.6.1`.
- `CommandPalette` kept no Escape handling of its own — `Dialog` owns it now.
  Its pre-migration handler only ever called `onClose()` (no query-clear, no
  close-only-if-empty branch), so deleting it in favour of `Dialog`'s own
  Escape was behaviour-preserving, not a scope reduction.
- Accumulated cosmetic drift, accepted rather than fixed: backdrop opacity is
  now standardised to `rgba(0,0,0,0.45)` everywhere (pre-migration sites were
  a mix of `bg-black/30` and `bg-black/40`), and `TopologyCanvas`'s reset
  confirm additionally lost a `backdrop-blur-sm` that had no equivalent in
  `Dialog`'s backdrop.
- Escape now dismisses several dialogs that previously had no Escape route at
  all. Each of the ten migrated sites was checked individually to confirm
  `onClose` maps to that site's Cancel semantics and never to a destructive
  action (e.g. `TopologyCanvas`'s reset confirm: Escape reaches the Cancel
  path, never `handleResetDiagram`).
- `Dialog` ships with no `dismissOnEscape` opt-out. Ruled YAGNI: across all
  ten migrated instances, none wanted non-default Escape behaviour, so the
  extra prop would be speculative surface area with no caller.
- `CommandPalette.test.tsx`'s header comment overclaims what its first test
  protects: it says the test would break if `Dialog`'s initial-focus effect
  were removed. In fact `Dialog` has two focus mechanisms (the initial-focus
  effect and the layout-effect-based restore capture) that mutually backstop
  each other for this call site's markup, and the test only fails when both
  are removed together, not either alone. This is a known-inaccurate comment,
  recorded here rather than corrected as part of this verification task.
- All nine actionable success criteria verified independently in
  `.superpowers/sdd/2026-07-28-modal-a11y-primitive/task-8-report.md`: 9/9
  pass. Current suite: 27 test files / 170 tests (baseline before Task 1 was
  23 files / 147 tests); no pre-existing test file was removed or skipped.
