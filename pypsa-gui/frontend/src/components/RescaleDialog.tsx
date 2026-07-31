import { Dialog } from './Dialog'
import type { RescalePreview } from '../utils/rescale'

// Asks whether a length change should carry its impedance with it.
//
// Presentational: no store, no Leaflet, no data fetching, so the copy the user
// reads is under test. The caller owns the decision and the API calls.
//
// It shows numbers rather than a bare yes/no because the choice is not
// obvious: keeping the old absolute values preserves solver results but leaves
// the per-km impedance physically wrong; taking the new ones fixes the physics
// and moves the results.
interface RescaleDialogProps {
  previews: RescalePreview[]
  blocked: RescalePreview[]
  onAccept: () => void
  onDecline: () => void
}

const BLOCKED_REASON: Record<string, string> = {
  'old_length<=0': 'had no length, so its per-km value is unknown',
  'new_length<=0': 'would end up with zero length',
}

export default function RescaleDialog({ previews, blocked, onAccept, onDecline }: RescaleDialogProps) {
  if (previews.length === 0 && blocked.length === 0) return null

  const perKm = (v: number, len: number) => (len > 0 ? (v / len) : NaN)
  const fmt = (v: number) => (Number.isFinite(v) ? v.toPrecision(3) : '—')
  // Lengths use fixed decimal places rather than 3 significant figures:
  // toPrecision(3) on a 3+ digit km value (e.g. 476.3) rounds away the
  // fractional part entirely ("476"), which reads as a whole-number length
  // even though it isn't one. Two decimal places keeps the fraction visible
  // regardless of how many integer digits the length has.
  const fmtLen = (v: number) => (Number.isFinite(v) ? v.toFixed(2) : '—')

  return (
    <Dialog
      open
      onClose={onDecline}
      title="Line lengths changed"
      panelClassName="bg-bg rounded-xl shadow-2xl w-[620px] max-w-[95vw] overflow-hidden"
    >
      <div className="p-4 text-xs text-text">
        {previews.length > 0 && (
          <>
            <p className="text-muted leading-relaxed">
              These lines are now a different length. Their resistance, reactance and
              susceptance are stored as absolute values, so unless they are rescaled the
              per-km figures shown in the properties panel change instead.
            </p>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-[11px] font-mono">
                <thead className="text-muted">
                  <tr>
                    <th className="text-left py-1">Line</th>
                    <th className="text-right">Length (km)</th>
                    <th className="text-right">r (Ω/km)</th>
                    <th className="text-right">x (Ω/km)</th>
                  </tr>
                </thead>
                <tbody>
                  {previews.map(p => (
                    <tr key={p.name} className="border-t border-border">
                      <td className="py-1 font-sans font-medium">{p.name}</td>
                      <td className="text-right">{fmtLen(p.old_length)} → {fmtLen(p.new_length)}</td>
                      <td className="text-right">
                        {fmt(perKm(p.old.r, p.old_length))} → {fmt(perKm(p.new.r, p.new_length))}
                      </td>
                      <td className="text-right">
                        {fmt(perKm(p.old.x, p.old_length))} → {fmt(perKm(p.new.x, p.new_length))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-3 text-[11px] text-muted">
              Updating keeps the per-km values you see above and changes the stored
              absolute values. It changes <span className="font-mono">x</span>, and power
              flow splits inversely with <span className="font-mono">x</span> —{' '}
              <span className="text-text font-medium">your results will change</span>. Undo
              reverses it.
            </p>
          </>
        )}

        {blocked.length > 0 && (
          <p className="mt-3 text-[11px] text-muted">
            Not rescaled:{' '}
            {blocked.map((b, i) => (
              <span key={b.name}>
                {i > 0 && ', '}
                <span className="font-mono text-text">{b.name}</span>{' '}
                {BLOCKED_REASON[b.skipped_reason ?? ''] ?? 'could not be rescaled'}
              </span>
            ))}
            .
          </p>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onDecline}
            className="px-3 py-1.5 rounded-md border border-border text-xs hover:bg-border/30 transition-colors"
          >Keep current values</button>
          <button
            type="button"
            onClick={onAccept}
            disabled={previews.length === 0}
            className="px-3 py-1.5 rounded-md bg-accent text-white text-xs font-medium hover:opacity-90 transition-opacity disabled:opacity-40"
          >Update {previews.length} line{previews.length === 1 ? '' : 's'}</button>
        </div>
      </div>
    </Dialog>
  )
}
