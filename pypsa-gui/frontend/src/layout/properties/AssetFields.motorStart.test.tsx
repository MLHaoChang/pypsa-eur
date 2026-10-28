// Grid-to-process issue 18: the motor-start fields show only on a process unit, with units beside the inputs,
// and an empty Start current multiple says in text which default it stands for (UX: never a placeholder only).
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

describe('the motor-start fields', () => {
  it('stay hidden on a link that is not a process unit', () => {
    card(BASE)
    expect(screen.queryByText('Motor rating (kW)')).toBeNull()
    expect(screen.queryByTestId('asset-start-multiple-default')).toBeNull()
  })

  it('show with their units on a process unit', () => {
    card({ ...BASE, eh_process: 'true' })
    expect(screen.getByText('Motor rating (kW)')).toBeTruthy()
    expect(screen.getByText('Start method')).toBeTruthy()
    expect(screen.getByText('Start current multiple (× rated)')).toBeTruthy()
  })

  it.each([
    ['soft', 'Default for Soft starter: 3 × rated'],
    ['dol', 'Default for Direct on line: 6 × rated'],
    ['vfd', 'Default for Variable-speed drive: 1 × rated'],
    ['', 'Default by start method: Direct on line 6, Soft starter 3, Variable-speed drive 1 × rated'],
  ])('an empty override with method %j says its default', (method, text) => {
    card({ ...BASE, eh_process: 'true', start_method: method })
    expect(screen.getByTestId('asset-start-multiple-default').textContent).toBe(text)
  })

  it('an override set hides the default', () => {
    card({ ...BASE, eh_process: 'true', start_method: 'dol', i_start_pu: '7' })
    expect(screen.queryByTestId('asset-start-multiple-default')).toBeNull()
  })
})
