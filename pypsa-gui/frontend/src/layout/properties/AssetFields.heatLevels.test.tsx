// Grid-to-process issue 15: the heat and steam level rows. The Bus card shows the supply temperature on a heat bus
// and the header pressure on a steam bus; the Link card shows a device's reach where one of its buses carries heat
// or steam. A row that holds a value always shows. An empty reach says "No limit" in text (UX: never a placeholder
// only).
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render as plainRender, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import fixture from '../../test/fixtures/assetSchema.json'
import type { AssetSchema } from '../../api/assetSchema'
import { hiddenLevelFields } from '../../utils/busCarriers'
import { AssetFieldsEdit } from './AssetFields'
import type { FS } from './cardKit'

const render = (ui: ReactElement) => plainRender(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>)
const BUS = (fixture as Record<string, unknown>).Bus as AssetSchema
const LINK = (fixture as Record<string, unknown>).Link as AssetSchema

function busCard(fs: FS) {
  render(<div className="grid grid-cols-2">
    <AssetFieldsEdit schema={BUS} fs={fs} set={() => {}} current={fs} carriers={[]} buses={[]}
      hidden={hiddenLevelFields([fs.carrier ?? ''], fs, 'temperature_c', 'pressure_bar')} />
  </div>)
}

function linkCard(fs: FS, busCarriers: string[]) {
  render(<div className="grid grid-cols-2">
    <AssetFieldsEdit schema={LINK} fs={fs} set={() => {}} current={fs} carriers={[]} buses={[]}
      hidden={hiddenLevelFields(busCarriers, fs, 'max_supply_temp_c', 'max_pressure_bar')} />
  </div>)
}

afterEach(() => cleanup())

describe('hiddenLevelFields', () => {
  it('shows the heat row for any carrier naming heat and the steam row for steam', () => {
    expect([...hiddenLevelFields(['urban central heat'], {}, 'h', 's')]).toEqual(['s'])
    expect([...hiddenLevelFields(['Steam'], {}, 'h', 's')]).toEqual(['h'])
    expect([...hiddenLevelFields(['AC', 'gas'], {}, 'h', 's')]).toEqual(['h', 's'])
  })

  it('never hides a row that holds a value', () => {
    expect([...hiddenLevelFields(['AC'], { h: '90', s: 'nan' }, 'h', 's')]).toEqual(['s'])
  })
})

describe('the Bus level rows', () => {
  it('show the supply temperature on a heat bus, not the pressure', () => {
    busCard({ name: 'b', carrier: 'heat' })
    expect(screen.getByText('Supply temperature (°C)')).toBeTruthy()
    expect(screen.queryByText('Header pressure (barg)')).toBeNull()
  })

  it('show the header pressure on a steam bus', () => {
    busCard({ name: 'b', carrier: 'steam' })
    expect(screen.getByText('Header pressure (barg)')).toBeTruthy()
    expect(screen.queryByText('Supply temperature (°C)')).toBeNull()
  })

  it('hide both on an electrical bus, unless one is set', () => {
    busCard({ name: 'b', carrier: 'AC' })
    expect(screen.queryByText('Supply temperature (°C)')).toBeNull()
    cleanup()
    busCard({ name: 'b', carrier: 'AC', temperature_c: '120' })
    expect(screen.getByText('Supply temperature (°C)')).toBeTruthy()
  })
})

describe('the Link reach rows', () => {
  const BASE: FS = { name: 'hp', bus0: 'grid', bus1: 'heat low', p_nom: '5' }

  it('show the max supply temperature where a bus carries heat, with No limit when empty', () => {
    linkCard(BASE, ['AC', 'heat'])
    expect(screen.getByText('Max supply temperature (°C)')).toBeTruthy()
    expect(screen.getByTestId('asset-no-limit-max_supply_temp_c').textContent).toBe('No limit')
    expect(screen.queryByText('Max steam pressure (barg)')).toBeNull()
  })

  it('drop No limit once a reach is set', () => {
    linkCard({ ...BASE, max_supply_temp_c: '120' }, ['AC', 'heat'])
    expect(screen.queryByTestId('asset-no-limit-max_supply_temp_c')).toBeNull()
  })

  it('stay hidden on a link between electrical and gas buses', () => {
    linkCard(BASE, ['AC', 'gas'])
    expect(screen.queryByText('Max supply temperature (°C)')).toBeNull()
    expect(screen.queryByText('Max steam pressure (barg)')).toBeNull()
  })
})
