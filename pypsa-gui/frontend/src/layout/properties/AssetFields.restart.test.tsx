// Grid-to-process issue 12b-2: the ride-through curve and restart time show only on a process unit, with the
// restart time's unit beside its input.
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render as plainRender, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import fixture from '../../test/fixtures/assetSchema.json'
import type { AssetSchema } from '../../api/assetSchema'
import { AssetFieldsEdit } from './AssetFields'
import type { FS } from './cardKit'

const render = (ui: ReactElement) => plainRender(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>)
const SCHEMA = (fixture as Record<string, unknown>).Link as AssetSchema
const BASE: FS = { name: 'mill', bus0: 'mv', bus1: 'ore', p_nom: '5' }

function card(fs: FS) {
  render(<div className="grid grid-cols-2">
    <AssetFieldsEdit schema={SCHEMA} fs={fs} set={() => {}} current={fs} carriers={[]} buses={[]} />
  </div>)
}

afterEach(() => cleanup())

describe('the ride-through and restart fields', () => {
  it('stay hidden on a link that is not a process unit', () => {
    card(BASE)
    expect(screen.queryByText('Ride-through curve')).toBeNull()
    expect(screen.queryByText('Restart time (h)')).toBeNull()
  })

  it('show on a process unit, the restart time with its unit', () => {
    card({ ...BASE, eh_process: 'true' })
    expect(screen.getByText('Ride-through curve')).toBeTruthy()
    expect(screen.getByText('Restart time (h)')).toBeTruthy()
  })
})
