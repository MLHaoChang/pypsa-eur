// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * template-e2e 09 screenshot harness: Expert's Issues panel lists the assumed
 * rows Guided shows (same AssumptionsCard, same words), dark and light, on the
 * real app with an eh_microgrid project. No mocked routes. Also reports the
 * sidebar Issues badge against the rows the panel lists.
 * Needs the API on :8000 and Vite on :5173 (dev admin, scripts/dev-credentials.mjs).
 *
 * Usage: node scripts/expert-assumptions-shots.mjs [outDir]      env: BASE
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const here = path.dirname(fileURLToPath(import.meta.url))
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(here, '..', '..', '..', '.scratch', 'template-e2e', 'shots-09'))
const PROJECT = 'Expert assumptions shots'
const TEMPLATE = 'eh_microgrid'

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
  // Load it on the server before any shot tab opens, so no tab's save meets an empty network (409 toast).
  const loaded = await login.evaluate(async ({ name }) => {
    const { projectsApi } = await import('/src/api/projects.ts')
    try { await projectsApi.load(name); return 'ok' } catch (e) { return `failed: ${e.message}` }
  }, { name: PROJECT })
  if (loaded !== 'ok') problems.push(`load: ${loaded}`)
  const auth = await owner.storageState()
  await owner.close()

  for (const theme of ['dark', 'light']) {
    const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 2400 }, deviceScaleFactor: 2, reducedMotion: 'reduce' })
    await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
    await ctx.addInitScript(({ theme, project }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:ui-mode', 'expert')
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', theme)
      localStorage.setItem('network-diagram:autosave', 'false')
    }, { theme, project: PROJECT })
    const page = await ctx.newPage()
    page.setDefaultTimeout(20000)
    page.on('pageerror', e => problems.push(`${theme}: page error: ${e.message}`))
    page.on('response', r => { if (r.status() === 409) problems.push(`${theme}: 409 on ${r.url()}`) })
    try {
      await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
      const resume = page.getByRole('link', { name: /Resume project/ })
      if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
      await page.getByText(PROJECT).first().waitFor(); await page.waitForTimeout(3000)
      const issuesBtn = page.getByRole('button', { name: /^Issues/ }).first()
      await issuesBtn.click()
      const box = page.getByTestId('issues-assumptions')
      await box.waitFor(); await page.waitForTimeout(1000)
      const badge = ((await issuesBtn.locator('[data-severity]').textContent().catch(() => '')) ?? '').trim()
      const assumedRows = await box.getByTestId('assumption-row').count()
      // Each section group is its header plus one child per row (preflight finding or flow-only item).
      const findings = await page.evaluate(() => Array.from(document.querySelectorAll('[data-testid^="issues-section-"]'))
        .reduce((n, g) => n + Math.max(0, g.children.length - 1), 0))
      if (badge !== String(assumedRows + findings)) problems.push(`${theme}: badge ${badge} != rows ${assumedRows + findings}`)
      console.log(`${theme}: badge=${badge} assumedRows=${assumedRows} findingRows=${findings}`)
      const section = box.locator('xpath=ancestor::section[1]')
      const target = (await section.count()) ? section.first() : box
      await target.scrollIntoViewIfNeeded()
      const file = path.join(OUT, `te2e-09-expert-${theme}.png`)
      await target.screenshot({ path: file }); written.push(file)
      const whole = path.join(OUT, `te2e-09-expert-${theme}-panel.png`)
      await page.screenshot({ path: whole }); written.push(whole)
    } catch (e) {
      problems.push(`${theme}: ${e.message.split('\n')[0]}`)
      await page.screenshot({ path: path.join(OUT, `failed-${theme}.png`) }).catch(() => {})
    }
    await ctx.close()
  }
} finally {
  await browser.close()
}
for (const w of written) console.log(w)
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
