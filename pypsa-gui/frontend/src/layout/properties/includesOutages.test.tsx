// Phase 12h — `IncludesOutagesInput` is its own component, and that is the
// point of it.
//
// The obvious implementation was a line inside `OutageInputs`. That component
// is shared by FIVE asset cards (Generator, Link, StorageUnit, Store, Line),
// and `p_max_pu_includes_outages` is a Generator column only: the finding it
// exists for is about generators, and the other occurrence-bearing classes
// carry no availability the engines read this way. Putting it there would
// have written a column onto four classes that have no use for it, and every
// one of those PUTs would be a 422 from a schema that does not declare it.
//
// So the two components are tested apart: the flag renders from its own, and
// the shared one still renders the occurrence trio and nothing else.
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { IncludesOutagesInput, OutageInputs } from './cardKit'

type FS = Record<string, string>

function Harness({ initial, onChange }: {
  initial: FS
  onChange: (next: FS) => void
}) {
  // `set` takes an updater exactly as the panel's `setForm` does, so the
  // checkbox's `set(p => ...)` call shape is exercised, not simulated.
  const set = (fn: (p: FS) => FS) => onChange(fn(initial))
  return <IncludesOutagesInput fs={initial} set={set as never} />
}

describe('IncludesOutagesInput', () => {
  it('renders unchecked for a clear flag', () => {
    render(<Harness initial={{}} onChange={() => {}} />)
    const box = screen.getByRole('checkbox') as HTMLInputElement
    expect(box.checked).toBe(false)
  })

  it("renders checked for the form's 'true' string", () => {
    render(<Harness initial={{ p_max_pu_includes_outages: 'true' }} onChange={() => {}} />)
    const box = screen.getByRole('checkbox') as HTMLInputElement
    expect(box.checked).toBe(true)
  })

  it('writes the form key on toggle', async () => {
    const onChange = vi.fn()
    render(<Harness initial={{}} onChange={onChange} />)
    await userEvent.click(screen.getByRole('checkbox'))
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange.mock.calls[0][0].p_max_pu_includes_outages).toBe('true')
  })

  it('clears the form key on untoggle', async () => {
    const onChange = vi.fn()
    render(<Harness initial={{ p_max_pu_includes_outages: 'true' }} onChange={onChange} />)
    await userEvent.click(screen.getByRole('checkbox'))
    expect(onChange.mock.calls[0][0].p_max_pu_includes_outages).toBe('false')
  })
})

describe('OutageInputs is shared by five cards and stays as it is', () => {
  it('renders the occurrence trio and NO flag checkbox', () => {
    // The bite: fold the flag into `OutageInputs`. Link, StorageUnit, Store
    // and Line all render this component, and none of them has the column.
    render(<OutageInputs fs={{}} set={(() => {}) as never} />)
    expect(screen.queryByRole('checkbox')).toBeNull()
    expect(screen.getAllByRole('spinbutton').length).toBe(2)   // rate, MTTR
    expect(screen.getByRole('combobox')).toBeDefined()         // rate basis
  })
})
