// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Issue 19 S3: the study card (Results > reference design) of the "Raw names shots" project, on the real app.
 * Runs a one-solve-budget study, scans the screen text for raw ids and shoots it.
 * Usage: node scripts/raw-names-study-card-shots.mjs <outDir> <tag> */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { devCredentials } from './dev-credentials.mjs'
const { chromium } = createRequire(import.meta.url)('playwright')
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2]); const TAG = process.argv[3] || 'after'
const PROJECT = 'Raw names shots'
const RAW = /[a-z0-9]+_[a-z0-9_]+|\[[A-Z]+\]|\w+=\w+|Class-B|ReferenceDesignReport/
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
    const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 1400 }, deviceScaleFactor: 1, reducedMotion: 'reduce' })
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
      const r = await page.evaluate(async () => {
        const { resultsApi } = await import('/src/api/simulation.ts')
        try { await resultsApi.startEhStudy({ budget_solves: 1 }) } catch (e) { return `start failed: ${e.message}` }
        for (let i = 0; i < 120; i++) {
          await new Promise(r => setTimeout(r, 3000))
          const s = await resultsApi.getEhStudy({ quiet: true }).catch(() => null)
          if (s && !['running', 'queued'].includes(String(s.status))) return `done: ${s.status}`
        }
        return 'timeout'
      })
      console.log('study', r)
    }
    const open = async panel => { await page.evaluate(async n => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().setSlidePanel(n) }, panel); await page.waitForTimeout(3500) }
    const scan = async (key) => {
      const text = await page.locator('body').innerText()
      const hits = [...new Set(text.split('\n').map(x => x.trim()).filter(x => RAW.test(x)).map(x => x.slice(0, 140)))]
      console.log(`## ${mode}/${key} raw-looking lines:\n  ` + hits.slice(0, 25).join('\n  '))
      await page.screenshot({ path: path.join(OUT, `card-${TAG}-${mode}-${key}.png`), fullPage: false })
    }
    await open('results'); await page.getByRole('tab', { name: /^Reliability/ }).click().catch(() => page.getByText('Reliability', { exact: true }).first().click()); await page.waitForTimeout(3000)
    await scan('reliability')
    await open('hubDesign'); await scan('hub')
    await ctx.close()
  }
} finally { await browser.close() }
