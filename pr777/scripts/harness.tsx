import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { LineChart, Line, XAxis, YAxis, Tooltip } from 'recharts'
import './index.css'
import { useUIStore } from './store/uiStore'
import { GridReadinessScorecard } from './components/gridReadiness/GridReadinessScorecard'
import { CHART_TOOLTIP } from './pages/results/shared'
import type { GridReadiness } from './api/campusElectrical'

const q = new URLSearchParams(location.search)
const mode = (q.get('mode') ?? 'guided') as 'expert' | 'guided'
useUIStore.setState({ uiMode: mode, currentProject: 'Demo' } as never)
const target = { section: 'connection', panel: null, component: 'Bus', entity: 'PoC', field: 'eh_sk_mva' }
const row = (id: string, requirement: string, over: Record<string, unknown>) => ({
  id, requirement, limit: 2, unit: '%', source: 'Generic grid code (SAGC, May 2026)', project_value: 1.2, verdict: 'pass',
  assumed: false, note: null, section: 'connection', target, ...over })
const data = { schema: 1, profile: { id: 'p', title: 'Generic', error: null }, template_id: null, envelope: false,
  summary: { pass: 1, warn: 0, fail: 1, unknown: 0, assumed: 0, red_flags: 0 }, basis: 'screening',
  rows: [
    row('voltage_unbalance', 'Voltage unbalance at the PoC from the site\'s single-phase load', {
      limit: 2, unit: '% of positive sequence', assumed: true, project_value: 4.95, verdict: 'fail',
      source: 'Screening rule, from the unbalance limit in the grid code of the connection point (SAGC, May 2026)',
      note: 'Current distortion 15.81 % of the load current against 8 %. At least 0.6712 % from the units with inputs.' }),
    row('tdd', 'Total demand distortion of the current at the point of connection', {
      limit: 8, unit: '% of\u00A0the\u00A0maximum\u00A0demand\u00A0current\u00A0at\u00A0the\u00A0point\u00A0of\u00A0connection', project_value: 5.4, verdict: 'pass' }),
  ] } as unknown as GridReadiness
const pts = Array.from({ length: 8 }, (_, i) => ({ h: i + 1, z: 100 * (i + 1) }))
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
createRoot(document.getElementById('root')!).render(
  <MemoryRouter initialEntries={['/harness']}><QueryClientProvider client={client}>
    <div className="bg-bg min-h-screen p-4">
      <div data-testid="score" className="border border-border rounded-lg bg-panel" style={{ width: Number(q.get("w") ?? 672) }}>
        <GridReadinessScorecard data={data} mode={mode} />
      </div>
      <div data-testid="chart" className="mt-6">
        <LineChart width={420} height={180} data={pts}><XAxis dataKey="h" /><YAxis />
          <Tooltip {...CHART_TOOLTIP} formatter={(v: number) => [`${v} Ω`, '|Z| max']} /><Line dataKey="z" name="|Z| max" isAnimationActive={false} /></LineChart>
      </div>
    </div></QueryClientProvider></MemoryRouter>)
