// DC 02 PR 2 shots: the CO₂ limit field, its Results figure and its refusal (Guided/Expert x light/dark).
// Component harness: a temporary page under pypsa-gui/frontend/src, served by vite and removed in `finally`;
// the API answers are route mocks in the shapes `GET /results/headline_kpis` and `GET /results/co2_limit` send.
// Run: node shoot.mjs
import fs from 'node:fs'
import path from 'node:path'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
const { chromium } = require('playwright')

const OUT = path.dirname(fileURLToPath(import.meta.url))
const FE = path.resolve(OUT, '../../../../pypsa-gui/frontend')
const PORT = 5234
const html = path.join(FE, 'src/harness02.tmp.html')
const tsx = path.join(FE, 'src/harness02.tmp.tsx')

fs.writeFileSync(html, `<!doctype html><html lang="en"><head><meta charset="UTF-8"/><title>h</title>
<link rel="stylesheet" href="/brand.css" /></head><body><div id="root"></div>
<script type="module" src="/src/harness02.tmp.tsx"></script></body></html>`)
fs.writeFileSync(tsx, `import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import { useUIStore } from './store/uiStore'
import { useHubDesignStore, HUB_DESIGN_INITIAL } from './pages/hubDesign/hubDesignStore'
import { GoalCard } from './pages/hubDesign/cards/GoalCard'
import { Co2Figure } from './pages/hubDesign/cards/ResultsHeadline'
import { Co2LimitRefusal } from './pages/results/Co2LimitRefusal'
import HeadlineStrip from './pages/results/HeadlineStrip'
import { ReliabilityAssumptions } from './pages/SolverSettings'
const q = new URLSearchParams(location.search)
const mode = (q.get('mode') ?? 'guided') as 'guided' | 'expert'
document.documentElement.dataset.theme = q.get('theme') ?? 'light'
useUIStore.setState({ uiMode: mode, currentProject: 'Demo' })
useHubDesignStore.setState({ ...HUB_DESIGN_INITIAL, project: 'Demo', ready: true, step: 'goal' })
const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
const CO2 = { id: 'co2_annual', value: 950, unit: 'tCO2/yr', state: 'ready', reason: null, formula_id: null, detail: null,
  delta: null, trend: null, limit_t_per_yr: 1000, reached: true, shadow_price_eur_per_t: 85.4, period: null } as const
const H = ({ children }: { children: string }) => <h2 className="text-[11px] font-semibold text-muted uppercase tracking-wide mt-3 mb-1">{children}</h2>
const DRAFT = { voll: 5000, ens_cap_permyriad: null, ens_zone_cap_multiple: null, reserve_margin: null, target_lole_h: 3,
  mc_draws: null, site_archetype: null, co2_limit_t_per_yr: 0 } as never
createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={qc}>
    <div id="frame" style={{ width: mode === 'guided' ? 460 : 900 }} className="bg-bg text-text p-3 flex flex-col gap-2">
      {mode === 'guided' ? <>
        <H>Goal step</H><GoalCard />
        <H>Results: the CO₂ figure</H><div style={{ width: 220 }}><Co2Figure card={CO2 as never} /></div>
        <H>Results: a limit no design meets</H><Co2LimitRefusal mode="guided" />
      </> : <>
        <H>Solver settings: the goal (a limit of 0)</H><ReliabilityAssumptions draft={DRAFT} patch={() => {}} />
        <H>Results › Summary: the strip, and a limit no design meets</H><HeadlineStrip />
      </>}
    </div>
  </QueryClientProvider>)
`)

const GOAL = { target_lole_h: { value: 3, source: 'stored' }, ens_cap_permyriad: { value: null, source: 'default' },
  mc_draws: { value: 500, source: 'default' }, site_archetype: { value: 'strong_grid', source: 'template' } }
const card = (id, over) => ({ id, value: null, unit: null, state: 'ready', reason: null, formula_id: null, detail: null, delta: null, trend: null, ...over })
const KPIS = { kind: 'energy_hub', cards: [
  card('system_cost', { value: 34_700_000, unit: 'currency' }),
  card('unserved_energy', { value: 0, unit: 'MWh', state: 'zero' }),
  card('co2_annual', { value: 1000, unit: 'tCO2/yr', limit_t_per_yr: 1000, reached: true, shadow_price_eur_per_t: 85.4, period: null }),
] }
const REFUSAL = { limit_t_per_yr: 100, rows: [], refusal: { code: 'co2_cap_unreachable',
  message: 'CO₂ limit: no design meets 100 t CO₂/yr. The least reachable is 420 t CO₂/yr. One extra solve without the limit found it.',
  least_t_per_yr: { all: 420 }, without_limit: 'feasible', extra_solves: 1 } }

const SCOPE = {
  scope: { version: 1, groups: ['capacity', 'reliability'], source: 'project', preset_id: 'eh_datacenter',
    options: { capacity: ['sizing', 'dispatch'], grid: [], control: [], reliability: ['goal_study'], commercial: [], handoff: [] } },
  cards: [{ id: 'reliability', label: 'Reliability', locked_reason: null, intended: ['goal_study'], sections: ['assets', 'scenarios'],
    chip: 'Reliability', options: [{ id: 'goal_study', label: 'Reliability goal & study', status: 'available', reason: null }] }],
  preset_id: 'eh_datacenter', preset_label: 'Data Center Energy Hub', preset: { version: 1, groups: [], options: {} },
}

let vite
try {
  vite = spawn(path.join(FE, 'node_modules/.bin/vite'), ['--port', String(PORT), '--host', '127.0.0.1', '--strictPort'], { cwd: FE, stdio: 'ignore' })
  const b = await chromium.launch()
  for (let i = 0; i < 60; i++) { try { if ((await fetch(`http://127.0.0.1:${PORT}/src/harness02.tmp.html`)).ok) break } catch {} await new Promise(r => setTimeout(r, 1000)) }
  for (const mode of ['guided', 'expert']) for (const theme of ['light', 'dark']) {
    const ctx = await b.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 })
    const p = await ctx.newPage()
    await p.route(`http://127.0.0.1:${PORT}/api/**`, r => {
      const u = r.request().url()
      if (u.includes('/flow/reliability_goal')) return r.fulfill({ json: GOAL })
      if (u.includes('/flow/scope')) return r.fulfill({ json: SCOPE })
      if (/\/flow(\?|$)/.test(u)) return r.fulfill({ json: { sections: [] } })
      if (u.includes('/simulation/solver_config')) return r.fulfill({ json: { voll: 5000, co2_limit_t_per_yr: null } })
      if (u.includes('/results/headline_kpis')) return r.fulfill({ json: KPIS })
      if (u.includes('/results/co2_limit')) return r.fulfill({ json: REFUSAL })
      return r.fulfill({ status: 204, body: '' })
    })
    p.on('console', m => m.type() === 'error' && console.log('console', m.text().slice(0, 300))); p.on('response', r => r.status() >= 400 && console.log('http', r.status(), r.url()))
    p.on('pageerror', e => console.log('pageerror', (e.stack ?? e.message).slice(0, 700)))
    await p.goto(`http://127.0.0.1:${PORT}/src/harness02.tmp.html?mode=${mode}&theme=${theme}`)

    await p.waitForSelector(mode === 'guided' ? '[data-testid=hub-goal-co2]' : '[data-testid=solver-goal-co2]')
    await p.waitForSelector(mode === 'guided' ? '[data-testid=hub-co2-refusal]' : '[data-testid=results-co2-refusal]')
    await p.waitForTimeout(800)
    await p.locator('#frame').screenshot({ path: path.join(OUT, `co2-limit-${mode}-${theme}.png`) })
    await ctx.close()
  }
  await b.close()
} finally {
  vite?.kill()
  for (const f of [html, tsx]) fs.rmSync(f, { force: true })
}
