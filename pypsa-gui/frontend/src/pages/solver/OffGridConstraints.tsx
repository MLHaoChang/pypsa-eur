// homer-parity 01a: the "Off-grid constraints" block of Solver Settings
// (workflow section 8). Today it holds the minimum renewable fraction; the
// operating-reserve inputs of 01b join this block.
//
// The field is in percent; the wire value is a fraction (0.8 == 80 %), null
// when 0 or empty (off), matching the backend's
// `min_renewable_fraction: float | None` (ge=0, le=1). An entry outside
// 0–100 or one that is not a number is refused with an inline sentence and
// never patched into the draft, so Save cannot send it.
import { useEffect, useState } from 'react'
import type { SolverConfig } from '../../api/types'

/** The field label, shared with the chat edit card's word map. */
export const MIN_RENEWABLE_FRACTION_LABEL = 'Minimum renewable fraction (%)'

/** Percent text → the wire value, or an error sentence. */
export function renewableFractionFromText(text: string): { value: number | null } | { error: string } {
  const t = text.trim()
  if (t === '') return { value: null }
  const pct = Number(t)
  if (!Number.isFinite(pct)) return { error: 'Minimum renewable fraction: enter a number from 0 to 100.' }
  if (pct < 0 || pct > 100) return { error: `Minimum renewable fraction: ${t} % is outside 0 to 100.` }
  return { value: pct > 0 ? pct / 100 : null }
}

function toText(v: number | null | undefined): string {
  return v ? String(Math.round(v * 100 * 1e6) / 1e6) : ''
}

export function OffGridConstraints({ draft, patch }: {
  draft: SolverConfig
  patch: (p: Partial<SolverConfig>) => void
}) {
  const [text, setText] = useState(toText(draft.min_renewable_fraction))
  const [error, setError] = useState<string | null>(null)
  // A draft changed elsewhere (a load, the chat) shows here.
  useEffect(() => {
    setText(toText(draft.min_renewable_fraction))
    setError(null)
  }, [draft.min_renewable_fraction])
  return (
    <section className="border border-border rounded" data-testid="offgrid-constraints">
      <div className="px-3 py-1.5 border-b border-border bg-panel text-[10px] font-semibold uppercase tracking-wide text-muted">
        Off-grid constraints
      </div>
      <div className="p-3">
        <p className="text-[10px] text-muted mb-2">
          For an off-grid or island design: limits on how the system is run, applied to every solve.
        </p>
        <label className="flex flex-col gap-1">
          <span className="text-[10px] text-muted">{MIN_RENEWABLE_FRACTION_LABEL}</span>
          <input
            type="number"
            inputMode="decimal"
            min={0}
            max={100}
            step={5}
            value={text}
            aria-invalid={error ? true : undefined}
            onChange={e => {
              setText(e.target.value)
              const r = renewableFractionFromText(e.target.value)
              if ('error' in r) {
                setError(r.error)
                return
              }
              setError(null)
              patch({ min_renewable_fraction: r.value })
            }}
            className="w-28 px-2 py-1 border border-border rounded text-xs font-mono bg-bg focus:outline-none focus:border-accent"
          />
        </label>
        {error && (
          <p className="text-[10px] text-danger mt-1" role="alert" data-testid="min-renewable-fraction-error">
            {error}
          </p>
        )}
        <p className="text-[10px] text-muted mt-1">
          Share of the electricity served that comes from renewable sources. It counts what
          diesel, gas, grid import and other non-renewable sources supply against the load
          served; storage passes on what charged it. 0 means no minimum. A solve that cannot
          reach the minimum fails, and its log names this constraint as the cause.
        </p>
      </div>
    </section>
  )
}
