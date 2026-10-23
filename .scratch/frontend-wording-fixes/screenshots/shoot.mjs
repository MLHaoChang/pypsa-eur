// Screenshots for PR #653. Run: node shoot.mjs (vite dev server on :5199 serving harness.html).
// The harness (pypsa-gui/frontend/harness.html + src/harness.tsx) is copied here as harness.html/harness.tsx.
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'
const BASE = 'http://127.0.0.1:5199/harness.html'
const OUT = new URL('.', import.meta.url).pathname
const SNAPS = Array.from({ length: 1000 }, (_, i) => new Date(Date.UTC(2026, 0, 1, i)).toISOString().slice(0, 19))
const cut = { period: 2030, hour: 3, scenario: 'seamless', event: 'E1', kind: 'inertia', required: 25, current: 12.5,
  delta: 12.5, unit: 'MW·s', binding: 'transition RoCoF 1.2 Hz/s', derivation: 'rocof: f0·ΔP/RoCoF_max', heuristic: true,
  feasible: true, note: null, applicable: true, reason: '', candidate: 'bess', added_mw: 10, estimate: 1000, label: 'estimate (primal): …' }
const REPORT = { caveat: 'Screening, not a certificate.', missing: [], tables: [
  { id: 'costs', group: 'study', title: 'Cost of island capability', model: 'hub LP', caveat: 'Screening.', columns: ['scenario', 'cost'], rows: [['ups_bridged', 2270000]], assumed_rows: [] }] }
const routes = {
  '/api/campus-electrical/Demo/island/report': REPORT,
  '/api/campus-electrical/Demo/island/cuts/status': null,
  '/api/campus-electrical/Demo/island/cuts': { limit: 'Bridge-energy failures get no cut.', max_trials: 3, proposed: [cut], applied: [], trials: [] },
  '/api/campus-electrical/Demo/island': { study: { ok: true }, study_at: '2026-10-22T10:00:00' },
  '/api/campus-electrical/Demo': { stale: false },
  '/api/network/snapshots': { snapshots: SNAPS, count: SNAPS.length },
  '/api/network/investment-periods': { periods: [] },
  '/api/simulation/status': { running: false },
}
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
async function page() {
  const p = await (await browser.newContext({ viewport: { width: 1100, height: 700 } })).newPage()
  p.on('requestfailed', r => console.log('fail', r.url()))
  await p.route(url => new URL(url).pathname.startsWith('/api/'), route => {
    const path = new URL(route.request().url()).pathname
    const hit = Object.keys(routes).sort((a, b) => b.length - a.length).find(k => path === k || path.startsWith(k + '?'))
    console.log('api', path, hit ? 'mocked' : '404')
    return hit ? route.fulfill({ json: routes[hit] }) : route.fulfill({ status: 404, json: { detail: 'mock' } })
  })
  return p
}
for (const mode of ['expert', 'guided']) {
  let p = await page()
  await p.goto(`${BASE}?view=report&mode=${mode}`); await p.waitForTimeout(800)
  await p.screenshot({ path: `${OUT}01-island-requirements-${mode}.png` })
  await p.goto(`${BASE}?view=control&mode=${mode}`); await p.waitForTimeout(1200)
  await p.screenshot({ path: `${OUT}02-control-frequency-cuts-${mode}.png` })
  await p.goto(`${BASE}?view=edit&mode=${mode}`); await p.waitForTimeout(800)
  await p.screenshot({ path: `${OUT}03-edit-card-rounded-${mode}.png` })
  await p.goto(`${BASE}?view=window&mode=${mode}&start=${SNAPS[20]}&end=${SNAPS[10]}`); await p.waitForTimeout(1500)
  await p.screenshot({ path: `${OUT}04-window-reversed-${mode}.png` })
  await p.getByText('Horizon filter').click()
  await p.getByText('From').locator('xpath=ancestor::label').locator('input').fill(SNAPS[5].slice(0, 16))
  await p.waitForTimeout(500)
  await p.screenshot({ path: `${OUT}05-window-note-cleared-${mode}.png` })
}
await browser.close()
