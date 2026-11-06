// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * Grid-to-process 12b-2 screenshot harness: the ride-through curve and restart time in the Properties form,
 * Guided and Expert, on the real API (the B template, "eh_concentrator_staged"). No route is mocked.
 *
 * Needs the API on :8000 and Vite on :5173, signed in as the dev admin (scripts/dev-credentials.mjs).
 *
 * States: `1-empty` (a process unit, flotation, with both fields empty), `2-filled` (mill_1 with B's demo
 * curve and 4 h), `3-refused` (a malformed curve typed into mill_1: the save's refusal), `4-not-process`
 * (grid_import, neither field shown; Expert only).
 *
 * Usage: node scripts/restart-fields-shots.mjs [outDir]      env: BASE
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(frontend, '..', '..', '.scratch', 'grid-to-process', 'shots'))
const PROJECT = 'Restart fields shots'
const TEMPLATE = 'eh_concentrator_staged'

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
    if (list.some(p => p.name === name)) return 'exists'
    try { await projectsApi.createFromTemplate(tid, name); return 'made' } catch (e) { return `failed: ${e.message}` }
  }, { tid: TEMPLATE, name: PROJECT })
  if (made.startsWith('failed')) problems.push(`create: ${made}`)
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
    page.setDefaultTimeout(15000)
    page.on('pageerror', e => problems.push(`${mode}: page error: ${e.message}`))
    await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
    const resume = page.getByRole('link', { name: /Resume project/ })
    if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }

    // The project loads after the page: wait until the top bar names it.
    await page.getByText(PROJECT).first().waitFor(); await page.waitForTimeout(4000)

    const shot = async (key) => {
      const file = path.join(OUT, `g2p-12b2-${mode}-${key}.png`)
      await page.screenshot({ path: file }); written.push(path.basename(file))
    }
    const step = async (key, fn) => { try { await fn() } catch (e) {
      problems.push(`${mode}/${key}: ${e.message.split('\n')[0]}`)
      await page.screenshot({ path: path.join(OUT, `failed-${mode}-${key}.png`) }).catch(() => {})
    } }
    const open = async (entity, field) => {
      await page.evaluate(async ({ mode, entity, field }) => {
        const nav = await import('/src/layout/flowNavigation.ts')
        const target = { section: 'assets', panel: null, component: 'Link', entity, field }
        if (mode === 'guided') nav.openFlowTargetInPlace(target); else nav.openFlowTarget(target)
      }, { mode, entity, field })
      await page.waitForTimeout(1200)
      const edit = page.getByRole('button', { name: /^Edit Link/ })
      if (await edit.count()) { await edit.first().click(); await page.waitForTimeout(800) }
    }
    const curve = () => page.getByRole('textbox', { name: /ride-through curve/i }).first()

    await step('1-empty', async () => {
      await open('flotation', 'ride_through_curve')
      // The first selection after load can be cleared as the canvas settles: open it once more if so.
      if (!(await curve().isVisible())) await open('flotation', 'ride_through_curve')
      await curve().waitFor()
      await curve().scrollIntoViewIfNeeded(); await shot('1-empty')
    })
    await step('2-filled', async () => {
      await open('mill_1', 'ride_through_curve'); await curve().waitFor()
      await curve().scrollIntoViewIfNeeded(); await shot('2-filled')
    })
    await step('3-refused', async () => {
      await curve().fill('15-1, 30:0.2')
      await curve().press('Tab')
      await page.getByRole('button', { name: 'Save', exact: true }).last().click()
      await page.getByText(/Save failed: Ride-through curve/).first().waitFor()
      await page.waitForTimeout(1500)
      await shot('3-refused')
    })
    if (mode === 'expert') {
      await step('4-not-process', async () => {
        await page.getByRole('button', { name: 'Cancel', exact: true }).last().click()
        await page.waitForTimeout(6000)                          // the refusal toasts fade
        await open('grid_import', null)
        if (await page.getByText('Ride-through curve', { exact: true }).count()) throw new Error('the curve shows on a non-process link')
        await shot('4-not-process')
      })
    }
    await ctx.close()
  }
} finally {
  await browser.close()
}
console.log(`wrote ${written.length}: ${written.join(', ')}`)
if (problems.length) { console.log(problems.join('\n')); process.exitCode = 1 }
