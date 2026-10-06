import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { NumInput } from './NumInput'

function Harness({ onValue }: { onValue: (v: number | null) => void }) {
  const [v, setV] = useState<number | null>(null)
  return <NumInput aria-label="rate" value={v} onChange={x => { setV(x); onValue(x) }} />
}

describe('NumInput (WP4.7 review B1)', () => {
  it('keeps the typed text: "0.0" stays while typing 0.07, never read as 7', () => {
    const seen: (number | null)[] = []
    render(<Harness onValue={v => seen.push(v)} />)
    const el = screen.getByLabelText('rate') as HTMLInputElement
    // jsdom sanitises an incomplete "0." to ''; the reset under test is "0.0" → "0".
    for (const t of ['0', '0.0', '0.07']) {
      fireEvent.change(el, { target: { value: t } })
      expect(el.value).toBe(t)
    }
    expect(seen.at(-1)).toBe(0.07)
    fireEvent.change(el, { target: { value: '1.10' } })
    expect(el.value).toBe('1.10')
  })

  it('empty is null, never 0; an outside value re-syncs', () => {
    const onChange = vi.fn()
    const { rerender } = render(<NumInput aria-label="r" value={0.5} onChange={onChange} />)
    const el = screen.getByLabelText('r') as HTMLInputElement
    fireEvent.change(el, { target: { value: '' } })
    expect(onChange).toHaveBeenLastCalledWith(null)
    rerender(<NumInput aria-label="r" value={0.25} onChange={onChange} />)
    expect(el.value).toBe('0.25')
  })
})
