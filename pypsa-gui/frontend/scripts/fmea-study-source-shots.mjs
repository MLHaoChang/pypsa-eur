// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * template-e2e 24 screenshot harness: the FMEA worksheet when its rows are the
 * last Energy Hub study's (`source: "eh_study"`): the source line in Guided and
 * Expert, the out-of-date state, unpriced study rows with the re-run note,
 * and live rows with no line; dark and light, on
 * the real app. Mocked routes only: `GET /api/results/fmea_modes` and the
 * project's worksheet sidecar.
 * Needs the API on :8000 and Vite on :5173 (dev admin, scripts/dev-credentials.mjs).
 *
 * Usage: node scripts/fmea-study-source-shots.mjs [outDir]      env: BASE
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const here = path.dirname(fileURLToPath(import.meta.url))
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(here, '..', '..', '..', '.scratch', 'template-e2e', 'shots-24'))
const PROJECT = 'FMEA study source shots'
const TEMPLATE = 'eh_datacenter'

const mode_ = (id, name, cls, comp, crit, engine) => ({
  mode_id: id, component_class: comp, name, failure_class: cls, occurrence_per_year: 0.5,
  occurrence_basis: 'FOR', severity_eur: crit * 2, criticality_eur_per_year: crit, delta_eue_mwh: 0.1,
  in_metric_scope: true, engine, fidelity: engine === 'copt' ? 'analytic_convolution' : 'deterministic_scenario',
})
const ROWS = [
  mode_('link:critical_feeder:forced_outage', 'critical_feeder', 'B', 'Link', 473000, 'lp_proxy'),
  mode_('link:site_transformer:forced_outage', 'site_transformer', 'B', 'Link', 61000, 'lp_proxy'),
  mode_('generator:genset_new:forced_outage', 'genset_new', 'A', 'Generator', 900000, 'copt'),
  mode_('generator:genset_1:forced_outage', 'genset_1', 'A', 'Generator', 760, 'copt'),
]
const STUDY = { per_mode: ROWS, source: 'eh_study', study_finished_at: 1795795200, edited_since_study: false,
  voll_eur_per_mwh: null, sweep_status: null, sweep_error: null }
const STATES = {
  study: STUDY,
  stale: { ...STUDY, edited_since_study: true },
  // Unpriced study rows: the "Not priced" line with the re-run note (STUDY_REPRICE).
  reprice: { ...STUDY, per_mode: ROWS.map(r => ({ ...r, severity_eur: null, criticality_eur_per_year: null })) },
  live: { ...STUDY, source: 'live', voll_eur_per_mwh: 5000, study_finished_at: undefined, edited_since_study: undefined },
}
const WORKSHEET = { version: 1, overlays: {}, manual_rows: [] }

const problems = []
const written = []
await fs.mkdir(OUT, { recursive: true })
const browser = await chromium.launch()
try {
  const owner = await browser.newContext()
  const login = await owner.newPage()
  const c = await devCredentials()
  await login.goto(`${BASE}/login`)
  await login.locator('#email').fill(c.email)
  await login.locator('#password').fill(c.password)
  await login.getByRole('button', { name: 'Sign in', exact: true }).click()
  await login.waitForURL(/\/projects/)
  const made = await login.evaluate(async ({ tid, name }) => {
    const { projectsApi } = await import('/src/api/projects.ts')
    const list = await projectsApi.list().catch(() => [])
    if (list.some(p => p.name === name)) return 'exists'
    try { await projectsApi.createFromTemplate(tid, name); return 'made' } catch (e) { return `failed: ${e.message}` }
  }, { tid: TEMPLATE, name: PROJECT })
  if (made.startsWith('failed')) problems.push(`create: ${made}`)
  // Load it on the server before any shot tab opens, so no tab's save meets an empty network (409 toast).
  const loaded = await login.evaluate(async ({ name }) => {
    const { projectsApi } = await import('/src/api/projects.ts')
    try { await projectsApi.load(name); return 'ok' } catch (e) { return `failed: ${e.message}` }
  }, { name: PROJECT })
  if (loaded !== 'ok') problems.push(`load: ${loaded}`)
  const auth = await owner.storageState()
  await owner.close()

  const shots = [['guided', 'study'], ['expert', 'study'], ['expert', 'live'], ['guided', 'stale'], ['expert', 'reprice']]
  for (const [mode, state] of shots) {
    for (const theme of ['dark', 'light']) {
      const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2, reducedMotion: 'reduce' })
      await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
      await ctx.addInitScript(({ mode, theme, project }) => {
        localStorage.setItem('network-diagram:current-project', project)
        localStorage.setItem('network-diagram:ui-mode', mode)
        localStorage.setItem('network-diagram:ui-mode-explicit', '1')
        localStorage.setItem('network-diagram:theme', theme)
        localStorage.setItem('network-diagram:autosave', 'false')
      }, { mode, theme, project: PROJECT })
      const page = await ctx.newPage()
      page.setDefaultTimeout(15000)
      page.on('pageerror', e => problems.push(`${mode}/${state}/${theme}: page error: ${e.message}`))
      await page.route(u => u.pathname === '/api/results/fmea_modes', r => r.fulfill({ json: STATES[state] }))
      const ws = `/api/projects/${encodeURIComponent(PROJECT)}/worksheet`
      await page.route(u => u.pathname === ws, r => (r.request().method() === 'GET' ? r.fulfill({ json: WORKSHEET }) : r.continue()))
      try {
        await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
        const resume = page.getByRole('link', { name: /Resume project/ })
        if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
        await page.getByText(PROJECT).first().waitFor(); await page.waitForTimeout(3000)
        await page.evaluate(async () => { const { showResultsTab } = await import('/src/layout/flowNavigation.ts'); showResultsTab('fmea') })
        await page.getByTestId('fmea-table').waitFor(); await page.waitForTimeout(800)
        const hasLine = await page.getByTestId('fmea-source-line').count()
        const hasStale = await page.getByTestId('fmea-study-stale').count()
        if ((state === 'live') === Boolean(hasLine)) problems.push(`${mode}/${state}/${theme}: source line ${hasLine ? 'shown' : 'missing'}`)
        const hasReprice = await page.getByTestId('fmea-study-reprice').count()
        if ((state === 'reprice') !== Boolean(hasReprice)) problems.push(`${mode}/${state}/${theme}: re-run note ${hasReprice ? 'shown' : 'missing'}`)
        if ((state === 'stale') !== Boolean(hasStale)) problems.push(`${mode}/${state}/${theme}: stale state ${hasStale ? 'shown' : 'missing'}`)
        // One crop from the header to the table's last row.
        const top = await page.getByTestId('results-view-fmea').locator('header').boundingBox()
        const table = await page.getByTestId('fmea-table').boundingBox()
        const x = Math.min(top.x, table.x) - 8
        const clip = { x, y: top.y - 8, width: Math.min(1420, Math.max(top.x + top.width, table.x + table.width) - x + 8),
          height: table.y + table.height - top.y + 16 }
        const file = path.join(OUT, `te2e-24-${mode}-${state}-${theme}.png`)
        await page.screenshot({ path: file, clip })
        written.push(file)
      } catch (e) {
        problems.push(`${mode}/${state}/${theme}: ${e.message.split('\n')[0]}`)
        await page.screenshot({ path: path.join(OUT, `failed-${mode}-${state}-${theme}.png`) }).catch(() => {})
      }
      await ctx.close()
    }
  }
} finally {
  await browser.close()
}
for (const w of written) console.log(w)
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
