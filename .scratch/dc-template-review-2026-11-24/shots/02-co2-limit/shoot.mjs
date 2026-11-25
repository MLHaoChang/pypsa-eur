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
fs.writeFileSync(tsx, `import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import { useUIStore } from './store/uiStore'
import { useHubDesignStore, HUB_DESIGN_INITIAL } from './pages/hubDesign/hubDesignStore'
import { GoalCard } from './pages/hubDesign/cards/GoalCard'
import { Co2Figure } from './pages/hubDesign/cards/ResultsHeadline'
import { Co2LimitRefusal } from './pages/results/Co2LimitRefusal'
import HeadlineStrip from './pages/results/HeadlineStrip'
import { ReliabilityAssumptions } from './pages/SolverSettings'
import { FlowReturnButton } from './layout/FlowReturnButton'
const q = new URLSearchParams(location.search)
const mode = (q.get('mode') ?? 'guided') as 'guided' | 'expert'
const view = q.get('view') ?? 'goal'
document.documentElement.dataset.theme = q.get('theme') ?? 'light'
useUIStore.setState({ uiMode: mode, currentProject: 'Demo', activeSlidePanel: 'hubDesign', activeSection: 'scenarios' })
useHubDesignStore.setState({ ...HUB_DESIGN_INITIAL, project: 'Demo', ready: true, step: view === 'goal' ? 'goal' : 'results' })
const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
const reached = view !== 'strip-not-reached'
const CO2 = { id: 'co2_annual', value: reached ? 50000 : 41250, unit: 'tCO2/yr', state: 'ready', reason: null, formula_id: null,
  detail: null, delta: null, trend: null, limit_t_per_yr: 50000, reached, shadow_price_eur_per_t: reached ? 180 : null, period: null }
function ExpertGoal() {
  const [draft, setDraft] = useState({ voll: 5000, ens_cap_permyriad: null, ens_zone_cap_multiple: null, reserve_margin: null,
    target_lole_h: 3, mc_draws: null, site_archetype: null, co2_limit_t_per_yr: null } as never)
  return <ReliabilityAssumptions draft={draft} patch={p => setDraft(d => ({ ...(d as object), ...p }) as never)} />
}
createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={qc}>
    <div id="frame" style={{ width: mode === 'guided' ? 460 : 900 }} className="bg-bg text-text p-3 flex flex-col gap-2">
      <FlowReturnButton />
      {view.startsWith('goal') ? (mode === 'guided' ? <GoalCard /> : <ExpertGoal />)
        : view === 'refusal' ? (mode === 'guided' ? <Co2LimitRefusal mode="guided" /> : <HeadlineStrip />)
        : (mode === 'guided' ? <div style={{ width: 230 }}><Co2Figure card={CO2 as never} /></div> : <HeadlineStrip />)}
    </div>
  </QueryClientProvider>)
`)

const GOAL = { target_lole_h: { value: 3, source: 'stored' }, ens_cap_permyriad: { value: null, source: 'default' },
  mc_draws: { value: 500, source: 'default' }, site_archetype: { value: 'strong_grid', source: 'template' } }
const card = (id, over) => ({ id, value: null, unit: null, state: 'ready', reason: null, formula_id: null, detail: null, delta: null, trend: null, ...over })
const kpis = (view) => ({ kind: 'energy_hub', cards: [
  card('system_cost', { value: 34_700_000, unit: 'currency' }),
  card('unserved_energy', { value: 0, unit: 'MWh', state: 'zero' }),
  ...(view === 'refusal' ? [] : [card('co2_annual', view === 'strip-not-reached'
    ? { value: 41250, unit: 'tCO2/yr', limit_t_per_yr: 50000, reached: false, shadow_price_eur_per_t: null, period: null }
    : { value: 50000, unit: 'tCO2/yr', limit_t_per_yr: 50000, reached: true, shadow_price_eur_per_t: 180, period: null })]),
] })
const REFUSAL = { limit_t_per_yr: 50000, rows: [], refusal: { code: 'co2_cap_unreachable',
  message: 'CO₂ limit: no design meets 50,000 t CO₂/yr. The least reachable is 61,200 t CO₂/yr in 2030. One extra solve without the limit found it.',
  least_t_per_yr: { 2030: 61200 }, without_limit: 'feasible', extra_solves: 1 } }
const scope = (on) => ({
  scope: { version: 1, groups: on ? ['capacity', 'reliability'] : ['capacity'], source: 'project', preset_id: 'eh_datacenter',
    options: { capacity: ['sizing', 'dispatch'], grid: [], control: [], reliability: on ? ['goal_study'] : [], commercial: [], handoff: [] } },
  cards: [{ id: 'reliability', label: 'Reliability', locked_reason: null, intended: ['goal_study'], sections: ['assets', 'scenarios'],
    chip: 'Reliability', options: [{ id: 'goal_study', label: 'Reliability goal & study', status: 'available', reason: null }] }],
  preset_id: 'eh_datacenter', preset_label: 'Data Center Energy Hub', preset: { version: 1, groups: [], options: {} },
})
// view, the value typed into the CO₂ field (null: none), click the card link first
const SHOTS = [
  ['goal-empty', null], ['goal-zero', '0'], ['goal-value', '50000'], ['goal-error', '-5'], ['goal-reliability-off', '50000'],
  ['strip-reached'], ['strip-not-reached'], ['refusal'], ['back-pill'],
]

let vite
try {
  vite = spawn(path.join(FE, 'node_modules/.bin/vite'), ['--port', String(PORT), '--host', '127.0.0.1', '--strictPort'], { cwd: FE, stdio: 'ignore' })
  const b = await chromium.launch()
  for (let i = 0; i < 60; i++) { try { if ((await fetch(`http://127.0.0.1:${PORT}/src/harness02.tmp.html`)).ok) break } catch {} await new Promise(r => setTimeout(r, 1000)) }
  for (const mode of ['guided', 'expert']) for (const theme of ['light', 'dark']) for (const [name, typed] of SHOTS) {
    if (name === 'goal-reliability-off' && mode === 'expert') continue
    const view = name === 'back-pill' ? 'strip-reached' : name
    const ctx = await b.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 })
    const p = await ctx.newPage()
    await p.route(`http://127.0.0.1:${PORT}/api/**`, r => {
      const u = r.request().url()
      if (u.includes('/flow/reliability_goal')) return r.fulfill({ json: GOAL })
      if (u.includes('/flow/scope')) return r.fulfill({ json: scope(name !== 'goal-reliability-off') })
      if (/\/flow(\?|$)/.test(u)) return r.fulfill({ json: { sections: [] } })
      if (u.includes('/simulation/solver_config')) return r.fulfill({ json: { voll: 5000, co2_limit_t_per_yr: null } })
      if (u.includes('/results/headline_kpis')) return r.fulfill({ json: kpis(view) })
      if (u.includes('/results/co2_limit')) return view === 'refusal' ? r.fulfill({ json: REFUSAL }) : r.fulfill({ status: 204, body: '' })
      return r.fulfill({ status: 204, body: '' })
    })
    p.on('pageerror', e => console.log('pageerror', name, (e.stack ?? e.message).slice(0, 400)))
    await p.goto(`http://127.0.0.1:${PORT}/src/harness02.tmp.html?mode=${mode}&theme=${theme}&view=${view}`)
    const co2 = mode === 'guided' ? '[data-testid=hub-goal-co2]' : '[data-testid=solver-goal-co2]'
    if (name.startsWith('goal')) {
      await p.waitForSelector(`${co2}:not([disabled])`)
      if (name === 'goal-reliability-off') await p.waitForSelector('[data-testid=hub-goal-out-of-scope-add]')
      if (typed != null) await p.fill(co2, typed)
    } else if (name === 'refusal') {
      await p.waitForSelector(mode === 'guided' ? '[data-testid=hub-co2-refusal]' : '[data-testid=results-co2-refusal]')
    } else {
      const fig = mode === 'guided' ? '[data-testid=hub-results-co2]' : '[data-testid=headline-co2_annual]'
      await p.waitForSelector(fig)
      if (name === 'back-pill') await p.locator(fig).getByRole('button', { name: 'Emissions ›' }).click()
    }
    await p.waitForTimeout(700)
    // The Back pill is fixed to the window's foot: that shot is the window.
    const file = path.join(OUT, `${name}-${mode}-${theme}.png`)
    if (name === 'back-pill') await p.screenshot({ path: file, clip: { x: 0, y: 500, width: 1280, height: 400 } })
    else await p.locator('#frame').screenshot({ path: file })
    await ctx.close()
  }
  await b.close()
} finally {
  vite?.kill()
  for (const f of [html, tsx]) fs.rmSync(f, { force: true })
}
