// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * DC grid readiness 05 screenshot harness: "Power architecture" in the Grid study panel, Guided and Expert, on
 * the real app. The real compare route refuses (the template has no tariff: `2-refused`); the result states answer
 * from the committed fixture `src/test/fixtures/dcArchitectureCompare.json`. The "out of date" state mocks the
 * Backup mode flip: it is answered in the browser (`PUT .../dc-backup`) and the template read then returns the hybrid spec, so the stored
 * project never changes.
 *
 * Needs the API on :8000 and Vite on :5173, signed in as the dev admin (scripts/dev-credentials.mjs). Makes the
 * `eh_datacenter` project "DC architecture shots" (a `dc_load` with the architecture unset, so AC assumed, as the fixture) when it is missing.
 *
 * States per mode: `1-before` (before Compare), `2-refused` (no tariff, no price), `2-after` (after Compare), `3-out-of-date` (after a Backup change).
 * Expert adds `4-expander` (the loss chain of 800 VDC open) and `5-1250` (the result at about 1250 px wide).
 *
 * Usage: node scripts/dc-architecture-shots.mjs [outDir]      env: BASE
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const here = path.dirname(fileURLToPath(import.meta.url))
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(here, '..', '..', '..', '.scratch', 'datacenter-grid-readiness', 'shots'))
const PROJECT = 'DC architecture shots'
const TEMPLATE = 'eh_datacenter'
const FIXTURE = JSON.parse(await fs.readFile(path.join(here, '..', 'src', 'test', 'fixtures', 'dcArchitectureCompare.json'), 'utf8'))

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
  await login.waitForLoadState('networkidle')
  const made = await login.evaluate(async ({ tid, name }) => {
    const { projectsApi } = await import('/src/api/projects.ts')
    const list = await projectsApi.list().catch(() => [])
    if (list.some(p => p.name === name)) {
      const { resultsApi } = await import('/src/api/simulation.ts')
      const meta = await resultsApi.getEhTemplate(name, { quiet: true }).catch(() => null)
      if (meta?.dc_load?.spec && meta.dc_load.spec.architecture == null) return 'exists'
      await projectsApi.delete(name, true)
    }
    try { await projectsApi.createFromTemplate(tid, name, { dc_load: {} }); return 'made' } catch (e) { return `failed: ${e.message}` }
  }, { tid: TEMPLATE, name: PROJECT })
  if (made.startsWith('failed')) problems.push(`create: ${made}`)
  const auth = await owner.storageState()
  await owner.close()

  for (const mode of ['guided', 'expert']) {
    const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2, reducedMotion: 'reduce' })
    await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
    await ctx.addInitScript(({ mode, project }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', 'light')
    }, { mode, project: PROJECT })
    const page = await ctx.newPage()
    page.setDefaultTimeout(30000)
    page.on('pageerror', e => problems.push(`${mode}: page error: ${e.message}`))
    let hybrid = false
    const base = `/api/campus-electrical/${encodeURIComponent(PROJECT)}`
    await page.route(u => u.pathname === `${base}/dc-backup`, r => (r.request().method() === 'PUT'
      ? (hybrid = true, r.fulfill({ json: { mode: 'hybrid' } }))
      : r.continue()))
    await page.route(u => u.pathname.endsWith('/eh_template'), async r => {
      if (!hybrid || r.request().method() !== 'GET') return r.continue()
      const res = await r.fetch(); const body = await res.json()
      if (body?.dc_load?.spec) body.dc_load.spec.backup = { ...(body.dc_load.spec.backup ?? {}), mode: 'hybrid' }
      return r.fulfill({ response: res, json: body })
    })

    await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
    const resume = page.getByRole('link', { name: /Resume project/ })
    if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
    await page.getByText(PROJECT).first().waitFor(); await page.waitForTimeout(3000)
    await page.evaluate(async () => {
      const { useUIStore } = await import('/src/store/uiStore.ts')
      useUIStore.getState().setSlidePanel('campusStudy')
    })
    const box = () => page.getByTestId('dc-architecture')
    await box().waitFor().catch(async e => { await page.screenshot({ path: path.join(OUT, `failed-dc05-${mode}-open.png`) }); throw e })

    const step = async (key, fn) => {
      try { await fn() } catch (e) {
        problems.push(`${mode}/${key}: ${e.message.split('\n')[0]}`)
        await page.screenshot({ path: path.join(OUT, `failed-dc05-${mode}-${key}.png`) }).catch(() => {})
      }
    }
    const shot = async (key) => {
      await box().scrollIntoViewIfNeeded()
      const view = page.viewportSize()
      const tall = (await box().boundingBox())?.height ?? 0
      if (tall + 400 > view.height) { await page.setViewportSize({ ...view, height: Math.ceil(tall) + 400 }); await page.waitForTimeout(500); await box().scrollIntoViewIfNeeded() }
      const hidden = await box().evaluate(el => {
        const out = []
        for (const n of document.body.querySelectorAll('*')) {
          if (el.contains(n) || n.contains(el)) continue
          if ((getComputedStyle(n).position === 'fixed' || n.matches('[data-testid="companion-launcher"]')) && n.style.visibility !== 'hidden') { n.style.visibility = 'hidden'; out.push(n) }
        }
        window.__dcHidden = out
        return out.length
      })
      const file = path.join(OUT, `dc05-${key}-${mode}.png`)
      await box().screenshot({ path: file })
      if (hidden) await page.evaluate(() => { for (const n of window.__dcHidden ?? []) n.style.visibility = '' })
      if (page.viewportSize().height !== view.height) await page.setViewportSize(view)
      written.push(path.basename(file))
    }

    await step('1-before', () => shot('1-before'))
    // The template ships no import tariff: the real route refuses without a price (shot), then answers from
    // the committed fixture (a compare of an unset, AC, spec) for the result states.
    await step('2-refused', async () => {
      await page.getByTestId('dc-architecture-compare').click()
      await page.getByTestId('dc-architecture-error').waitFor()
      await shot('2-refused')
    })
    await page.route(u => u.pathname === `${base}/dc-architecture/compare`, r => r.fulfill({ json: FIXTURE }))
    await step('2-after', async () => {
      await page.getByTestId('dc-architecture-compare').click()
      await page.getByTestId('dc-architecture-result').or(page.getByTestId('dc-architecture-error')).first().waitFor({ timeout: 120000 })
      if (await page.getByTestId('dc-architecture-error').count()) problems.push(`${mode}: compare refused: ${await page.getByTestId('dc-architecture-error').innerText()}`)
      await shot('2-after')
    })
    if (mode === 'expert') {
      await step('4-expander', async () => {
        await page.getByRole('button', { name: 'Loss chain of 800 VDC' }).click()
        await page.getByTestId('dc-architecture-chain-dc_800v').first().waitFor()
        await shot('4-expander')
      })
      await step('5-1250', async () => {
        await page.setViewportSize({ width: 1250, height: 1000 }); await page.waitForTimeout(800)
        await shot('5-1250')
        await page.setViewportSize({ width: 1440, height: 1000 }); await page.waitForTimeout(500)
      })
    }
    await step('3-out-of-date', async () => {
      await page.getByRole('group', { name: 'Backup mode' }).getByRole('button', { name: /Hybrid/i }).click()
      await page.getByTestId('dc-architecture-stale').waitFor()
      await shot('3-out-of-date')
    })
    await ctx.close()
  }
} finally {
  await browser.close()
}
console.log(`wrote ${written.length}: ${written.join(', ')}`)
if (problems.length) { console.log(problems.join('\n')); process.exitCode = 1 }
