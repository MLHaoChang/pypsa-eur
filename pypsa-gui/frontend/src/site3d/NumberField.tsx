// A labelled numeric input for the Site 3D controls (coordinates and building
// correction): unit beside the label, an empty field is NaN, and the error is
// visible text tied to the input (never a tooltip alone).
import { useId } from 'react'

interface Props {
  label: string
  unit?: string
  value: number
  onChange: (v: number) => void
  error?: string | null
  hint?: string
  step?: number | 'any'
  min?: number
  max?: number
  disabled?: boolean
  testId?: string
  width?: string
}

export default function NumberField({ label, unit, value, onChange, error, hint, step = 'any', min, max, disabled, testId, width = 'w-28' }: Props) {
  const id = useId()
  const errId = `${id}-err`
  return (
    <label className="flex flex-col gap-0.5 text-[11px]" htmlFor={id}>
      <span className="text-muted">{label}{unit ? <span className="text-ink-400"> ({unit})</span> : null}</span>
      <input
        id={id} type="number" inputMode="decimal" step={step} min={min} max={max} disabled={disabled}
        data-testid={testId}
        value={Number.isFinite(value) ? value : ''}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errId : undefined}
        onChange={e => onChange(e.target.value === '' ? NaN : Number(e.target.value))}
        className={`${width} rounded border bg-bg px-2 py-1 text-[12px] text-text disabled:opacity-50 ${error ? 'border-danger' : 'border-border'}`}
      />
      {hint && !error && <span className="text-[10px] text-ink-400">{hint}</span>}
      {error && <span id={errId} role="alert" className="text-[10px] text-danger">{error}</span>}
    </label>
  )
}
