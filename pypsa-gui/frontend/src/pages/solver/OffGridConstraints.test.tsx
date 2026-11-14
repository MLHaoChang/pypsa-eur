// homer-parity 01a: the minimum renewable fraction field (GK B R11, UX U1–U4).
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { SolverConfig } from '../../api/types'
import {
  MIN_RENEWABLE_FRACTION_LABEL, OffGridConstraints, RESERVE_LABELS, renewableFractionFromText, shareFromText,
} from './OffGridConstraints'

const DRAFT = { min_renewable_fraction: null } as unknown as SolverConfig

function input(): HTMLInputElement {
  const span = screen.getByText(MIN_RENEWABLE_FRACTION_LABEL)
  const el = span.closest('label')?.querySelector('input')
  if (!el) throw new Error('no input under the label')
  return el as HTMLInputElement
}

function renderBlock(draft: SolverConfig = DRAFT) {
  const patch = vi.fn()
  render(<OffGridConstraints draft={draft} patch={patch} />)
  return patch
}

describe('Off-grid constraints — minimum renewable fraction', () => {
  it('sits under its block heading with its unit in the label', () => {
    renderBlock()
    expect(screen.getByText('Off-grid constraints')).toBeTruthy()
    expect(MIN_RENEWABLE_FRACTION_LABEL).toBe('Minimum renewable fraction (%)')
    expect(input().value).toBe('')
  })

  it('sends 80 % as 0.8', () => {
    const patch = renderBlock()
    fireEvent.change(input(), { target: { value: '80' } })
    expect(patch).toHaveBeenCalledWith({ min_renewable_fraction: 0.8 })
  })

  it('sends empty and 0 as null (off)', () => {
    const patch = renderBlock({ min_renewable_fraction: 0.5 } as unknown as SolverConfig)
    expect(input().value).toBe('50')
    fireEvent.change(input(), { target: { value: '' } })
    expect(patch).toHaveBeenLastCalledWith({ min_renewable_fraction: null })
    fireEvent.change(input(), { target: { value: '0' } })
    expect(patch).toHaveBeenLastCalledWith({ min_renewable_fraction: null })
  })

  it.each(['-5', '150'])('refuses %s with an inline sentence and patches nothing', bad => {
    const patch = renderBlock()
    fireEvent.change(input(), { target: { value: bad } })
    expect(patch).not.toHaveBeenCalled()
    const err = screen.getByTestId('min-renewable-fraction-error')
    expect(err.textContent).toMatch(/^Minimum renewable fraction: .*outside 0 to 100/)
    expect(input().value).toBe(bad)          // not silently clamped
  })

  it('refuses text that is not a number', () => {
    expect(renewableFractionFromText('abc')).toEqual({
      error: 'Minimum renewable fraction: enter a number from 0 to 100.' })
    expect(renewableFractionFromText(' 100 ')).toEqual({ value: 1 })
  })

  it('reports a refusal up so the page disables Save, and clears it on a valid entry', () => {
    // Typing 150 patches 0.01 and 0.15 on the way; Save must not then send 0.15.
    const patch = vi.fn()
    const onError = vi.fn()
    render(<OffGridConstraints draft={DRAFT} patch={patch} onError={onError} />)
    for (const v of ['1', '15', '150']) fireEvent.change(input(), { target: { value: v } })
    expect(onError).toHaveBeenLastCalledWith(expect.stringMatching(/outside 0 to 100/))
    fireEvent.change(input(), { target: { value: '80' } })
    expect(onError).toHaveBeenLastCalledWith(null)
    expect(patch).toHaveBeenLastCalledWith({ min_renewable_fraction: 0.8 })
  })

  it('refuses typed text that is not a number instead of turning the target off', () => {
    const patch = vi.fn()
    const onError = vi.fn()
    render(<OffGridConstraints draft={{ min_renewable_fraction: 0.8 } as unknown as SolverConfig}
      patch={patch} onError={onError} />)
    fireEvent.change(input(), { target: { value: '80e' } })
    expect(patch).not.toHaveBeenCalled()
    expect(input().value).toBe('80e')
    expect(screen.getByTestId('min-renewable-fraction-error').textContent)
      .toBe('Minimum renewable fraction: enter a number from 0 to 100.')
    expect(onError).toHaveBeenLastCalledWith(expect.stringMatching(/enter a number/))
  })

  it('never shows the engine key, and says 0 means no minimum', () => {
    renderBlock()
    const block = screen.getByTestId('offgrid-constraints')
    expect(block.textContent).not.toContain('min_renewable_fraction')
    expect(block.textContent).toContain('0 means no minimum')
  })
})

// homer-parity 01b-2: the operating reserve shares (UX 21 asks 1–2).
describe('Off-grid constraints — operating reserve', () => {
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
      expect(screen.getByTestId('offgrid-constraints').textContent).not.toContain(k)
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
    render(<OffGridConstraints draft={DRAFT} patch={patch} onError={onError} />)
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
    render(<OffGridConstraints draft={DRAFT} patch={vi.fn()} onError={onError} />)
    fireEvent.change(field(RESERVE_LABELS.reserve_share_pv), { target: { value: '-1' } })
    fireEvent.change(input(), { target: { value: '200' } })
    fireEvent.change(input(), { target: { value: '50' } })     // the PV refusal still holds
    expect(onError).toHaveBeenLastCalledWith(expect.stringMatching(/^Reserve for solar output: /))
    fireEvent.change(field(RESERVE_LABELS.reserve_share_pv), { target: { value: '20' } })
    expect(onError).toHaveBeenLastCalledWith(null)
  })

  it('shareFromText names the field it was given', () => {
    expect(shareFromText('Reserve for load', 'x')).toEqual({ error: 'Reserve for load: enter a number from 0 to 100.' })
    expect(shareFromText('Reserve for load', '12.5')).toEqual({ value: 0.125 })
  })
})
