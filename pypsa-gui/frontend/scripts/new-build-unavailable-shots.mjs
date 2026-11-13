// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * ui-modern 06 screenshot harness: the "new-build CAPEX unavailable" state on Results › Capacity
 * expansion (Annualised) and Results › Summary, in Guided and Expert. The project is solved for real;
 * the browser rewrites two responses to the failed-expansion-compute payload:
 * `/api/results/cost_breakdown` (capex_expansion, storage_capex_expansion and every
 * by_component[].capex_expansion set to null) and `/api/results/headline_kpis` (the new_build_capital
 * card unavailable). `summary-no-strip-card.png` drops that card from the strip so the Summary cost
 * card shows its own "of which new build: unavailable" line.
 *
 * Needs the API on :8000 and Vite on :5173, signed in as the dev admin (scripts/dev-credentials.mjs),
 * and a solved project (default "Island Microgrid", from the eh_microgrid template).
 * Usage: node scripts/new-build-unavailable-shots.mjs [outDir]      env: BASE, PROJECT
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const here = path.dirname(fileURLToPath(import.meta.url))
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const PROJECT = process.env.PROJECT || 'Island Microgrid'
const OUT = path.resolve(process.argv[2] || path.join(here, '..', '..', '..', '.scratch', 'ui-modern', 'shots-06'))
const REASON = 'This figure could not be computed for this solve.'
await fs.mkdir(OUT, { recursive: true })

function failCostBreakdown(j) {
  return {
    ...j, capex_expansion: null, storage_capex_expansion: null,
    by_component: (j.by_component || []).map(c => ({ ...c, capex_expansion: null })),
  }
}
function failHeadline(j, dropCard) {
  const cards = (j.cards || []).map(c => c.id === 'new_build_capital'
    ? { ...c, state: 'unavailable', value: null, reason: REASON } : c)
  return { ...j, cards: dropCard ? cards.filter(c => c.id !== 'new_build_capital') : cards }
}

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
  const auth = await owner.storageState()
  await owner.close()

  const shots = [
    ['guided', 'capex', 'guided-capacity-expansion', false],
    ['guided', 'summary', 'guided-summary', false],
    ['expert', 'capex', 'expert-capacity-expansion', false],
    ['expert', 'summary', 'expert-summary', false],
    ['expert', 'summary', 'summary-no-strip-card', true],
  ]
  for (const [mode, tab, name, dropCard] of shots) {
    const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, reducedMotion: 'reduce' })
    await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
    await ctx.route('**/api/results/cost_breakdown*', async r => {
      const res = await r.fetch()
      if (!res.ok()) return r.fulfill({ response: res })
      return r.fulfill({ response: res, json: failCostBreakdown(await res.json()) })
    })
    await ctx.route('**/api/results/headline_kpis*', async r => {
      const res = await r.fetch()
      if (!res.ok()) return r.fulfill({ response: res })
      return r.fulfill({ response: res, json: failHeadline(await res.json(), dropCard) })
    })
    await ctx.addInitScript(({ project, mode }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
    }, { project: PROJECT, mode })
    const page = await ctx.newPage()
    page.setDefaultTimeout(30000)
    page.on('pageerror', e => problems.push(`${name}: page error: ${e.message}`))
    await page.goto(`${BASE}/`)
    await page.waitForLoadState('networkidle')
    await page.evaluate(async ({ url, name }) => {
      const { projectsApi } = await import(url)
      await projectsApi.activate(name)
    }, { url: '/src/api/projects.ts', name: PROJECT })
    await page.reload()
    await page.waitForLoadState('networkidle')
    const resume = page.getByRole('link', { name: /Resume project/ })
    if (await resume.count()) {
      await resume.first().click()
      await page.waitForURL(u => !u.pathname.startsWith('/projects'))
      await page.waitForLoadState('networkidle')
    }
    await page.waitForTimeout(3000)   // the app's own project switch settles first
    await page.evaluate(async t => {
      const { showResultsTab } = await import('/src/layout/flowNavigation.ts')
      showResultsTab(t)
    }, tab)
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(2500)
    if (tab === 'capex') {
      const step = page.getByText('System cost build-up').first()
      if (await step.count()) await step.scrollIntoViewIfNeeded().catch(() => {})
      else problems.push(`${name}: "System cost build-up" not found`)
      await page.waitForTimeout(500)
    }
    await page.screenshot({ path: path.join(OUT, `${name}.png`) })
    await ctx.close()
  }
} finally {
  await browser.close()
}
console.log(problems.length ? problems.join('\n') : 'no problems')
console.log(`shots in ${OUT}`)
