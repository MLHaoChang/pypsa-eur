// homer-parity 01a/01b: the "Off-grid constraints" block of Solver Settings
// (workflow section 8): the minimum renewable fraction (01a) and the
// operating reserve shares (01b-2).
//
// Each field is in percent; the wire value is a fraction (0.8 == 80 %), null
// when 0 or empty (off), matching the backend's `float | None` (ge=0, le=1)
// fields `min_renewable_fraction`, `reserve_share_load`, `reserve_share_pv`
// and `reserve_share_wind`. An entry outside 0–100 or one that is not a number
// is refused with an inline sentence opening with the field's label and never
// patched into the draft, and Save stays disabled while any refusal shows.
import { useEffect, useRef, useState } from 'react'
import type { SolverConfig } from '../../api/types'

/** The field label, shared with the chat edit card's word map. */
export const MIN_RENEWABLE_FRACTION_LABEL = 'Minimum renewable fraction (%)'

/** The operating reserve fields (homer-parity 01b), each label shared with the chat edit card's word map. */
export const RESERVE_LABELS = {
  reserve_share_load: 'Reserve for load (%)',
  reserve_share_pv: 'Reserve for solar output (%)',
  reserve_share_wind: 'Reserve for wind output (%)',
} as const

type ShareKey = 'min_renewable_fraction' | keyof typeof RESERVE_LABELS

/** Percent text → the wire value, or an error sentence opening with ``name`` (the label without its unit). */
export function shareFromText(name: string, text: string): { value: number | null } | { error: string } {
  const t = text.trim()
  if (t === '') return { value: null }
  const pct = Number(t)
  if (!Number.isFinite(pct)) return { error: `${name}: enter a number from 0 to 100.` }
  if (pct < 0 || pct > 100) return { error: `${name}: ${t} % is outside 0 to 100.` }
  return { value: pct > 0 ? pct / 100 : null }
}

/** Percent text → the wire value of the minimum renewable fraction, or an error sentence. */
export function renewableFractionFromText(text: string): { value: number | null } | { error: string } {
  return shareFromText('Minimum renewable fraction', text)
}

function toText(v: number | null | undefined): string {
  return v ? String(Math.round(v * 100 * 1e6) / 1e6) : ''
}

const nameOf = (label: string) => label.replace(/ \(%\)$/, '')

/** One percent field: its own text and refusal; the block collects the refusals. */
function ShareField({ field, label, value, patch, onError, errorTestId }: {
  field: ShareKey
  label: string
  value: number | null | undefined
  patch: (p: Partial<SolverConfig>) => void
  onError: (field: ShareKey, error: string | null) => void
  errorTestId: string
}) {
  const [text, setText] = useState(toText(value))
  const [error, setErrorState] = useState<string | null>(null)
  const setError = (e: string | null) => {
    setErrorState(e)
    onError(field, e)
  }
  // A draft changed elsewhere (a load, the chat) shows here.
  useEffect(() => {
    setText(toText(value))
    setErrorState(null)
    onError(field, null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])
  return (
    <>
      <label className="flex flex-col gap-1">
        <span className="text-[10px] text-muted">{label}</span>
        <input
          type="text"
          inputMode="decimal"
          value={text}
          aria-invalid={error ? true : undefined}
          onChange={e => {
            setText(e.target.value)
            const r = shareFromText(nameOf(label), e.target.value)
            if ('error' in r) {
              setError(r.error)
              return
            }
            setError(null)
            patch({ [field]: r.value } as Partial<SolverConfig>)
          }}
          className="w-28 px-2 py-1 border border-border rounded text-xs font-mono bg-bg focus:outline-none focus:border-accent"
        />
      </label>
      {error && (
        <p className="text-[10px] text-danger mt-1" role="alert" data-testid={errorTestId}>
          {error}
        </p>
      )}
    </>
  )
}

export function OffGridConstraints({ draft, patch, onError }: {
  draft: SolverConfig
  patch: (p: Partial<SolverConfig>) => void
  /** The first refusal sentence of the block, or null; the page disables Save while it is set. */
  onError?: (error: string | null) => void
}) {
  const errors = useRef<Partial<Record<ShareKey, string | null>>>({})
  const report = (field: ShareKey, e: string | null) => {
    errors.current[field] = e
    onError?.(Object.values(errors.current).find(v => v) ?? null)
  }
  return (
    <section id="solver-offgrid-constraints" className="border border-border rounded" data-testid="offgrid-constraints">
      <div className="px-3 py-1.5 border-b border-border bg-panel text-[10px] font-semibold uppercase tracking-wide text-muted">
        Off-grid constraints
      </div>
      <div className="p-3">
        <p className="text-[10px] text-muted mb-2">
          For an off-grid or island design: limits on how the system is run, applied to every solve.
        </p>
        <ShareField
          field="min_renewable_fraction"
          label={MIN_RENEWABLE_FRACTION_LABEL}
          value={draft.min_renewable_fraction}
          patch={patch}
          onError={report}
          errorTestId="min-renewable-fraction-error"
        />
        <p className="text-[10px] text-muted mt-1">
          Share of the electricity used on site (load served plus what electrolysers, heat
          pumps and other converters draw) that comes from renewable sources. It counts what
          diesel, gas, grid import and other non-renewable sources supply against that use;
          storage passes on what charged it. 0 means no minimum. A solve that cannot
          reach the minimum fails, and its log names this constraint as the cause; with a
          lost-load price (VOLL) set, it sheds load at that price instead.
        </p>
        <div className="mt-3 pt-2 border-t border-border" data-testid="operating-reserve">
          <div className="text-[10px] font-semibold text-muted mb-1">Operating reserve</div>
          <p className="text-[10px] text-muted mb-2">
            Dispatchable generators and storage keep this much spare power every hour, as a share
            of the load and of the solar and wind output, to cover a sudden drop. 0 means no reserve.
          </p>
          <div className="flex flex-col gap-2">
            {(Object.keys(RESERVE_LABELS) as (keyof typeof RESERVE_LABELS)[]).map(k => (
              <div key={k}>
                <ShareField
                  field={k}
                  label={RESERVE_LABELS[k]}
                  value={draft[k]}
                  patch={patch}
                  onError={report}
                  errorTestId={`${k.replace(/_/g, '-')}-error`}
                />
              </div>
            ))}
          </div>
          <p className="text-[10px] text-muted mt-1">
            A solve that cannot hold the reserve fails, and its log names the operating reserve,
            with the hours where the units cannot cover it.
          </p>
        </div>
      </div>
    </section>
  )
}
