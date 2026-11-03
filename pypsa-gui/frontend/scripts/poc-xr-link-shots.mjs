// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * AS-18: screenshot the "Set PoC X/R ›" link on the grid-readiness scorecard, Guided and Expert, light theme:
 * (1) the row with the link, (2) after the click (field focused), (3) after Back, (4) the field read-only.
 * Real API on :8000 and Vite on :5173 (real sign-in and project); only the grid-readiness route is mocked,
 * since a real RVC row needs a modelled GPU envelope.
 *
 * Usage: node scripts/poc-xr-link-shots.mjs [outDir]
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(frontend, '..', '..', '.scratch', 'asset-sizing-loop', 'evidence', '18'))
const PROJECT = 'PoC XR link shots'
const target = (section, component, entity, field) => ({ section, panel: null, component, entity, field })
const row = (over) => ({ id: 'x', requirement: 'R', limit: null, unit: null, source: 's', project_value: null,
  verdict: 'pass', assumed: false, note: null, section: 'connection', target: target('connection', null, null, null), ...over })
const READINESS = {
  schema: 1, profile: { id: 'eu_rfg_dcc_ce', title: 'EU RfG and DCC', error: null }, template_id: 'eh_datacenter',
  envelope: true, basis: 'advisory', summary: { pass: 1, warn: 1, fail: 0, unknown: 0, assumed: 2, red_flags: 0 },
  rows: [
    row({ id: 'rapid_voltage_change', requirement: 'Rapid voltage change at the PoC within the limit', limit: 3, unit: '% of nominal',
      project_value: 4.02, verdict: 'warn', assumed: true, xr_assumed: true, source: 'EU RfG and DCC',
      note: "the voltage form at the assumed X/R 4 passes (1.358 %), the X/R-free bound does not (4.02 %); enter the network operator's X/R in Project info, Settings",
      target: target('connection', 'Bus', 'grid', 'eh_sk_mva') }),
    row({ id: 'scr', requirement: 'Short-circuit ratio at the PoC', limit: 3, unit: 'SCR', project_value: 8.3,
      target: target('connection', 'Bus', 'grid', 'eh_sk_mva') }),
  ],
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

  for (const mode of ['guided', 'expert']) {
    const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' })
    await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
    await ctx.addInitScript(({ mode, project }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', 'light')
    }, { mode, project: PROJECT })
    const page = await ctx.newPage()
    page.setDefaultTimeout(10000)
    page.on('pageerror', e => problems.push(`${mode}: page error: ${e.message}`))
    await page.route(u => /\/grid-readiness$/.test(u.pathname), r => r.fulfill({ json: READINESS }))

    await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
    await page.evaluate(async ({ url, name }) => {
      const { projectsApi } = await import(url)
      const list = await projectsApi.list().catch(() => [])
      if (!list.some(p => p.name === name)) await projectsApi.save(name, false, true, undefined, true)
      await projectsApi.updateSettings(name, { poc_xr: null })
    }, { url: '/src/api/projects.ts', name: PROJECT })
    await page.reload(); await page.waitForLoadState('networkidle')
    const resume = page.getByRole('link', { name: /Resume project/ })
    if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }

    const shot = async (key) => {
      const file = path.join(OUT, `${mode}-light-${key}.png`)
      await page.screenshot({ path: file }); written.push(path.basename(file))
    }
    const step = async (key, fn) => { try { await fn() } catch (e) { problems.push(`${mode}/${key}: ${e.message.split('\n')[0]}`) } }
    const link = page.getByTestId('gr-set-xr-rapid_voltage_change')
    const showReadiness = async () => {
      await page.evaluate(async () => { const { showGridReadiness } = await import('/src/layout/flowNavigation.ts'); showGridReadiness() })
      await link.waitFor(); await page.waitForTimeout(500)
    }
    const field = () => page.getByRole('textbox', { name: /poc x\/r/i })

    await step('1-row', async () => { await showReadiness(); await link.scrollIntoViewIfNeeded(); await shot('1-row') })
    await step('2-after-click', async () => {
      await link.click(); await field().waitFor(); await page.waitForTimeout(500)
      if (!(await field().evaluate(el => el === document.activeElement))) throw new Error('the PoC X/R field is not focused')
      await shot('2-after-click')
    })
    await step('3-back', async () => {
      await page.getByRole('button', { name: /^Back to/ }).click(); await link.waitFor(); await page.waitForTimeout(500); await shot('3-back')
    })
    await step('4-read-only', async () => {
      await page.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.setState({ readOnly: true, readOnlyReason: 'locked-by-user' }) })
      await link.click(); await field().waitFor(); await page.waitForTimeout(500); await shot('4-read-only')
    })
    await ctx.close()
  }
} finally { await browser.close() }
console.log(`wrote ${written.length}: ${OUT}`)
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
