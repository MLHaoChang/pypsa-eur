import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { devCredentials } from '../../../../../pypsa-gui/frontend/scripts/dev-credentials.mjs'
const { chromium } = createRequire(import.meta.url)('playwright')
const BASE = 'http://127.0.0.1:5173'
const OUT = process.env.OUT || '/tmp/asset-walk-shots'
const PROJECT = 'asset-walk-dc'
const TEMPLATE = 'eh_datacenter'
const ONLY = process.env.ONLY || ''
const problems = []
const written = []

const MC_WARNING = 'Sequential MC results rest on ONE weather realisation (the modelled horizon\'s profiles — no inter-annual variability); unit outages are drawn INDEPENDENT of one another (no common-mode or cold-snap-correlated derating); and demand response is EXCLUDED as a resource.'
const MC = { status: 'done', error: null, started_at: 1, finished_at: 2, result: {
  engine: 'mc', fidelity: 'sequential_mc', warning: MC_WARNING,
  metrics: { lole_hours: 9.3241, lole_ci: [9.1123, 9.5432], eue_mwh: 412.55, eue_ci: [388.12, 436.98], by_period: {},
    n_samples: 500, converged: true, time_basis: 'hours_per_year', horizon_years: 1.0, resolution_floor_h: 0.002, warning: MC_WARNING },
  draws: { first_batch: 500, max: 4000, cov_target: 0.05 },
  goal: { target_lole_h: 3, target_source: 'default', verdict: 'miss', confident: false, settle_draws: null, settle_reason: null },
  elcc: [
    { kind: 'generator', name: 'CCGT-1', nameplate_mw: 400, elcc_mw: 312.5, elcc_share: 0.78125, status: 'ok', reason: null, baseline_lole_h: 9.3241, baseline_lole_ci: [9.1123, 9.5432] },
    { kind: 'vre', name: 'Wind-North', nameplate_mw: 4.85, elcc_mw: 1.2, elcc_share: 0.247, status: 'ok', reason: null, baseline_lole_h: 9.3241, baseline_lole_ci: [9.1123, 9.5432] },
    { kind: 'vre', name: 'Solar-East', nameplate_mw: 3.1, elcc_mw: 0.4, elcc_share: 0.129, status: 'ok', reason: null, baseline_lole_h: 9.3241, baseline_lole_ci: [9.1123, 9.5432] },
    { kind: 'vre', name: 'Solar-New', nameplate_mw: 0, elcc_mw: 0, elcc_share: null, status: 'ok', reason: null, baseline_lole_h: 9.3241, baseline_lole_ci: [9.1123, 9.5432] },
  ],
  elcc_installed: { 'Wind-North': { mw: 6, basis: 'p_nom' }, 'Solar-East': { mw: 4, basis: 'p_nom_opt' }, 'Solar-New': { mw: 0, basis: 'not_built' } },
} }
const SIZING = { status: 'ok', payload: { by_carrier: {}, total_p_nom_mw: 6.7, assets: [
  // The project's own extendable assets, so each row reads its unit from section 5.
  { component: 'Generator', name: 'genset_new', carrier: 'gas', built_mw: 4.2, min_mw: 0, max_mw: 12 },
  { component: 'StorageUnit', name: 'bess_new', carrier: 'battery', built_mw: 2.5, min_mw: 0, max_mw: 15, max_hours: 4, built_mwh: 10 },
] } }
const REPORT = { archetype: 'strong_grid', pack_hash: 'x', assumptions_hash: 'y', ens_cap_permyriad: 0.5, achieved_ens_permyriad: 0.3,
  mc_lole_h: 2.1, certified: true, cost_at_target_eur: 1.2e6, period_basis: 'year', completeness: { sizing: 'ok' },
  sections: { sizing: SIZING }, pipeline: { stages: [], solves_consumed: 3, budget_solves: 10 } }

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

  for (const scheme of ['light', 'dark']) {
   for (const mode of ['expert', 'guided']) {
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
    page.on('pageerror', e => problems.push(`${scheme}/${mode}: page error: ${e.message}`))
    await page.route(u => u.pathname === '/api/results/mc', r => r.request().method() === 'GET' ? r.fulfill({ json: MC }) : r.continue())
    await page.route(u => u.pathname === '/api/results/eh_reference_design', r => r.fulfill({ json: REPORT }))
    if (mode === 'guided') {
      await page.route(u => u.pathname === '/api/results/eh_study', r => r.request().method() === 'GET' ? r.fulfill({ json: { status: 'done', report: REPORT, error: null } }) : r.continue())
      await page.route(u => u.pathname === '/api/results/eh_review', r => r.fulfill({ json: { status: 'ok', stale: false, summary: { verdict: 'fail', target_lole_h: 3, mc_lole_h_per_year: 9.3 }, findings: [] } }))
    }
    await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
    const resume = page.getByRole('link', { name: /Resume project/ })
    if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
    await page.getByText(PROJECT).first().waitFor(); await page.waitForTimeout(3000)
    const theme = await page.evaluate(() => document.documentElement.outerHTML.slice(0, 200))
    console.log(scheme, mode, theme)
    const shot = async (box, name) => {
      try {
        await box.scrollIntoViewIfNeeded()
        const hidden = await box.evaluate(el => { const out = []; for (const n of document.body.querySelectorAll('*')) { if (el.contains(n) || n.contains(el)) continue
          if ((getComputedStyle(n).position === 'fixed' || n.matches('[data-testid="companion-launcher"]')) && n.style.visibility !== 'hidden') { n.style.visibility = 'hidden'; out.push(n) } } window.__h = out; return out.length })
        const f = path.join(OUT, `${name}-${scheme}.png`)
        await box.screenshot({ path: f }); written.push(f)
        await page.evaluate(() => { for (const n of window.__h ?? []) n.style.visibility = '' })
      } catch (e) { problems.push(`${scheme}/${mode}/${name}: ${e.message.split('\n')[0]}`); await page.screenshot({ path: path.join(OUT, `failed-${mode}-${name}-${scheme}.png`) }).catch(() => {}) }
    }
    if (mode === 'expert') {
      await page.evaluate(async () => { const { showResultsTab } = await import('/src/layout/flowNavigation.ts'); showResultsTab('adequacy') })
      await page.getByTestId('adequacy-goal').waitFor(); await page.waitForTimeout(1500)
      if (process.env.DEBUG) await page.screenshot({ path: path.join(OUT, `debug-${scheme}.png`), fullPage: true })
      await page.getByRole('button', { name: /SEQUENTIAL MONTE CARLO/i }).first().click().catch(e => problems.push('open mc ' + e.message.split('\n')[0]))
      await page.getByRole('button', { name: /ENERGY HUB REFERENCE DESIGN/i }).first().click().catch(e => problems.push('open eh ' + e.message.split('\n')[0]))
      await page.waitForTimeout(1500)
      const mcp = page.getByTestId('mc-goal-verdict')
      try {
        await mcp.waitFor()
        await shot(page.getByTestId('mc-goal'), '1b-mc-draws')
        const top = page.getByText(/Sequential MC results rest on ONE/).first()
        await top.evaluate(el => el.scrollIntoView({ block: 'start' })); await page.evaluate(() => { const sc = [...document.querySelectorAll('*')].filter(e => e.scrollHeight > e.clientHeight + 50 && /auto|scroll/.test(getComputedStyle(e).overflowY)); for (const e of sc) if (e.contains(document.querySelector('[data-testid=mc-goal-verdict]'))) e.scrollTop -= 60 })
        await page.waitForTimeout(400)
        const a = await top.boundingBox(); const b = await page.getByTestId('elcc-table').boundingBox(); const w = await page.getByTestId('mc-goal-verdict').boundingBox()
        const y0 = w.y - 70; const clip = { x: Math.max(0, b.x - 16), y: y0, width: 1120, height: Math.min(b.y + b.height - y0 + 30, 868 - y0) }
        console.log('clip', clip)
        const f = path.join(OUT, `1-mc-panel-${scheme}.png`)
        await page.evaluate(() => { for (const n of document.body.querySelectorAll('*')) if (getComputedStyle(n).position === 'fixed' && !n.closest('[data-testid=elcc-table]')) n.dataset.hid = n.style.visibility, n.style.visibility = 'hidden' })
        await page.screenshot({ path: f, clip }); written.push(f)
        await page.evaluate(() => { for (const n of document.querySelectorAll('[data-hid]')) n.style.visibility = n.dataset.hid })
      } catch (e) { problems.push(`${scheme}: mc ${e.message.split('\n')[0]}`) }
      await shot(page.getByTestId('adequacy-goal'), '3-adequacy-goal-line')
      await shot(page.getByTestId('eh-report-sized'), '2-sized-design-expert')
    }
    if (mode === 'guided') {
      try {
        await page.getByTestId('hub-design-panel').waitFor({ timeout: 8000 }).catch(async () => {
          await page.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().setSlidePanel('hubDesign') })
          await page.getByTestId('hub-design-panel').waitFor()
        })
        await page.getByTestId('hub-rail-section-assets').click(); await page.waitForTimeout(2500)
        const held = page.getByTestId('section-held-list')
        await held.waitFor()
        await shot(held, '4-holdings-section5')
        await page.getByTestId('hub-rail-step-results').click(); await page.waitForTimeout(2500)
        await shot(page.getByTestId('hub-results-sized'), '2-sized-design-guided')
      } catch (e) { problems.push(`${scheme}/guided: ${e.message.split('\n')[0]}`); await page.screenshot({ path: path.join(OUT, `failed-guided-${scheme}.png`) }) }
    }
    await ctx.close()
   }
  }
} finally { await browser.close() }
console.log(written.join('\n')); console.log('problems', problems)
