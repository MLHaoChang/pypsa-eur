// homer-parity 01a: the minimum renewable fraction field (GK B R11, UX U1–U4).
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { SolverConfig } from '../../api/types'
import {
  MIN_RENEWABLE_FRACTION_LABEL, OffGridConstraints, renewableFractionFromText,
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
