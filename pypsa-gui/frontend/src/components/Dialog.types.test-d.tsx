// Type-level test for Dialog's accessible-name requirement.
//
// The design doc calls a Dialog with no accessible name a defect, not a
// lint nit. DialogProps enforces it at the type level: at least one of
// `title`, `aria-label`, or `aria-labelledby` is required, so a nameless
// Dialog cannot be written at all — this file is the proof.
//
// Named `*.test-d.tsx` (not `*.test.tsx`) so vitest's `src/**/*.test.tsx`
// include glob does not try to execute it as a runtime test — it has no
// assertions to run, only a compile-time claim to check. It IS picked up
// by `tsc --noEmit` via tsconfig.json's `include: ["src"]`, which is this
// repo's documented typecheck step. If DialogProps ever regresses to a
// plain optional `title?: string`, the `@ts-expect-error` below goes
// unused and that typecheck fails.
import { Dialog } from './Dialog'

function omitsAllThreeNameSources() {
  return (
    // @ts-expect-error - omitting title, aria-label, and aria-labelledby must not typecheck
    <Dialog open onClose={() => {}}>
      <button>x</button>
    </Dialog>
  )
}

// Each name source alone must be sufficient — no combination is required.
function titleAloneIsSufficient() {
  return (
    <Dialog open onClose={() => {}} title="t">
      <button>x</button>
    </Dialog>
  )
}

function ariaLabelAloneIsSufficient() {
  return (
    <Dialog open onClose={() => {}} aria-label="t">
      <button>x</button>
    </Dialog>
  )
}

function ariaLabelledbyAloneIsSufficient() {
  return (
    <Dialog open onClose={() => {}} aria-labelledby="t">
      <button>x</button>
    </Dialog>
  )
}

export const __dialogNameTypeProbes = [
  omitsAllThreeNameSources,
  titleAloneIsSufficient,
  ariaLabelAloneIsSufficient,
  ariaLabelledbyAloneIsSufficient,
]
