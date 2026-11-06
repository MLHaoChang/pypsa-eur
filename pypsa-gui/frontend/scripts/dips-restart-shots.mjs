// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * Grid-to-process 12b-3 screenshot harness: "Dips and restarts" in section 10's campus results (Results ›
 * Grid code), Guided and Expert, on the real app. Only the campus state is mocked (`GET
 * /api/campus-electrical/<project>`): the sizing results are a backend test run's
 * (`dips-restart-shots.state.json`) and the dip and restart screens are set per state below, on the B
 * template's unit names, so the fix links open real Links of the project.
 *
 * Needs the API on :8000 and Vite on :5173, signed in as the dev admin (scripts/dev-credentials.mjs). Makes
 * the B-template project "Dips restart shots" when it is missing.
 *
 * States: `1-full` (pass, fail and unknown rows, an incomplete and two lower-bound totals, the demo tag, one
 * unit out of date; the timeline in Expert), `2-complete` (operator statistics, every verdict known: no tag),
 * `3-not-computed`, `4-error`, `5-fix-link` (after the Ride-through curve › link of flotation: the Guided field
 * drawer or the Expert Properties, each with Back to the results).
 *
 * Usage: node scripts/dips-restart-shots.mjs [outDir]      env: BASE
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
const PROJECT = 'Dips restart shots'
const TEMPLATE = 'eh_concentrator_staged'
const STATE = JSON.parse(await fs.readFile(path.join(here, 'dips-restart-shots.state.json'), 'utf8'))

const NONE = { ride_through_curve: null, restart_time_h: null, motor_kw: null, start_method: null, i_start_pu: null }
const ISLAND = 'no genset on the island: its short-circuit power is unknown'
const BASIS = [
  'dip statistics per PoC: EN 50160 / IEC 61000-2-8 classes, demo counts per year',
  'pickups start 1 s after the event, one block every 60 s (demo, assumed)',
]
const dipUnit = (unit, over = {}) => ({ level: 'unit', unit, trips_per_year: 20.45, restart_time_h: 4, rate_tph: 550,
  mass_pull: 1, lost_t_per_trip: 2200, lost_t_per_year: 44990, verdict: 'known', dip_stats_source: 'demo', note: null, ...over })
const restartUnit = (transition, unit, start, over = {}) => ({ level: 'unit', transition, unit, start_verdict: start,
  verdict: start, du_pct: start === 'unknown' ? null : 1.62, lost_t: 2209, note: null, ...over })
const total = (transition, over = {}) => ({ level: 'total', transition, lost_t: 4418, fail_count: 0, unknown_count: 0,
  lower_bound: false, ...over })
const event = (transition, unit, t_s, ev, over = {}) => ({ level: 'event', transition, unit, t_s, event: ev, ...over })

function full() {
  return {
    inputs: {
      mill_1: { ride_through_curve: '15:1, 30:0.2, 50:0.05', restart_time_h: 4, motor_kw: 6000, start_method: 'vfd', i_start_pu: null },
      crusher: { ride_through_curve: '20:0.5', restart_time_h: 2, motor_kw: 3000, start_method: 'dol', i_start_pu: null },
      flotation: NONE,
    },
    dip_label: 'screening estimate; the binding answer is the operator\'s dip statistics and the units\' tested curves',
    restart_label: 'event model, not dynamics; the binding answer is an RMS study of the restart',
    basis: BASIS,
    dip_trips: [
      dipUnit('mill_1'), dipUnit('crusher', { restart_time_h: 2, rate_tph: 1100, lost_t_per_year: 44990 }),
      dipUnit('flotation', { verdict: 'unknown', trips_per_year: null, restart_time_h: null, lost_t_per_year: null }),
      { level: 'total', lost_t_per_year: 89980, incomplete: true, unknown_count: 1, known_count: 2, dip_stats_source: 'demo' },
    ],
    restart: [
      event('trip', 'flotation', 0, 'trip', { note: 'flotation trips (assumed: no ride-through curve)' }),
      event('trip', 'mill_1', 0, 'trip', { note: 'mill_1 trips' }),
      event('trip', 'crusher', 0, 'trip', { note: 'crusher trips' }),
      restartUnit('trip', 'flotation', 'unknown', { note: 'no ride-through curve: assumed to trip; no input: restart time not set; motor start on the PoC unknown' }),
      restartUnit('trip', 'mill_1', 'pass'),
      restartUnit('trip', 'crusher', 'fail', { du_pct: 29.63 }),
      event('trip', 'flotation', 1, 'pickup', { block: 1, verdict: 'unknown', note: 'motor start on the PoC: no input: Motor rating and Start method not set' }),
      event('trip', 'mill_1', 61, 'pickup', { block: 2, du_pct: 1.62, verdict: 'pass', note: 'motor start on the PoC: variable-speed drive start of 6000 kW; dU 1.62 % (within 3 %)' }),
      event('trip', 'crusher', 121, 'pickup', { block: 3, du_pct: 29.63, verdict: 'fail', note: 'motor start on the PoC: direct-on-line start of 3000 kW; dU 29.63 % (above 3 %)' }),
      event('trip', 'crusher', 7321, 'full_rate', { block: 3, note: 'crusher back at full rate' }),
      event('trip', 'mill_1', 14461, 'full_rate', { block: 2, note: 'mill_1 back at full rate' }),
      total('trip', { fail_count: 1, unknown_count: 1, lower_bound: true }),
      restartUnit('island', 'flotation', 'unknown', { note: `${ISLAND}; no input: restart time not set; motor start on the island unknown` }),
      restartUnit('island', 'mill_1', 'unknown', { note: `${ISLAND}; motor start on the island unknown` }),
      restartUnit('island', 'crusher', 'unknown', { note: `${ISLAND}; motor start on the island unknown` }),
      event('island', 'mill_1', 0, 'island', { note: 'mill_1 trips' }),
      total('island', { unknown_count: 3, lower_bound: true }),
    ],
    out_of_date: ['mill_1'],
  }
}

function complete() {
  const p = full()
  p.inputs.flotation = { ride_through_curve: '20:0.5', restart_time_h: 1, motor_kw: 800, start_method: 'soft', i_start_pu: null }
  p.dip_trips = [dipUnit('mill_1'), dipUnit('crusher', { restart_time_h: 2 }),
    dipUnit('flotation', { restart_time_h: 1, lost_t_per_year: 22495 }),
    { level: 'total', lost_t_per_year: 112475, incomplete: false, unknown_count: 0, known_count: 3 }]
    .map(r => ({ ...r, dip_stats_source: 'user' }))
  p.restart = [
    restartUnit('trip', 'mill_1', 'pass'), restartUnit('trip', 'crusher', 'pass', { du_pct: 2.4 }),
    restartUnit('trip', 'flotation', 'pass', { du_pct: 0.4 }),
    event('trip', 'mill_1', 1, 'pickup', { block: 1, du_pct: 1.62, verdict: 'pass', note: 'motor start on the PoC: pass' }),
    total('trip'),
    restartUnit('island', 'mill_1', 'pass', { du_pct: 2.1 }), restartUnit('island', 'crusher', 'pass', { du_pct: 2.9 }),
    restartUnit('island', 'flotation', 'pass', { du_pct: 0.6 }),
    total('island'),
  ]
  p.out_of_date = []
  return p
}

const STATES = {
  '1-full': { dips: full(), stale: true },
  '2-complete': { dips: complete(), stale: false },
  '3-not-computed': { dips: null, stale: false },
  '4-error': { dips: { error: 'no process unit on the campus' }, stale: false },
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
  // The B template leaves AC power flow & grid's sub-options empty. The section needs its Grid code &
  // electrical design option: "Add AC power flow & grid" would add only Load flow, which opening the
  // project drops again (it follows the solver config), so the card falls back out of scope on reload.
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
    let current = STATES['1-full']
    const statePath = `/api/campus-electrical/${encodeURIComponent(PROJECT)}`
    await page.route(u => u.pathname === statePath, r => (r.request().method() === 'GET'
      ? r.fulfill({ json: { ...STATE, stale: current.stale, results: { ...STATE.results, dips_restart: current.dips } } })
      : r.continue()))

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
    const section = () => page.getByText('Dips and restarts', { exact: true }).first()
    const showResults = async () => {
      await page.evaluate(async () => { const { showResultsTab } = await import('/src/layout/flowNavigation.ts'); showResultsTab('grid_code') })
      await section().waitFor(); await page.waitForTimeout(800)
    }
    const shotSection = async (key) => {
      await section().scrollIntoViewIfNeeded()
      const box = page.locator('section').filter({ has: page.getByText('Dips and restarts', { exact: true }) }).last()
      const file = path.join(OUT, `g2p-12b3-${mode}-${key}.png`)
      // A section near the window's height would run under the status bar: grow the window for the crop.
      const view = page.viewportSize()
      const tall = (await box.boundingBox())?.height ?? 0
      if (tall + 600 > view.height) { await page.setViewportSize({ ...view, height: Math.ceil(tall) + 600 }); await page.waitForTimeout(500); await section().scrollIntoViewIfNeeded() }
      // The floating chat dock, assistant button and "Back to …" pill sit over the section: hide fixed overlays
      // outside it for the crop only.
      const hidden = await box.evaluate(el => {
        const out = []
        for (const n of document.body.querySelectorAll('*')) {
          if (el.contains(n) || n.contains(el)) continue
          if ((getComputedStyle(n).position === 'fixed' || n.matches('[data-testid="companion-launcher"]')) && n.style.visibility !== 'hidden') { n.style.visibility = 'hidden'; out.push(n) }
        }
        window.__dipsHidden = out
        return out.length
      })
      await box.screenshot({ path: file }).catch(async () => page.screenshot({ path: file }))
      if (hidden) await page.evaluate(() => { for (const n of window.__dipsHidden ?? []) n.style.visibility = '' })
      if (page.viewportSize().height !== view.height) await page.setViewportSize(view)
      written.push(path.basename(file))
    }

    for (const key of Object.keys(STATES)) {
      await step(key, async () => {
        current = STATES[key]
        await page.reload(); await page.waitForLoadState('networkidle')
        await page.getByText(PROJECT).first().waitFor(); await page.waitForTimeout(2500)
        await showResults()
        await shotSection(key)
      })
    }
    await step('5-fix-link', async () => {
      current = STATES['1-full']
      await page.reload(); await page.waitForLoadState('networkidle')
      await page.getByText(PROJECT).first().waitFor(); await page.waitForTimeout(2500)
      await showResults()
      const link = page.getByTestId('dips-fix-flotation-ride_through_curve')
      await link.scrollIntoViewIfNeeded(); await link.click()
      await page.getByRole('button', { name: /^Back to/ }).first().waitFor()
      await page.waitForTimeout(1200)
      const file = path.join(OUT, `g2p-12b3-${mode}-5-fix-link.png`)
      await page.screenshot({ path: file }); written.push(path.basename(file))
    })
    await ctx.close()
  }
} finally {
  await browser.close()
}
console.log(`wrote ${written.length}: ${written.join(', ')}`)
if (problems.length) { console.log(problems.join('\n')); process.exitCode = 1 }
