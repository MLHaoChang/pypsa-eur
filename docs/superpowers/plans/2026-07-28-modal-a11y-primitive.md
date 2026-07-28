# Accessible Dialog Primitive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the frontend one accessible `Dialog` primitive with focus trapping, the DOM-testing capability to prove it works, and migrate the ten actionable dialog instances onto it.

**Architecture:** A single new `components/Dialog.tsx` owning ARIA attributes, focus trap, initial focus, focus restoration, and self-contained Escape. It renders in place — no portal — matching every existing call site. Migration is mechanical: replace each hand-rolled backdrop-plus-panel with `<Dialog>`, preserving the existing inner markup.

**Tech Stack:** React 19, TypeScript, Tailwind v4, Vitest 4, jsdom, `@testing-library/react`.

## Global Constraints

- **Work only in the worktree `/Users/orange/Desktop/Code Test/pypsa-eur-modal-a11y-primitive` on branch `feature/modal-a11y-primitive`.** A different agent session is working in `/Users/orange/Desktop/Code Test/pypsa-eur` on `feature/local-app-impl`. Never read or write there.
- **Before every commit, re-run `git branch --show-current`** and confirm it reads `feature/modal-a11y-primitive`. Per `CLAUDE.md`, the branch can change under you mid-task.
- **Commit path-limited** — `git add <exact paths>` then `git commit`, never `git add -A`.
- **These files are barred. They must not appear in the branch diff:** `backend/desktop/*`, `backend/main.py`, `backend/services/shutdown.py`, `main.py`, `pixi.toml`, `frontend/src/utils/download.ts`, `utils/projectActions.ts`, `layout/Sidebar.tsx`, `pages/TimeSeriesManager.tsx`, `pages/results/shared.tsx`, `pages/LoadProfileManager.tsx`, `pages/ImportExport.tsx`, `pages/ModelHorizon.tsx`, `pages/OverviewPanel.tsx`, `components/ChatPanel.tsx`.
- **Node and npm come from pixi.** Run them as `pixi run npm …` from the repo root, or prefix `PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH"`. Never hardcode an interpreter path.
- **Cross-platform.** The repo is developed on Windows and macOS arm64. Add no dependency with platform-specific native binaries.
- **Cite by symbol, not line number.** Per `CLAUDE.md` and the precedent of the concurrent session's plan v3, line numbers go stale between writing, review, and execution. Where this plan gives one it is marked *approximate*.
- **Canonical z-index is `z-[9999]`**, caller-overridable via the `z` prop.
- **Scroll locking is out of scope.** Do not add it.

All paths below are relative to `pypsa-gui/frontend/` unless stated otherwise.

---

### Task 1: DOM test capability

**Files:**
- Modify: `vite.config.ts` (the `test` block)
- Modify: `package.json` (devDependencies)
- Create: `src/components/Dialog.smoke.test.tsx`

**Interfaces:**
- Produces: a Vitest environment that can render React components, and the `*.test.tsx` include glob every later task's tests rely on.

- [ ] **Step 1: Record the baseline**

From the repo root:

```bash
cd pypsa-gui/frontend && pixi run npm test 2>&1 | tail -20
```

Write the reported file and test counts into the Task 1 commit message. Success criterion 8 compares against this, not against any number quoted in the spec.

- [ ] **Step 2: Install the dependencies**

```bash
cd pypsa-gui/frontend && pixi run npm install -D jsdom @testing-library/react @testing-library/user-event
```

All three are pure JavaScript — no native binaries, so the cross-platform constraint holds. `@testing-library/react` v16+ is required for React 19; `npm install` resolves this automatically since React 19 is already a dependency.

- [ ] **Step 3: Write the failing smoke test**

Create `src/components/Dialog.smoke.test.tsx`:

```tsx
// Proves the suite can render a React component and query the DOM.
// This file exists to verify the jsdom + @testing-library wiring itself,
// independently of Dialog's own behaviour — if it fails, the environment is
// wrong, not the component.
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'

function Hello() {
  return <p>hello from jsdom</p>
}

describe('DOM test environment', () => {
  it('renders a component and finds its text', () => {
    render(<Hello />)
    expect(screen.getByText('hello from jsdom')).toBeTruthy()
  })

  it('exposes a real document', () => {
    expect(typeof document).toBe('object')
    expect(document.createElement('div').tagName).toBe('DIV')
  })
})
```

- [ ] **Step 4: Run it and watch it fail**

```bash
cd pypsa-gui/frontend && pixi run npx vitest run src/components/Dialog.smoke.test.tsx
```

Expected: FAIL. The `include` glob does not match `*.test.tsx`, so Vitest reports no test files found; if the glob is reached, `environment: 'node'` means `document` is undefined.

- [ ] **Step 5: Update the vite config**

In `vite.config.ts`, replace the entire `test` block with:

```ts
  test: {
    // jsdom (not `node`): component tests render React and query the DOM.
    // The suite was `node`-only while it covered pure helpers exclusively;
    // the first component test (Dialog) is what changed that.
    environment: 'jsdom',
    globals: false,
    include: [
      'src/**/*.test.ts',
      'src/**/*.test.tsx',
      'vite.auth-gate.test.ts',
      'brand.theme.test.ts',
    ],
  },
```

`globals: false` keeps the existing house style — every current test file imports `describe`/`it`/`expect` from `'vitest'` explicitly, and that stays true.

- [ ] **Step 6: Run it and watch it pass**

```bash
cd pypsa-gui/frontend && pixi run npx vitest run src/components/Dialog.smoke.test.tsx
```

Expected: PASS, 2 tests.

- [ ] **Step 7: Confirm the pre-existing suite is unharmed**

```bash
cd pypsa-gui/frontend && pixi run npm test 2>&1 | tail -20
```

Expected: every file and test from Step 1's baseline still passes, plus the 2 new ones. If any previously-passing test now fails, the jsdom switch broke it — fix that before committing; do not proceed with a red suite.

- [ ] **Step 8: Commit**

```bash
git branch --show-current   # must read feature/modal-a11y-primitive
git add pypsa-gui/frontend/vite.config.ts pypsa-gui/frontend/package.json pypsa-gui/frontend/package-lock.json pypsa-gui/frontend/src/components/Dialog.smoke.test.tsx
git commit -m "test(gui): add jsdom + testing-library so components can be tested

Baseline before: <files> files / <tests> tests. After: +2."
```

---

### Task 2: The Dialog primitive

**Files:**
- Create: `src/components/Dialog.tsx`
- Create: `src/components/Dialog.test.tsx`

**Interfaces:**
- Consumes: the jsdom environment from Task 1.
- Produces: `export function Dialog(props: DialogProps)` — a **named** export (matching `PageKit.tsx`'s convention for primitives, not `ShortcutsHelp.tsx`'s default export). Props:
  - `open: boolean` — required
  - `onClose: () => void` — required
  - `children: ReactNode` — required, the panel's contents
  - `title?: string` — renders nothing itself; supplies the accessible name via a generated `aria-labelledby` target
  - `dismissOnBackdrop?: boolean` — default `true`
  - `z?: number` — default `9999`
  - `panelClassName?: string` — Tailwind classes for the inner panel
  - plus every native `div` attribute via `...props` spread (this is how a caller supplies `aria-label` instead of `title`)

- [ ] **Step 1: Write the failing tests**

Create `src/components/Dialog.test.tsx`:

```tsx
// Behavioural contract for the Dialog primitive. Every test here maps to a
// success criterion in
// docs/superpowers/specs/2026-07-28-modal-a11y-primitive-design.md.
// The focus-management tests are the point of the file: nothing in this
// frontend trapped focus before this component existed.
import { describe, it, expect, vi } from 'vitest'
import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Dialog } from './Dialog'

function TwoButtonDialog({ onClose = () => {}, ...rest }: { onClose?: () => void; [k: string]: unknown }) {
  return (
    <Dialog open onClose={onClose} title="Test dialog" {...rest}>
      <button>first</button>
      <button>last</button>
    </Dialog>
  )
}

describe('Dialog', () => {
  it('renders nothing when closed', () => {
    render(
      <Dialog open={false} onClose={() => {}} title="Test dialog">
        <button>first</button>
      </Dialog>,
    )
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('exposes role=dialog and aria-modal when open', () => {
    render(<TwoButtonDialog />)
    const dlg = screen.getByRole('dialog')
    expect(dlg.getAttribute('aria-modal')).toBe('true')
  })

  it('has an accessible name from the title prop', () => {
    render(<TwoButtonDialog />)
    expect(screen.getByRole('dialog', { name: 'Test dialog' })).toBeTruthy()
  })

  it('accepts a caller-supplied aria-label instead of title', () => {
    render(
      <Dialog open onClose={() => {}} aria-label="Named by caller">
        <button>first</button>
      </Dialog>,
    )
    expect(screen.getByRole('dialog', { name: 'Named by caller' })).toBeTruthy()
  })

  it('moves focus into the dialog on open', () => {
    render(<TwoButtonDialog />)
    expect(screen.getByRole('dialog').contains(document.activeElement)).toBe(true)
  })

  it('wraps Tab from the last focusable back to the first', async () => {
    const user = userEvent.setup()
    render(<TwoButtonDialog />)
    const first = screen.getByRole('button', { name: 'first' })
    const last = screen.getByRole('button', { name: 'last' })
    last.focus()
    await user.tab()
    expect(document.activeElement).toBe(first)
  })

  it('wraps Shift+Tab from the first focusable back to the last', async () => {
    const user = userEvent.setup()
    render(<TwoButtonDialog />)
    const first = screen.getByRole('button', { name: 'first' })
    const last = screen.getByRole('button', { name: 'last' })
    first.focus()
    await user.tab({ shift: true })
    expect(document.activeElement).toBe(last)
  })

  it('closes on Escape without any global key handler', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<TwoButtonDialog onClose={onClose} />)
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('closes on backdrop click by default', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<TwoButtonDialog onClose={onClose} />)
    await user.click(screen.getByRole('dialog'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('does not close on backdrop click when dismissOnBackdrop is false', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<TwoButtonDialog onClose={onClose} dismissOnBackdrop={false} />)
    await user.click(screen.getByRole('dialog'))
    expect(onClose).not.toHaveBeenCalled()
  })

  it('does not close when the click originates inside the panel', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<TwoButtonDialog onClose={onClose} />)
    await user.click(screen.getByRole('button', { name: 'first' }))
    expect(onClose).not.toHaveBeenCalled()
  })

  it('restores focus to the invoking element on close', async () => {
    const user = userEvent.setup()

    function Harness() {
      const [open, setOpen] = useState(false)
      return (
        <>
          <button onClick={() => setOpen(true)}>open me</button>
          <Dialog open={open} onClose={() => setOpen(false)} title="Test dialog">
            <button>inside</button>
          </Dialog>
        </>
      )
    }

    render(<Harness />)
    const trigger = screen.getByRole('button', { name: 'open me' })
    await user.click(trigger)
    expect(screen.getByRole('dialog').contains(document.activeElement)).toBe(true)
    await user.keyboard('{Escape}')
    expect(document.activeElement).toBe(trigger)
  })
})
```

- [ ] **Step 2: Run the tests and watch them fail**

```bash
cd pypsa-gui/frontend && pixi run npx vitest run src/components/Dialog.test.tsx
```

Expected: FAIL — `Failed to resolve import "./Dialog"`, because the component does not exist yet.

- [ ] **Step 3: Write the component**

Create `src/components/Dialog.tsx`:

```tsx
import { useEffect, useId, useRef, type HTMLAttributes, type ReactNode } from 'react'

// The app's only accessible dialog. Owns exactly the behaviours that were
// missing everywhere before it existed: ARIA role/modal state, a focus trap,
// initial focus, focus restoration, and its own Escape handling.
//
// Renders IN PLACE rather than through a portal, matching every pre-existing
// call site — see the design doc for that decision and its accepted risk
// (an ancestor overflow/transform can still clip the panel).
//
// It deliberately owns no data fetching. Call sites that load their own data
// (SnapshotsPanel does) keep that in a wrapper around Dialog, not inside it.

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

export interface DialogProps extends Omit<HTMLAttributes<HTMLDivElement>, 'title'> {
  open: boolean
  onClose: () => void
  children: ReactNode
  title?: string
  dismissOnBackdrop?: boolean
  z?: number
  panelClassName?: string
}

export function Dialog({
  open,
  onClose,
  children,
  title,
  dismissOnBackdrop = true,
  z = 9999,
  panelClassName = 'bg-bg rounded-xl shadow-2xl w-[420px] max-w-[95vw] overflow-hidden',
  className,
  ...props
}: DialogProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const restoreRef = useRef<HTMLElement | null>(null)
  const titleId = useId()

  // Initial focus on open + restoration on close. Capturing the previously
  // focused element must happen before we move focus away from it.
  useEffect(() => {
    if (!open) return
    restoreRef.current = document.activeElement as HTMLElement | null
    const panel = panelRef.current
    const first = panel?.querySelector<HTMLElement>(FOCUSABLE)
    ;(first ?? panel)?.focus()
    return () => {
      restoreRef.current?.focus?.()
    }
  }, [open])

  // Escape and the Tab cycle. Bound to the dialog subtree, not to window, so
  // the primitive does not depend on — or fight with — any global handler.
  useEffect(() => {
    if (!open) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
        return
      }
      if (e.key !== 'Tab') return
      const panel = panelRef.current
      if (!panel) return
      const items = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE))
      if (items.length === 0) {
        e.preventDefault()
        return
      }
      const first = items[0]
      const last = items[items.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown, true)
    return () => document.removeEventListener('keydown', onKeyDown, true)
  }, [open, onClose])

  if (!open) return null

  return (
    <div
      className={className ?? 'fixed inset-0 flex items-center justify-center'}
      style={{ background: 'rgba(0,0,0,0.45)', zIndex: z }}
      onClick={e => {
        if (dismissOnBackdrop && e.target === e.currentTarget) onClose()
      }}
      role="dialog"
      aria-modal="true"
      {...(title ? { 'aria-labelledby': titleId } : null)}
      {...props}
    >
      {title ? (
        <span id={titleId} className="sr-only">
          {title}
        </span>
      ) : null}
      <div ref={panelRef} tabIndex={-1} className={panelClassName}>
        {children}
      </div>
    </div>
  )
}
```

Two details that are load-bearing and easy to get wrong:

- The keydown listener uses **capture phase** (`true`), so Escape reaches the dialog before a bubbling ancestor handler consumes it, and `stopPropagation` then prevents a global handler from acting on the same key.
- The `title` span is inside the backdrop but outside the panel, so it does not become the panel's first focusable child. It is `sr-only`, so it is announced but invisible.

- [ ] **Step 4: Run the tests and watch them pass**

```bash
cd pypsa-gui/frontend && pixi run npx vitest run src/components/Dialog.test.tsx
```

Expected: PASS, 12 tests.

- [ ] **Step 5: Typecheck**

```bash
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc --noEmit -p tsconfig.json
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git branch --show-current   # must read feature/modal-a11y-primitive
git add pypsa-gui/frontend/src/components/Dialog.tsx pypsa-gui/frontend/src/components/Dialog.test.tsx
git commit -m "feat(gui): accessible Dialog primitive with focus trap and restoration"
```

---

### Tasks 3-7: migration

Every migration task follows the identical recipe below. The per-task sections
that follow give only what differs: which files, which dialog, and any
site-specific wrinkle.

**The recipe, for each dialog instance:**

1. Read the file. Find the backdrop element — a `div` whose className contains `fixed inset-0` and which wraps a panel `div`.
2. Replace the backdrop `div` and its immediate panel child with `<Dialog>`:
   - The panel's existing className becomes `panelClassName`.
   - The panel's existing children move inside `<Dialog>` unchanged.
   - Supply an accessible name: `title="…"` describing the dialog's purpose.
   - If the existing z-index was not `9999`, pass `z={<the existing value>}` so stacking behaviour is unchanged by this task.
3. Delete the hand-rolled backdrop `onClick` handler — `Dialog` owns dismissal now.
4. Delete any `onClick={e => e.stopPropagation()}` on the panel — `Dialog`'s `e.target === e.currentTarget` check makes it redundant.
5. If the component special-cased Escape itself, delete that handler — `Dialog` owns it.
6. Import with `import { Dialog } from '../components/Dialog'` (adjust the relative path per file).

**Verification, identical for every migration task:**

```bash
cd pypsa-gui/frontend
pixi run npm test
PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc --noEmit -p tsconfig.json
grep -n 'role="dialog"' src/<each migrated file>
```

Expected: suite green, no type errors, and `role="dialog"` no longer appears
hand-written in the migrated file (it comes from `Dialog` now) — the grep
should return nothing except in `Dialog.tsx` itself.

**Commit, identical shape for every migration task:**

```bash
git branch --show-current   # must read feature/modal-a11y-primitive
git add <exact paths touched>
git commit -m "refactor(gui): migrate <what> onto the Dialog primitive"
```

---

### Task 3: Migrate the four form dialogs

**Files:**
- Modify: `src/layout/AssignMembersDialog.tsx`
- Modify: `src/pages/ScenariosPanel.tsx`
- Modify: `src/pages/SnapshotsPanel.tsx` (**two** dialogs — the save-snapshot form and the restore confirm)

**Interfaces:**
- Consumes: `Dialog` from Task 2.

These four instances share one shape: the backdrop carries `onClick={onClose}`
and the panel carries `onClick={e => e.stopPropagation()}`. Both go away per
recipe steps 3 and 4.

- [ ] **Step 1: Apply the recipe to each of the four dialogs**

Existing z-index at all four sites is `z-[400]` (*approximate* — confirm by reading each file). Pass `z={400}` at each so this task changes accessibility only, never stacking.

`SnapshotsPanel.tsx`'s restore confirm is one of the two destructive confirms the design doc flags: the `CLAUDE.md` house rule says destructive actions should be immediate-plus-undo-toast rather than a confirm dialog. **Do not convert it here.** Migrate it as a dialog like the others; the conversion is a recorded follow-up, and mixing it into this task would turn an accessibility change into a behaviour change.

`SnapshotsPanel.tsx`'s dialogs read `useUIStore` and fire `useQuery` from inside the dialog component. That stays exactly where it is — `Dialog` wraps presentation only, so the surrounding component keeps its data fetching.

- [ ] **Step 2: Verify** — run the verification block from the shared recipe.

- [ ] **Step 3: Commit** — per the shared commit shape.

---

### Task 4: Migrate the three self-dismissing dialogs

**Files:**
- Modify: `src/layout/NewProjectWizard.tsx`
- Modify: `src/layout/ProjectTabs.tsx`
- Modify: `src/components/VintagePeriodBoundsModal.tsx`

**Interfaces:**
- Consumes: `Dialog` from Task 2.

These three share the other shape: the backdrop's own `onClick` inline-checks
`e.target === e.currentTarget`. That check is exactly what `Dialog` does
internally, so recipe step 3 deletes it with no behaviour change.

- [ ] **Step 1: Apply the recipe to each of the three dialogs**

Existing z-index at all three is `z-[9999]` (*approximate*), which is the primitive's default — omit the `z` prop.

`NewProjectWizard` is a multi-step wizard. Its steps render different focusable content, and `Dialog` queries focusable descendants at keydown time rather than caching them, so the trap follows the wizard between steps with no extra wiring. Confirm this by tabbing through a later step after migrating.

- [ ] **Step 2: Verify** — run the verification block from the shared recipe.

- [ ] **Step 3: Commit** — per the shared commit shape.

---

### Task 5: Migrate the command palette

**Files:**
- Modify: `src/components/CommandPalette.tsx`

**Interfaces:**
- Consumes: `Dialog` from Task 2.

A palette is its own task because its focus behaviour is not generic: it has a
search input that must hold focus while arrow keys move a highlighted result,
and it very likely binds its own Escape.

- [ ] **Step 1: Read the file and record its existing key handling**

Before changing anything, note in the commit message: which keys the palette handles, where it binds them (component-local or global), and whether it focuses its input on open. This is the input to Step 2's judgement.

- [ ] **Step 2: Apply the recipe, with these two exceptions**

- Existing z-index is `z-[500]` (*approximate*). Pass `z={500}`.
- **If the palette binds its own Escape**, delete that binding and let `Dialog` own it — one owner per key. **If the palette's Escape does something other than close** (for example clearing the query first, closing only when empty), keep the palette's handler and confirm the two do not double-fire: `Dialog` calls `stopPropagation` on Escape in the capture phase, so a bubble-phase handler in the palette will not run. In that case the palette's behaviour must move into its `onClose`, or `Dialog` must not be given `onClose={close}` directly.

- [ ] **Step 3: Verify the input still takes focus on open**

`Dialog` focuses the first focusable descendant, which for a palette is normally the search input — so this usually needs no extra work. Open the palette and confirm typing goes into the input without a click. If some other element wins, give the input `autoFocus`; `Dialog` runs its focus effect on mount, and React applies `autoFocus` on mount too, so the later of the two wins — verify rather than assume.

- [ ] **Step 4: Verify** — run the verification block from the shared recipe.

- [ ] **Step 5: Commit** — per the shared commit shape.

---

### Task 6: Migrate the reset confirm

**Files:**
- Modify: `src/pages/TopologyCanvas.tsx` (the "Reset diagram?" confirm only)

**Interfaces:**
- Consumes: `Dialog` from Task 2.

`TopologyCanvas.tsx` is 3,677 lines. **Touch only the reset-confirm dialog.**
Decomposing this file is explicitly out of scope and would collide with any
future canvas work.

- [ ] **Step 1: Apply the recipe, with one exception**

This is the only site with **no backdrop dismissal at all** — its backdrop has no `onClick`. That is plausibly deliberate: "Reset" discards unsaved layout work, and an accidental backdrop click should not trigger it. Preserve it exactly:

```tsx
<Dialog open={…} onClose={…} title="Reset diagram" dismissOnBackdrop={false}>
```

Existing z-index is `z-[9999]` (*approximate*) — the default, so omit `z`.

This is the second of the two destructive confirms. As in Task 3, migrate it as a dialog; do not convert it to an undo-toast.

- [ ] **Step 2: Verify the backdrop still does not dismiss**

Open the confirm and click the darkened area outside the panel. Expected: the dialog stays open. This is the behaviour `dismissOnBackdrop={false}` exists to preserve, and `Dialog.test.tsx` already covers it at the unit level.

- [ ] **Step 3: Verify** — run the verification block from the shared recipe.

- [ ] **Step 4: Commit** — per the shared commit shape.

---

### Task 7: Migrate ShortcutsHelp and untangle its Escape

**Files:**
- Modify: `src/components/ShortcutsHelp.tsx`
- Modify: `src/App.tsx` (the global keydown effect)

**Interfaces:**
- Consumes: `Dialog` from Task 2.

**This task is last because it is the only one that reaches outside its own
file.** `ShortcutsHelp` has no Escape handler of its own — it closes only
because `App.tsx`'s single global `window` keydown effect special-cases
`showShortcuts`. Give the dialog its own Escape without removing that
special-case and the key is handled twice.

`App.tsx` is shared surface. Keeping this in its own commit means the one
cross-file risk in the plan is independently revertable.

- [ ] **Step 1: Read `App.tsx`'s global keydown effect and record what it does**

Find the effect that handles `Escape`. It is *approximately* at `App.tsx:466-508`, with `showShortcuts` in its dependency array — locate it by reading, not by line number. Note every branch it has: `showShortcuts` is one of several, and the others must survive this task untouched.

- [ ] **Step 2: Migrate `ShortcutsHelp.tsx` onto `Dialog`**

Its current backdrop already carries `role="dialog"`, `aria-modal="true"`, and `aria-label="Keyboard shortcuts"` — the only site in the app that does. Those attributes now come from `Dialog`; pass `title="Keyboard shortcuts"` to preserve the accessible name. Its existing z-index is `z-[10000]`, deliberately above the `9999` band, so pass `z={10000}`.

Its backdrop `onClick` inline-checks `e.target === e.currentTarget`, which `Dialog` replicates — delete it per recipe step 3. Its panel className `bg-bg rounded-xl shadow-2xl w-[420px] max-w-[95vw] overflow-hidden` is already the primitive's default `panelClassName`, so it can be omitted; passing it explicitly is equally correct.

- [ ] **Step 3: Remove the `showShortcuts` special-case from `App.tsx`**

Delete only the `showShortcuts` branch of the Escape handling. Leave every other branch, and remove `showShortcuts` from the effect's dependency array only if no other code in the effect still reads it.

- [ ] **Step 4: Verify Escape closes it exactly once**

Open the shortcuts overlay with `?` and press Escape. Expected: it closes. Then confirm the other Escape behaviours the effect owns — the ones recorded in Step 1 — still work, since this task edited their handler.

- [ ] **Step 5: Verify** — run the verification block from the shared recipe, plus:

```bash
grep -rn 'showShortcuts' pypsa-gui/frontend/src/App.tsx
```

Expected: the state and its setter remain; no Escape special-case remains.

- [ ] **Step 6: Commit**

```bash
git branch --show-current   # must read feature/modal-a11y-primitive
git add pypsa-gui/frontend/src/components/ShortcutsHelp.tsx pypsa-gui/frontend/src/App.tsx
git commit -m "refactor(gui): migrate ShortcutsHelp onto Dialog, drop its App.tsx Escape special-case"
```

---

### Task 8: Close out

**Files:**
- Modify: `docs/superpowers/specs/2026-07-28-modal-a11y-primitive-design.md` (the follow-ups section)

- [ ] **Step 1: Verify every success criterion in the spec**

Walk the spec's nine numbered criteria one at a time and record the command and its actual output for each. Criterion 9 — that no barred file appears in the diff — is checked with:

```bash
git diff --name-only $(git merge-base feature/local-app-impl HEAD)..HEAD
```

Expected: no path from the Global Constraints barred list appears.

- [ ] **Step 2: Record what shipped and what did not**

Append to the spec's "Known follow-ups" section: any dialog left unmigrated and why, whether `CommandPalette` kept its own Escape handling, and the resolved dependency versions from Task 1.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-07-28-modal-a11y-primitive-design.md
git commit -m "docs(gui): record what the Dialog migration shipped"
```

---

## Self-Review

**Spec coverage.** Success criterion 1 → Task 1. Criteria 2-4, 6 → Task 2's test suite (focus trap, restoration, Escape, backdrop). Criterion 5 → Tasks 3-7's shared verification grep. Criterion 6's `TopologyCanvas` half → Task 6 Step 2. Criterion 7 → every task's typecheck. Criterion 8 → Task 1 Steps 1 and 7. Criterion 9 → Task 8 Step 1. The spec's "what the primitive does" list maps one-to-one onto `Dialog.test.tsx`'s twelve tests. The accessible-name requirement — added during spec self-review — is covered by the `title` and `aria-label` tests. Scroll locking appears nowhere, as specified. All nine actionable files appear in exactly one task each: Task 3 (3 files, 4 instances), Task 4 (3), Task 5 (1), Task 6 (1), Task 7 (1) — 9 files, 10 instances.

**Placeholder scan.** No "TBD", no "add error handling", no "similar to Task N". The migration recipe is written once and referenced rather than repeated, which is DRY rather than a placeholder — every task states its own files, z-index, and exceptions. Task 5's Escape branch is a genuine conditional on an observable fact ("if the palette binds its own Escape"), with both paths specified, not an unresolved decision.

**Type consistency.** `Dialog`'s prop names are used identically everywhere they appear: `open`, `onClose`, `title`, `dismissOnBackdrop`, `z`, `panelClassName`. Task 6 uses `dismissOnBackdrop={false}` exactly as Task 2 declares it. Tasks 3, 5, and 7 pass `z` as a number (`400`, `500`, `10000`) matching the `z?: number` signature, and Task 4 omits it to take the `9999` default. The named export `Dialog` is consistent across the import lines in Tasks 3-7 and the declaration in Task 2.

**One risk worth naming.** The migration recipe assumes each site's backdrop and panel are adjacent elements that can be swapped as a pair. That held for `ShortcutsHelp`, which was read in full. It is *inferred* for the other eight from the recon's shape taxonomy, not verified line by line. A site whose panel is not a direct child of its backdrop needs the recipe adapted rather than applied literally — an implementer hitting that should report it rather than force the shape.
