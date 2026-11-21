// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Issue 19 S2: a Solve Queue row's log, Guided and Expert, on the real app. Enqueues one solve of the
 * "Raw names shots" project (template eh_datacenter). Usage: node scripts/raw-names-queue-shots.mjs <outDir> <tag> */
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
  const auth = await owner.storageState(); await owner.close()
  for (const mode of ['guided', 'expert']) {
    const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1, reducedMotion: 'reduce' })
    await ctx.addInitScript(({ mode, project }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', 'light')
    }, { mode, project: PROJECT })
    const page = await ctx.newPage(); page.setDefaultTimeout(60000)
    await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
    const resume = page.getByRole('link', { name: /Resume project/ })
    if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
    await page.getByText(PROJECT).first().waitFor(); await page.waitForTimeout(2500)
    if (mode === 'guided') {
      const r = await page.evaluate(async name => {
        const { solveQueueApi } = await import('/src/api/solveQueue.ts')
        const { projectsApi } = await import('/src/api/projects.ts')
        const proj = (await projectsApi.list()).find(x => x.name === name)
        try { return JSON.stringify(await solveQueueApi.enqueue(proj.id ?? proj.name)).slice(0, 120) } catch (e) { return `failed: ${e.message}` }
      }, PROJECT)
      console.log('enqueue', r)
    }
    await page.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().setSlidePanel('solveQueue') })
    await page.getByTitle(/Show this job.s log/).first().waitFor({ timeout: 180000 })
    await page.waitForTimeout(8000)
    await page.getByTitle(/Show this job.s log/).first().click(); await page.waitForTimeout(1500)
    await page.screenshot({ path: path.join(OUT, `queue-${TAG}-${mode}-row-open.png`) })
    const d = page.getByTestId('job-log-details')
    if (await d.count()) { await d.locator('summary').click(); await page.waitForTimeout(500); await page.screenshot({ path: path.join(OUT, `queue-${TAG}-${mode}-details-open.png`) }) }
    await ctx.close()
  }
} finally { await browser.close() }
