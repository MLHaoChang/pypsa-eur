// Proves the helper decides the cases a source scan cannot.
//
// Each block below is a pattern the static scanner in claude-skills either got
// wrong or had to report as undecidable. The point of the runtime check is
// that every one of them has a definite answer once rendered.
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { unnamedButtons, expectAllButtonsNamed } from './accessibleName'

function Svg() {
  return <svg aria-hidden="true" width="8" height="8" />
}

describe('unnamedButtons', () => {
  it('finds an icon-only button', () => {
    const { container } = render(<button onClick={() => {}}><Svg /></button>)
    expect(unnamedButtons(container)).toHaveLength(1)
  })

  it('finds an empty button', () => {
    const { container } = render(<button onClick={() => {}} />)
    expect(unnamedButtons(container)).toHaveLength(1)
  })

  it('accepts visible text', () => {
    const { container } = render(<button onClick={() => {}}>Save</button>)
    expect(unnamedButtons(container)).toHaveLength(0)
  })

  it('accepts a label starting with a symbol', () => {
    // The static scanner reported both of these as unnamed: its text pattern
    // required the first visible character to be [A-Za-z0-9].
    const { container } = render(
      <>
        <button onClick={() => {}}>↓ scroll to bottom</button>
        <button onClick={() => {}}>% of total</button>
      </>,
    )
    expect(unnamedButtons(container)).toHaveLength(0)
  })

  it('resolves an expression child that renders text', () => {
    // THE case source cannot decide — 55 of the scanner's 62 findings.
    const label = 'Run solve'
    const { container } = render(<button onClick={() => {}}>{label}</button>)
    expect(unnamedButtons(container)).toHaveLength(0)
  })

  it('resolves an expression child that renders an icon', () => {
    // Identical in source to the case above, opposite verdict at runtime.
    const label = <Svg />
    const { container } = render(<button onClick={() => {}}>{label}</button>)
    expect(unnamedButtons(container)).toHaveLength(1)
  })

  it('accepts aria-label on an icon-only button', () => {
    const { container } = render(
      <button aria-label="Close" onClick={() => {}}><Svg /></button>,
    )
    expect(unnamedButtons(container)).toHaveLength(0)
  })

  it('accepts title as a last-resort name', () => {
    // accname does fall back to title, so this is named — weakly, but named.
    // Worth pinning: it is why the scanner's 29 "weak" hits are not defects.
    const { container } = render(
      <button title="Close" onClick={() => {}}><Svg /></button>,
    )
    expect(unnamedButtons(container)).toHaveLength(0)
  })

  it('accepts a name composed from nested elements', () => {
    const { container } = render(
      <button onClick={() => {}}>
        <Svg />
        <span>Delete</span>
      </button>,
    )
    expect(unnamedButtons(container)).toHaveLength(0)
  })

  it('ignores buttons hidden from the accessibility tree', () => {
    const { container } = render(
      <div aria-hidden="true">
        <button onClick={() => {}}><Svg /></button>
      </div>,
    )
    expect(unnamedButtons(container)).toHaveLength(0)
  })

  it('reports every unnamed button, not just the first', () => {
    const { container } = render(
      <>
        <button onClick={() => {}}><Svg /></button>
        <button onClick={() => {}}>Named</button>
        <button onClick={() => {}}><Svg /></button>
      </>,
    )
    expect(unnamedButtons(container)).toHaveLength(2)
  })
})

describe('expectAllButtonsNamed', () => {
  it('passes when every button is named', () => {
    const { container } = render(<button onClick={() => {}}>Save</button>)
    expect(() => expectAllButtonsNamed(container)).not.toThrow()
  })

  it('throws naming the count', () => {
    const { container } = render(<button onClick={() => {}}><Svg /></button>)
    expect(() => expectAllButtonsNamed(container)).toThrow(/1 button\(s\) have no accessible name/)
  })

  it('identifies the offender well enough to find it', () => {
    const { container } = render(
      <button className="icon-btn danger" onClick={() => {}}><Svg /></button>,
    )
    expect(() => expectAllButtonsNamed(container)).toThrow(/icon-btn danger/)
  })
})
