// Issue template-e2e 20: Guided, Expert and the report show the same size for
// every asset the study built, on every template. The fixture is each
// template's sized rows and the report's Built and Allowed range cells for
// each, plus an `_edge` case of rows no template has
// (`backend/tests/test_sized_design_parity.py` checks the report side and
// rebuilds it).
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import type { EhReferenceDesignReport, EhSizedAsset } from '../../api/simulation'
import { SizedDesignTable } from './SizedDesignTable'
import fixture from './__fixtures__/sized_design_parity.json'
import { fmt1 } from './notCounted'

// No section 5 rows: each sized row carries its own unit.
vi.mock('../../hooks/useProjectFlow', () => ({ useProjectFlow: () => ({ flow: null }) }))

afterEach(cleanup)

type Left = [string, string][] | null
type Case = {
  assets: EhSizedAsset[]; built: Record<string, string>; range: Record<string, string>
  sizing?: Record<string, unknown>; not_counted?: Left
  sizing_cases?: { sizing: Record<string, unknown>; not_counted: Left }[]
}
const cases = Object.entries(fixture as unknown as Record<string, Case>)

const report = (assets: EhSizedAsset[], sizing: Record<string, unknown> = {}): EhReferenceDesignReport =>
  ({ sections: { sizing: { status: 'ok', payload: { ...sizing, assets } } } } as unknown as EhReferenceDesignReport)

/** The not-counted block's rows as shown, label then cell; null when absent. */
const leftShown = (): Left => {
  const block = screen.queryByTestId('t-not-counted')
  if (!block) return null
  return [...block.querySelectorAll('tbody tr')].map(r => {
    const [label, value] = [...r.querySelectorAll('td')].map(td => td.textContent ?? '')
    return [label, value]
  })
}

describe('SizedDesignTable matches the report on every template', () => {
  it('has a case per template', () => {
    expect(cases.length).toBeGreaterThanOrEqual(11)
    expect(fixture).toHaveProperty('_edge')
  })

  for (const [tid, c] of cases) {
    for (const mode of ['guided', 'expert'] as const) {
      it(`${tid}, ${mode}: every built asset, its report figure, per stage`, () => {
        render(<SizedDesignTable testId="t" mode={mode} report={report(c.assets, c.sizing)} />)
        const staged = c.assets.some(a => a.stage != null)
        expect(screen.getByTestId('t').querySelector('th')?.textContent).toBe(staged ? 'Period' : 'Asset')
        const shown: Record<string, string> = {}
        const ranges: Record<string, string> = {}
        for (const a of c.assets) {
          const cells = screen.getByTestId(`t-row-${a.name}`).querySelectorAll('td')
          shown[a.name] = cells[cells.length - 2].textContent ?? ''
          ranges[a.name] = cells[cells.length - 1].textContent ?? ''
          if (staged) expect(cells[0].textContent).toBe(String(a.stage))
        }
        expect(shown).toEqual(c.built)
        expect(ranges).toEqual(c.range)
        // Rows in the report's order: by stage.
        const order = [...screen.getByTestId('t').querySelectorAll('tbody tr[data-testid^="t-row-"]')]
          .map(r => r.getAttribute('data-testid')?.replace('t-row-', ''))
        expect(order).toEqual(Object.keys(c.built))
        // What the report leaves out of installed capacity, its own words.
        if (tid !== '_edge') {
          expect(c.not_counted?.length).toBeGreaterThan(0)
          expect(leftShown()).toEqual(c.not_counted)
        }
      })
    }
  }
})

describe('the not-counted rows match the report on every edge case', () => {
  const edge = (fixture as unknown as Record<string, Case>)._edge.sizing_cases ?? []

  it('has the edge cases', () => {
    expect(edge.length).toBeGreaterThanOrEqual(7)
    expect(edge.some(c => c.not_counted === null)).toBe(true)
  })

  edge.forEach((c, i) => {
    for (const mode of ['guided', 'expert'] as const) {
      it(`case ${i}, ${mode}`, () => {
        render(<SizedDesignTable testId="t" mode={mode} report={report([], c.sizing)} />)
        expect(leftShown()).toEqual(c.not_counted)
      })
    }
  })

  it('shows nothing for an old payload with no assets', () => {
    const { container } = render(<SizedDesignTable testId="t" mode="guided" report={report([], { total_p_nom_mw: 3 })} />)
    expect(container.innerHTML).toBe('')
  })

  it("rounds a one-digit tie to even, as Python's format does", () => {
    // f"{v:,.1f}" for each, read from CPython.
    expect([0.25, 0.75, -0.25, -0.75, 32.25, 1234.56, -0.04, 0.05].map(v => fmt1(v)))
      .toEqual(['0.2', '0.8', '-0.2', '-0.8', '32.2', '1,234.6', '-0.0', '0.1'])
    expect(fmt1(null, 'MW')).toBe('not established')
    expect(fmt1(Number.NaN, 'MW')).toBe('not established')
  })
})
