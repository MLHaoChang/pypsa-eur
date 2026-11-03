// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * AS-17c: screenshot the project PoC X/R field (Project info → Settings) in four states, Guided and Expert,
 * light theme. Real API on :8000 and Vite on :5173 (real sign-in, real settings PATCH); only the 422 case is
 * mocked, because the client range check refuses the out-of-range values before the API sees them.
 *
 * Usage: node scripts/poc-xr-shots.mjs [outDir]
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(frontend, '..', '..', '.scratch', 'asset-sizing-loop', 'evidence', '17c'))
const PROJECT = 'PoC XR shots'

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
    let mock422 = false
    await page.route(u => /\/api\/projects\/[^/]+\/settings$/.test(u.pathname), r => (mock422 && r.request().method() === 'PATCH')
      ? r.fulfill({ status: 422, json: { detail: 'PoC X/R must be above 0 and at most 100.' } }) : r.continue())

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

    const open = async () => {
      await page.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().setSlidePanel('overview') })
      await page.waitForTimeout(800)
      const f = page.getByRole('textbox', { name: /poc x\/r/i }); await f.waitFor(); return f
    }
    const shoot = async (key, f) => {
      const section = f.locator('xpath=ancestor::section[1]')
      const target = (await section.count()) ? section : f.locator('xpath=..')
      await target.first().scrollIntoViewIfNeeded()
      const file = path.join(OUT, `${mode}-light-${key}.png`)
      await target.first().screenshot({ path: file }); written.push(path.basename(file))
    }
    const step = async (key, fn) => { try { await fn() } catch (e) { problems.push(`${mode}/${key}: ${e.message.split('\n')[0]}`) } }

    await step('1-empty', async () => { await page.reload(); await page.waitForLoadState('networkidle'); await shoot('1-empty', await open()) })
    await step('2-saved', async () => {
      const f = await open(); await f.fill('7.5'); await f.press('Tab'); await page.waitForTimeout(800)
      await page.reload(); await page.waitForLoadState('networkidle'); const g = await open()
      if ((await g.inputValue()) !== '7.5') throw new Error('stored value did not survive a reload')
      await shoot('2-saved', g)
    })
    await step('3-invalid-range', async () => {
      const f = await open(); await f.fill('150'); await f.press('Tab'); await page.getByRole('alert').first().waitFor(); await shoot('3-invalid-range', f)
    })
    await step('4-invalid-api-422', async () => {
      mock422 = true
      const f = await open(); await f.fill('0.5'); await f.press('Tab'); await page.getByRole('alert').first().waitFor(); await shoot('4-invalid-api-422', f)
      mock422 = false
    })
    await step('5-read-only', async () => {
      await page.reload(); await page.waitForLoadState('networkidle')
      await page.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.setState({ readOnly: true, readOnlyReason: 'locked-by-user' }) })
      await shoot('5-read-only', await open())
    })
    await ctx.close()
  }
} finally { await browser.close() }
console.log(`wrote ${written.length}: ${OUT}`)
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
