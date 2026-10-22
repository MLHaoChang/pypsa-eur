// U3 F part 2: the Expert card's provenance chips (S1a's per-field stamps).
// A card with no `provenance` prop renders exactly as before (no chip
// nodes); each status shows its own chip on the field it stamps; a derived
// row shows only its own key's stamp, never one inferred from its parts.
// The collapsed read-out carries the same chips, so "open in Expert" shows
// what is still the pack's default without opening the form.
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render as plainRender, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import fixture from '../../test/fixtures/assetSchema.json'
import type { AssetSchema, ProvenanceEntry } from '../../api/assetSchema'
import { AssetFieldsEdit, assetReadoutItems } from './AssetFields'
import { ExpandedProps, type FS } from './cardKit'

// The lifetime note under a card's field reads the project default from the query cache.
const render = (ui: ReactElement) => plainRender(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>)

const SCHEMA = (fixture as Record<string, unknown>).StorageUnit as AssetSchema
const BESS: Record<string, unknown> = {
  name: 'bess', bus: 'B1', carrier: 'battery', p_nom: 80, p_nom_extendable: true, p_nom_min: 0,
  p_nom_max: 80, max_hours: 4, efficiency_store: 0.96, efficiency_dispatch: 0.96, standing_loss: 0,
  cyclic_state_of_charge: true, marginal_cost: 0, discount_rate: 0.07, capital_cost: 95_712, fom_cost: 722,
  lifetime: 25, inv_power_overnight: 213_900, inv_power_lifetime: 10, inv_power_fom_share: 0.0034,
  inv_energy_overnight: 189_900, inv_energy_lifetime: 25, inv_energy_fom_share: 0,
}
const FORM: FS = Object.fromEntries(Object.entries(BESS).map(([k, v]) => [k, String(v)]))
const entry = (status: ProvenanceEntry['status']): ProvenanceEntry => ({
  status, source: 'generic-defaults 1.0 abc123', source_year: 2020, currency_year: 2020,
  range: null, illustrative: false })

function html(provenance?: Record<string, ProvenanceEntry>): string {
  const { container } = render(
    <div className="grid grid-cols-2">
      <AssetFieldsEdit schema={SCHEMA} fs={FORM} set={() => {}} current={BESS} carriers={[]} buses={[]}
        {...(provenance ? { provenance } : {})} />
    </div>)
  const out = container.innerHTML
  cleanup()
  return out
}

afterEach(() => cleanup())

describe('the Expert card without provenance', () => {
  it('renders exactly as before: no prop, an empty map, or stamps on no shown field', () => {
    const before = html()
    expect(before).not.toContain('asset-prov-')
    expect(html({})).toBe(before)
    expect(html({ not_a_field: entry('default') })).toBe(before)
  })
})

describe('the Expert card with provenance', () => {
  it.each([
    ['default', 'inv_power_overnight', 'Default'],
    ['customised', 'efficiency_store', 'Customised'],
    ['measured', 'max_hours', 'Measured'],
  ] as const)('a %s stamp shows its chip on %s', (status, key, label) => {
    render(<div className="grid grid-cols-2"><AssetFieldsEdit schema={SCHEMA} fs={FORM} set={() => {}}
      current={BESS} carriers={[]} buses={[]} provenance={{ [key]: entry(status) }} /></div>)
    const chip = screen.getByTestId(`asset-prov-${key}`)
    expect(chip.getAttribute('data-status')).toBe(status)
    expect(chip.textContent).toContain(label)
    expect(document.querySelectorAll('[data-testid^="asset-prov-"]')).toHaveLength(1)
  })

  // The S1c follow-up: a number, enum or flag carries its chip in its own
  // label, so the two-column grid keeps its pairs (no full-width chip row).
  it.each([
    ['max_hours', 'number'],
    ['outage_rate_basis', 'enum'],
    ['cyclic_state_of_charge', 'flag'],
  ] as const)('the chip on %s (%s) sits in its own label, not a row of its own', (key, _kind) => {
    render(<div className="grid grid-cols-2"><AssetFieldsEdit schema={SCHEMA} fs={FORM}
      set={() => {}} current={BESS} carriers={[]} buses={[]} provenance={{ [key]: entry('customised') }} /></div>)
    const chip = screen.getByTestId(`asset-prov-${key}`)
    expect(chip.closest('label')?.querySelector('input, select')).toBeTruthy()
    expect(chip.closest('.col-span-2.-mt-0\\.5')).toBeNull()
  })

  it('a text field, with no badge slot, keeps its chip on a row below it', () => {
    render(<div className="grid grid-cols-2"><AssetFieldsEdit schema={SCHEMA} fs={FORM}
      set={() => {}} current={BESS} carriers={[]} buses={[]} provenance={{ name: entry('customised') }} /></div>)
    expect(screen.getByTestId('asset-prov-name').closest('label')).toBeNull()
  })

  it('a derived row shows its own stamp, never one inferred from its parts', () => {
    render(<div className="grid grid-cols-2"><AssetFieldsEdit schema={SCHEMA} fs={FORM} set={() => {}}
      current={BESS} carriers={[]} buses={[]}
      provenance={{ inv_power_overnight: entry('default'), inv_energy_overnight: entry('default') }} /></div>)
    expect(screen.queryByTestId('asset-prov-capital_cost')).toBeNull()
    expect(screen.queryByTestId('asset-prov-lifetime')).toBeNull()
    cleanup()
    render(<div className="grid grid-cols-2"><AssetFieldsEdit schema={SCHEMA} fs={FORM} set={() => {}}
      current={BESS} carriers={[]} buses={[]} provenance={{ capital_cost: entry('customised') }} /></div>)
    expect(screen.getByTestId('asset-derived-capital_cost')
      .contains(screen.getByTestId('asset-prov-capital_cost'))).toBe(true)
  })
})

describe('the collapsed read-out', () => {
  function readout(provenance?: Record<string, ProvenanceEntry>): string {
    const { container } = render(<ExpandedProps items={assetReadoutItems(SCHEMA, BESS, undefined, provenance)} />)
    const out = container.innerHTML
    cleanup()
    return out
  }

  it('renders exactly as before without stamps', () => {
    const before = readout()
    expect(before).not.toContain('asset-prov-')
    expect(readout({})).toBe(before)
    expect(readout({ not_a_field: entry('default') })).toBe(before)
    expect(assetReadoutItems(SCHEMA, BESS, undefined, {})).toEqual(assetReadoutItems(SCHEMA, BESS))
  })

  it.each([
    ['default', 'inv_power_overnight', 'Default'],
    ['customised', 'efficiency_store', 'Customised'],
    ['measured', 'max_hours', 'Measured'],
  ] as const)('a %s stamp shows its chip on the %s row', (status, key, label) => {
    render(<ExpandedProps items={assetReadoutItems(SCHEMA, BESS, undefined, { [key]: entry(status) })} />)
    const chip = screen.getByTestId(`asset-prov-${key}`)
    expect(chip.getAttribute('data-status')).toBe(status)
    expect(chip.textContent).toContain(label)
    expect(document.querySelectorAll('[data-testid^="asset-prov-"]')).toHaveLength(1)
  })

  it('a derived row shows its own stamp only', () => {
    render(<ExpandedProps items={assetReadoutItems(SCHEMA, BESS, undefined,
      { inv_power_overnight: entry('default'), inv_energy_overnight: entry('default') })} />)
    expect(screen.queryByTestId('asset-prov-capital_cost')).toBeNull()
    expect(screen.queryByTestId('asset-prov-lifetime')).toBeNull()
  })
})
