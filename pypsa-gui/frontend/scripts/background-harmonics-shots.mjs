// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * Grid-compliance 29 screenshot harness, on the real app (pattern of dips-restart-shots.mjs): (a) the
 * "Background harmonics at the PCC" row at the end of Connection limits and (b) Results > Grid code >
 * "Harmonic emission (screening)". Guided and Expert, light and dark. Mocked: the campus state GET (its
 * results get `harmonic_emission`) and `.../background-harmonics` (GET/PUT/DELETE).
 *
 * Needs the API on :8000 and Vite on :5173, signed in as the dev admin (scripts/dev-credentials.mjs). Makes
 * the B-template project "Background harmonics shots" when it is missing.
 *
 * Files: gc29b-<mode>-<theme>-<state>.png; input row states `row-empty`, `row-stored`, `row-stale`,
 * `row-error`, `row-remove`; Guided home (mode guided only) `site-card-empty|stored|stale` (the read line on the
 * Site card) and `drawer-empty|stored|error` (the field drawer after Edit ›); Results states `res-none`, `res-used`, `res-unused`, `res-notcomputed`, `res-error`.
 *
 * Usage: node scripts/background-harmonics-shots.mjs [outDir]      env: BASE, MODES (guided,expert)
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const here = path.dirname(fileURLToPath(import.meta.url))
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(here, '..', '..', '..', '.scratch', 'grid-compliance', 'shots'))
const PROJECT = 'Background harmonics shots'
const TEMPLATE = 'eh_concentrator_staged'
const STATE = JSON.parse(await fs.readFile(path.join(here, 'dips-restart-shots.state.json'), 'utf8'))

const STORED = { has_background: true, source: 'Grid operator measurement campaign', measured_on: '2026-10-03',
  filename: 'pcc_background.csv', orders: 12, poc: 'grid', uploaded_at: '2026-11-20T09:14:00Z', in_latest_run: true }
const BASIS = 'screening estimate: harmonic current sources at the PCC against the grid code voltage-distortion limits; the binding answer is a harmonic study'
const rows = [
  { level: 'unit', unit: 'mill_1', thd_pct: 1.84, thd_limit_pct: 3, worst_order: 5, worst_u_h_pct: 1.2, worst_limit_pct: 2, verdict: 'pass' },
  { level: 'unit', unit: 'crusher', thd_pct: 3.6, thd_limit_pct: 3, worst_order: 7, worst_u_h_pct: 2.4, worst_limit_pct: 2, verdict: 'fail' },
  { level: 'unit', unit: 'flotation', thd_pct: null, thd_limit_pct: 3, worst_order: null, worst_u_h_pct: null, worst_limit_pct: null,
    verdict: 'unknown', note: 'no harmonic current spectrum set for this unit' },
  { level: 'poc', poc: 'grid', label: 'Point of common coupling', thd_pct: 2.9, thd_limit_pct: 3, worst_order: 5, worst_u_h_pct: 1.9, worst_limit_pct: 2, verdict: 'pass' },
]
const emission = background => ({ background, label: BASIS, rows })
const RES = {
  none: emission({ status: 'none' }),
  used: emission({ status: 'used', citation: 'utility measurement of 2026-10-03' }),
  unused: emission({ status: 'unusable', reason: 'the measurement is for another PCC than the one this run studies' }),
  notcomputed: null,
  error: { error: 'no harmonic source in the campus model' },
}
const ROWS = {
  empty: { bg: { has_background: false } },
  stored: { bg: STORED },
  stale: { bg: { ...STORED, in_latest_run: false } },
  error: { bg: { has_background: false }, putError: true },
  remove: { bg: STORED, remove: true },
}

await fs.mkdir(OUT, { recursive: true })
const browser = await chromium.launch()
const problems = [], written = []
try {
  const owner = await browser.newContext()
  const login = await owner.newPage()
  await login.goto(BASE)
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

  for (const mode of (process.env.MODES || 'guided,expert').split(',')) for (const theme of ['light', 'dark']) {
    const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2, reducedMotion: 'reduce' })
    await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
    await ctx.addInitScript(({ mode, project, theme }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', theme)
    }, { mode, project: PROJECT, theme })
    const page = await ctx.newPage()
    page.setDefaultTimeout(15000)
    page.on('pageerror', e => problems.push(`${mode}/${theme}: page error: ${e.message}`))
    let emissionNow = RES.used, rowNow = ROWS.empty
    const statePath = `/api/campus-electrical/${encodeURIComponent(PROJECT)}`
    await page.route(u => u.pathname === statePath, r => (r.request().method() === 'GET'
      ? r.fulfill({ json: { ...STATE, stale: false, results: { ...STATE.results, harmonic_emission: emissionNow } } })
      : r.continue()))
    await page.route(u => u.pathname === `${statePath}/background-harmonics`, r => {
      const m = r.request().method()
      if (m === 'PUT' && rowNow.putError) return r.fulfill({ status: 413, json: { detail: { message: 'File: larger than 1 MB', field: 'file' } } })
      if (m === 'GET') return r.fulfill({ json: rowNow.bg })
      return r.fulfill({ json: { has_background: false } })
    })

    await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
    const resume = page.getByRole('link', { name: /Resume project/ })
    if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
    await page.getByText(PROJECT).first().waitFor(); await page.waitForTimeout(3000)

    const step = async (key, fn) => {
      try { await fn() } catch (e) {
        problems.push(`${mode}/${theme}/${key}: ${e.message.split('\n')[0]}`)
        await page.screenshot({ path: path.join(OUT, `failed-${mode}-${theme}-${key}.png`) }).catch(() => {})
      }
    }
    // Crop one element; hide fixed overlays (chat dock, back pill) that sit over it.
    const shot = async (box, key) => {
      await box.scrollIntoViewIfNeeded()
      const view = page.viewportSize()
      const tall = (await box.boundingBox())?.height ?? 0
      if (tall + 600 > view.height) { await page.setViewportSize({ ...view, height: Math.ceil(tall) + 600 }); await page.waitForTimeout(500); await box.scrollIntoViewIfNeeded() }
      await box.evaluate(el => {
        window.__hid = []
        for (const n of document.body.querySelectorAll('*')) {
          if (el.contains(n) || n.contains(el)) continue
          if ((getComputedStyle(n).position === 'fixed' || n.matches('[data-testid="companion-launcher"]')) && n.style.visibility !== 'hidden') { n.style.visibility = 'hidden'; window.__hid.push(n) }
        }
      })
      const file = path.join(OUT, `gc29b-${mode}-${theme}-${key}.png`)
      await box.screenshot({ path: file })
      await page.evaluate(() => { for (const n of window.__hid ?? []) n.style.visibility = '' })
      if (page.viewportSize().height !== view.height) await page.setViewportSize(view)
      written.push(path.basename(file))
    }
    const reload = async () => { await page.reload(); await page.waitForLoadState('networkidle'); await page.getByText(PROJECT).first().waitFor(); await page.waitForTimeout(2500) }
    const rowLoc = () => page.getByTestId('connection-limit-background_harmonics')

    // Guided shows no input row: its Site card's connection section is the Grid readiness scorecard, and
    // ConnectionLimits renders only in Expert's campus electrical panel (the Guided field drawer passes
    // `only`, which hides the row). So Guided has the Results shots alone.
    for (const [key, def] of Object.entries(mode === 'guided' ? {} : ROWS)) {
      await step(`row-${key}`, async () => {
        rowNow = def; emissionNow = RES.used
        await reload()
        // Guided: the Site card's connection section; Expert: Connection & grid's campus electrical panel.
        await page.evaluate(async mode => {
          const nav = await import('/src/layout/flowNavigation.ts')
          if (mode === 'guided') nav.showGridReadiness(); else nav.openFlowTarget(nav.FAULT_LEVEL_TARGET)
        }, mode)
        await rowLoc().waitFor(); await page.waitForTimeout(800)
        if (def.putError) {
          await page.locator('#bg-source').fill('Grid operator')
          await page.locator('#bg-date').fill('2026-10-03')
          await page.locator('#bg-file').setInputFiles({ name: 'pcc_background.csv', mimeType: 'text/csv', buffer: Buffer.from('order,u_pct\n5,1.2\n7,0.8\n') })
          await page.getByRole('button', { name: 'Save the measurement' }).click()
          await page.getByTestId('background-upload-error').waitFor()
        }
        if (def.remove) { await page.getByRole('button', { name: 'Remove', exact: true }).click(); await page.getByRole('group', { name: 'Remove the measurement' }).waitFor() }
        await shot(rowLoc(), `row-${key}`)
      })
    }
    // Guided's home: the read line on the Site card, and the field drawer Edit › opens (the row alone).
    const GUIDED = { empty: ROWS.empty, stored: ROWS.stored, stale: ROWS.stale }
    for (const key of Object.keys(mode === 'guided' ? GUIDED : {})) {
      await step(`site-card-${key}`, async () => {
        rowNow = GUIDED[key]; emissionNow = RES.used
        await reload()
        await page.evaluate(async () => { const nav = await import('/src/layout/flowNavigation.ts'); nav.showGridReadiness() })
        await page.getByTestId('hub-site-background-harmonics').waitFor(); await page.waitForTimeout(800)
        await shot(page.getByTestId('hub-site-readiness'), `site-card-${key}`)
      })
    }
    for (const [key, def] of Object.entries(mode === 'guided' ? { empty: ROWS.empty, stored: ROWS.stored, error: ROWS.error } : {})) {
      await step(`drawer-${key}`, async () => {
        rowNow = def; emissionNow = RES.used
        await reload()
        await page.evaluate(async () => { const nav = await import('/src/layout/flowNavigation.ts'); nav.showGridReadiness() })
        await page.getByTestId('hub-site-background-harmonics-edit').click()
        await rowLoc().waitFor(); await page.waitForTimeout(800)
        if (def.putError) {
          await page.locator('#bg-source').fill('Grid operator')
          await page.locator('#bg-date').fill('2026-10-03')
          await page.locator('#bg-file').setInputFiles({ name: 'pcc_background.csv', mimeType: 'text/csv', buffer: Buffer.from('order,u_pct\n5,1.2\n7,0.8\n') })
          await page.getByRole('button', { name: 'Save the measurement' }).click()
          await page.getByTestId('background-upload-error').waitFor()
        }
        await shot(page.getByTestId('field-drawer'), `drawer-${key}`)
      })
    }
    for (const key of Object.keys(process.env.ONLY_NEW ? {} : RES)) {
      await step(`res-${key}`, async () => {
        rowNow = ROWS.stored; emissionNow = RES[key]
        await reload()
        await page.evaluate(async () => { const { showResultsTab } = await import('/src/layout/flowNavigation.ts'); showResultsTab('grid_code') })
        const title = page.getByText('Harmonic emission (screening)', { exact: true }).first()
        await title.waitFor(); await page.waitForTimeout(800)
        await shot(page.locator('section').filter({ has: title }).last(), `res-${key}`)
      })
    }
    await ctx.close()
  }
} finally {
  await browser.close()
}
console.log(`wrote ${written.length}: ${written.join(', ')}`)
if (problems.length) { console.log(problems.join('\n')); process.exitCode = 1 }
