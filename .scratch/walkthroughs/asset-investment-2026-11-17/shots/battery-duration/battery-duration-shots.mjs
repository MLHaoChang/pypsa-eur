// Shots for WALK-IMPROVE-1 improvement 1 (offer the cheaper battery duration).
//   OUT=<dir> node battery-duration-shots.mjs
// Guided: Results verdict line + "Design options ›", the Improve card line, and
// where "Design options ›" lands. Expert: the Energy Hub panel's levers block.
// The study, review, report and levers payloads are mocked (rendering only).
import path from 'node:path'
import { createRequire } from 'node:module'
import { devCredentials } from '../../../../../pypsa-gui/frontend/scripts/dev-credentials.mjs'
const { chromium } = createRequire(import.meta.url)('playwright')
const BASE = 'http://127.0.0.1:5173'
const OUT = process.env.OUT || '/tmp/battery-duration-shots'
const PROJECT = 'asset-walk-island'
const TEMPLATE = 'eh_microgrid'
const problems = []
const written = []

const SIZING = { status: 'ok', payload: { by_carrier: {}, total_p_nom_mw: 6.5, assets: [
  { component: 'Generator', name: 'pv_new', carrier: 'solar', built_mw: 4, min_mw: 0, max_mw: 20 },
  { component: 'StorageUnit', name: 'bess_new', carrier: 'battery', built_mw: 2.5, min_mw: 0, max_mw: 15, max_hours: 4, built_mwh: 10 },
] } }
const REPORT = { archetype: 'off_grid', pack_hash: 'x', assumptions_hash: 'y', ens_cap_permyriad: 0.5, achieved_ens_permyriad: 0.3,
  mc_lole_h: 2.1, certified: true, cost_at_target_eur: 3.5e6, period_basis: 'year', completeness: { sizing: 'ok' },
  sections: { sizing: SIZING }, pipeline: { stages: [], solves_consumed: 3, budget_solves: 10 } }
const opt = (value, cost) => ({ kind: 'storage_duration', value, unit: 'h', status: 'solved', cost_at_target_eur: cost, achieved_ens_mwh: 0.2, meets_target: true })
const LEVERS = { kind: 'storage_duration', options: [opt(4, 3.5e6), opt(24, 3.1e6), opt(72, 3.1e6)], comparable_solved: 3, skipped_kinds: [], honesty_notes: [] }
const FINDING = { id: 'levers_best_option', severity: 'info', title: 'A cheaper battery duration meets the cap',
  evidence: { kind: 'storage_duration', value: 24, cost_at_target_eur: 3.1e6 }, recommendation: 'Consider a 24 h battery.', actions: [] }
const REVIEW = { status: 'ok', stale: false, summary: { verdict: 'pass', target_lole_h: 3, mc_lole_h_per_year: 2.1 }, findings: [FINDING], next_steps: [] }

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
  console.log('project', await login.evaluate(async ({ tid, name }) => {
    const { projectsApi } = await import('/src/api/projects.ts')
    const list = await projectsApi.list().catch(() => [])
    if (list.some(p => p.name === name)) return 'exists'
    try { await projectsApi.createFromTemplate(tid, name); return 'made' } catch (e) { return `failed: ${e.message}` }
  }, { tid: TEMPLATE, name: PROJECT }))
  const auth = await owner.storageState()
  await owner.close()

  for (const scheme of ['light', 'dark']) {
    for (const mode of ['guided', 'expert']) {
      const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 1100 }, deviceScaleFactor: 2, reducedMotion: 'reduce', colorScheme: scheme })
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
      await page.route(u => u.pathname === '/api/results/eh_study', r => r.request().method() === 'GET' ? r.fulfill({ json: { status: 'done', report: REPORT, error: null } }) : r.continue())
      await page.route(u => u.pathname === '/api/results/eh_review', r => r.fulfill({ json: REVIEW }))
      await page.route(u => u.pathname === '/api/results/eh_reference_design', r => r.fulfill({ json: REPORT }))
      await page.route(u => u.pathname === '/api/results/eh_levers', r => r.fulfill({ json: LEVERS }))
      await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
      const resume = page.getByRole('link', { name: /Resume project/ })
      if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
      await page.getByText(PROJECT).first().waitFor(); await page.waitForTimeout(2500)
      const hideFixed = el => el.evaluate(box => { for (const n of document.body.querySelectorAll('*')) { if (box.contains(n) || n.contains(box)) continue
        if (getComputedStyle(n).position === 'fixed' || n.matches('[data-testid="companion-launcher"]')) n.style.visibility = 'hidden' } })
      const shot = async (box, name) => {
        try {
          await box.waitFor(); await box.scrollIntoViewIfNeeded(); await hideFixed(box)
          const f = path.join(OUT, `${name}-${scheme}.png`)
          await box.screenshot({ path: f }); written.push(f)
        } catch (e) { problems.push(`${scheme}/${mode}/${name}: ${e.message.split('\n')[0]}`); await page.screenshot({ path: path.join(OUT, `failed-${name}-${scheme}.png`) }).catch(() => {}) }
      }
      if (mode === 'guided') {
        try {
          await page.getByTestId('hub-design-panel').waitFor({ timeout: 8000 }).catch(async () => {
            await page.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().setSlidePanel('hubDesign') })
            await page.getByTestId('hub-design-panel').waitFor()
          })
          await page.getByTestId('hub-rail-step-results').click(); await page.waitForTimeout(2000)
          // The verdict block: from VERDICT down to the cheaper-duration line and its link.
          const v = page.getByTestId('hub-results-verdict'); const line = page.getByTestId('hub-results-cheaper-duration')
          await line.waitFor(); await v.scrollIntoViewIfNeeded(); await hideFixed(line)
          const a = await v.boundingBox(); const b = await line.boundingBox()
          const f = path.join(OUT, `1-results-verdict-guided-${scheme}.png`)
          await page.screenshot({ path: f, clip: { x: a.x - 16, y: a.y - 40, width: Math.max(a.width, b.width) + 32, height: b.y + b.height - a.y + 56 } }); written.push(f)
          await page.getByTestId('hub-rail-step-improve').click(); await page.waitForTimeout(2000)
          await shot(page.getByTestId('hub-improve-cheaper-duration').locator('xpath=..'), '2-improve-guided')
          await page.getByTestId('hub-rail-step-results').click(); await page.waitForTimeout(1500)
          await page.getByTestId('hub-results-design-options').click(); await page.waitForTimeout(3000)
          await shot(page.getByTestId('eh-levers'), '3-design-options-landing')
        } catch (e) { problems.push(`${scheme}/guided: ${e.message.split('\n')[0]}`); await page.screenshot({ path: path.join(OUT, `failed-guided-${scheme}.png`) }) }
      } else {
        try {
          await page.evaluate(async () => { const { showResultsTab } = await import('/src/layout/flowNavigation.ts'); showResultsTab('adequacy') })
          await page.waitForTimeout(1500)
          await page.getByRole('button', { name: /ENERGY HUB REFERENCE DESIGN/i }).first().click().catch(e => problems.push('open eh ' + e.message.split('\n')[0]))
          await page.waitForTimeout(2000)
          await shot(page.getByTestId('eh-levers'), '4-levers-expert')
        } catch (e) { problems.push(`${scheme}/expert: ${e.message.split('\n')[0]}`) }
      }
      await ctx.close()
    }
  }
} finally { await browser.close() }
console.log(written.join('\n')); console.log('problems', problems)
