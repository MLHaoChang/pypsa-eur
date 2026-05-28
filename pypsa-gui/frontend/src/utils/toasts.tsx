import { useState } from 'react'
import toast from 'react-hot-toast'

// Inline-confirm pattern. Replaces native `confirm()` / `prompt()` which:
//   • block the main thread (no other UI updates render while open)
//   • cannot be styled to match the rest of the app
//   • produce inconsistent rendering across browsers
//   • are bypassed in CI / headless setups, silently auto-cancelling
//
// Usage:
//   confirmToast('Delete this generator?', () => deleteGen(name))
//   confirmToast('Reset network?', () => reset(), { confirmLabel: 'Reset', danger: true })
//
// Returns nothing — the caller's onConfirm runs (sync or async) when the
// user clicks the confirm button; clicking outside or the cancel button
// dismisses without firing.
export function confirmToast(
  message: string,
  onConfirm: () => void | Promise<void>,
  opts: {
    confirmLabel?: string
    cancelLabel?: string
    danger?: boolean
    duration?: number
  } = {},
): void {
  const {
    confirmLabel = 'Confirm',
    cancelLabel = 'Cancel',
    danger = false,
    duration = 8000,
  } = opts

  toast(
    (t) => (
      <div className="flex items-center gap-3 text-xs">
        <span className="text-text">{message}</span>
        <button
          type="button"
          onClick={async () => {
            toast.dismiss(t.id)
            try {
              await onConfirm()
            } catch (e) {
              const msg = e instanceof Error ? e.message : 'Action failed'
              toast.error(msg)
            }
          }}
          className={
            'px-2 py-1 rounded text-[11px] font-semibold transition-colors ' +
            (danger
              ? 'bg-danger text-white hover:bg-danger/80'
              : 'bg-accent text-white hover:bg-accent/80')
          }
        >
          {confirmLabel}
        </button>
        <button
          type="button"
          onClick={() => toast.dismiss(t.id)}
          className="px-2 py-1 rounded text-[11px] text-muted hover:text-text border border-border"
        >
          {cancelLabel}
        </button>
      </div>
    ),
    { duration },
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Vintage-clone inline form. Used by Generator / StorageUnit / Link detail
// panels to create a future-vintage copy of an asset for multi-investment-
// period planning. The clone shares everything with the original except:
//   • name      → `${original}_${year}` so the LP sees a separate component
//   • build_year → user-supplied (defaults to the next decade)
//   • overnight_cost → user-supplied (defaults to original's value)
//
// The form is rendered inside a toast so it doesn't require a full modal
// stack. State is owned by the inner component — toast (t) => <Form/> calls
// re-render but the child's useState persists.
// ─────────────────────────────────────────────────────────────────────────────
function VintageCloneForm({
  assetName,
  defaultYear,
  defaultCost,
  onSubmit,
  onCancel,
}: {
  assetName: string
  defaultYear: number
  defaultCost: number | null
  onSubmit: (year: number, overnightCost: number | null) => void | Promise<void>
  onCancel: () => void
}) {
  const [year, setYear] = useState<string>(String(defaultYear))
  const [cost, setCost] = useState<string>(defaultCost != null ? String(defaultCost) : '')
  const [submitting, setSubmitting] = useState(false)

  const submit = async () => {
    const yNum = parseInt(year, 10)
    if (!Number.isFinite(yNum) || yNum < 1900 || yNum > 2200) {
      toast.error('Year must be between 1900 and 2200')
      return
    }
    const cNum = cost.trim() === '' ? null : parseFloat(cost)
    if (cNum !== null && (!Number.isFinite(cNum) || cNum < 0)) {
      toast.error('Cost must be a non-negative number (or empty)')
      return
    }
    setSubmitting(true)
    try { await onSubmit(yNum, cNum) } finally { setSubmitting(false) }
  }

  return (
    <div className="flex flex-col gap-2 text-xs min-w-[260px]">
      <div className="text-text">
        Clone <span className="font-mono">{assetName}</span> as a future vintage:
      </div>
      <div className="grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-0.5">
          <span className="text-[10px] text-muted">Build year</span>
          <input
            type="number"
            value={year}
            onChange={e => setYear(e.target.value)}
            className="px-2 py-1 border border-border rounded text-[11px] bg-bg font-mono"
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[10px] text-muted">Overnight cost</span>
          <input
            type="number"
            value={cost}
            onChange={e => setCost(e.target.value)}
            placeholder="(blank = keep)"
            className="px-2 py-1 border border-border rounded text-[11px] bg-bg font-mono"
          />
        </label>
      </div>
      <div className="flex justify-end gap-2 mt-1">
        <button
          type="button"
          onClick={onCancel}
          className="px-2 py-1 rounded text-[11px] text-muted hover:text-text border border-border"
        >Cancel</button>
        <button
          type="button"
          disabled={submitting}
          onClick={submit}
          className="px-2 py-1 rounded text-[11px] font-semibold bg-accent text-white hover:bg-accent/80 disabled:opacity-40"
        >{submitting ? 'Cloning…' : 'Clone'}</button>
      </div>
    </div>
  )
}

export function vintageCloneToast(opts: {
  assetName: string
  defaultYear: number
  defaultCost: number | null
  onSubmit: (year: number, overnightCost: number | null) => void | Promise<void>
}): void {
  toast(
    (t) => (
      <VintageCloneForm
        assetName={opts.assetName}
        defaultYear={opts.defaultYear}
        defaultCost={opts.defaultCost}
        onCancel={() => toast.dismiss(t.id)}
        onSubmit={async (year, cost) => {
          toast.dismiss(t.id)
          try { await opts.onSubmit(year, cost) }
          catch (e) {
            const msg = e instanceof Error ? e.message : 'Clone failed'
            toast.error(msg)
          }
        }}
      />
    ),
    { duration: Infinity },
  )
}

// Undo-after-delete pattern. Already used in TopologyCanvas for line/link
// removal; lifted here for re-use. The action runs IMMEDIATELY (consistent
// with how single-row deletes work); the toast offers an undo within `duration`.
export function showUndoToast(
  message: string,
  onUndo: () => void | Promise<void>,
  opts: { duration?: number } = {},
): void {
  const { duration = 5000 } = opts
  toast(
    (t) => (
      <div className="flex items-center gap-3 text-xs">
        <span className="text-text">{message}</span>
        <button
          type="button"
          onClick={async () => {
            toast.dismiss(t.id)
            try {
              await onUndo()
            } catch (e) {
              const msg = e instanceof Error ? e.message : 'Undo failed'
              toast.error(msg)
            }
          }}
          className="px-2 py-1 rounded text-[11px] font-semibold bg-accent text-white hover:bg-accent/80"
        >
          Undo
        </button>
      </div>
    ),
    { duration },
  )
}
