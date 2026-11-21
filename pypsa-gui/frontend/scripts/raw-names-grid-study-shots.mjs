// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Issue 19 S1: the Island section of the Grid study panel on the real app, Guided and Expert.
 * Needs API :8000 and Vite :5173, signed in as the dev admin. Usage: node scripts/raw-names-grid-study-shots.mjs <outDir> <tag> */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2]); const TAG = process.argv[3] || 'after'
const PROJECT = 'Raw names shots'
await fs.mkdir(OUT, { recursive: true })
const browser = await chromium.launch()
try {
  const owner = await browser.newContext(); const login = await owner.newPage()
  await login.goto(BASE); const c = devCredentials()
  await login.locator('#email').fill(c.email); await login.locator('#password').fill(c.password)
  await login.getByRole('button', { name: 'Sign in', exact: true }).click()
  await login.waitForURL(/\/projects/); await login.waitForLoadState('networkidle')
  const made = await login.evaluate(async ({ name }) => {
    const { projectsApi } = await import('/src/api/projects.ts')
    const list = await projectsApi.list().catch(() => [])
    if (list.some(p => p.name === name)) return 'exists'
    try { await projectsApi.createFromTemplate('eh_datacenter', name, { dc_load: {} }); return 'made' } catch (e) { return `failed: ${e.message}` }
  }, { name: PROJECT })
  console.log('project', made)
  const auth = await owner.storageState(); await owner.close()
  for (const mode of ['guided', 'expert']) {
    const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 1400 }, deviceScaleFactor: 1, reducedMotion: 'reduce' })
    await ctx.addInitScript(({ mode, project }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', 'light')
    }, { mode, project: PROJECT })
    const page = await ctx.newPage(); page.setDefaultTimeout(30000)
    await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
    const resume = page.getByRole('link', { name: /Resume project/ })
    if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
    await page.getByText(PROJECT).first().waitFor().catch(async e => { await page.screenshot({ path: path.join(OUT, 'dbg.png') }); console.log(page.url()); throw e }); await page.waitForTimeout(3000)
    await page.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().setSlidePanel('campusStudy') })
    const box = page.getByTestId('island-section')
    await box.waitFor().catch(async e => { await page.screenshot({ path: path.join(OUT, `failed-${mode}.png`) }); throw e })
    await box.scrollIntoViewIfNeeded(); await page.waitForTimeout(1000)
    await box.screenshot({ path: path.join(OUT, `island-${TAG}-${mode}.png`) })
    console.log('shot', mode, await box.innerText().then(t => (t.match(/Control mode of [^\n]*/g) || []).join(' | ')))
    await ctx.close()
  }
} finally { await browser.close() }
