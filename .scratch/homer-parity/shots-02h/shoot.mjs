// Shots for homer-parity 02h (#1541). Needs Vite on :5173 (no backend: every /api route is mocked here).
// node shoot.mjs <rows|notsolved|hidden>   (Expert only: the capex tab is `expertOnly` in Results.tsx TABS)
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'
const STATE = process.argv[2] || 'rows'
const SNAPS = Array.from({ length: 4 }, (_, i) => new Date(Date.UTC(2026, 0, 1, i)).toISOString().slice(0, 19))
const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const p = await (await b.newContext({ viewport: { width: 1600, height: 1100 }, deviceScaleFactor: 2 })).newPage()
const proj = { id: 'p1', name: 'Demo', created_at: '2026-01-01T00:00:00', has_solver_config: true, bus_count: 0, snapshot_count: 4, objective: 1 }
const fuel = [
  { generator: 'diesel genset 1', carrier: 'diesel', fuel_mwh: 12480, no_load_fuel_mwh: 1320, fuel_cost_eur: 374400, no_load_cost_eur: 39600 },
  { generator: 'gas turbine', carrier: 'gas', fuel_mwh: STATE === 'notsolved' ? null : 80250, no_load_fuel_mwh: STATE === 'notsolved' ? null : 4000, fuel_cost_eur: STATE === 'notsolved' ? null : 1605000, no_load_cost_eur: STATE === 'notsolved' ? null : 80000 },
]
const hours = [
  { generator: 'diesel genset 1', carrier: 'diesel', counted_from: 'status', hours_run: 2310, om_cost_eur: 46200 },
  { generator: 'backup genset', carrier: 'diesel', counted_from: 'dispatch', hours_run: STATE === 'notsolved' ? null : 96, om_cost_eur: STATE === 'notsolved' ? null : 1920 },
]
const cost = {
  capex: 4.2e6, capex_lifetime: 4.2e7, capex_expansion: 1e6, capex_expansion_lifetime: 1e7, opex: 2.6e6, total: 6.8e6,
  curtailment_cost: 0, storage_capex_expansion: 0, storage_capex_expansion_lifetime: 0, capex_lifetime_available: true,
  by_component: [], by_carrier: [], by_period: [],
  ...(STATE === 'hidden' ? { fuel: [], run_hours: [] } : { fuel, run_hours: hours }),
}
const R = {
  '/api/health': { auth_enabled: false }, '/api/projects/': [proj],
  '/api/network/snapshots': { snapshots: SNAPS, count: 4 },
  '/api/network/meta': { name: 'Demo', bus_count: 0, snapshot_count: 4 },
  '/api/results/cost_breakdown': cost, '/api/results/economics_by_carrier': {},
}
p.on('pageerror', e => console.log('PAGEERR', e.message.slice(0, 200)))
await p.route(u => new URL(u).pathname.startsWith('/api/'), r => {
  const path = new URL(r.request().url()).pathname
  if (process.env.LOG) console.log(path)
  if (R[path]) return r.fulfill({ json: R[path] })
  if (r.request().method() !== 'GET') return r.fulfill({ json: {} })
  if (path.startsWith('/api/network/')) return r.fulfill({ json: [] })
  r.fulfill({ status: 404, json: { detail: 'mock' } })
})
await p.goto('http://127.0.0.1:5173/projects'); await p.waitForTimeout(1500)
await p.goto('http://127.0.0.1:5173/app?project=p1'); await p.waitForTimeout(2500)
await p.evaluate(async () => {
  const { useUIStore } = await import('/src/store/uiStore.ts')
  const s = useUIStore.getState()
  s.setUiMode?.('expert', { explicit: true })
  useUIStore.setState({ currentProject: 'Demo', resultsOverlayEnabled: true, canvasView: 'blank' })
  s.setSlidePanel('results'); s.requestResultsTab('capex')
})
await p.waitForTimeout(3000)
await p.addStyleTag({ content: 'div:has(> div > [role=status]){display:none!important}' })
console.log(p.url()); console.log('section present:', await p.getByTestId('fuel-run-hours').count())
await p.screenshot({ path: `shot-expert-${STATE}.png` })
const sec = p.getByTestId('fuel-run-hours')
if (await sec.count()) await sec.screenshot({ path: `shot-expert-${STATE}-section.png` })
await b.close()
