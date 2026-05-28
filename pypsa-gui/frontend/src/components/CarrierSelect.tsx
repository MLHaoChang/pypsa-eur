import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import type { Carrier } from '../api/types'
import { CARRIER_CATALOG_NAMES } from '../utils/carrierCatalog'

// ── CarrierSelect — shared <select> for carrier names ─────────────────────
// Single source of truth for grouping carriers across forms. Both the
// PropertiesPanel.CarrierGroupSelect and the SolverSettings constraint
// form (and any future carrier picker) consume this. The option list is:
//
//   1. n.carriers (whatever the project has)
//   2. ∪ the curated PyPSA-Eur catalog (so a fresh project still shows
//      "onwind", "CCGT", … without forcing the user to create them first)
//   3. ∪ the current value (so a legacy/one-off carrier still appears
//      selected instead of silently disappearing from the dropdown)
//
// Renders an `<optgroup>` per category — Electricity / Renewables / Thermal /
// Hydrogen / Heat / Storage / Other.

export interface CarrierSelectProps {
  value: string
  onChange: (v: string) => void
  /** Optional label rendered above the select. Pass null to omit. */
  label?: string | null
  /** Placeholder option text shown when value is empty. */
  placeholder?: string
  /** Extra tailwind classes appended to the <select>. */
  className?: string
  /** Tailwind class for the wrapping <label>. Default 'flex flex-col gap-0.5'. */
  wrapperClassName?: string
  /** Whether to add a blank "(any)" / placeholder option at the top. */
  allowEmpty?: boolean
  /** Filter to a subset of categories — e.g. ['Thermal & Nuclear', 'Hydrogen & Fuels'] to
   *  scope a fuel-cap dropdown to fossil/storable carriers only. */
  categoryFilter?: string[]
  /** Optional title attribute for accessibility / tooltip. */
  title?: string
  /** Inline id forwarded to the <select>. Useful when pairing with an
   *  external <label htmlFor>. */
  id?: string
}

// Group definition mirrors PropertiesPanel's categorizeCarriers — keeping
// them aligned is the WHOLE point of extracting this component.
const GROUPS: Array<{ label: string; test: (n: string) => boolean }> = [
  { label: 'Electricity',
    test: n => n === 'AC' || n === 'DC' || /electricity|low\s*voltage|^elec$/i.test(n) },
  { label: 'Hydrogen & Fuels',
    test: n => { const l = n.toLowerCase(); return l.includes('h2') || l.includes('hydrogen')
      || l.includes('electrolysis') || l.includes('fuel cell') || l.includes('nh3')
      || l.includes('methanol') || l.includes('methane') || l.includes('ammonia')
      || l.includes('biogas') } },
  { label: 'Renewables',
    test: n => { const l = n.toLowerCase(); return l.includes('wind') || l.includes('solar')
      || l.includes('pv') || l === 'ror' || l === 'hydro' || l.includes('geothermal')
      || l === 'run-of-river' || l.includes('rooftop') || l.includes('wave') } },
  { label: 'Thermal & Nuclear',
    test: n => { const l = n.toLowerCase(); return l.includes('coal') || l.includes('lignite')
      || l.includes('ocgt') || l.includes('ccgt') || l.includes('nuclear')
      || l.includes('oil') || l.includes('gas') || l.includes('biomass')
      || l.includes('cgt') } },
  { label: 'Heat',
    test: n => { const l = n.toLowerCase(); return l.includes('heat') || l.includes('chp')
      || l.includes('boiler') || l.includes('pump') } },
  { label: 'Storage',
    test: n => { const l = n.toLowerCase(); return l.includes('battery') || l.includes('phs')
      || l.includes('pumped') || l.includes('tank') || l === 'co2' || l.includes('co2 stored') } },
]

export function categorizeCarriers(
  carrierNames: string[],
  currentValue: string,
): Array<{ label: string; names: string[] }> {
  const allNames = [...new Set([
    ...carrierNames,
    ...CARRIER_CATALOG_NAMES,
    ...(currentValue ? [currentValue] : []),
  ])]
  const buckets: Array<{ label: string; names: string[] }> = GROUPS.map(g => ({
    label: g.label, names: [],
  }))
  const assigned = new Set<string>()
  for (let i = 0; i < GROUPS.length; i++) {
    for (const n of allNames) {
      if (!assigned.has(n) && GROUPS[i].test(n)) {
        buckets[i].names.push(n)
        assigned.add(n)
      }
    }
  }
  const other = allNames.filter(n => !assigned.has(n)).sort()
  // Sort each group alphabetically — PyPSA-Eur catalog isn't pre-sorted.
  for (const b of buckets) b.names.sort()
  return [
    ...buckets.filter(g => g.names.length > 0),
    ...(other.length ? [{ label: 'Other', names: other }] : []),
  ]
}

export default function CarrierSelect({
  value, onChange, label, placeholder, className, wrapperClassName,
  allowEmpty, categoryFilter, title, id,
}: CarrierSelectProps) {
  const { data: carriers = [] } = useQuery({
    queryKey: ['carriers'],
    queryFn: networkApi.getCarriers,
    staleTime: 60_000,
  })
  const groups = useMemo(() => {
    const all = categorizeCarriers(
      (carriers as Carrier[]).map(c => c.name),
      value,
    )
    if (!categoryFilter || categoryFilter.length === 0) return all
    return all.filter(g => categoryFilter.includes(g.label))
  }, [carriers, value, categoryFilter])

  // Empty state: surface the current value as a synthetic option so the
  // select isn't blank when the carrier list hasn't loaded yet or no
  // groups matched.
  const hasAny = groups.some(g => g.names.length > 0)

  return (
    <label className={wrapperClassName ?? 'flex flex-col gap-0.5'}>
      {label !== null && (
        <span className="text-[10px] text-muted" title={title}>
          {label ?? 'Carrier'}
        </span>
      )}
      <select
        id={id}
        value={value}
        onChange={e => onChange(e.target.value)}
        title={title}
        className={`px-2 py-1 border border-border rounded text-xs bg-bg ${className ?? ''}`}
      >
        {allowEmpty && <option value="">{placeholder ?? '— any —'}</option>}
        {!hasAny && (
          <option value={value}>{value || (placeholder ?? '—')}</option>
        )}
        {groups.map(g => (
          <optgroup key={g.label} label={g.label}>
            {g.names.map(name => (
              <option key={name} value={name}>{name}</option>
            ))}
          </optgroup>
        ))}
      </select>
    </label>
  )
}
