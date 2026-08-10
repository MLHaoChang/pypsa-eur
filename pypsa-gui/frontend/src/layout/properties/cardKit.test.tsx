// Characterization of the cardKit primitives spec D20 depends on, written
// BEFORE extrasPatch and ExtrasSection are added beside them. cardKit.tsx has
// 33 exports and zero tests today.
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { EditShell, nf, ni, no, toFS } from './cardKit'

describe('toFS — the form-state seed', () => {
  it('stringifies the requested keys', () => {
    expect(toFS({ a: 1, b: 'x' }, ['a', 'b'])).toEqual({ a: '1', b: 'x' })
  })

  it('maps null and undefined to an empty string', () => {
    expect(toFS({ a: null, b: undefined }, ['a', 'b'])).toEqual({ a: '', b: '' })
  })

  it('maps a non-finite number to an empty string, so inf renders blank', () => {
    expect(toFS({ a: Infinity, b: NaN }, ['a', 'b'])).toEqual({ a: '', b: '' })
  })

  it('renders booleans as the strings the cards compare against', () => {
    expect(toFS({ a: true, b: false }, ['a', 'b'])).toEqual({ a: 'true', b: 'false' })
  })

  it('includes only the requested keys — this is what extras widen', () => {
    expect(toFS({ a: 1, b: 2 }, ['a'])).toEqual({ a: '1' })
  })
})

describe('nf / ni / no — the payload readers', () => {
  it('nf falls back when the value is not a number', () => {
    expect(nf({ a: '5.5' }, 'a', 1)).toBe(5.5)
    expect(nf({ a: '' }, 'a', 1)).toBe(1)
  })

  it('ni parses an integer', () => {
    expect(ni({ a: '7' }, 'a', 1)).toBe(7)
    expect(ni({ a: 'x' }, 'a', 1)).toBe(1)
  })

  it('no returns null for a blank, which is how a bound is cleared', () => {
    expect(no({ a: '' }, 'a')).toBe(null)
    expect(no({ a: '  ' }, 'a')).toBe(null)
    expect(no({ a: '3' }, 'a')).toBe(3)
  })
})

describe('EditShell — the render seam D20 depends on', () => {
  it('renders arbitrary children', () => {
    render(
      <EditShell title="T" onSave={() => {}} onCancel={() => {}} saving={false}>
        <div data-testid="child">hello</div>
      </EditShell>,
    )
    expect(screen.getByTestId('child').textContent).toBe('hello')
  })

  it('keeps the Save and Cancel footer alongside the children', () => {
    render(
      <EditShell title="T" onSave={() => {}} onCancel={() => {}} saving={false}>
        <div data-testid="child">hello</div>
      </EditShell>,
    )
    expect(screen.getByRole('button', { name: /^save$/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /^cancel$/i })).toBeTruthy()
  })

  it('calls onSave', async () => {
    const onSave = vi.fn()
    render(
      <EditShell title="T" onSave={onSave} onCancel={() => {}} saving={false}>
        <div />
      </EditShell>,
    )
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    expect(onSave).toHaveBeenCalled()
  })

  it('disables Save while saving', () => {
    render(
      <EditShell title="T" onSave={() => {}} onCancel={() => {}} saving={true}>
        <div />
      </EditShell>,
    )
    expect((screen.getByRole('button', { name: /saving/i }) as HTMLButtonElement).disabled)
      .toBe(true)
  })
})
