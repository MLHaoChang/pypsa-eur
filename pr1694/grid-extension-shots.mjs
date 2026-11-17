// PR #1694 "Grid extension vs island" card: every state, Guided and Expert, light and dark.
// Live app (127.0.0.1:5173) on an eh_microgrid project; the study routes are mocked with the
// payloads of GridExtensionCard.test.tsx. The reliability goal is patched to off_grid so the
// card shows, and /api/health answers auth_enabled:false (the card's local mode) for every
// state but "needs local mode", which keeps the real multi-user answer.
// States 13-15 (Guided only) reach the card's three message lines: the compare POST refused
// as baseline_not_saved with undo info unsaved, Find with the grid NPC empty, and the
// break-even POST refused 409 supply_architectures_stale.
//   PROBE=1 logs the first button's disabled state and computed colours.
//   OUT=<dir> node grid-extension-shots.mjs     (ONLY=<state> to run one state)
import path from 'node:path'
import { createRequire } from 'node:module'
import { devCredentials } from '../pypsa-gui/frontend/scripts/dev-credentials.mjs'
const { chromium } = createRequire(import.meta.url)('playwright')
const BASE = 'http://127.0.0.1:5173'
const OUT = process.env.OUT || '.'
const PROJECT = 'grid-ext-island'
const TEMPLATE = 'eh_microgrid'
const ONLY = process.env.ONLY || ''
const problems = []
const written = []

const LEDGER = { ledger_version: 'v', honesty_notes: [], rows: [
  { key: 'discount_rate', label: 'Discount rate', value: 0.07 },
  { key: 'battery_storage_lifetime_years', label: 'Battery life', value: 25 },
] }
const STUDY = { study_id: 'ge1', name: 'Grid extension vs island', question_id: 'grid_extension',
  currency_year: 2025, ledger: LEDGER }
const BASIS = { currency: 'EUR', currency_year: 2025, discount_rate: 0.07, horizon_years: 25 }
const row = (id, label, status, cost) => ({ architecture_id: id, label, groups: id.split('+'), status,
  total_cost_eur_per_year: cost, capex_eur: cost == null ? null : 0, renewable_fraction: null, sizes: [] })
const DONE = { status: 'done', stale: false, best_architecture_id: 'pv+genset', rows: [
  row('pv', 'PV', 'infeasible', null),
  row('genset', 'Genset', 'ok', 3_500_000),
  row('pv+genset', 'PV + Genset', 'ok', 14_000),
] }
const ABORTED = { status: 'aborted', stale: false, best_architecture_id: 'pv+battery', rows: [
  row('pv+battery', 'PV + Battery', 'ok', 182_400),
  row('genset', 'Genset', 'failed', null),
  row('pv', 'PV', 'infeasible', null),
  row('pv+battery+genset', 'PV + Battery + Genset', 'not_reached', null),
] }
const RUNNING = { status: 'running', architectures: ['pv', 'genset', 'pv+genset'], current: 'genset',
  solves_estimated: 3, solves_charged: 1 }
const result = over => ({ status: 'break_even', km: 8.1075, architecture_id: 'pv+genset', total_cost_eur_per_year: 14_000,
  offgrid_npc: 163_150, basis: BASIS, approximation: 'annualised approximation', ...over })

// name, study list, supply-architectures answer, break-even answer (null = do not press Find), max km typed, extra action
const STATES = [
  ['not-run', [], null, null],
  ['running', [STUDY], RUNNING, null],
  ['ranked-done', [STUDY], DONE, null],
  ['result-break-even', [STUDY], DONE, result({})],
  ['result-island-cheaper', [STUDY], DONE, result({ status: 'offgrid_cheaper_everywhere', km: null })],
  ['result-grid-cheaper', [STUDY], DONE, result({ status: 'grid_cheaper_everywhere', km: null })],
  ['result-beyond-max', [STUDY], DONE, result({ status: 'beyond_max_km', km: 81.3 }), '50'],
  ['result-not-established', [STUDY], DONE, result({ status: 'break_even_not_established', km: null,
    reason: 'input_not_finite_or_out_of_range', invalid: ['max_km'], message: 'Not computed.' })],
  ['stale', [STUDY], { ...DONE, stale: true }, null],
  ['aborted-row-statuses', [STUDY], ABORTED, null],
  ['comparison-failed', [STUDY], { status: 'failed', rows: [], error: 'x' }, null],
  ['needs-local-mode', null, null, null],
  ['save-first', [STUDY], null, null, null, 'save-first'],
  ['input-error', [STUDY], DONE, null, null, 'input-error'],
  ['route-refusal', [STUDY], DONE, null, null, 'refusal'],
]

const browser = await chromium.launch()
try {
  const owner = await browser.newContext()
  const login = await owner.newPage()
  await login.goto(`${BASE}/login`)
  const c = devCredentials()
  await login.locator('#email').fill(c.email)
  await login.locator('#password').fill(c.password)
  await login.getByRole('button', { name: 'Sign in', exact: true }).click()
  await login.waitForURL(/\/projects/)
  await login.waitForLoadState('networkidle')
  const made = await login.evaluate(async ({ tid, name }) => {
    const { projectsApi } = await import('/src/api/projects.ts')
    const list = await projectsApi.list().catch(() => [])
    if (list.some(p => p.name === name)) return 'exists'
    try { await projectsApi.createFromTemplate(tid, name); return 'made' } catch (e) { return `failed: ${e.message}` }
  }, { tid: TEMPLATE, name: PROJECT })
  console.log('project', made)
  const auth = await owner.storageState()
  await owner.close()

  let n = 0
  for (const [name, list, arch, be, maxKm, act] of STATES) {
   n += 1
   if (ONLY && ONLY !== name) continue
   for (const scheme of ['light', 'dark']) {
    for (const mode of act ? ['guided'] : ['guided', 'expert']) {
     const tag = `${n}-${name}-${mode}-${scheme}`
     const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, reducedMotion: 'reduce', colorScheme: scheme })
     await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
     await ctx.addInitScript(({ mode, project, scheme }) => {
       localStorage.setItem('network-diagram:current-project', project)
       localStorage.setItem('network-diagram:ui-mode', mode)
       localStorage.setItem('network-diagram:ui-mode-explicit', '1')
       localStorage.setItem('network-diagram:theme', scheme)
     }, { mode, project: PROJECT, scheme })
     const page = await ctx.newPage()
     page.setDefaultTimeout(15000)
     page.on('response', r => { if (process.env.DEBUG && (r.status() >= 400)) console.log(tag, r.status(), r.url()) })
     page.on('pageerror', e => problems.push(`${tag}: page error: ${e.message}`))
     if (list) {
       await page.route(u => u.pathname === '/api/health', async r => {
         const res = await r.fetch(); const j = await res.json(); await r.fulfill({ response: res, json: { ...j, auth_enabled: false } })
       })
     }
     await page.route(u => /\/flow\/reliability_goal$/.test(u.pathname), async r => {
       const res = await r.fetch(); const j = await res.json()
       await r.fulfill({ response: res, json: { ...j, site_archetype: { value: 'off_grid', source: 'stored' } } })
     })
     await page.route(u => /\/api\/projects\/[^/]+\/studies\/?$/.test(u.pathname), r =>
       r.request().method() === 'GET' ? r.fulfill({ json: list ?? [] }) : r.continue())
     await page.route(u => /\/studies\/[^/]+\/supply-architectures$/.test(u.pathname), r =>
       r.request().method() === 'GET'
         ? (arch ? r.fulfill({ json: arch }) : r.fulfill({ status: 404, json: { detail: { error_kind: 'supply_architectures_never_run', message: 'x' } } }))
         : act === 'save-first'
           ? r.fulfill({ status: 409, json: { detail: { error_kind: 'baseline_not_saved', message: 'x' } } })
           : r.fulfill({ json: RUNNING }))
     await page.route(u => /\/grid-extension-break-even$/.test(u.pathname), r => act === 'refusal'
       ? r.fulfill({ status: 409, json: { detail: { error_kind: 'supply_architectures_stale', message: 'the base project changed' } } })
       : r.fulfill({ json: be ?? result({}) }))
     if (act === 'save-first') await page.route(u => u.pathname.endsWith('/network/undo/info'), r => r.fulfill({ json: { depth: 1, unsaved: true } }))

     try {
       await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
       const resume = page.getByRole('link', { name: /Resume project/ })
       if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
       await page.getByText(PROJECT).first().waitFor(); await page.waitForTimeout(2000)
       if (mode === 'expert') {
         await page.evaluate(async () => { const { showResultsTab } = await import('/src/layout/flowNavigation.ts'); showResultsTab('investment', { section: 'grid_extension' }) })
       } else {
         await page.getByTestId('hub-design-panel').waitFor({ timeout: 8000 }).catch(async () => {
           await page.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().setSlidePanel('hubDesign') })
           await page.getByTestId('hub-design-panel').waitFor()
         })
         await page.getByTestId('hub-rail-step-results').click()
       }
       const card = page.getByTestId('grid-extension-card').first()
       await card.waitFor(); await page.waitForTimeout(1500)
       if (be) {
         await card.getByLabel('Grid extension cost (€ per km)').fill('25000')
         await card.getByLabel('Grid supply net present cost (€)').fill('-120000')
         if (maxKm) await card.getByLabel('Maximum distance (km)').fill(maxKm)
         await card.getByTestId('grid-ext-find').click()
         await card.getByTestId('grid-ext-result').waitFor(); await page.waitForTimeout(500)
       }
       if (act === 'save-first') {
         await card.getByTestId('grid-ext-compare').click()
         await card.getByTestId('grid-ext-save-first').waitFor(); await page.waitForTimeout(500)
       }
       if (act === 'input-error' || act === 'refusal') {
         await card.getByLabel('Grid extension cost (€ per km)').fill('25000')
         if (act === 'refusal') await card.getByLabel('Grid supply net present cost (€)').fill('-120000')
         await card.getByTestId('grid-ext-find').click()
         await card.getByTestId(act === 'refusal' ? 'grid-ext-error' : 'grid-ext-input-error').waitFor(); await page.waitForTimeout(500)
       }
       if (process.env.PROBE) console.log(tag, JSON.stringify(await card.locator('button').first().evaluate(b => { const c = getComputedStyle(b); return { disabled: b.disabled, color: c.color, background: c.backgroundColor } })))
       await card.scrollIntoViewIfNeeded()
       await card.evaluate(el => { const out = []; for (const x of document.body.querySelectorAll('*')) { if (el.contains(x) || x.contains(el)) continue
         if ((getComputedStyle(x).position === 'fixed' || x.matches('[data-testid="companion-launcher"]')) && x.style.visibility !== 'hidden') { x.style.visibility = 'hidden'; out.push(x) } } window.__h = out })
       const f = path.join(OUT, `${tag}.png`)
       await card.screenshot({ path: f }); written.push(f)
     } catch (e) {
       problems.push(`${tag}: ${e.message.split('\n')[0]}`)
       await page.screenshot({ path: path.join(OUT, `failed-${tag}.png`) }).catch(() => {})
     }
     await ctx.close()
    }
   }
  }
} finally { await browser.close() }
console.log(written.length, 'written'); console.log('problems', problems)
