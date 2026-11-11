// "Add to report" on a chart's header (ui-modern 02, slice 7b): the third
// action next to SVG / CSV, its pressed state, toasts, the cap and the saved spec.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { RefObject } from 'react'
import { ChartFrame, ChartSourceProvider } from '../../components/charts/chartTheme'
import { ReportsError, type ChartSnapshot } from '../../api/reports'
import { useUIStore } from '../../store/uiStore'
import { ChartActions, type ChartReportSpec } from './shared'
import { resetReportCharts } from './reportCharts'

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))
vi.mock('../../utils/chartPng', () => ({
  svgToPngBlob: vi.fn().mockResolvedValue(new Blob(['png'], { type: 'image/png' })),
}))
const api = vi.hoisted(() => ({ listReportCharts: vi.fn(), addReportChart: vi.fn(), removeReportChart: vi.fn() }))
vi.mock('../../api/reports', async () => ({
  ...(await vi.importActual<typeof import('../../api/reports')>('../../api/reports')),
  ...api,
}))

const row = (over: Partial<ChartSnapshot> = {}): ChartSnapshot => ({
  id: 'chart-0123456789abcdef', view: 'dispatch-stack', title: 'Dispatch stack · electricity', figure: 'electricity',
  unit: 'MW', source_line: 'src', period: null, out_of_date_when_added: false, created_at: 't',
  earlier_solve: false, rows_total: 1, ...over,
})

const spec: ChartReportSpec = {
  view: 'dispatch-stack', figure: 'electricity', unit: 'MW',
  columns: ['snapshot', 'g'], rows: [['t0', Number.NaN], ['t1', Number.POSITIVE_INFINITY], ['t2', 'x'.repeat(300)]],
}

function mount(stale = false, withReport = true) {
  const svgHost = document.createElement('div')
  svgHost.innerHTML = '<svg width="100" height="50"></svg>'
  const ref = { current: svgHost } as RefObject<HTMLDivElement | null>
  render(
    <ChartSourceProvider value={{ line: 'Latest solve · solved', stale }}>
      <ChartFrame title="Dispatch stack · electricity" basis="all periods"
        right={<ChartActions chartRef={ref} onExportCSV={() => {}} filename="f" report={withReport ? spec : undefined} />}>
        <div />
      </ChartFrame>
    </ChartSourceProvider>,
  )
}

beforeEach(() => {
  resetReportCharts()
  Object.values(toast).forEach(f => f.mockReset())
  api.listReportCharts.mockReset().mockResolvedValue({ charts: [], limit: 12 })
  api.addReportChart.mockReset().mockImplementation(async () => row())
  api.removeReportChart.mockReset().mockResolvedValue(undefined)
  useUIStore.setState({ currentProject: 'demo' })
})
afterEach(() => cleanup())

describe('ChartActions · Add to report', () => {
  it('offers nothing without a report prop', () => {
    mount(false, false)
    expect(screen.queryByRole('button', { name: /report/i })).toBeNull()
  })

  it('adds, shows "In report" pressed, then removes', async () => {
    const user = userEvent.setup()
    mount()
    const add = await screen.findByRole('button', { name: 'Add to report' })
    expect(add.getAttribute('aria-pressed')).toBe('false')
    await user.click(add)
    const inReport = await screen.findByRole('button', { name: 'Remove from report' })
    expect(inReport.getAttribute('aria-pressed')).toBe('true')
    expect(inReport.getAttribute('title')).toBe('Remove from report')
    expect(inReport.textContent).toContain('In report')
    expect(toast.success).toHaveBeenCalledWith('Added to report')
    await user.click(inReport)
    await screen.findByRole('button', { name: 'Add to report' })
    expect(api.removeReportChart).toHaveBeenCalledWith('demo', 'chart-0123456789abcdef')
    expect(toast.success).toHaveBeenCalledWith('Removed from report')
  })

  it('sends the frame title, source line, out-of-date flag and sanitised cells', async () => {
    const user = userEvent.setup()
    mount(true)
    await user.click(await screen.findByRole('button', { name: 'Add to report' }))
    await waitFor(() => expect(api.addReportChart).toHaveBeenCalled())
    const sent = api.addReportChart.mock.calls[0][1]
    expect(sent.title).toBe('Dispatch stack · electricity')
    expect(sent.source_line).toBe('Latest solve · solved · all periods')
    expect(sent.out_of_date_when_added).toBe(true)
    expect(sent.rows[0]).toEqual(['t0', null])
    expect(sent.rows[1]).toEqual(['t1', null])
    expect(sent.rows[2][1]).toHaveLength(200)
    expect(JSON.stringify(sent)).not.toMatch(/stale/i)
  })

  it('at the cap disables the button and says why in visible text', async () => {
    api.listReportCharts.mockResolvedValue({
      charts: Array.from({ length: 12 }, (_, i) => row({ id: `chart-${String(i).padStart(16, '0')}`, view: 'other' })),
      limit: 12,
    })
    mount()
    const add = await screen.findByRole('button', { name: 'Add to report' })
    await waitFor(() => expect((add as HTMLButtonElement).disabled).toBe(true))
    expect(screen.getByTestId('report-chart-cap').textContent)
      .toBe('The report holds 12 charts: remove one on the Reports page')
  })

  it('a 409 chart_limit_reached toasts plain words and keeps the button state', async () => {
    api.addReportChart.mockRejectedValue(new ReportsError({ error_kind: 'chart_limit_reached', message: 'the project already has 12 saved charts (limit 12)' }, 409))
    const user = userEvent.setup()
    mount()
    await user.click(await screen.findByRole('button', { name: 'Add to report' }))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(toast.error).toHaveBeenCalledWith('The report holds 12 charts: remove one on the Reports page')
    expect(screen.getByRole('button', { name: 'Add to report' }).getAttribute('aria-pressed')).toBe('false')
  })
})
