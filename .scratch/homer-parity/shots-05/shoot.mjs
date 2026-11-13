// Shots for PR #1477: node shoot.mjs <expert|guided> [two]   (Vite on :5173; API routes are Playwright mocks)
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'
const MODE = process.argv[2] || 'expert', TWO = process.argv[3] === 'two'
const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const p = await (await b.newContext({ viewport: { width: 1500, height: 900 }, deviceScaleFactor: 2 })).newPage()
const proj = { id: 'p1', name: 'Demo', created_at: '2026-01-01T00:00:00', has_solver_config: true, bus_count: 0, snapshot_count: 4, objective: 1 }
const cfg = { solver_name: 'highs', mode: 'lopf', transmission_losses: false, multi_investment_periods: false, solver_options: {}, extra_functionality_code: '', min_renewable_fraction: 1, voll: 0 }
const R = {
  '/api/health': { auth_enabled: false }, '/api/projects/': [proj],
  '/api/simulation/solver_config': cfg, '/api/simulation/queue': { jobs: [], paused: false },
  '/api/simulation/preflight': { ok: true, errors: 0, warnings: 0, issues: [] },
}
p.on('pageerror', e => console.log('PAGEERR', e.message.slice(0, 200)))
await p.route(u => new URL(u).pathname.startsWith('/api/'), r => {
  const path = new URL(r.request().url()).pathname
  if (process.env.LOG) console.log(path)
  if (R[path]) return r.fulfill({ json: R[path] })
  if (r.request().method() !== 'GET') return r.fulfill({ json: {} })
  if (path.startsWith('/api/network/') || path.startsWith('/api/simulation/')) return r.fulfill({ json: [] })
  r.fulfill({ status: 404, json: { detail: 'mock' } })
})
await p.goto('http://127.0.0.1:5173/projects'); await p.waitForTimeout(1500)
await p.goto('http://127.0.0.1:5173/app?project=p1'); await p.waitForTimeout(2500)
const hints = [{ constraint: 'Minimum renewable fraction', setting: 'offgrid',
  text: 'the target of 100% needs 55,347.1 MWh of renewable electricity served; the renewable generators can produce at most 46,539.8 MWh.' }]
if (TWO) hints.push({ constraint: 'Operating reserve', setting: 'other', text: 'the reserve needs 12.0 MW; the plan can hold at most 9.5 MW.' })
await p.evaluate(async ([mode, hints]) => {
  const { useUIStore } = await import('/src/store/uiStore.ts')
  const { useSimulationStore } = await import('/src/store/simulationStore.ts')
  useUIStore.getState().setUiMode?.(mode, { explicit: true })
  useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: 'issues', assistantDockOpen: false })
  useSimulationStore.getState().setLastFailure({ category: 'infeasible', title: 'No feasible solution',
    hint: 'The solver proved the model has no solution that satisfies every constraint at once.', detail: 'infeasible', constraint_hints: hints })
}, [MODE, hints])
await p.waitForTimeout(1500)
const hide = 'div:has(> div > [role=status]){display:none!important}'
await p.addStyleTag({ content: hide })
const sfx = `${MODE}${TWO ? '-two' : ''}`
await p.screenshot({ path: `card-${sfx}.png` })
const link = p.getByRole('button', { name: /Open Off-grid constraints/ })
await link.click(); await p.waitForTimeout(2500)
await p.screenshot({ path: `landing-${sfx}.png` })

await b.close()
