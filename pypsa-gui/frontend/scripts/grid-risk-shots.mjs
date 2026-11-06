// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * Grid-to-process 13 screenshot harness: "Grid risk to production" (Results › Reliability › Grid risk to
 * production) and its report chapter, Guided and Expert, on the real app. Mocked routes only:
 * `GET /api/results/grid_risk_production` (the states below), `GET /api/results/process_view` (one process unit, so
 * the tab shows) and the report list and document (`GET /api/projects/<project>/reports[/<id>]`).
 *
 * `grid-risk-shots.state.json` holds real backend output: each state is `services.results.grid_risk_production` on
 * the solved chain of `tests/test_grid_risk_production.py` (with and without a campus run, a site transformer, a
 * lost-production value, a stale run, two periods), and `report` is `assemble.evidence_only_document` of the first.
 *
 * Needs the API on :8000 and Vite on :5173, signed in as the dev admin (scripts/dev-credentials.mjs). Makes the
 * B-template project "Grid risk shots" when it is missing.
 *
 * States: `1-all` (every line known), `2-partial` (ride-through and island lower bounds), `3-missing` (no campus
 * run and no site transformer: Missing links, total incomplete), `4-eur-unavailable`, `5-out-of-date`,
 * `6-multi-period` (the period Seg, 2040 picked), `7-missing-link` (after the "Missing: Dips and restarts" link,
 * Back shown), `8-report` (the report chapter).
 *
 * Usage: node scripts/grid-risk-shots.mjs [outDir]      env: BASE
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const here = path.dirname(fileURLToPath(import.meta.url))
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(here, '..', '..', '..', '.scratch', 'grid-to-process', 'shots'))
const PROJECT = 'Grid risk shots'
const TEMPLATE = 'eh_concentrator_staged'
const DATA = JSON.parse(await fs.readFile(path.join(here, 'grid-risk-shots.state.json'), 'utf8'))
const TITLE = 'Grid risk to production'
const PROCESS_VIEW = { available: true, process_units: [{ name: 'mill', product_bus: 'concentrate' }] }
const REPORT = DATA.report
const META = { report_id: REPORT.report_id, title: 'Grid risk shots', created_at: '2026-11-06T09:30:00+00:00',
  updated_at: '2026-11-06T09:30:00+00:00', latest_version: 1, mode: 'evidence_only', evidence_hash: REPORT.evidence_hash ?? '' }

const problems = []
const written = []
await fs.mkdir(OUT, { recursive: true })
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
  if (made.startsWith('failed')) problems.push(`create: ${made}`)
  // The "Dips and restarts" links open Results › Grid code, which needs the Grid code & electrical design option
  // in scope (the B template leaves it out; see dips-restart-shots.mjs).
  const scoped = await login.evaluate(async ({ name }) => {
    const { getFlowScope, putFlowScope } = await import('/src/api/flow.ts')
    const { scope } = await getFlowScope(name)
    const grid = scope.options.grid ?? []
    if (scope.groups.includes('grid') && grid.includes('grid_code')) return 'in scope'
    try {
      await putFlowScope(name, { ...scope, groups: [...scope.groups.filter(g => g !== 'grid'), 'grid'],
        options: { ...scope.options, grid: [...grid, 'grid_code'] } })
      return 'added'
    } catch (e) { return `failed: ${e.message}` }
  }, { name: PROJECT })
  if (scoped.startsWith('failed')) problems.push(`scope: ${scoped}`)
  const auth = await owner.storageState()
  await owner.close()

  for (const mode of ['guided', 'expert']) {
    const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2, reducedMotion: 'reduce' })
    await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
    await ctx.addInitScript(({ mode, project }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', 'light')
    }, { mode, project: PROJECT })
    const page = await ctx.newPage()
    page.setDefaultTimeout(15000)
    page.on('pageerror', e => problems.push(`${mode}: page error: ${e.message}`))
    let current = DATA.states['1-all']
    await page.route(u => u.pathname === '/api/results/grid_risk_production', r => r.fulfill({ json: current }))
    await page.route(u => u.pathname === '/api/results/process_view', r => r.fulfill({ json: PROCESS_VIEW }))
    const reports = `/api/projects/${encodeURIComponent(PROJECT)}/reports`
    await page.route(u => u.pathname === reports, r => (r.request().method() === 'GET' ? r.fulfill({ json: [META] }) : r.continue()))
    await page.route(u => u.pathname === `${reports}/${REPORT.report_id}`, r => r.fulfill({ json: REPORT }))

    await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
    const resume = page.getByRole('link', { name: /Resume project/ })
    if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
    await page.getByText(PROJECT).first().waitFor(); await page.waitForTimeout(4000)

    const step = async (key, fn) => {
      try { await fn() } catch (e) {
        problems.push(`${mode}/${key}: ${e.message.split('\n')[0]}`)
        await page.screenshot({ path: path.join(OUT, `failed-${mode}-${key}.png`) }).catch(() => {})
      }
    }
    const reload = async () => {
      await page.reload(); await page.waitForLoadState('networkidle')
      await page.getByText(PROJECT).first().waitFor(); await page.waitForTimeout(2500)
    }
    const showTab = async () => {
      await page.evaluate(async () => { const { showResultsTab } = await import('/src/layout/flowNavigation.ts'); showResultsTab('grid_risk') })
      await page.getByTestId('grid-risk-section').waitFor(); await page.waitForTimeout(800)
    }
    // Crop an element; fixed overlays outside it (chat dock, assistant button, Back pill) are hidden for the crop.
    const shot = async (box, key) => {
      await box.scrollIntoViewIfNeeded()
      const file = path.join(OUT, `g2p-13-${mode}-${key}.png`)
      const view = page.viewportSize()
      const tall = (await box.boundingBox())?.height ?? 0
      if (tall + 400 > view.height) { await page.setViewportSize({ ...view, height: Math.ceil(tall) + 400 }); await page.waitForTimeout(500); await box.scrollIntoViewIfNeeded() }
      const hidden = await box.evaluate(el => {
        const out = []
        for (const n of document.body.querySelectorAll('*')) {
          if (el.contains(n) || n.contains(el)) continue
          if ((getComputedStyle(n).position === 'fixed' || n.matches('[data-testid="companion-launcher"]')) && n.style.visibility !== 'hidden') { n.style.visibility = 'hidden'; out.push(n) }
        }
        window.__shotHidden = out
        return out.length
      })
      await box.screenshot({ path: file }).catch(async () => page.screenshot({ path: file }))
      if (hidden) await page.evaluate(() => { for (const n of window.__shotHidden ?? []) n.style.visibility = '' })
      if (page.viewportSize().height !== view.height) await page.setViewportSize(view)
      written.push(path.basename(file))
    }
    const tabSection = () => page.locator('section').filter({ has: page.getByTestId('grid-risk-section') }).last()

    for (const key of Object.keys(DATA.states)) {
      await step(key, async () => {
        current = DATA.states[key]
        await reload(); await showTab()
        if (key === '6-multi-period') {
          await page.getByTestId('grid-risk-period').getByRole('button', { name: '2040' }).click()
          await page.waitForTimeout(400)
        }
        await shot(tabSection(), key)
      })
    }
    await step('7-missing-link', async () => {
      current = DATA.states['3-missing']
      await reload(); await showTab()
      await page.getByTestId('grid-risk-ride_through-missing-0').click()
      await page.getByRole('button', { name: /^Back to/ }).first().waitFor()
      await page.waitForTimeout(1200)
      const file = path.join(OUT, `g2p-13-${mode}-7-missing-link.png`)
      await page.screenshot({ path: file }); written.push(path.basename(file))
    })
    await step('8-report', async () => {
      await reload()
      await page.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().setSlidePanel('reports') })
      await page.getByTestId('report-open').first().click()
      const card = page.locator('section').filter({ has: page.getByRole('heading', { name: TITLE, exact: true }) }).last()
      await card.waitFor(); await page.waitForTimeout(800)
      await shot(card, '8-report')
    })
    await ctx.close()
  }
} finally {
  await browser.close()
}
console.log(`wrote ${written.length}: ${written.join(', ')}`)
if (problems.length) { console.log(problems.join('\n')); process.exitCode = 1 }
