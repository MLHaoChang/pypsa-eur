// The asset-side entry to the Library's cost references: a "Compare with references"
// link under a Generator's or Link's investment cost (not on a storage unit, whose cost
// comes in parts), the money year beside the unit, and the chip naming a value applied
// from a reference.
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import fixture from '../../test/fixtures/assetSchema.json'
import type { AssetSchema, ProvenanceEntry } from '../../api/assetSchema'
import { AssetFieldsEdit } from './AssetFields'
import type { FS } from './cardKit'

const schemas = fixture as Record<string, unknown>
const GEN: Record<string, unknown> = { name: 'pv', bus: 'B1', carrier: 'solar', p_nom: 5, overnight_cost: 556_220.85,
  lifetime: 40, fom_cost: 9_880, capital_cost: 0 }
const form = (asset: Record<string, unknown>): FS => Object.fromEntries(Object.entries(asset).map(([k, v]) => [k, String(v)]))
const applied: ProvenanceEntry = { status: 'customised', source: 'eu-dea-2026-08#e034aad727495a36/solar-utility@2030',
  source_year: 2026, currency_year: 2025, range: null, illustrative: false, currency: 'EUR',
  approved_by: 'admin@example.com', approved_at: '2026-10-21T10:00:00+00:00' }

function renderFields(component: string, asset: Record<string, unknown>, provenance?: Record<string, ProvenanceEntry>) {
  return render(<div className="grid grid-cols-2"><AssetFieldsEdit schema={schemas[component] as AssetSchema}
    fs={form(asset)} set={() => {}} current={asset} carriers={[]} buses={[]} provenance={provenance} /></div>)
}

afterEach(() => cleanup())

describe('Compare with references', () => {
  it('sits under a generator\'s investment cost', () => {
    renderFields('Generator', GEN)
    expect(screen.getByTestId('asset-compare-references').textContent).toBe('Compare with references')
  })

  it('is not offered on a storage unit', () => {
    renderFields('StorageUnit', { name: 'bess', bus: 'B1', p_nom: 1 })
    expect(screen.queryByTestId('asset-compare-references')).toBeNull()
  })

  it('shows the money year beside the unit and a Reference chip for an applied value', () => {
    const { container } = renderFields('Generator', GEN, { overnight_cost: applied, fom_cost: applied })
    expect(screen.getByTestId('asset-money-overnight_cost').textContent).toBe('In 2025 €')
    expect(container.textContent).not.toContain('€/kW, 2025')
    expect(screen.getByTestId('asset-money-fom_cost').textContent).toBe('In 2025 €')
    const chip = screen.getByTestId('asset-prov-overnight_cost')
    expect(chip.textContent).toBe('Reference')
    expect(chip.getAttribute('title')).toContain('Applied from a Library cost reference')
    expect(chip.getAttribute('title')).toContain('the Danish Energy Agency technology catalogue')
    expect(chip.getAttribute('title')).not.toContain('eu-dea-')
  })
})
