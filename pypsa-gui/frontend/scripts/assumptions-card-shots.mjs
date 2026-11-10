// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * LC-46 shots on the real app: the Assumptions card in Guided on the data centre and the island microgrid
 * (card, after "Keep all defaults", a section with no card) and the Expert Issues panel that still lists the values.
 * Needs the API on :8000 and Vite on :5173 (dev admin, scripts/dev-credentials.mjs).
 * Usage: node scripts/assumptions-card-shots.mjs [outDir]      env: BASE, THEME (dark|light)
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
const OUT = path.resolve(process.argv[2] || path.join(here, '..', '..', '..', '.scratch', 'live-copilot', 'shots', 'lc46'))
await fs.mkdir(OUT, { recursive: true })
const PROJECTS = [
  { key: 'dc', name: 'LC46 data centre', tid: 'eh_datacenter', body: { dc_load: {} } },
  { key: 'mg', name: 'LC46 island microgrid', tid: 'eh_microgrid', body: {} },
]
const SECTIONS = ['project', 'carriers', 'connection', 'loads', 'assets', 'profiles', 'commercial', 'scenarios']
const RAIL = { project: 'step-start', connection: 'step-site', scenarios: 'step-goal' }
const railId = s => `hub-rail-${RAIL[s] ?? `section-${s}`}`
const browser = await chromium.launch()
const problems = []
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

  for (const p of PROJECTS) {
    const owner2 = await browser.newContext({ storageState: auth })
    const login = await owner2.newPage()
    await login.goto(BASE)
    await login.waitForLoadState('networkidle')
    const made = await login.evaluate(async ({ tid, name, body }) => {
      const { projectsApi } = await import('/src/api/projects.ts')
      const list = await projectsApi.list().catch(() => [])
      if (list.some(x => x.name === name)) await projectsApi.delete(name, true)
      try { await projectsApi.createFromTemplate(tid, name, body); return 'made' } catch (e) { return `failed: ${e.message}` }
    }, p)
    if (made !== 'made') problems.push(`${p.key}: ${made}`)
    await owner2.close()
    const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1, reducedMotion: 'reduce' })
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
    let total = 0, shotCard = false, shotNone = false, cardSection = null
    for (const s of SECTIONS) {
      const step = page.locator(`[data-testid="${railId(s)}"]`)
      if (!(await step.count()) || !(await step.isEnabled())) continue
      await step.click()
      await page.waitForSelector(`[data-testid="section-view-${s}"]`)
      await page.waitForTimeout(1200)
      const rows = await page.locator('[data-testid="assumption-row"]').count()
      total += rows
      console.log(p.key, s, 'assumed rows', rows)
      if (rows && !cardSection) cardSection = s
      if (rows && !shotCard) { await page.screenshot({ path: path.join(OUT, `${p.key}-${THEME}-card-${s}.png`) }); shotCard = true }
      if (!rows && !shotNone) { await page.screenshot({ path: path.join(OUT, `${p.key}-${THEME}-no-card-${s}.png`) }); shotNone = true }
    }
    console.log(p.key, 'assumed rows in total', total)
    // Keep all defaults in the first section that has the card.
    if (cardSection) {
      await page.locator(`[data-testid="${railId(cardSection)}"]`).click()
      await page.waitForSelector(`[data-testid="section-view-${cardSection}"]`)
      await page.getByTestId('assumptions-accept-all').click()
      await page.waitForSelector('[data-testid="assumptions-kept"]')
      await page.screenshot({ path: path.join(OUT, `${p.key}-${THEME}-kept-${cardSection}.png`) })
    }
    if (p.key === 'dc') {
      await page.getByRole('button', { name: 'Expert', exact: true }).click()
      await page.waitForTimeout(1500)
      const issues = page.getByText('Issues', { exact: true }).first()
      if (await issues.count()) { await issues.click(); await page.waitForTimeout(1500) }
      await page.screenshot({ path: path.join(OUT, `${p.key}-${THEME}-expert-issues.png`) })
    }
    await ctx.close()
  }
} finally { await browser.close() }
if (problems.length) { console.error(problems.join('\n')); process.exitCode = 1 }
