// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * Guided-results 03 shots on the real app: the section summary line and lists (carriers, loads, assets,
 * profiles) on the data centre and island microgrid, section 5's bar chart, an empty section, the hydrogen
 * hub and a tonne-bus template in section 5, the Assumptions card (collapsed, open, waiting) and the Expert
 * Issues panel for the same project.
 * Needs the API on :8000 and Vite on :5173 (dev admin, scripts/dev-credentials.mjs).
 * Usage: node scripts/guided-results-03-shots.mjs [outDir]      env: BASE, THEME (dark|light)
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const here = path.dirname(fileURLToPath(import.meta.url))
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const THEME = process.env.THEME || 'dark'
const OUT = path.resolve(process.argv[2] || path.join(here, '..', '..', '..', '.scratch', 'guided-results', 'shots-03'))
await fs.mkdir(OUT, { recursive: true })
const PROJECTS = [
  { key: 'dc', name: 'GR03 data centre', tid: 'eh_datacenter', body: { dc_load: {} }, sections: ['carriers', 'loads', 'assets', 'profiles'], expert: true },
  { key: 'mg', name: 'GR03 island microgrid', tid: 'eh_microgrid', body: {}, sections: ['carriers', 'loads', 'assets', 'profiles'] },
  { key: 'h2', name: 'GR03 hydrogen hub', tid: 'eh_h2_hub', body: {}, sections: ['assets'] },
  // A tonne bus: the concentrator's ore and concentrate buses carry unit "t". It takes no parameters.
  { key: 'mc', name: 'GR03 concentrator', tid: 'eh_concentrator_staged', body: undefined, sections: ['assets'] },
  // A blank project: its sections hold nothing, so they show the plain empty state.
  { key: 'bl', name: 'GR03 blank', tid: null, body: undefined, sections: [] },
]
const SECTIONS = ['project', 'carriers', 'connection', 'loads', 'assets', 'profiles', 'commercial', 'scenarios']
const RAIL = { project: 'step-start', connection: 'step-site', scenarios: 'step-goal' }
const railId = s => `hub-rail-${RAIL[s] ?? `section-${s}`}`
const browser = await chromium.launch()
const problems = []
const shot = async (page, name, target) => {
  if (target && await target.count()) await target.first().scrollIntoViewIfNeeded()
  await page.waitForTimeout(300)
  await page.screenshot({ path: path.join(OUT, `${name}-${THEME}.png`) })
  console.log('shot', `${name}-${THEME}.png`)
}
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
  const auth = await owner.storageState()
  await owner.close()

  let shotEmpty = false, shotWaiting = false, shotGroups = false
  const only = process.env.ONLY ? process.env.ONLY.split(',') : null
  for (const p of PROJECTS.filter(x => !only || only.includes(x.key))) {
    const setup = await browser.newContext({ storageState: auth })
    const sp = await setup.newPage()
    await sp.goto(BASE)
    await sp.waitForLoadState('networkidle')
    const made = await sp.evaluate(async ({ tid, name, body }) => {
      const { projectsApi } = await import('/src/api/projects.ts')
      const list = await projectsApi.list().catch(() => [])
      if (list.some(x => x.name === name)) await projectsApi.delete(name, true)
      try {
        if (tid) await projectsApi.createFromTemplate(tid, name, body)
        else {
          const { networkApi } = await import('/src/api/network.ts')
          await networkApi.resetNetwork()
          await projectsApi.save(name, true)
        }
        return 'made'
      } catch (e) { return `failed: ${e.message}` }
    }, p)
    await setup.close()
    if (made !== 'made') { problems.push(`${p.key}: ${made}`); continue }
    const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1280, height: 1000 }, deviceScaleFactor: 1, reducedMotion: 'reduce' })
    await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
    await ctx.addInitScript(({ mode, project, theme }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', theme)
    }, { mode: 'guided', project: p.name, theme: THEME })
    const page = await ctx.newPage()
    page.setDefaultTimeout(30000)
    page.on('pageerror', e => problems.push(`${p.key}: page error: ${e.message}`))
    await page.goto(BASE)
    await page.getByText('Open project').first().waitFor()
    await page.getByRole('link', { name: new RegExp(p.name) }).or(page.getByRole('button', { name: new RegExp(p.name) })).last().click()
    await page.waitForSelector('[data-testid="hub-rail"]')
    let cardDone = false
    for (const s of SECTIONS) {
      const step = page.locator(`[data-testid="${railId(s)}"]`)
      if (!(await step.count()) || !(await step.isEnabled())) continue
      await step.click()
      await page.waitForSelector(`[data-testid="section-view-${s}"]`)
      await page.locator('[data-testid="section-held-loading"]').waitFor({ state: 'detached' }).catch(() => {})
      await page.waitForTimeout(1200)
      const held = await page.locator('[data-testid="section-held"]').count()
      const line = await page.locator('[data-testid="section-held-line"]').first().textContent().catch(() => null)
      const empty = await page.locator('[data-testid="section-held-empty"]').count()
      console.log(p.key, s, { held, line, empty })
      if (p.sections.includes(s)) {
        const n = SECTIONS.indexOf(s) + 1
        await shot(page, `${p.key}-s${n}-${s}`, page.locator('[data-testid="section-held-line"]'))
        if (s === 'assets') {
          const chart = page.locator('[data-testid="assets-chart"]')
          console.log(p.key, 'chart units', await chart.evaluateAll(es => es.map(e => e.dataset.unit)))
          if (await chart.count()) await shot(page, `${p.key}-s5-chart`, chart.last())
          else problems.push(`${p.key}: no assets-chart in section 5`)
        }
      }
      if (empty && !shotEmpty) { await shot(page, `${p.key}-empty-${s}`, page.locator('[data-testid="section-held-empty"]')); shotEmpty = true }
      const card = page.locator('[data-testid="assumptions-card"]')
      if (await card.count()) {
        const waiting = await page.locator('[data-testid="assumptions-waiting"]').count()
        const first = !cardDone || (waiting && !shotWaiting)
        if (first) await shot(page, `${p.key}-card-collapsed-${s}`, card)
        const toggle = page.locator('[data-testid="assumptions-toggle"][aria-expanded="false"]')
        if (await toggle.count()) await toggle.click()
        await page.waitForTimeout(400)
        const groups = await page.locator('[data-testid="assumption-group"]').count()
        const rows = await page.locator('[data-testid="assumption-row"]').count()
        console.log(p.key, s, 'card', { groups, rows, waiting })
        if (first || (groups > 1 && !shotGroups)) await shot(page, `${p.key}-card-open-${s}`, card)
        if (groups > 1) shotGroups = true
        if (waiting && !shotWaiting) { await shot(page, `${p.key}-card-waiting-${s}`, page.locator('[data-testid="assumptions-waiting"]')); shotWaiting = true }
        cardDone = true
      }
    }
    if (p.expert) {
      await page.getByRole('button', { name: 'Expert', exact: true }).click()
      await page.waitForTimeout(1500)
      const issues = page.getByText('Issues', { exact: true }).first()
      if (await issues.count()) { await issues.click(); await page.waitForTimeout(1500) }
      await shot(page, `${p.key}-expert-issues`)
    }
    await ctx.close()
  }
  if (!shotEmpty) problems.push('no section showed section-held-empty')
  if (!shotWaiting) problems.push('no section showed assumptions-waiting')
  if (!shotGroups) problems.push('no open Assumptions card had more than one group')
} finally { await browser.close() }
if (problems.length) { console.error(problems.join('\n')); process.exitCode = 1 }
