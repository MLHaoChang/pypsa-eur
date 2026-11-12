// ui-modern 02 slice 8: "Add to report" on the remaining result charts. A chart's
// identity is view + figure + title, so each figure id must carry every filter the chart shows.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import type { ChartSpec } from '../../api/reports'
import { useUIStore } from '../../store/uiStore'
import HourDayHeatmap from './HourDayHeatmap'
import StorageHeatmap from './StorageHeatmap'
import TornadoChart, { type TornadoBar } from './TornadoChart'
import { heatScale, hourDayGrid } from './hourDay'
import { resetReportCharts } from './reportCharts'

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))
vi.mock('../../utils/chartPng', () => ({
  svgToPngBlob: vi.fn().mockResolvedValue(new Blob(['png'], { type: 'image/png' })),
}))
const api = vi.hoisted(() => ({ listReportCharts: vi.fn(), addReportChart: vi.fn(), removeReportChart: vi.fn() }))
vi.mock('../../api/reports', async () => ({
  ...(await vi.importActual<typeof import('../../api/reports')>('../../api/reports')),
  ...api,
}))
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  const { cloneElement } = await import('react')
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactElement }) =>
      cloneElement(children, { width: 518, height: 220 } as Record<string, unknown>),
  }
})

beforeEach(() => {
  resetReportCharts()
  api.listReportCharts.mockReset().mockResolvedValue({ charts: [], limit: 12 })
  api.addReportChart.mockReset().mockImplementation(async (_p: string, spec: ChartSpec) => ({
    id: 'chart-0123456789abcdef', created_at: 't', earlier_solve: false, ...spec,
  }))
  useUIStore.setState({ currentProject: 'demo' })
})
afterEach(cleanup)

/** Mount, press "Add to report", and return what the chart told the report. */
async function saved(node: ReactElement): Promise<ChartSpec> {
  const { unmount } = render(node)
  await userEvent.click(await screen.findByRole('button', { name: 'Add to report' }))
  await waitFor(() => expect(api.addReportChart).toHaveBeenCalled())
  const spec = api.addReportChart.mock.calls.at(-1)![1] as ChartSpec
  unmount()
  api.addReportChart.mockClear()
  return spec
}

const day = (d: string) => Array.from({ length: 24 }, (_, h) => `${d}T${String(h).padStart(2, '0')}:00:00`)
const gridOf = (days: string[]) => {
  const stamps = days.flatMap(day)
  return hourDayGrid(stamps, stamps.map((_, i) => 40 + i))
}
const heat = (days: string[], filename = 'price_hour_day') => {
  const grid = gridOf(days)
  return <HourDayHeatmap title="Price by hour and day" grid={grid} scale={heatScale([grid])} colour="red"
    format={v => `${v}`} csv="price_EUR_per_MWh" filename={filename} />
}

describe('hour-by-day heatmap · report', () => {
  it('saves the table with its unit and a figure that names the window', async () => {
    const a = await saved(heat(['2030-07-01', '2030-07-02']))
    expect(a.view).toBe('hour-day-heatmap')
    expect(a.unit).toBe('EUR/MWh')
    expect(a.columns).toEqual(['date', 'hour', 'price_EUR_per_MWh'])
    expect(a.rows_total).toBe(48)
    expect(a.source_line).toContain('stronger colour is higher') // the key travels with the picture
  })
  it('two windows, and two carriers, give three ids', async () => {
    const ids = new Set([
      (await saved(heat(['2030-07-01', '2030-07-02']))).figure,
      (await saved(heat(['2030-07-01', '2030-07-03']))).figure,
      (await saved(heat(['2030-07-01', '2030-07-02'], 'storage_soc_hour_by_day_battery'))).figure,
    ])
    expect(ids.size).toBe(3)
  })
})

describe('storage heatmap · report', () => {
  const ts = (days: string[]) => {
    const index = days.flatMap(day)
    return { index, columns: ['a', 'b'], data: index.map((_, i) => [i % 5, -1]) }
  }
  const draw = (days: string[], csvName?: string, cols?: string[]) => (
    <StorageHeatmap storPowerTS={ts(days)} range={{ from: 0, to: days.length * 24 - 1 }}
      columnFilter={new Set(cols ?? ['a', 'b'])} csvName={csvName} />
  )
  it('draws its own picture (an HTML grid has no <svg>) and saves the cells', async () => {
    const spec = await saved(draw(['2030-07-01', '2030-07-02']))
    expect(spec.view).toBe('storage-heatmap')
    expect(spec.columns).toEqual(['date', 'hour', 'power_MW'])
    expect(spec.rows_total).toBe(48)
    expect(spec.source_line).toContain('Charge (blue) · Discharge (red)')
  })
  it('says in the caption when only the first days are drawn', async () => {
    const days = Array.from({ length: 33 }, (_, i) => `2030-07-${String(i + 1).padStart(2, '0')}`.replace('2030-07-3', '2030-08-0'))
    const spec = await saved(draw(days))
    expect(spec.source_line).toContain('Showing the first 31 of 33 days')
  })
  it('a different window or file name is a different chart', async () => {
    const ids = new Set([
      (await saved(draw(['2030-07-01', '2030-07-02']))).figure,
      (await saved(draw(['2030-07-02', '2030-07-03']))).figure,
      (await saved(draw(['2030-07-01', '2030-07-02'], 'electrolyzer_heatmap.csv'))).figure,
    ])
    expect(ids.size).toBe(3)
  })
})

describe('tornado · report', () => {
  const bar = (key: string): TornadoBar => ({
    key, label: `Driver ${key}`, unit: 'per unit', low_value: 1, high_value: 2, npv_low: 10, npv_high: 30, swing: 20,
  })
  it('saves one row per driver, in the currency', async () => {
    const spec = await saved(<TornadoChart title="NPV drivers" measure="NPV" rows={[bar('a'), bar('b')]} base={20}
      formatInput={v => `${v}`} filename="battery-npv-drivers" />)
    expect(spec.view).toBe('tornado')
    expect(spec.figure).toBe('battery-npv-drivers')
    expect(spec.rows).toHaveLength(2)
    expect(spec.source_line).toContain('red: below the base case · green: above the base case')
  })
})

describe('seasonal panels · one picture', () => {
  it('draws every season panel, titled, into one svg', async () => {
    const { serializeSVG } = await import('./shared')
    const host = document.createElement('div')
    host.innerHTML = ['Winter', 'Spring', 'Summer', 'Autumn'].map(s =>
      `<div data-report-panel="${s}"><svg width="100" height="50"><path d="M0 0"/></svg></div>`).join('')
    const out = serializeSVG(host, '#fff', true)!
    expect(out.source.match(/<text /g)).toHaveLength(4)
    for (const s of ['Winter', 'Spring', 'Summer', 'Autumn']) expect(out.source).toContain(`>${s}<`)
    // The test setup sizes every svg 500 x 500: two panels across, a gap, and a title line above each.
    expect(out.width).toBe(2 * 500 + 12)
    expect(out.height).toBe(2 * (500 + 18) + 12)
  })
  it('without panels the first svg alone is drawn, as before', async () => {
    const { serializeSVG } = await import('./shared')
    const host = document.createElement('div')
    host.innerHTML = '<div data-report-panel="Winter"><svg width="100" height="50"></svg></div>'
    expect(serializeSVG(host)!.source).not.toContain('<text')
  })
})
