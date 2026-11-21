// The capacity-bounds editor's empty state (expert-catalogue 02c, RAW-NAMES-1):
// it names the Extendable setting with the Properties panel's own word, taken
// from that panel's word map, and shows no model name.
import fs from 'node:fs'
import path from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  const empty = vi.fn(async () => [])
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getGenerators: empty, getStorageUnits: empty, getStores: empty,
      getLinks: empty, getLines: empty, getTransformers: empty,
      listVintageBounds: vi.fn(async () => ({ bounds: {} })),
      getInvestmentPeriods: vi.fn(async () => ({ periods: [] })),
    },
  }
})

import CapacityBoundsEditor, { EXTENDABLE_LABEL } from './CapacityBoundsEditor'
import { FIELD_MAP } from '../layout/CreationForm'

afterEach(cleanup)

describe('CapacityBoundsEditor empty state', () => {
  it('uses the Properties panel word for Extendable, from its word map', () => {
    const ext = Object.values(FIELD_MAP).flat().filter(f => /^(p|e)_nom_extendable$/.test(f.key))
    expect(ext.length).toBeGreaterThan(5)
    for (const f of ext) expect(f.label, f.key).toBe(EXTENDABLE_LABEL)
    // The edit form takes its label from the asset schema: the same word.
    const fields = fs.readFileSync(path.resolve(__dirname, '../../../backend/services/asset_schema/fields.py'), 'utf8')
    const schema = [...fields.matchAll(/FieldSpec\("[pse]_nom_extendable", "size", "([^"]+)"/g)].map(m => m[1])
    expect(schema.length).toBeGreaterThanOrEqual(3)
    for (const label of schema) expect(label).toBe(EXTENDABLE_LABEL)
  })

  it('says the cause first, in plain words, with no model name and no link', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><CapacityBoundsEditor /></QueryClientProvider>)
    const empty = await screen.findByTestId('capacity-bounds-empty')
    expect(empty.textContent!.replace(/\s+/g, ' ').trim()).toBe(
      `No asset in this network is sized by the optimiser. Tick “${EXTENDABLE_LABEL}” on an asset in the Properties panel to list it here.`)
    expect(empty.textContent).not.toMatch(/_nom|=True|›/)
    expect(empty.querySelector('a, button, code')).toBeNull()
  })
})
