// Optional site location on the create forms (P5): latitude and longitude, in
// the Latitude (°) / Longitude (°) words of the Project & site drawer.
import type { LocationDraft, LocationErrors } from '../siteLocation'

const INPUT = 'rounded border border-border bg-bg px-1.5 py-0.5 text-[12px] tabular-nums'

export function SiteLocationFields({ value, errors, onChange }: {
  value: LocationDraft
  errors: LocationErrors
  onChange: (v: LocationDraft) => void
}) {
  const one = (k: 'lat' | 'lng', label: string) => (
    <div className="flex flex-col gap-1">
      <label htmlFor={`site-loc-${k}`} className="font-semibold text-text">{label}</label>
      <input id={`site-loc-${k}`} data-testid={`site-loc-${k}`} type="number" step="any" className={`${INPUT} w-32`}
        value={value[k]} onChange={e => onChange({ ...value, [k]: e.target.value })}
        aria-invalid={!!errors[k]} aria-describedby={errors[k] ? `site-loc-${k}-err` : 'site-loc-hint'} />
      {errors[k] && <p id={`site-loc-${k}-err`} role="alert" data-testid={`site-loc-error-${k}`}
        className="text-[11px] text-danger">{errors[k]}</p>}
    </div>
  )
  return (
    <fieldset data-testid="site-location" className="flex flex-col gap-1 text-[12px]">
      <legend className="font-semibold text-text">Site location</legend>
      <div className="flex flex-wrap items-start gap-3">
        {one('lat', 'Latitude (°)')}
        {one('lng', 'Longitude (°)')}
      </div>
      <p id="site-loc-hint" className="text-muted">
        Weather profiles and the map need it. Leave both blank to set it later in the Project &amp; site step.
      </p>
    </fieldset>
  )
}
