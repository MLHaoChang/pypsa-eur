// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Issue 19 S3, Expert: Results > Adequacy on the real app. Shoots the reserve-margin empty text and the
 * Energy Hub reference-design panel (sized-design table, notes). The project "Raw names shots" must hold a
 * finished study (raw-names-study-card-shots.mjs runs one). Usage: node scripts/raw-names-study-card-expert-shots.mjs <outDir> <tag> */
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
  const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 1600 }, deviceScaleFactor: 1, reducedMotion: 'reduce' })
  await ctx.addInitScript(project => {
    localStorage.setItem('network-diagram:current-project', project)
    localStorage.setItem('network-diagram:ui-mode', 'expert')
    localStorage.setItem('network-diagram:ui-mode-explicit', '1')
    localStorage.setItem('network-diagram:theme', 'light')
  }, PROJECT)
  const page = await ctx.newPage(); page.setDefaultTimeout(60000)
  await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
  const resume = page.getByRole('link', { name: /Resume project/ })
  if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
  await page.getByText(PROJECT).first().waitFor(); await page.waitForTimeout(2500)
  await page.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().setSlidePanel('results') })
  await page.waitForTimeout(3000)
  const cands = page.getByText('Reliability', { exact: true }); let clicked = false
  for (let i = 0; i < await cands.count(); i++) { const b = await cands.nth(i).boundingBox(); if (b && b.x > 400 && b.y > 100 && b.y < 220) { await cands.nth(i).click(); clicked = true; break } }
  if (!clicked) throw new Error('no Reliability tab found'); await page.waitForTimeout(3000)
  const shoot = async (loc, name) => { await loc.scrollIntoViewIfNeeded(); await page.waitForTimeout(500); await loc.screenshot({ path: path.join(OUT, `${name}-${TAG}-expert.png`) }); console.log('shot', name, (await loc.innerText()).split('\n').slice(0, 3).join(' | ').slice(0, 120)) }
  await shoot(page.getByTestId('reserve-margin-empty'), 'adequacy-reserve-margin-empty')
  const panel = page.getByTestId('eh-reference-design-panel')
  const toggle = page.getByTestId('eh-reference-design-toggle')
  if (await page.getByTestId('eh-report-sized').count() === 0) { await toggle.click().catch(() => {}); await page.waitForTimeout(2000) }
  await shoot(panel, 'adequacy-eh-reference-design')
  const sized = page.getByTestId('eh-report-sized')
  if (await sized.count()) await shoot(sized, 'adequacy-eh-sized-design-table'); else console.log('no sized table')
} finally { await browser.close() }
