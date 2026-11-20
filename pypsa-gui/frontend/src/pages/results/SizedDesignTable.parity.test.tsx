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

// No section 5 rows: each sized row carries its own unit.
vi.mock('../../hooks/useProjectFlow', () => ({ useProjectFlow: () => ({ flow: null }) }))

afterEach(cleanup)

type Case = { assets: EhSizedAsset[]; built: Record<string, string>; range: Record<string, string> }
const cases = Object.entries(fixture as unknown as Record<string, Case>)

const report = (assets: EhSizedAsset[]): EhReferenceDesignReport =>
  ({ sections: { sizing: { status: 'ok', payload: { assets } } } } as unknown as EhReferenceDesignReport)

describe('SizedDesignTable matches the report on every template', () => {
  it('has a case per template', () => {
    expect(cases.length).toBeGreaterThanOrEqual(11)
    expect(fixture).toHaveProperty('_edge')
  })

  for (const [tid, c] of cases) {
    for (const mode of ['guided', 'expert'] as const) {
      it(`${tid}, ${mode}: every built asset, its report figure, per stage`, () => {
        render(<SizedDesignTable testId="t" mode={mode} report={report(c.assets)} />)
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
        const order = [...screen.getByTestId('t').querySelectorAll('tbody tr')]
          .map(r => r.getAttribute('data-testid')?.replace('t-row-', ''))
        expect(order).toEqual(Object.keys(c.built))
      })
    }
  }
})
