import { describe, expect, it, beforeEach, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import type { GridRiskLine, GridRiskProduction } from '../../api/types'
import { GridRiskBody, fmtEurShort, fmtTonnesYear, flagWords, missingWord } from './GridRiskTab'

const nav = vi.hoisted(() => ({ openFlowTarget: vi.fn(), openFlowTargetInPlace: vi.fn(), showResultsTab: vi.fn() }))
vi.mock('../../layout/flowNavigation', () => nav)

const cell = (t: number | null, eur: number | null, eur_flag: string | null = null) =>
  ({ t_per_year: t, eur_per_year: eur, eur_flag })

function line(key: GridRiskLine['key'], over: Partial<GridRiskLine> = {}): GridRiskLine {
  return {
    key, label: key, status: 'ok', missing: [], missing_inputs: [], flags: [], basis: 'basis', stale: false,
    source_files: ['network.nc'], stamp: {}, rows: [], by_period: { _: cell(100, 10_000) }, ...over,
  }
}

function data(lines: GridRiskLine[], over: Partial<GridRiskProduction> = {}): GridRiskProduction {
  const total = lines.reduce((s, l) => s + (l.by_period._?.t_per_year ?? 0), 0)
  return {
    available: true, omitted: null, periods: ['_'], lines,
    eur_inputs: [{ product: 'concentrate', word: 'lost_production_value', set: true,
      target: { section: 'assets', panel: null, component: 'Bus', entity: 'concentrate', field: 'lost_production_value' } }],
    total: { label: 'upper bound (lines may overlap)', overlap_rule: 'upper_bound',
      incomplete: lines.some(l => l.status === 'missing'), lower_bound_lines: [], by_period: { _: cell(total, total * 100) } },
    ...over,
  }
}

const ALL = () => [line('ride_through'), line('island'), line('n_1'), line('connection')]

beforeEach(() => { nav.openFlowTarget.mockReset(); nav.openFlowTargetInPlace.mockReset(); nav.showResultsTab.mockReset() })

describe('Grid risk to production (G2P-13)', () => {
  it('formats tonnes and money with non-breaking spaces', () => {
    expect(fmtTonnesYear(350)).toBe('350 t/yr')
    expect(fmtTonnesYear(1234)).toBe('1.23 kt/yr')
    expect(fmtEurShort(1_200_000)).toBe('€ 1.2 m')
    expect(fmtEurShort(350_000)).toBe('€ 350 k')
  })

  it('shows every line, the total as an upper bound with its reason, and the source in words', () => {
    render(<GridRiskBody data={data(ALL())} mode="guided" />)
    for (const k of ['ride_through', 'island', 'n_1', 'connection']) {
      expect(screen.getByTestId(`grid-risk-${k}-t`).textContent).toBe('100 t/yr')
    }
    expect(screen.getByTestId('grid-risk-total-t').textContent).toBe('400 t/yr')
    const bound = screen.getByTestId('grid-risk-total-bound')
    expect(bound.textContent).toContain('upper bound')
    expect(bound.textContent).toContain('the lines may overlap')
    expect(screen.queryByTestId('grid-risk-total-incomplete')).toBeNull()
    expect(screen.getByTestId('grid-risk-n_1').textContent).toContain('from the N-1 screen')
    // Guided names no file; Expert's table does.
    expect(screen.queryByText(/network\.nc/)).toBeNull()
    expect(screen.queryByTestId('grid-risk-table')).toBeNull()
  })

  it('a missing line reads Missing: <form word> › and links to its input, never 0', () => {
    const missing = line('n_1', {
      status: 'missing', by_period: {}, missing: ['outage rate'],
      missing_inputs: [{ word: 'mttr_hours', target: { section: 'assets', panel: null, component: 'Link', entity: 'trafo', field: 'mttr_hours' } }],
    })
    render(<GridRiskBody data={data([line('ride_through'), missing])} mode="guided" />)
    const tile = screen.getByTestId('grid-risk-n_1')
    expect(tile.textContent).toContain('Missing:')
    expect(tile.textContent).toContain('Mean time to repair ›')
    expect(tile.textContent).not.toMatch(/\b0\s*t/)
    fireEvent.click(screen.getByTestId('grid-risk-n_1-missing-0'))
    expect(nav.openFlowTargetInPlace).toHaveBeenCalledWith(expect.objectContaining({ entity: 'trafo', field: 'mttr_hours' }))
    expect(screen.getByTestId('grid-risk-total-incomplete').textContent).toContain('1 line is missing')
  })

  it('Expert opens the input in Expert', () => {
    const missing = line('island', { status: 'missing', by_period: {},
      missing_inputs: [{ word: 'campus_run', target: { section: 'results', panel: 'campusResults', component: null, entity: null, field: null } }] })
    render(<GridRiskBody data={data([missing])} mode="expert" />)
    expect(screen.getByTestId('grid-risk-island').textContent).toContain('Missing:campus study run ›')
    fireEvent.click(screen.getByTestId('grid-risk-island-missing-0'))
    // The campus results are a Results tab: the Control tabs' jump, which keeps Back (the alias would drop it).
    expect(nav.showResultsTab).toHaveBeenCalledWith('grid_code')
    expect(nav.openFlowTarget).not.toHaveBeenCalled()
  })

  it('Expert opens a field input in its Properties', () => {
    const missing = line('n_1', { status: 'missing', by_period: {},
      missing_inputs: [{ word: 'mttr_hours', target: { section: 'assets', panel: null, component: 'Link', entity: 'trafo', field: 'mttr_hours' } }] })
    render(<GridRiskBody data={data([missing])} mode="expert" />)
    fireEvent.click(screen.getByTestId('grid-risk-n_1-missing-0'))
    expect(nav.openFlowTarget).toHaveBeenCalledWith(expect.objectContaining({ entity: 'trafo', field: 'mttr_hours' }))
  })

  it('EUR without a lost-production value reads Unavailable with a link to the value', () => {
    const d = data([line('connection', { by_period: { _: cell(50, null, 'lost_production_value_missing') } })])
    d.eur_inputs![0].set = false
    d.total!.by_period._ = cell(50, null, 'lost_production_value_missing')
    render(<GridRiskBody data={d} mode="guided" />)
    const eur = screen.getByTestId('grid-risk-connection-eur')
    expect(eur.textContent).toBe('Unavailable:no lost-production value ›')
    fireEvent.click(screen.getByTestId('grid-risk-connection-eur-fix'))
    expect(nav.openFlowTargetInPlace).toHaveBeenCalledWith(expect.objectContaining({ field: 'lost_production_value' }))
    expect(screen.getByTestId('grid-risk-total-eur').textContent).toContain('Unavailable')
  })

  it('a partial line is tagged a lower bound, an out-of-date one says so', () => {
    render(<GridRiskBody data={data([line('ride_through', { status: 'partial', flags: ['lower_bound'] }),
      line('island', { stale: true, flags: ['stale'] })])} mode="guided" />)
    expect(screen.getByTestId('grid-risk-ride_through').textContent).toContain('lower bound')
    expect(screen.getByTestId('grid-risk-island-stale').textContent).toBe('Out of date')
    expect(document.body.textContent).not.toContain('stale')
  })

  it('the campus lines link to Dips and restarts for the per-unit breakdown', () => {
    render(<GridRiskBody data={data(ALL())} mode="guided" />)
    expect(screen.getByTestId('grid-risk-ride_through-dips').textContent).toBe('Dips and restarts ›')
    expect(screen.getByTestId('grid-risk-island-dips')).toBeTruthy()
    expect(screen.queryByTestId('grid-risk-n_1-dips')).toBeNull()
  })

  it('a missing campus line links once, from its Missing input', () => {
    const missing = line('ride_through', { status: 'missing', by_period: {},
      missing_inputs: [{ word: 'campus_run', target: { section: 'results', panel: 'campusResults', component: null, entity: null, field: null } }] })
    render(<GridRiskBody data={data([missing])} mode="guided" />)
    expect(screen.getByTestId('grid-risk-ride_through').textContent).toContain('Missing:campus study run ›')
    expect(screen.queryByTestId('grid-risk-ride_through-dips')).toBeNull()
  })

  it('a line on demo dip statistics carries a demo Tag, and so does the total, in both modes', () => {
    for (const mode of ['guided', 'expert'] as const) {
      const { unmount } = render(<GridRiskBody data={data([line('ride_through', { flags: ['dip_stats_source:demo'] }),
        line('n_1')])} mode={mode} />)
      expect(screen.getByTestId('grid-risk-ride_through-demo').textContent).toBe('demo')
      expect(screen.queryByTestId('grid-risk-n_1-demo')).toBeNull()
      expect(screen.getByTestId('grid-risk-total-demo').textContent).toContain('demo')
      unmount()
    }
    render(<GridRiskBody data={data([line('n_1')])} mode="guided" />)
    expect(screen.queryByTestId('grid-risk-total-demo')).toBeNull()
  })

  it('multi-period: one selector, the period named, never a horizon sum', () => {
    const l = line('connection', { by_period: { '2030': cell(10, 1000), '2040': cell(20, 2000) } })
    const d = data([l], { periods: ['2030', '2040'] })
    d.total!.by_period = { '2030': cell(10, 1000), '2040': cell(20, 2000) }
    render(<GridRiskBody data={d} mode="guided" />)
    const sel = screen.getByTestId('grid-risk-period')
    expect(sel.textContent).toContain('Showing 2030, per year')
    expect(sel.textContent).not.toContain('Aggregated')
    expect(screen.getByTestId('grid-risk-connection-t').textContent).toBe('10 t/yr')
    fireEvent.click(within(sel).getByText('2040'))
    expect(screen.getByTestId('grid-risk-connection-t').textContent).toBe('20 t/yr')
    expect(screen.getByTestId('grid-risk-total-t').textContent).toBe('20 t/yr')
  })

  it("Expert's table names the file and stamp, and the island's rows with their flags in words", () => {
    const island = line('island', {
      source_files: ['campus_restart.csv', 'network.nc'], stamp: { run_at: '2026-11-06T08:00:00+00:00' },
      flags: ['no_inventory_credit'],
      rows: [
        { key: 'outside_blocks', label: 'Units outside the pickup blocks, lost for the outage', units: ['mill'],
          flags: ['no_inventory_credit'], by_period: { _: cell(60, 6000) } },
        { key: 'in_block_restart', label: "In-block units' restart loss", units: ['pump'], flags: [],
          by_period: { _: cell(40, 4000) } },
      ],
    })
    render(<GridRiskBody data={data([island])} mode="expert" />)
    const table = screen.getByTestId('grid-risk-table')
    expect(table.textContent).toContain('campus_restart.csv')
    expect(table.textContent).toContain('run 2026-11-06 08:00:00 UTC')
    expect(screen.getByTestId('grid-risk-row-island-outside_blocks').textContent).toContain('no inventory credit, so it is conservative')
    expect(table.textContent).not.toContain('no_inventory_credit')
    expect(screen.getByTestId('grid-risk-row-island').textContent).toContain('complete')
  })

  it("Expert's status column uses the tile's words, never a raw key", () => {
    render(<GridRiskBody data={data([line('ride_through'), line('island', { status: 'partial', flags: ['lower_bound'] }),
      line('n_1', { status: 'missing', by_period: {} }), line('connection', { stale: true, flags: ['stale'] })])} mode="expert" />)
    const status = (k: string) => screen.getByTestId(`grid-risk-row-${k}`).children[3].textContent
    expect([status('ride_through'), status('island'), status('n_1'), status('connection')])
      .toEqual(['complete', 'partial', 'missing', 'out of date'])
  })

  it('names flags and inputs in words', () => {
    expect(flagWords('dip_stats_source:demo')).toBe('demo dip statistics')
    expect(flagWords('tier_unset:mill')).toBe('no criticality tier on mill')
    expect(missingWord('lost_production_value')).toBe('Lost-production value')
    expect(missingWord('site_transformer')).toBe('Site transformer')
  })
})
