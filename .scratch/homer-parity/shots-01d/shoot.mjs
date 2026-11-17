// Shots for homer-parity 01d. Needs Vite on :5173 (no backend: every /api route is mocked here).
// node shoot.mjs <rows|empty>   (Expert only: the capex tab is `expertOnly` in Results.tsx TABS)
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'
const STATE = process.argv[2] || 'rows'
const SNAPS = Array.from({ length: 4 }, (_, i) => new Date(Date.UTC(2026, 0, 1, i)).toISOString().slice(0, 19))
const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const p = await (await b.newContext({ viewport: { width: 1600, height: 1100 }, deviceScaleFactor: 2 })).newPage()
const proj = { id: 'p1', name: 'Demo', created_at: '2026-01-01T00:00:00', has_solver_config: true, bus_count: 0, snapshot_count: 4, objective: 1 }
const g = (name, v) => ({ name, kind: 'generator', held_mw: v })
const HOLD = [
  [g('Diesel genset 1', 90), { name: 'Battery', kind: 'storage_unit', held_mw: 30 }],
  [g('Diesel genset 1', 40), g('Diesel genset 2', 30), g('Gas engine', 20), g('Hydro', 10), g('Backup 1', 8), g('Backup 2', 6), g('Backup 3', 4)],
  [],
]
const per = (n, bindAt) => Array.from({ length: n }, (_, i) => ({ snapshot: `2026-01-${String(1 + Math.floor(i / 24)).padStart(2, '0')} ${String(i % 24).padStart(2, '0')}:00`, required_mw: 120 + (i % 7) * 4, held_mw: bindAt(i) ? 120 + (i % 7) * 4 : 400, binding: bindAt(i), ...(bindAt(i) ? { holders: HOLD[i % 3] } : {}) }))
const reserve = STATE === 'empty' ? { shares: { load: 0.1, pv: 0.2, wind: 0.15 }, not_counted: [], per_snapshot: per(12, () => true).map(r => ({ ...r, holders: [] })), binding: [], reason: null }
  : { shares: { load: 0.1, pv: 0.2, wind: 0.15 }, not_counted: ['battery store'], per_snapshot: per(12, () => true), binding: [], reason: null }
const cost = {
  capex: 4.2e6, capex_lifetime: 4.2e7, capex_expansion: 1e6, capex_expansion_lifetime: 1e7, opex: 2.6e6, total: 6.8e6,
  curtailment_cost: 0, storage_capex_expansion: 0, storage_capex_expansion_lifetime: 0, capex_lifetime_available: true,
  by_component: [], by_carrier: [], by_period: [],
  fuel: [], run_hours: [],
}
const R = {
  '/api/health': { auth_enabled: false }, '/api/projects/': [proj],
  '/api/network/snapshots': { snapshots: SNAPS, count: 4 },
  '/api/network/meta': { name: 'Demo', bus_count: 0, snapshot_count: 4 },
  '/api/results/cost_breakdown': cost, '/api/results/economics_by_carrier': {}, '/api/results/operating_reserve': reserve,
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
console.log(p.url()); console.log('section present:', await p.getByTestId('operating-reserve').count())
await p.screenshot({ path: `shot-expert-${STATE}.png` })
const sec = p.getByTestId('operating-reserve')
if (await sec.count()) await sec.screenshot({ path: `shot-expert-${STATE}-section.png` })
if (process.env.DUMP) console.log((await sec.innerText()).slice(-300))
await b.close()
