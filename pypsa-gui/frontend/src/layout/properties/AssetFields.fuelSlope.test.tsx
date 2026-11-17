// Homer-parity 02i: a fuel curve slope in (0, 1) shows the units hint under its field, worded as the backend warning.
import { afterEach, expect, it } from 'vitest'
import { cleanup, render as plainRender, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import fixture from '../../test/fixtures/assetSchema.json'
import type { AssetSchema } from '../../api/assetSchema'
import { AssetFieldsEdit } from './AssetFields'
import type { FS } from './cardKit'
import { SLOPE_HINT_CASES } from './fuelSlopeHint'

const render = (ui: ReactElement) => plainRender(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>)
const SCHEMA = (fixture as Record<string, unknown>).Generator as AssetSchema

function card(slope: string) {
  const fs: FS = { name: 'genset', bus: 'b', carrier: 'diesel', p_nom: '1', fuel_curve_slope: slope }
  render(<div className="grid grid-cols-2">
    <AssetFieldsEdit schema={SCHEMA} fs={fs} set={() => {}} current={fs} carriers={[]} buses={[]} />
  </div>)
}

afterEach(() => cleanup())

it.each(SLOPE_HINT_CASES)('slope %s shows the backend wording', (slope, text) => {
  card(slope)
  expect(screen.getByTestId('asset-slope-hint').textContent).toBe(text)
})

it.each(['', '0', '1', '3.5'])('slope %j shows no hint', slope => {
  card(slope)
  expect(screen.queryByTestId('asset-slope-hint')).toBeNull()
})
