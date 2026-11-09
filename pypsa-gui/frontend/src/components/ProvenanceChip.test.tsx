// U3 F part 2: the provenance chip and its hook. A recorded field shows its
// status and, on hover, its source; an unrecorded field shows nothing; the
// hook reads the asset's provenance under the class's network root, so the
// class's own invalidation refreshes it.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ProvenanceChip, provenanceTitle, staleNote } from './ProvenanceChip'
import { assetProvenanceKey, useAssetProvenance } from '../hooks/useAssetProvenance'
import { useUIStore } from '../store/uiStore'
import { invalidateAssetQueries } from '../utils/assetWrite'
import type { ProvenanceEntry } from '../api/assetSchema'

const provenance = vi.hoisted(() => vi.fn())
vi.mock('../api/assetSchema', async (orig) => {
  const actual = await orig<typeof import('../api/assetSchema')>()
  return { ...actual, assetSchemaApi: { ...actual.assetSchemaApi, provenance } }
})

const DEFAULT: ProvenanceEntry = { status: 'default', source: 'generic-defaults 1.0 abc123', source_year: 2020,
  currency_year: 2020, range: [100, 200], illustrative: true }

afterEach(() => { cleanup(); provenance.mockReset() })

describe('ProvenanceChip', () => {
  it('shows the status and names the source on hover', () => {
    render(<ProvenanceChip entry={DEFAULT} testId="c" />)
    const c = screen.getByTestId('c')
    expect(c.textContent).toContain('Default')
    expect(c.textContent).toContain('illustrative')
    expect(c.getAttribute('title')).toBe(provenanceTitle(DEFAULT))
    expect(c.getAttribute('title')).toContain('generic-defaults 1.0 abc123 (2020)')
  })

  it('a customised field says so, without the illustrative mark', () => {
    render(<ProvenanceChip entry={{ ...DEFAULT, status: 'customised', source: 'quote' }} testId="c" />)
    expect(screen.getByTestId('c').textContent).toBe('Customised')
    expect(screen.getByTestId('c').getAttribute('data-status')).toBe('customised')
  })

  it('an unrecorded field shows nothing: unknown is not default', () => {
    const { container } = render(<ProvenanceChip entry={undefined} testId="c" />)
    expect(container.innerHTML).toBe('')
  })
})

describe('useAssetProvenance', () => {
  function Probe({ cls, name }: { cls: string; name: string }) {
    const p = useAssetProvenance(cls, name)
    return <span data-testid="out">{Object.keys(p).join(',')}</span>
  }

  it('reads the asset under its class root, so the class invalidation refreshes it', async () => {
    useUIStore.setState({ currentProject: 'P' })
    provenance.mockResolvedValue({ component: 'StorageUnit', name: 'battery', fields: { inv_power_overnight: DEFAULT } })
    const qc = new QueryClient()
    render(<QueryClientProvider client={qc}><Probe cls="StorageUnit" name="battery" /></QueryClientProvider>)
    await waitFor(() => expect(screen.getByTestId('out').textContent).toBe('inv_power_overnight'))
    expect(provenance).toHaveBeenCalledWith('StorageUnit', 'battery')
    expect(assetProvenanceKey('P', 'StorageUnit', 'battery').slice(0, 2)).toEqual(['storage_units', 'P'])
    await qc.invalidateQueries({ queryKey: ['storage_units', 'P'] })
    await waitFor(() => expect(provenance).toHaveBeenCalledTimes(2))
    // Every asset save (and rename) goes through `updateAsset`, which calls
    // this blanket invalidation: the provenance refreshes with it.
    invalidateAssetQueries(qc, 'P')
    await waitFor(() => expect(provenance).toHaveBeenCalledTimes(3))
  })

  it('asks nothing for a class without a schema', () => {
    // A SubNetwork has no schema (every editable class has one since M5, the Bus included).
    render(<QueryClientProvider client={new QueryClient()}><Probe cls="SubNetwork" name="b1" /></QueryClientProvider>)
    expect(provenance).not.toHaveBeenCalled()
    expect(screen.getByTestId('out').textContent).toBe('')
  })
})

describe('out of date (TCU-05)', () => {
  const stale = (s: Partial<NonNullable<ProvenanceEntry['stale']>>): ProvenanceEntry => ({
    ...DEFAULT, status: 'customised', stale: { field: 'dc_ac_ratio', recorded: 1.3, current_field: 'dc_ac_ratio',
      current: 1.5, reason: 'scale input changed', ...s } })

  it('adds an out-of-date chip beside the status chip, never the word stale', () => {
    render(<ProvenanceChip entry={stale({})} testId="c" />)
    expect(screen.getByTestId('c').textContent).toContain('Customised')
    expect(screen.getByTestId('c-stale').textContent).toBe('out of date')
    expect(document.body.textContent).not.toMatch(/stale/i)
  })

  it('shows no chip for a fresh cost', () => {
    render(<ProvenanceChip entry={DEFAULT} testId="c" />)
    expect(screen.queryByTestId('c-stale')).toBeNull()
  })

  it('says what changed in the panel words', () => {
    expect(staleNote(stale({}), 'card')).toBe(
      'Out of date: DC/AC ratio changed. Priced with DC/AC ratio 1.3, now 1.5. Compare with references to re-price.')
    expect(staleNote(stale({ field: 'efficiency', current_field: 'efficiency', recorded: 0.9, current: 0.8 }), 'table'))
      .toBe('Out of date: Efficiency changed. Priced with Efficiency 0.9, now 0.8. '
        + 'Open the asset and compare with references to re-price.')
  })

  it('names a field that is gone, an unrecorded input and a malformed record', () => {
    expect(staleNote(stale({ current: null, current_field: null, reason: 'cannot be priced now: x' }), 'card'))
      .toContain('Out of date: DC/AC ratio is no longer available. Priced with DC/AC ratio 1.3.')
    const unrecorded = staleNote(stale({ field: null, recorded: null, current_field: null, current: null,
      reason: 'scale input not recorded (the applied reference is from another release)' }), 'card')
    expect(unrecorded).toContain('the input it was priced with was not recorded (the applied reference is from another release)')
    expect(staleNote(stale({ field: null, recorded: null, current_field: null, current: null,
      reason: 'scale input record malformed' }), 'card')).toContain('its pricing record is unreadable')
    expect(staleNote(DEFAULT, 'card')).toBeNull()
  })
})
