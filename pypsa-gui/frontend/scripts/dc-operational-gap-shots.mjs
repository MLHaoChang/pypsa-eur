// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * DC grid readiness 07 S4 screenshot harness: Results › Summary's "Plan vs operation" card, Guided and Expert, on
 * the real app. The gap route and the Summary's own result routes answer from route mocks, so the project never
 * changes and no solve runs.
 *
 * Needs the API on :8000 and Vite on :5173, signed in as the dev admin (scripts/dev-credentials.mjs). Makes the
 * `eh_datacenter` project "DC operational gap shots" when it is missing.
 *
 * States per mode: `1-fresh`, `2-dash-row` (a measure the network lacks), `3-out-of-date`, `4-window-failed`,
 * `5-reference-not-solved`, `6-back-pill` (after "Solver settings ›" from the window-failed line).
 *
 * Usage: node scripts/dc-operational-gap-shots.mjs [outDir]      env: BASE
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const here = path.dirname(fileURLToPath(import.meta.url))
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(here, '..', '..', '..', '.scratch', 'datacenter-grid-readiness', 'shots'))
const PROJECT = 'DC operational gap shots'
const TEMPLATE = 'eh_datacenter'

const DEF = 'engine definition (Source)'
const row = (o, r, extra = {}) => ({
  definition: DEF, operational: o, reference: r,
  gap: o == null || r == null ? null : o - r,
  gap_pct: o == null || r == null || r === 0 ? null : (100 * (o - r)) / Math.abs(r),
  ...extra,
})
const BASE_GAP = {
  label: 'full-horizon dispatch on the same sizes',
  opex_basis: 'PyPSA statistics.opex (marginal + storage costs, curtailment and VOLL slack included as marginal cost) x objective snapshot weightings over the full horizon, on each side\'s own dispatch; LP-only objective terms excluded; n.objective is never read',
  excluded_terms: ['capital costs (the sizes are fixed and equal on both sides)', 'the objective constant', 'LP-only penalty and cost rows added by extra_functionality'],
  claim_limit: 'The gap is what the lack of look-ahead past each window costs on these sizes, against a solve that sees the whole horizon at once. It is not a forecast-error measure: both sides see the same prices and loads.', gap_definition: 'gap = operational - reference; gap_pct = 100 x gap / |reference|.',
  windows_definition: 'windows', definitions: {},
  windows: { total: 12, solved: 12, failed: 0, failed_index: null },
  reference: { status: 'ok', condition: 'optimal' }, horizon: 24, overlap: 6,
}
const FRESH_METRICS = {
  opex_eur: row(1_284_000, 1_231_500),
  unserved_energy_mwh: row(3.2, 0.8),
  curtailed_energy_mwh: row(41.5, 28.0),
  storage_discharge_mwh: row(612.4, 655.1),
  storage_end_mwh: row(4.0, 0),
}
const STATES = {
  '1-fresh': { ...BASE_GAP, flag: 'fresh', metrics: FRESH_METRICS },
  '2-dash-row': { ...BASE_GAP, flag: 'fresh', metrics: { ...FRESH_METRICS,
    curtailed_energy_mwh: row(null, null, { operational_reason: 'curtailment is off (no dc_curtailment generator)', reference_reason: 'curtailment is off (no dc_curtailment generator)' }) } },
  '3-out-of-date': { ...BASE_GAP, flag: 'stale', metrics: null, windows: null, reference: null, horizon: undefined, overlap: undefined },
  '4-window-failed': { ...BASE_GAP, flag: 'operational_window_failed', metrics: null, windows: { total: 12, solved: 8, failed: 1, failed_index: 8 } },
  '5-reference-not-solved': { ...BASE_GAP, flag: 'reference_not_solved', metrics: null, reference: { status: 'warning', condition: 'infeasible' } },
}
const STATUS = { running: false, status: 'ok', condition: 'optimal', objective: 1_402_000, solve_time: 14.2, dispatch: 'fresh' }
const COST = { total: 2_900_000, capex: 1_600_000, opex: 1_300_000, fom: 0, capex_expansion: 0, capex_lifetime: 0, capex_expansion_lifetime: 0, by_period: [] }
const EMISSIONS = { total_tCO2: 812, caps: [], by_period: [] }

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
    try { await projectsApi.createFromTemplate(tid, name, { dc_load: {} }); return 'made' } catch (e) { return `failed: ${e.message}` }
  }, { tid: TEMPLATE, name: PROJECT })
  if (made.startsWith('failed')) problems.push(`create: ${made}`)
  const auth = await owner.storageState()
  await owner.close()

  for (const mode of (process.env.MODES || 'guided,expert').split(',')) {
    for (const [state, gap] of Object.entries(STATES).filter(([k]) => !process.env.ONLY || k === process.env.ONLY)) {
      const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2, reducedMotion: 'reduce' })
      await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
      await ctx.addInitScript(({ mode, project }) => {
        localStorage.setItem('network-diagram:current-project', project)
        localStorage.setItem('network-diagram:ui-mode', mode)
        localStorage.setItem('network-diagram:ui-mode-explicit', '1')
        localStorage.setItem('network-diagram:theme', 'light')
      }, { mode, project: PROJECT })
      const page = await ctx.newPage()
      page.setDefaultTimeout(30000)
      page.on('pageerror', e => problems.push(`${mode}/${state}: page error: ${e.message}`))
      await page.route(u => u.pathname === '/api/results/operational_gap', r => r.fulfill({ json: gap }))
      await page.route(u => u.pathname === '/api/simulation/status', r => r.fulfill({ json: STATUS }))
      await page.route(u => u.pathname === '/api/results/cost_breakdown', r => r.fulfill({ json: COST }))
      await page.route(u => u.pathname === '/api/results/emissions', r => r.fulfill({ json: EMISSIONS }))
      await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
      const resume = page.getByText('Resume project')
      await resume.first().waitFor()
      await resume.first().click()
      await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle')
      await page.waitForTimeout(3000)
      await page.evaluate(async () => {
        const { useUIStore } = await import('/src/store/uiStore.ts')
        useUIStore.getState().setSlidePanel('results')
      })
      const card = page.getByTestId('summary-operational-gap')
      try {
        await card.waitFor()
        // The card at the top, so the floating chat button never covers the table.
        await card.evaluate(el => el.scrollIntoView({ block: 'start' }))
        // Let any startup toast clear, so it does not cover the card.
        await page.getByText('Refusing to overwrite').waitFor({ state: 'detached', timeout: 20000 }).catch(() => {})
        await page.waitForTimeout(300)
        const file = path.join(OUT, `dc07-s4-${mode}-${state}.png`)
        await page.screenshot({ path: file })
        written.push(file)
        if (state === '4-window-failed') {
          await page.getByTestId('operational-gap-open-strategy').click()
          await page.locator('#solver-solve-strategy').waitFor()
          await page.waitForTimeout(400)
          const back = path.join(OUT, `dc07-s4-${mode}-6-back-pill.png`)
          await page.screenshot({ path: back })
          written.push(back)
        }
      } catch (e) {
        problems.push(`${mode}/${state}: ${e.message.split('\n')[0]}`)
        if (process.env.DEBUG_SHOT) await page.screenshot({ path: path.join(OUT, `debug-${mode}-${state}.png`) })
      }
      await ctx.close()
    }
  }
} finally {
  await browser.close()
}
console.log(written.join('\n'))
if (problems.length) { console.error(problems.join('\n')); process.exitCode = 1 }
