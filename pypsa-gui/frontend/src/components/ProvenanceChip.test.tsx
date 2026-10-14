// U3 F part 2: the provenance chip and its hook. A recorded field shows its
// status and, on hover, its source; an unrecorded field shows nothing; the
// hook reads the asset's provenance under the class's network root, so the
// class's own invalidation refreshes it.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ProvenanceChip, provenanceTitle } from './ProvenanceChip'
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
    // A Bus has no asset schema (the S1c views, Line included, all do).
    render(<QueryClientProvider client={new QueryClient()}><Probe cls="Bus" name="b1" /></QueryClientProvider>)
    expect(provenance).not.toHaveBeenCalled()
    expect(screen.getByTestId('out').textContent).toBe('')
  })
})
