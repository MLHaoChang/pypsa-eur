import { useId } from 'react'
import type { CheckedAnswerCapabilities } from '../api/reliability'

export function CheckedAnswerControl({ checked, required, available, loading, failed, busy, mode, onChange, onRetry }: {
  checked: boolean; required: boolean; available: boolean; loading: boolean; failed: boolean; busy: boolean
  mode?: CheckedAnswerCapabilities['mode']
  onChange: (value: boolean) => void; onRetry: () => void
}) {
  const helpId = useId()
  const status = failed ? 'Could not load answer-check availability.'
    : loading ? 'Loading answer-check availability…'
    : required ? 'Answer checks are required on this server.'
    : !available ? mode === 'pilot' ? 'Calculation checks are unavailable.' : 'Answer checks are not enabled on this server.'
    : checked ? 'Answers will be checked before they are shown.' : 'Opt in to checked answers for this chat.'
  return <div className="border-t border-border px-3 py-1.5 text-[11px] shrink-0" data-testid="checked-answer-control">
    <div className="flex items-center gap-2">
      <label className="flex items-center gap-1.5">
        <input type="checkbox" checked={checked} disabled={busy || required || (!checked && !available)}
          aria-describedby={helpId} onChange={event => onChange(event.target.checked)} className="accent-accent" />
        Check answers
      </label>
      {failed && <button type="button" onClick={onRetry} disabled={busy}
        className="underline text-muted" aria-label="Retry answer-check availability">Retry availability</button>}
    </div>
    <p id={helpId} className="text-muted mt-0.5" aria-live="polite">{status}</p>
    {checked && <p className="text-muted">Supports selected project inputs and calculations; other requests may be unavailable.</p>}
  </div>
}
