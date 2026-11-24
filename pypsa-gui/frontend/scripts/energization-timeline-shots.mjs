// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * DC template review 06: the demand-against-energization chart, Guided (Site card) and Expert
 * (Issues › Grid readiness), light and dark. Real API on :8000 and Vite on :5173 (real sign-in and a
 * data-centre staged project); only GET /flow/energization_timeline is mocked, one body per state:
 * shortfall from inputs, no shortfall from inputs, after a run, after a run that is out of date.
 * Usage: node scripts/energization-timeline-shots.mjs [outDir]
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(frontend, '..', '..', '.scratch', 'dc-template-review-2026-11-24', 'shots', '06'))
const PROJECT = 'DC timeline shots'

const period = (p, start, end, demand, grid, gens, bess, run) => ({
  period: p, start, end, demand_mw: demand, grid_mw: grid, grid_set: true,
  onsite: [...(gens ? [{ kind: 'genset', mw: gens, units: ['genset_1'] }] : []), ...(bess ? [{ kind: 'battery', mw: bess, units: ['ups_battery'] }] : [])],
  onsite_mw: gens + bess, shortfall_mw: Math.max(demand - grid - gens - bess, 0), ...(run ? { run } : {}),
})
const ttp = { status: 'available', months: 14, first_power: '2028-03-01', stage: '2027' }
const mk = (basis, rows, extra = {}) => ({
  unit: 'MW', basis, run_state: basis === 'run' ? 'fresh' : null,
  basis_note: basis === 'run'
    ? 'the same inputs, with the last run\'s energy served, shed and curtailed in each period.'
    : 'the largest hourly facility load in each period, the energization schedule and the committed on-site units.',
  periods: rows, time_to_power: ttp, stage_reason: null, ...extra,
})
const RUN = { served_mwh: 410000, shed_mwh: 1200, curtailed_mwh: 300 }
const STATES = {
  'inputs-shortfall': mk('inputs', [period('2027', '2027-01-01', '2030-01-01', 40, 15, 12, 5), period('2030', '2030-01-01', '2035-01-01', 80, 55, 12, 5), period('2035', '2035-01-01', '2040-01-01', 90, 55, 12, 5)]),
  'inputs-none': mk('inputs', [period('2027', '2027-01-01', '2030-01-01', 30, 15, 12, 5), period('2030', '2030-01-01', '2035-01-01', 60, 55, 12, 5), period('2035', '2035-01-01', '2040-01-01', 70, 55, 12, 5)]),
  'run': mk('run', [period('2027', '2027-01-01', '2030-01-01', 40, 15, 12, 5, RUN), period('2030', '2030-01-01', '2035-01-01', 80, 55, 12, 5, RUN), period('2035', '2035-01-01', '2040-01-01', 90, 55, 12, 5, RUN)]),
  'run-out-of-date': { ...mk('run', [period('2027', '2027-01-01', '2030-01-01', 40, 15, 12, 5, RUN), period('2030', '2030-01-01', '2035-01-01', 80, 55, 12, 5, RUN)]), run_state: 'out_of_date' },
}

await fs.mkdir(OUT, { recursive: true })
const browser = await chromium.launch({ args: ['--no-sandbox'] })
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
  const auth = await owner.storageState()
  await owner.close()

  for (const mode of ['guided', 'expert']) for (const theme of ['light', 'dark']) {
    const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 1100 }, deviceScaleFactor: 2, reducedMotion: 'reduce' })
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
    let state = 'inputs-shortfall'
    await page.route(u => /\/flow\/energization_timeline$/.test(u.pathname), r => r.fulfill({ json: STATES[state] }))
    await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
    await page.evaluate(async ({ name }) => {
      const { default: client } = await import('/src/api/client.ts')
      const { projectsApi } = await import('/src/api/projects.ts')
      const list = await projectsApi.list().catch(() => [])
      if (!list.some(p => p.name === name)) await client.post(`/projects/from_template/eh_datacenter_staged?name=${encodeURIComponent(name)}`, { dc_load: {} })
    }, { name: PROJECT })
    await page.reload(); await page.waitForLoadState('networkidle')
    const resume = page.getByRole('link', { name: /Resume project/ })
    if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
    for (const key of ['inputs-shortfall', ...Object.keys(STATES)]) {
      try {
        state = key
        if (written.includes(`${mode}-${theme}-${key}.png`)) { /* the warm-up pass wrote it */ }
        await page.evaluate(async () => { const { showGridReadiness } = await import('/src/layout/flowNavigation.ts'); showGridReadiness() })
        const tl = page.getByTestId('energization-timeline')
        await page.reload(); await page.waitForLoadState('networkidle')
        if (mode === 'guided') {
          const t = page.getByTestId('hub-site-grid-readiness-toggle')
          if (await t.count() && (await t.getAttribute('aria-expanded')) !== 'true') await t.click()
        } else {
          await page.evaluate(async () => { const { showGridReadiness } = await import('/src/layout/flowNavigation.ts'); showGridReadiness() })
        }
        await tl.waitFor(); await tl.scrollIntoViewIfNeeded(); await page.waitForTimeout(600)
        const file = path.join(OUT, `${mode}-${theme}-${key}.png`)
        await tl.screenshot({ path: file }); written.push(path.basename(file))
      } catch (e) { if (!written.includes(`${mode}-${theme}-${key}.png`)) problems.push(`${mode}/${theme}/${key}: ${e.message.split("\n")[0]}`) }
    }
    await ctx.close()
  }
} finally { await browser.close() }
console.log(`wrote ${written.length}: ${OUT}`)
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
