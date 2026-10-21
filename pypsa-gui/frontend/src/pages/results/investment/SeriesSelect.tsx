// A Library series picker, shared by the contract editor and the market-price block
// (IPP ticket 07). A stored ref the Library no longer lists (an older version) stays
// shown as such — never as "none" (WP3.7c review #3).
import type { LibraryRef } from '../../../api/types'

const input = 'border border-border rounded px-1 py-0.5 text-[11px] bg-bg'

export const seriesKey = (r: LibraryRef) => `${r.id}@${r.version}`

export function SeriesSelect({ label, value, series, onChange }: {
  label: string; value: LibraryRef | null; series: LibraryRef[]; onChange: (r: LibraryRef | null) => void
}) {
  const listed = !value || series.some(r => seriesKey(r) === seriesKey(value))
  return (
    <select aria-label={label} className={input} value={value ? seriesKey(value) : ''}
            onChange={e => onChange(e.target.value === '' ? null
              : series.find(r => seriesKey(r) === e.target.value) ?? value)}>
      <option value="">none</option>
      {series.map(r => <option key={seriesKey(r)} value={seriesKey(r)}>{r.id} v{r.version}</option>)}
      {!listed && <option value={seriesKey(value!)}>{value!.id} v{value!.version} (not listed)</option>}
    </select>
  )
}
