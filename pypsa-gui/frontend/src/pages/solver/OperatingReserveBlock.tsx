// homer-parity 01b-2: the "Operating reserve" block of Solver Settings
// (workflow section 8, Dispatch tab; control-strategy walkthrough 03 moved it
// here from Network › Off-grid constraints, where a user looking for a
// dispatch setting did not find it).
//
// Each field is in percent; the wire value is a fraction (0.1 == 10 %), null
// when 0 or empty (off), matching the backend's `float | None` (ge=0, le=1)
// fields `reserve_share_load`, `reserve_share_pv` and `reserve_share_wind`. An
// entry outside 0–100 or one that is not a number is refused with an inline
// sentence opening with the field's label and never patched into the draft,
// and Save stays disabled while any refusal shows.
import { useRef } from 'react'
import type { SolverConfig } from '../../api/types'
import { RESERVE_LABELS, ShareField, type ShareKey } from './OffGridConstraints'

export function OperatingReserveBlock({ draft, patch, onError }: {
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
    <section id="solver-operating-reserve" className="border border-border rounded" data-testid="operating-reserve">
      <div className="px-3 py-1.5 border-b border-border bg-panel text-[10px] font-semibold uppercase tracking-wide text-muted">
        Operating reserve
      </div>
      <div className="p-3">
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
    </section>
  )
}
