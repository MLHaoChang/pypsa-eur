import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeAll, describe, expect, it, vi } from 'vitest'

// jsdom reports 0 for every measured box; give the virtualiser a real viewport.
// Must run before the component import below executes any module-level code
// that could touch layout, and (more importantly) before any render() call.
beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight',
    { configurable: true, value: 600 })
  Object.defineProperty(HTMLElement.prototype, 'getBoundingClientRect',
    { configurable: true, value: () => ({ height: 600, width: 240, top: 0, left: 0,
      right: 240, bottom: 600, x: 0, y: 0, toJSON: () => ({}) }) })
})

import AssetPicker, { filterAssets, groupByClass } from './AssetPicker'
import type { AssetRef } from './types'

const A = (cls: string, name: string, carrier = ''): AssetRef =>
  ({ class: cls, name, carrier, bus: 'B1' })

const ASSETS = [
  A('Generator', 'Gas 1', 'gas'), A('Generator', 'Gas 2', 'gas'),
  A('Generator', 'Wind 1', 'onwind'), A('Line', 'L1'), A('Bus', 'B1'),
]

describe('filterAssets', () => {
  it('matches a case-insensitive substring of the name', () => {
    expect(filterAssets(ASSETS, 'gas').map(a => a.name)).toEqual(['Gas 1', 'Gas 2'])
  })
  it('also matches the carrier, so "onwind" finds the turbine', () => {
    expect(filterAssets(ASSETS, 'onwind').map(a => a.name)).toEqual(['Wind 1'])
  })
  it('returns everything for an empty query', () => {
    expect(filterAssets(ASSETS, '   ')).toHaveLength(5)
  })
})

describe('groupByClass', () => {
  it('groups in the canonical class order, skipping empty classes', () => {
    expect(groupByClass(ASSETS).map(([c, rows]) => [c, rows.length]))
      .toEqual([['Bus', 1], ['Generator', 3], ['Line', 1]])
  })
})

describe('AssetPicker', () => {
  it('reports the clicked asset', async () => {
    const onSelect = vi.fn()
    render(<AssetPicker assets={ASSETS} selected={null} onSelect={onSelect} />)
    await userEvent.click(screen.getByRole('button', { name: /Gas 2/ }))
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ name: 'Gas 2' }))
  })

  it('narrows the list as the user types', async () => {
    render(<AssetPicker assets={ASSETS} selected={null} onSelect={vi.fn()} />)
    await userEvent.type(screen.getByRole('searchbox'), 'wind')
    expect(screen.queryByRole('button', { name: /Gas 1/ })).toBeNull()
    expect(screen.getByRole('button', { name: /Wind 1/ })).toBeTruthy()
  })

  it('selects the first match on Enter', async () => {
    const onSelect = vi.fn()
    render(<AssetPicker assets={ASSETS} selected={null} onSelect={onSelect} />)
    await userEvent.type(screen.getByRole('searchbox'), 'gas{Enter}')
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ name: 'Gas 1' }))
  })

  it('marks the selected row as current', () => {
    render(<AssetPicker assets={ASSETS} selected={ASSETS[0]} onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: /Gas 1/ }))
      .toHaveProperty('ariaCurrent', 'true')
  })

  it('shows an empty state when nothing matches', async () => {
    render(<AssetPicker assets={ASSETS} selected={null} onSelect={vi.fn()} />)
    await userEvent.type(screen.getByRole('searchbox'), 'zzzz')
    expect(screen.getByText(/no assets match/i)).toBeTruthy()
  })
})
