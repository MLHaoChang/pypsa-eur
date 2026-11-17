// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * template-e2e 11 screenshot harness: the FMEA worksheet with no VOLL (unpriced
 * modes read "Not set", listed last, one visible line with the VOLL link),
 * Guided and Expert, dark and light, on the real app. Mocked routes only:
 * `GET /api/results/fmea_modes` and the project's worksheet sidecar.
 * Needs the API on :8000 and Vite on :5173 (dev admin, scripts/dev-credentials.mjs).
 *
 * Usage: node scripts/fmea-unpriced-shots.mjs [outDir]      env: BASE
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const here = path.dirname(fileURLToPath(import.meta.url))
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(here, '..', '..', '..', '.scratch', 'template-e2e', 'shots-11'))
const PROJECT = 'FMEA unpriced shots'
const TEMPLATE = 'eh_datacenter'

const mode_ = (name, cls, occ, basis) => ({
  mode_id: `generator:${name}:forced_outage`, component_class: cls, name, failure_class: 'A',
  occurrence_per_year: occ, occurrence_basis: basis, severity_eur: null, criticality_eur_per_year: null,
  delta_eue_mwh: 1.2, in_metric_scope: true, engine: 'copt', fidelity: 'analytic_convolution',
})
const MODES = {
  per_mode: [mode_('grid import', 'Generator', 2.0, 'FOR'), mode_('genset 1', 'Generator', 0.8, 'FOR'),
    mode_('UPS', 'StorageUnit', 0.3, 'FOR')],
  sweep_status: null, sweep_error: null, voll_eur_per_mwh: 0,
}
const WORKSHEET = { version: 1, overlays: {}, manual_rows: [{
  mode_id: 'manual:cyber', component_class: 'Network', name: 'cyber attack', failure_class: 'D',
  occurrence_per_year: 0.1, occurrence_basis: 'expert', severity_eur: 250000, criticality_eur_per_year: 25000,
  in_metric_scope: false, mitigability: 'segmentation', engine: 'expert', fidelity: 'expert_judgement' }] }

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
  const auth = await owner.storageState()
  await owner.close()

  for (const mode of ['guided', 'expert']) {
    for (const theme of ['dark', 'light']) {
      const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2, reducedMotion: 'reduce' })
      await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
      await ctx.addInitScript(({ mode, theme, project }) => {
        localStorage.setItem('network-diagram:current-project', project)
        localStorage.setItem('network-diagram:ui-mode', mode)
        localStorage.setItem('network-diagram:ui-mode-explicit', '1')
        localStorage.setItem('network-diagram:theme', theme)
      }, { mode, theme, project: PROJECT })
      const page = await ctx.newPage()
      page.setDefaultTimeout(15000)
      page.on('pageerror', e => problems.push(`${mode}/${theme}: page error: ${e.message}`))
      await page.route(u => u.pathname === '/api/results/fmea_modes', r => r.fulfill({ json: MODES }))
      const ws = `/api/projects/${encodeURIComponent(PROJECT)}/worksheet`
      await page.route(u => u.pathname === ws, r => (r.request().method() === 'GET' ? r.fulfill({ json: WORKSHEET }) : r.continue()))
      try {
        await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
        const resume = page.getByRole('link', { name: /Resume project/ })
        if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
        await page.getByText(PROJECT).first().waitFor(); await page.waitForTimeout(3000)
        await page.evaluate(async () => { const { showResultsTab } = await import('/src/layout/flowNavigation.ts'); showResultsTab('fmea') })
        await page.getByTestId('fmea-unpriced-line').waitFor(); await page.waitForTimeout(800)
        // One crop from the "Not priced" line to the table's last row.
        await page.getByTestId('fmea-unpriced-line').scrollIntoViewIfNeeded()
        const top = await page.getByTestId('fmea-unpriced-line').boundingBox()
        const table = await page.getByTestId('fmea-table').boundingBox()
        const x = Math.min(top.x, table.x) - 8
        const clip = { x, y: top.y - 8, width: Math.max(top.x + top.width, table.x + table.width) - x + 8,
          height: table.y + table.height - top.y + 16 }
        const file = path.join(OUT, `te2e-11-${mode}-${theme}.png`)
        await page.screenshot({ path: file, clip })
        written.push(file)
      } catch (e) {
        problems.push(`${mode}/${theme}: ${e.message.split('\n')[0]}`)
        await page.screenshot({ path: path.join(OUT, `failed-${mode}-${theme}.png`) }).catch(() => {})
      }
      await ctx.close()
    }
  }
} finally {
  await browser.close()
}
for (const w of written) console.log(w)
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
