// The Library's shipped cost references and their apply drawer: plain words only
// (no basis codes or internal ids), the reference beside the default with both money
// years, the source notes spelled out, the credit badge, confirmations only when the
// preview asks for them, the card before Apply, and an "applied" receipt.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../../store/uiStore'
import { libraryReferencesApi, type ReferencePreview, type ReferenceSummary } from '../../../api/libraryReferences'
import { networkApi } from '../../../api/network'
import { LibraryReferences } from './LibraryReferences'
import { ReferenceApplyDrawer } from './ReferenceApplyDrawer'
import { spellOutNotes } from './referenceWords'
import { expectAllButtonsNamed } from '../../../test-utils/accessibleName'

vi.mock('../../../api/libraryReferences', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/libraryReferences')>()
  return { ...actual, libraryReferencesApi: { list: vi.fn(), detail: vi.fn(), preview: vi.fn(), apply: vi.fn() } }
})
vi.mock('../../../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/network')>()
  return { ...actual, networkApi: { ...actual.networkApi, getGenerators: vi.fn(), getLinks: vi.fn() } }
})
const api = vi.mocked(libraryReferencesApi)
const net = vi.mocked(networkApi)

const PV: ReferenceSummary = {
  scope: 'common', reference_id: 'eu-dea-2026-08', technology: 'solar-utility', projection_year: 2030,
  currency: 'EUR', currency_year: 2025, capacity_basis: 'MW_e AC output', capacity_basis_code: 'output_electric_ac',
  includes_grid_connection: true, applicability: 'DEA Danish representative technology benchmark',
  publisher: 'Danish Energy Agency and Energinet', licence: 'CC-BY-4.0',
  attribution: 'Danish Energy Agency / Energinet technology catalogues, August 2026.', source_url: 'https://ens.dk/media/8615/download',
}
const VALUE = (value: number, unit: string, year: number | null, source: string, projection: number) => ({
  key: 'solar-utility.investment.overnight', label: 'Investment', parameter: 'overnight', value, unit,
  currency: year ? 'EUR' : null, currency_year: year, projection_year: projection, source,
})
const DETAIL = {
  scope: 'common' as const,
  reference: { ...PV, included_costs: 'Fixed-tilt total investment includes grid connection; FOM includes land rent.',
               unmapped_terms: ['Do not substitute DC module capacity for AC capacity', 'FOM excludes inverter replacement'],
               source_sheet: '22 Utility-scale PV', source_cells: {} },
  pack: { stamp: 'generic_defaults@2026-10-20:sha256:x', version: '2026-10-20', chosen_by: 'latest_generic' as const, note: null },
  comparison: [{ parameter: 'overnight', pack_stamp: 'p', differs: true,
                 reference: VALUE(556_220.85, 'EUR/MW', 2025, 'Danish Energy Agency / Energinet; 22 Utility-scale PV!E17', 2030),
                 pack: VALUE(482_478.5, 'EUR/MW', 2020, 'Danish Energy Agency, technology-data v0.14.0', 2030) }],
}
const ROWS = [{ field: 'overnight_cost', new: 556_220.85, differs: true,
  reference: { parameter: 'overnight', value: 556_220.85, unit: 'EUR/MW', currency_year: 2025, projection_year: 2030,
               source: 'Danish Energy Agency / Energinet; 22 Utility-scale PV!E17', cell: 'E17', label: 'Nominal investment' },
  pack: { key: 'solar-utility.investment.overnight', value: 482_478.5, unit: 'EUR/MW', currency_year: 2020,
          projection_year: 2030, source: 'technology-data v0.14.0' } }]
const NEEDS = [
  { confirmation: 'dc_ac' as const, text: 'This PV cost is per MW of AC output. Unconfirmed, nothing is applied.' },
  { confirmation: 'money_year' as const, text: 'x', disclosure: '2025 EUR price data, read as 2020 money: not adjusted for inflation.',
    read_as_source: 'the default it replaces (2020 EUR)' },
]
const CARD = { version: 1 as const, kind: 'parameter_edit', tool: 'apply_library_reference', says: 'pv overnight_cost', complete: true,
  digest: 'd1', rows: [{ component_class: 'Generator', name: 'pv', field: 'overnight_cost', label: 'Overnight cost',
                         old: 400_000, new: 556_220.85, unit: 'EUR/MW', role: 'typed' as const }] }
const preview = (over: Partial<ReferencePreview>): ReferencePreview => ({
  card: null, needs: NEEDS, missing: ['dc_ac', 'money_year'], notes: [], rows: ROWS as never, pack: DETAIL.pack,
  licence: 'CC-BY-4.0', attribution: PV.attribution, ...over })

function wrap(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

const INTERNAL = /output_electric|input_electric|eu-dea-|solar-utility|onwind|P2[a-d]|GS-|§/

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  api.list.mockResolvedValue([PV])
  api.detail.mockResolvedValue(DETAIL as never)
  net.getGenerators.mockResolvedValue([{ name: 'pv' }] as never)
  net.getLinks.mockResolvedValue([] as never)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('LibraryReferences', () => {
  it('lists references in plain words with the credit badge', async () => {
    const { container } = wrap(<LibraryReferences />)
    const item = await screen.findByRole('button', { name: 'Utility-scale PV, fixed tilt, 2030' })
    const list = screen.getByTestId('lib-reference-list')
    expect(list.textContent).toContain('2025 EUR per MW AC output')
    expect(list.textContent).toContain('Includes grid connection')
    expect(screen.getByTestId('lib-reference-badge').textContent).toBe('DEA · CC BY 4.0')
    fireEvent.click(item)
    const detail = await screen.findByTestId('lib-reference-detail')
    const row = within(screen.getByTestId('lib-reference-comparison')).getByText('Investment cost').closest('tr')!
    expect(row.getAttribute('data-differs')).toBe('true')
    expect(row.textContent).toContain('EUR/kW (2020 EUR)')
    expect(row.textContent).toContain('(2025 EUR)')
    const notes = screen.getByTestId('lib-reference-notes').textContent!
    expect(notes).toContain('fixed O&M (FOM) includes land rent')
    expect(notes.match(/fixed O&M \(FOM\)/g)).toHaveLength(1)
    expect(detail.textContent).not.toMatch(INTERNAL)
    expect(container.textContent).not.toMatch(INTERNAL)
    expectAllButtonsNamed(container)
  })

  it('opens the apply drawer for a chosen asset', async () => {
    api.preview.mockResolvedValue(preview({}))
    wrap(<LibraryReferences />)
    fireEvent.click(await screen.findByRole('button', { name: 'Utility-scale PV, fixed tilt, 2030' }))
    const pick = await screen.findByTestId('lib-reference-asset')
    await waitFor(() => expect(within(pick).getByRole('option', { name: 'pv' })).toBeTruthy())
    fireEvent.change(pick, { target: { value: 'pv' } })
    fireEvent.click(screen.getByTestId('lib-reference-use'))
    expect(await screen.findByTestId('ref-apply-drawer')).toBeTruthy()
    await waitFor(() => expect(api.preview).toHaveBeenCalledWith(expect.objectContaining({
      component_class: 'Generator', name: 'pv', technology: 'solar-utility', projection_year: 2030, confirm: [] })))
  })
})

describe('ReferenceApplyDrawer', () => {
  it('asks only for the confirmations the preview names, then shows the card and applies', async () => {
    api.preview.mockImplementation(async args => args.confirm.length === 2
      ? preview({ card: CARD as never, missing: [] }) : preview({}))
    api.apply.mockResolvedValue({ component_class: 'Generator', name: 'pv', written: { overnight_cost: 556_220.85 },
      source: 's', currency: 'EUR', currency_year: 2025, licence: 'CC-BY-4.0', attribution: PV.attribution, notes: [],
      receipt: { version: 1, digest: 'd1', outcome: 'verified', says: '', results_stale: true,
                 undo: { tool: 'undo_last', available: true },
                 rows: [{ ...CARD.rows[0], readback: 556_220.85 }] } } as never)
    const { container } = wrap(<ReferenceApplyDrawer open onClose={() => {}} componentClass="Generator" name="pv"
      technology="solar-utility" projectionYear={2030} />)
    expect(await screen.findByTestId('ref-apply-waiting')).toBeTruthy()
    expect(screen.queryByTestId('ref-apply-apply')).toBeNull()
    const needs = screen.getByTestId('ref-apply-needs')
    expect(needs.textContent).toContain('Use it as it is, without adjusting for inflation')
    expect(needs.textContent).toContain('2025 EUR price data, read as 2020 money: not adjusted for inflation.')
    expect(needs.textContent).toContain('Compared with the default it replaces (2020 EUR)')
    expect(screen.queryByTestId('ref-apply-confirm-grid_connection')).toBeNull()
    fireEvent.click(screen.getByTestId('ref-apply-confirm-dc_ac'))
    fireEvent.click(screen.getByTestId('ref-apply-confirm-money_year'))
    fireEvent.click(await screen.findByTestId('ref-apply-apply'))
    await waitFor(() => expect(api.apply).toHaveBeenCalledWith(expect.objectContaining({
      digest: 'd1', confirm: ['dc_ac', 'money_year'] })))
    const receipt = await screen.findByTestId('ref-apply-receipt')
    expect(receipt.textContent).toContain('Investment cost: applied. The project now shows')
    expect(receipt.textContent).not.toMatch(/verified/i)
    expect(container.textContent).not.toMatch(INTERNAL)
    expectAllButtonsNamed(container)
  })

  it('shows a refusal and applies nothing', async () => {
    api.preview.mockRejectedValue({ response: { data: { detail: {
      error_kind: 'currency_mismatch', message: 'This asset is priced from a GBP pack. Nothing is converted.' } } } })
    wrap(<ReferenceApplyDrawer open onClose={() => {}} componentClass="Generator" name="pv"
      technology="solar-utility" projectionYear={2030} />)
    expect((await screen.findByTestId('ref-apply-refused')).textContent).toContain('Nothing is converted')
    expect(api.apply).not.toHaveBeenCalled()
  })
})

describe('spellOutNotes', () => {
  it('spells out an abbreviation the first time only', () => {
    expect(spellOutNotes(['FOM a', 'FOM b', 'LHV c'])).toEqual(['fixed O&M (FOM) a', 'FOM b', 'lower heating value (LHV) c'])
  })
})
