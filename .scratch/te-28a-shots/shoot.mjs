// node shoot.mjs  -> PNGs of the Guided Results card (needs Vite on :5173)
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'
const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const proj = { id: 'p1', name: 'Demo', created_at: '2026-01-01T00:00:00', has_solver_config: true, bus_count: 0, snapshot_count: 4, objective: 1 }
const SNAPS = Array.from({ length: 4 }, (_, i) => new Date(Date.UTC(2026, 0, 1, i)).toISOString().slice(0, 19))
const base = {
  archetype: 'weak_flexible', pack_hash: 'h', assumptions_hash: 'a', mc_lole_h: 12.38, cost_at_target_eur: 12_345_678,
  completeness: { target: 'ok', frontier: 'not_established', certification: 'ok' },
  sections: {
    certification: { status: 'ok', payload: { verdict: 'fail', lole_h_per_year: 12.38, target_lole_h: 3 } },
    frontier: { status: 'not_established', note: 'VOLL must be above zero for the frontier.' },
    fmea_top: { status: 'ok', payload: { rows: [
      { mode_id: 'B:grid_import', name: 'grid_import', criticality_eur_per_year: 590_000 },
      { mode_id: 'B:genset_1', name: 'genset_1', criticality_eur_per_year: 120_000 },
      { mode_id: 'B:genset_2', name: 'genset_2', criticality_eur_per_year: 90_000 } ] } },
  },
}
const review = {
  status: 'ok', source: 'stored report', stale: false,
  summary: { verdict: 'fail', mc_lole_h_per_year: 12.38, target_lole_h: 3, cost_at_target_eur: 12_345_678 },
  findings: [{ id: 'certification_fail', severity: 'high', title: 'Not certified', evidence: { verdict: 'fail', lole_h_per_year: 12.38, target_lole_h: 3 }, recommendation: 'Add firm local capacity.', actions: [] }],
  next_steps: [],
}
// The ammonia template's figures from a real solve of eh_ammonia_staged (template-e2e 28a).
const ammoniaReport = { ...base, period_basis: 'npv_multi_period', valued_to_year: 2045,
  tea: { lcoh_eur_per_kg: 1.725, lcoh_status: 'ok', lcoa_eur_per_t: 439.09, lcoa_status: 'ok', currency: 'EUR' } }
const h2Report = { ...base, period_basis: 'npv_multi_period', valued_to_year: 2045,
  tea: { lcoh_eur_per_kg: 2.345, lcoh_status: 'ok', lcoa_eur_per_t: null, lcoa_status: 'skipped', currency: 'EUR' } }
// The flow and business-case routes are mocked too, so no "could not be read" line shows (#2017 UX nit).
const SECTIONS = ['project', 'carriers', 'connection', 'loads', 'assets', 'profiles', 'commercial', 'scenarios', 'readiness', 'results']
const flow = {
  sections: SECTIONS.map(section => ({ section, label: section, required: true, items: [], summary: null,
    state: section === 'readiness' || section === 'results' ? null : 'complete' })),
  run_ready: true, run_blocker: null, run_request: { optimisation: true, study: true },
  verdicts: { optimisation: { ready: true, blocker: null }, study: { ready: true, blocker: null } },
  notes: [], deferred: false, deferred_reason: null,
}
const businessCaseInputs = { business_case: null, digest: 'd', status: 'not_set' }

async function shoot(file, report, theme) {
  const p = await (await b.newContext({ viewport: { width: 1250, height: 1700 }, deviceScaleFactor: 2 })).newPage()
  p.on('pageerror', e => console.log('PAGEERR', e.message.slice(0, 200)))
  const R = {
    '/api/health': { auth_enabled: false }, '/api/projects/': [proj],
    '/api/network/snapshots': { snapshots: SNAPS, count: 4 },
    '/api/network/meta': { name: 'Demo', bus_count: 0, snapshot_count: 4 },
    '/api/network/investment_periods': { periods: [2027, 2030, 2035], weightings: [] },
    '/api/results/eh_study': { status: 'done', archetype: 'weak_flexible', report, edited_since_study: false },
    '/api/results/eh_review': review,
    '/api/results/eh_reference_design': report,
    '/api/projects/Demo/flow': flow,
    '/api/results/business_case/inputs': businessCaseInputs,
  }
  await p.route(u => new URL(u).pathname.startsWith('/api/'), r => {
    const path = new URL(r.request().url()).pathname
    if (R[path]) return r.fulfill({ json: R[path] })
    if (path.endsWith('eh_template') || path.endsWith('eh_levers') || path.endsWith('headline_kpis')) return r.fulfill({ status: 204, body: '' })
    if (r.request().method() !== 'GET') return r.fulfill({ json: {} })
    if (path.startsWith('/api/network/')) return r.fulfill({ json: [] })
    r.fulfill({ status: 404, json: { detail: 'mock' } })
  })
  await p.goto('http://127.0.0.1:5173/projects'); await p.waitForTimeout(1500)
  await p.goto('http://127.0.0.1:5173/app?project=p1'); await p.waitForTimeout(2500)
  await p.evaluate(async ([theme]) => {
    const { useUIStore } = await import('/src/store/uiStore.ts')
    const { useHubDesignStore } = await import('/src/pages/hubDesign/hubDesignStore.ts')
    useUIStore.getState().setUiMode?.('guided', { explicit: true })
    useUIStore.setState({ currentProject: 'Demo', theme, activeSlidePanel: 'hubDesign' })
    useHubDesignStore.setState({ project: 'Demo', ready: true, step: 'results' })
  }, [theme])
  await p.waitForTimeout(2500)
  console.log(file, await p.evaluate(() => document.documentElement.dataset.theme),
    await p.evaluate(() => [...document.querySelectorAll('[data-testid^=hub-results-]')].map(e => e.dataset.testid)).then(a => a.join(',')))
  // drop the mock's error toasts (fixed-position boxes that say 'mock' / 'could not be loaded')
  await p.evaluate(() => { for (const e of document.querySelectorAll('body *')) {
    if (getComputedStyle(e).position === 'fixed' && /mock|could not be loaded/.test(e.textContent || '') && (e.textContent || '').length < 120) e.style.display = 'none' } })
  if (process.env.DBG) console.log(JSON.stringify(await p.evaluate(() => { const e=document.querySelector('[data-testid=hub-card-results]')||document.body; const r=e.getBoundingClientRect(); return [r.x,r.y,r.width,r.height, document.body.innerText.slice(0,300)] })))
  console.log(file, 'could-not-be-read:', await p.evaluate(() => (document.body.innerText.match(/[^\n]{0,80}could not be read[^\n]*/g) || []).join(' | ')))
  await p.screenshot({ path: file })
}
await shoot('shot-guided-ammonia-light.png', ammoniaReport, 'light')
await shoot('shot-guided-ammonia-dark.png', ammoniaReport, 'dark')
await shoot('shot-guided-h2-no-ammonia-light.png', h2Report, 'light')
await b.close()
