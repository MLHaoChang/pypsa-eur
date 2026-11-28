// Issue template-e2e 20: Guided, Expert and the report show the same size for
// every asset the study built, on every template. The fixture is each
// template's sized rows and the report's Built and Allowed range cells for
// each, plus an `_edge` case of rows no template has
// (`backend/tests/test_sized_design_parity.py` checks the report side and
// rebuilds it).
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { EhReferenceDesignReport, EhSizedAsset } from '../../api/simulation'
import { SizedDesignTable } from './SizedDesignTable'
import fixture from './__fixtures__/sized_design_parity.json'
import { GRID_ROW as GRID, fmt1 } from './notCounted'

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

  it('an old payload with assets shows the table and no not-counted block', () => {
    const assets = (fixture as unknown as Record<string, Case>).eh_datacenter.assets
    render(<SizedDesignTable testId="t" mode="expert" report={report(assets, { total_p_nom_mw: 3 })} />)
    expect(screen.getByTestId(`t-row-${assets[0].name}`)).toBeTruthy()
    expect(screen.queryByTestId('t-not-counted')).toBeNull()
  })

  it("rounds a one-digit tie to even, as Python's format does", () => {
    // f"{v:,.1f}" for each, read from CPython.
    const vs = [0.25, 0.75, -0.25, -0.75, 32.25, 1234.56, -0.04, 0.05, 0.15, 0.35, 1000.05, -1234567.85, 999.95]
    expect(vs.map(v => fmt1(v)))
      .toEqual(['0.2', '0.8', '-0.2', '-0.8', '32.2', '1,234.6', '-0.0', '0.1', '0.1', '0.3', '1,000.0', '-1,234,567.9', '1,000.0'])
    expect(fmt1(null, 'MW')).toBe('not established')
    expect(fmt1(Number.NaN, 'MW')).toBe('not established')
  })
})

describe('the not-counted block reads at a glance (UX 34)', () => {
  const slack = (i: number) => ({ name: `__lost_${i}`, process_role: 'shortfall', carrier: `P${i}`,
    p_nom: 1, energy: 2, unit: 't/h', energy_unit: 't' })
  const base = { grid_connection_mw: 40, grid_import_peak_mw: 20, connection_check: 'within' }

  it('warn-colours the connection row only when it exceeds the limit, with the word shown', () => {
    render(<SizedDesignTable testId="t" mode="guided"
      report={report([], { ...base, grid_import_peak_mw: 42.8, connection_check: 'exceeds' })} />)
    const value = screen.getByTestId('t-not-counted-0').querySelectorAll('td')[1]
    expect(value.className).toContain('text-warn')
    expect(value.textContent).toBe('40.0 MW · peak import 42.8 MW · exceeds the limit by 2.8 MW')
    cleanup()
    render(<SizedDesignTable testId="t" mode="guided" report={report([], base)} />)
    expect(screen.getByTestId('t-not-counted-0').querySelectorAll('td')[1].className).not.toContain('text-warn')
  })

  it('folds more than five shortfall rows behind "Show all", keeping the rest', () => {
    const p = { ...base, product_slacks: [1, 2, 3, 4, 5, 6, 7].map(slack),
      grid_supply_counted: ['g is counted: bus grid also holds load L'], horizon: '8,760 h' }
    render(<SizedDesignTable testId="t" mode="expert" report={report([], p)} />)
    const labels = () => leftShown()?.map(r => r[0]) ?? []
    expect(labels()).toEqual([GRID, ...[1, 2, 3, 4, 5].map(i => `Shortfall, P${i} (not counted)`), 'Grid supply counted'])
    fireEvent.click(screen.getByTestId('t-not-counted-more'))
    expect(screen.getByTestId('t').textContent).not.toContain('Show all')
    expect(labels()).toHaveLength(9)
    expect(leftShown()?.[7][1]).toBe('1.0 t/h · 2.0 t over the horizon (8,760 h)')
  })

  it('five shortfall rows show without a fold', () => {
    render(<SizedDesignTable testId="t" mode="guided"
      report={report([], { ...base, product_slacks: [1, 2, 3, 4, 5].map(slack) })} />)
    expect(screen.queryByTestId('t-not-counted-more')).toBeNull()
    expect(leftShown()).toHaveLength(6)
  })
})

describe('the not-counted cells set figures apart from sentences (UX 34)', () => {
  const cellOf = (i: number) => screen.getByTestId(`t-not-counted-${i}`).querySelectorAll('td')[1]

  it('a refusal and a counted reason are sentences; a limit and a slack are figures', () => {
    render(<SizedDesignTable testId="t" mode="expert" report={report([], {
      grid_connection_mw: null, grid_connection_refusal: 'No link is marked as the grid import.',
      product_slacks: [{ name: 's', process_role: 'shortfall', carrier: 'NH3', p_nom: 1, energy: 2, unit: 'MW', energy_unit: 'MWh' }],
      grid_supply_counted: ['g is counted: bus grid also holds load L'] })} />)
    expect(cellOf(0).className).not.toContain('font-mono')
    expect(cellOf(0).className).toContain('text-left')
    expect(cellOf(1).className).toContain('font-mono')
    expect(cellOf(1).className).toContain('text-right')
    expect(cellOf(2).className).not.toContain('font-mono')
    expect(cellOf(2).className).toContain('text-left')
    cleanup()
    render(<SizedDesignTable testId="t" mode="expert" report={report([], { grid_connection_mw: 40 })} />)
    expect(cellOf(0).className).toContain('font-mono')
  })

  it('warns on the connection row by its kind, never on a slack row', () => {
    render(<SizedDesignTable testId="t" mode="guided" report={report([], {
      grid_connection_mw: 40, grid_import_peak_mw: 41, connection_check: 'exceeds',
      product_slacks: [{ name: 's', process_role: 'shortfall', carrier: 'NH3', p_nom: 1, energy: 2, unit: 'MW', energy_unit: 'MWh' }] })} />)
    expect(cellOf(0).className).toContain('text-warn')
    expect(cellOf(1).className).not.toContain('text-warn')
  })

  it('the fold control is an ink, always-underlined link', () => {
    render(<SizedDesignTable testId="t" mode="guided" report={report([], { grid_connection_mw: 40,
      product_slacks: [1, 2, 3, 4, 5, 6].map(i => ({ name: `s${i}`, process_role: 'shortfall', carrier: `P${i}`, p_nom: 1, energy: 2 })) })} />)
    const more = screen.getByTestId('t-not-counted-more')
    expect(more.className).toContain('underline')
    expect(more.className).not.toContain('text-accent')
    expect(more.className).not.toContain('hover:underline')
  })
})

