// homer-parity 01b-2: the operating reserve shares, under Solver settings ›
// Dispatch since control-strategy walkthrough 03 (moved from Network).
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { SolverConfig } from '../../api/types'
import { RESERVE_LABELS, shareFromText } from './OffGridConstraints'
import { OperatingReserveBlock } from './OperatingReserveBlock'

const DRAFT = { reserve_share_load: null, reserve_share_pv: null, reserve_share_wind: null } as unknown as SolverConfig

function renderBlock(draft: SolverConfig = DRAFT) {
  const patch = vi.fn()
  render(<OperatingReserveBlock draft={draft} patch={patch} />)
  return patch
}

it('carries the anchor a link scrolls to', () => {
  renderBlock()
  expect(screen.getByTestId('operating-reserve').id).toBe('solver-operating-reserve')
})

describe('Operating reserve block (Solver settings › Dispatch)', () => {
  const KEYS = Object.keys(RESERVE_LABELS) as (keyof typeof RESERVE_LABELS)[]

  function field(label: string): HTMLInputElement {
    const el = screen.getByText(label).closest('label')?.querySelector('input')
    if (!el) throw new Error(`no input under ${label}`)
    return el as HTMLInputElement
  }

  it('shows the three labels with their unit, plain words and no engine key', () => {
    renderBlock()
    const block = screen.getByTestId('operating-reserve')
    expect(block.textContent).toContain('Operating reserve')
    expect(block.textContent).toContain('0 means no reserve')
    expect(block.textContent).toContain('keep this much spare power every hour')
    expect(Object.values(RESERVE_LABELS)).toEqual([
      'Reserve for load (%)', 'Reserve for solar output (%)', 'Reserve for wind output (%)'])
    for (const k of KEYS) {
      expect(field(RESERVE_LABELS[k]).value).toBe('')
      expect(block.textContent).not.toContain(k)
    }
  })

  it.each(KEYS)('sends %s 10 % as 0.1 and empty or 0 as null', k => {
    const patch = renderBlock({ [k]: 0.25 } as unknown as SolverConfig)
    expect(field(RESERVE_LABELS[k]).value).toBe('25')
    fireEvent.change(field(RESERVE_LABELS[k]), { target: { value: '10' } })
    expect(patch).toHaveBeenLastCalledWith({ [k]: 0.1 })
    fireEvent.change(field(RESERVE_LABELS[k]), { target: { value: '0' } })
    expect(patch).toHaveBeenLastCalledWith({ [k]: null })
    fireEvent.change(field(RESERVE_LABELS[k]), { target: { value: '' } })
    expect(patch).toHaveBeenLastCalledWith({ [k]: null })
  })

  it.each(KEYS)('refuses %s out of range or text with a sentence opening with its label', k => {
    const patch = vi.fn()
    const onError = vi.fn()
    render(<OperatingReserveBlock draft={DRAFT} patch={patch} onError={onError} />)
    const name = RESERVE_LABELS[k].replace(' (%)', '')
    fireEvent.change(field(RESERVE_LABELS[k]), { target: { value: '150' } })
    const err = screen.getByTestId(`${k.replace(/_/g, '-')}-error`)
    expect(err.textContent).toBe(`${name}: 150 % is outside 0 to 100.`)
    expect(field(RESERVE_LABELS[k]).value).toBe('150')     // not silently clamped
    fireEvent.change(field(RESERVE_LABELS[k]), { target: { value: 'ten' } })
    expect(screen.getByTestId(`${k.replace(/_/g, '-')}-error`).textContent)
      .toBe(`${name}: enter a number from 0 to 100.`)
    expect(patch).not.toHaveBeenCalled()
    expect(onError).toHaveBeenLastCalledWith(`${name}: enter a number from 0 to 100.`)
  })

  it('keeps Save blocked while any field of the block refuses, and clears when all are valid', () => {
    const onError = vi.fn()
    render(<OperatingReserveBlock draft={DRAFT} patch={vi.fn()} onError={onError} />)
    fireEvent.change(field(RESERVE_LABELS.reserve_share_pv), { target: { value: '-1' } })
    fireEvent.change(field(RESERVE_LABELS.reserve_share_load), { target: { value: '200' } })
    fireEvent.change(field(RESERVE_LABELS.reserve_share_load), { target: { value: '50' } })  // the PV refusal still holds
    expect(onError).toHaveBeenLastCalledWith(expect.stringMatching(/^Reserve for solar output: /))
    fireEvent.change(field(RESERVE_LABELS.reserve_share_pv), { target: { value: '20' } })
    expect(onError).toHaveBeenLastCalledWith(null)
  })

  it('shareFromText names the field it was given', () => {
    expect(shareFromText('Reserve for load', 'x')).toEqual({ error: 'Reserve for load: enter a number from 0 to 100.' })
    expect(shareFromText('Reserve for load', '12.5')).toEqual({ value: 0.125 })
  })
})
