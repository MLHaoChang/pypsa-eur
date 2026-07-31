import { Ban, CircleAlert, Sigma } from 'lucide-react'
import type { MetricRow, Remedy } from './types'

interface Props {
  metrics: MetricRow[]
  selected: string[]
  onToggle: (id: string) => void
  onRemedy: (remedy: Remedy) => void
}

// Three visual states, deliberately distinct. `blocked` means "you can fix
// this"; `na` means "this can never apply here". Collapsing them into one
// grey would leave the user unable to tell whether running AC PF would light
// half the strip up.
function Row({ m, checked, onToggle, onRemedy }: {
  m: MetricRow; checked: boolean
  onToggle: (id: string) => void; onRemedy: (r: Remedy) => void
}) {
  const disabled = m.status !== 'ok'
  return (
    <li className="flex flex-col gap-0.5 py-0.5">
      <label className={`flex items-center gap-1.5 text-[11px] ${disabled ? 'text-muted' : 'text-text'}`}>
        <input
          type="checkbox"
          aria-label={m.label}
          checked={checked}
          disabled={disabled}
          onChange={() => { if (!disabled) onToggle(m.id) }}
          className="accent-accent disabled:opacity-40"
        />
        <span className={disabled ? 'line-through decoration-border' : ''}>{m.label}</span>
        {m.unit && <span className="text-[10px] text-muted font-mono">{m.unit}</span>}
        {m.origin === 'input' && (
          <span title="Model input, not a solver result"
            className="text-[9px] uppercase tracking-wide text-muted border border-border rounded px-1">in</span>
        )}
        {m.origin === 'derived' && m.formula && (
          <span title={m.formula} className="text-muted"><Sigma size={10} /></span>
        )}
        {m.status === 'blocked' && <CircleAlert size={11} className="text-warn" />}
        {m.status === 'na' && <Ban size={11} className="text-muted" />}
      </label>
      {disabled && m.reason && (
        <span className="pl-5 text-[10px] text-muted flex items-center gap-1.5">
          {m.reason}
          {m.status === 'blocked' && m.remedy && (
            <button
              onClick={() => onRemedy(m.remedy!)}
              className="text-accent hover:underline"
            >{m.remedy.label} →</button>
          )}
        </span>
      )}
    </li>
  )
}

export default function MetricChecklist({ metrics, selected, onToggle, onRemedy }: Props) {
  const sel = new Set(selected)
  const scalars = metrics.filter(m => m.kind === 'scalar')
  const series = metrics.filter(m => m.kind === 'series')
  const zone = (title: string, rows: MetricRow[]) => rows.length === 0 ? null : (
    <div className="mb-2">
      <div className="text-[9px] uppercase tracking-wider text-muted mb-1">{title}</div>
      <ul className="flex flex-col">
        {rows.map(m => (
          <Row key={m.id} m={m} checked={sel.has(m.id)}
            onToggle={onToggle} onRemedy={onRemedy} />
        ))}
      </ul>
    </div>
  )
  return (
    <div className="px-2 py-2 border-b border-border">
      {zone('Summary values', scalars)}
      {zone('Time series', series)}
    </div>
  )
}
