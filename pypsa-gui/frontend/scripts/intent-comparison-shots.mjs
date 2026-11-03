// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * AS-10 PR 2: screenshot the preference-comparison section in eight states, Guided and Expert, light and dark
 * (UX plan condition). Route-mocked harness (as #917, #928, #990): the API on :8000 and Vite on :5173 run for
 * the real sign-in; only the campus state, island and intent-comparison routes are mocked. Invented numbers.
 *
 * Usage: node scripts/intent-comparison-shots.mjs [outDir]
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(frontend, '..', '..', '.scratch', 'asset-sizing-loop', 'evidence', '10'))
const PROJECT = 'Intent comparison shots'

const chk = (name, state) => ({ name, state, value: null, limit: null, note: '' })
const row = (intent, over = {}) => ({
  intent, status: 'converged', outcome: 'pass', verdict: '', cost_eur: 12400000 + intent.charCodeAt(0) * 91000,
  assets: { 'store:bess': { value: 320, unit: 'MWh' }, 'charger:bess': { value: 80, unit: 'MW' }, q_ss: { value: 12, unit: 'Mvar' } },
  transformer_mva: 100, statcom_mvar: 20,
  checks: [chk('escr', 'ok'), chk('transformer', 'ok'), chk('harmonics', 'not_run')],
  basis_note: 'same LP; UC/MIP options not reproduced', warnings: [], backup_autonomy_h: null, backup_energy_mwh: null, ...over,
})
const job = (rows, over = {}) => ({ status: 'done', progress: Object.fromEntries(rows.map(r => [r.intent, 'done'])), rows, error: null, ...over })
const D_WARN = 'This grid code may not count transfer to the BESS as ride-through: check it before relying on this row.'
const FAILED = { status: 'failed', outcome: 'failed', verdict: 'Solver refused the model', cost_eur: null, assets: {}, transformer_mva: null, statcom_mvar: null, checks: [] }
const STOPPED = { status: 'aborted', outcome: 'aborted', verdict: 'Stopped before this intent ran', cost_eur: null, assets: {}, transformer_mva: null, statcom_mvar: null, checks: [] }
// `fill`: the form inputs are typed in (false = left empty, so the run is blocked and says why).
const STATES = {
  '1-nothing-picked': { pick: [], fill: false, job: null },
  '2-one-picked': { pick: ['B'], fill: true, job: null },
  '3-d-no-grid-code': { pick: ['A', 'D'], fill: true, gridCode: null, job: null },
  '4-d-grid-code-warning': { pick: [], fill: false, gridCode: 'eu_rfg_dcc_ce', job: job([row('D', { warnings: [D_WARN] }), row('B')]) },
  '5-running': { pick: [], fill: false, job: job([], { status: 'running', progress: { A: 'done', B: 'running', C: 'queued' } }) },
  '6-mixed-results': { pick: [], fill: false, job: job([row('B'), row('C', { status: 'not_converged', outcome: 'not_converged', verdict: 'Loop hit its round limit' }),
    row('E', { ...FAILED }), row('D', { outcome: 'fail', checks: [chk('escr', 'fail'), chk('transformer', 'ok')] }), row('A', { backup_autonomy_h: 4, backup_energy_mwh: 320, checks: [chk('escr', 'ok'), chk('harmonics', 'not_run'), chk('grid_readiness', 'not_run')] })]) },
  '7-blocked-no-inputs': { pick: ['A', 'B'], fill: false, job: null },
  '8-stopped': { pick: [], fill: false, job: job([row('B'), { ...row('C'), ...STOPPED }], { status: 'aborted' }) },
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
  const auth = await owner.storageState()
  await owner.close()

  for (const mode of ['guided', 'expert']) for (const theme of ['light', 'dark']) {
    const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 1400 }, reducedMotion: 'reduce' })
    await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
    await ctx.addInitScript(({ mode, theme, project }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', theme)
    }, { mode, theme, project: PROJECT })
    const page = await ctx.newPage()
    page.setDefaultTimeout(10000)
    page.on('pageerror', e => problems.push(`${mode}/${theme}: page error: ${e.message}`))
    let current = STATES['1-nothing-picked']
    const campusState = () => ({ campus_yaml: null, skipped: [], profiles: {}, stale: false, hub_cost: null, hub_cost_reason: null,
      results: null, settings: current.gridCode ? { profile: current.gridCode } : null })
    await page.route(u => u.pathname.includes('/intent-comparison'), r => r.request().method() === 'GET'
      ? (current.job ? r.fulfill({ json: current.job }) : r.fulfill({ status: 404, json: { detail: 'none' } })) : r.fulfill({ json: current.job }))
    await page.route(u => /\/api\/campus-electrical\/[^/]+$/.test(u.pathname), r => r.fulfill({ json: campusState() }))
    await page.route(u => u.pathname.startsWith('/api/campus-electrical/') && !/\/[^/]+$/.test(u.pathname.replace('/api/campus-electrical/', '')), r => r.fulfill({ json: {} }))
    await page.goto(`${BASE}/`)
    await page.waitForLoadState('networkidle')
    await page.evaluate(async ({ url, name }) => {
      const { projectsApi } = await import(url)
      const list = await projectsApi.list().catch(() => [])
      if (!list.some(p => p.name === name)) await projectsApi.save(name, false, true, undefined, true)
    }, { url: '/src/api/projects.ts', name: PROJECT })
    await page.reload(); await page.waitForLoadState('networkidle')
    const resume = page.getByRole('link', { name: /Resume project/ })
    if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
    await page.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().setSlidePanel('campusStudy') })

    for (const [key, st] of Object.entries(STATES)) {
      if (process.env.ONLY && !key.startsWith(process.env.ONLY)) continue
      try {
        current = st
        await page.reload(); await page.waitForLoadState('networkidle')
        await page.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().setSlidePanel('campusStudy') })
        await page.waitForTimeout(1500)
        const anchor = page.locator('[data-testid="intent-run"], [data-testid="intent-abort"]').first()
        const section = anchor.locator('xpath=ancestor::section[1]')
        await anchor.waitFor()
        for (const i of st.pick) await page.getByLabel(new RegExp(`^${i}\\. `)).check()
        if (st.fill) {
          const v = { critical: '80', link: 'poc', ramp: '10', sk: '1000', library: '50,100', swing: '20', amp: '5', period: '2' }
          for (const [k, val] of Object.entries(v)) await page.getByTestId(`intent-in-${k}`).fill(val)
        }
        await page.waitForTimeout(300)
        const target = (await section.count()) ? section : page.locator('main')
        await target.first().scrollIntoViewIfNeeded()
        const file = path.join(OUT, `${mode}-${theme}-${key}.png`)
        await target.first().screenshot({ path: file })
        written.push(path.basename(file))
      } catch (e) { problems.push(`${mode}/${theme}/${key}: ${e.message.split('\n')[0]}`); if (process.env.DEBUG_SHOT) await page.screenshot({ path: `/tmp/claude-0/fail-${mode}-${theme}-${key}.png` }).catch(() => {}) }
    }
    await ctx.close()
  }
} finally { await browser.close() }
console.log(`wrote ${written.length}: ${OUT}`)
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
