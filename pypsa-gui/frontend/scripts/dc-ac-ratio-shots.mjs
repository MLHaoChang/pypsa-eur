// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * Typed-cost-units issue 02 screenshot harness: the Generator's "DC/AC ratio" field, Guided and Expert, on the
 * real API (the "eh_ipp_hybrid" template, its `pv_plant`). One route is mocked: no shipped cost reference is
 * priced per MW of DC, so state 4 answers the reference preview with the backend's own refusal.
 *
 * Needs the API on :8000 and Vite on :5173, signed in as the dev admin (scripts/dev-credentials.mjs).
 *
 * States: `1-blank` (pv_plant, ratio not declared), `2-set` (1.25 saved through the form), `3-refused`
 * (125 typed: the save's refusal with the percentage hint), `4-apply-refused` (a DC-priced reference on a
 * generator without a ratio: the drawer's refusal).
 *
 * Usage: node scripts/dc-ac-ratio-shots.mjs [outDir]      env: BASE
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(frontend, '..', '..', '.scratch', 'typed-cost-units', 'shots'))
const PROJECT = 'DC-AC ratio shots'
const TEMPLATE = 'eh_ipp_hybrid'
const GEN = 'pv_plant'
// The backend's refusal for a DC-priced reference on a generator without a ratio (reference_apply._dc_ac_ratio).
const REFUSAL = {
  error_kind: 'dc_ac_ratio_missing',
  message: "This cost is priced per MW of DC module rating, and Capacity is the AC rating: set the generator's "
    + 'DC/AC ratio first (not declared).',
}

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
    await page.getByText(PROJECT).first().waitFor(); await page.waitForTimeout(4000)

    // Each mode starts from "not declared", through the API the form itself saves with.
    await page.evaluate(async (gen) => {
      const { default: client } = await import('/src/api/client.ts')
      await client.patch('/network/_bulk', { component_class: 'Generator', names: [gen], updates: { dc_ac_ratio: '' } })
    }, GEN).catch(e => problems.push(`${mode}: reset: ${e.message}`))

    const shot = async (key) => {
      const file = path.join(OUT, `tc02-${mode}-${key}.png`)
      await page.screenshot({ path: file }); written.push(path.basename(file))
    }
    const step = async (key, fn) => { try { await fn() } catch (e) {
      problems.push(`${mode}/${key}: ${e.message.split('\n')[0]}`)
      await page.screenshot({ path: path.join(OUT, `failed-${mode}-${key}.png`) }).catch(() => {})
    } }
    const open = async () => {
      await page.evaluate(async ({ mode, gen }) => {
        const nav = await import('/src/layout/flowNavigation.ts')
        const target = { section: 'assets', panel: null, component: 'Generator', entity: gen, field: 'dc_ac_ratio' }
        if (mode === 'guided') nav.openFlowTargetInPlace(target); else nav.openFlowTarget(target)
      }, { mode, gen: GEN })
      await page.waitForTimeout(1200)
      const edit = page.getByRole('button', { name: /^Edit Generator/ })
      if (await edit.count()) { await edit.first().click(); await page.waitForTimeout(800) }
    }
    const ratio = () => page.getByRole('spinbutton', { name: /DC\/AC ratio/i }).or(
      page.getByRole('textbox', { name: /DC\/AC ratio/i })).first()
    // The edit form shows every field in both modes; unfold "Defaults in use" only if a face folds it.
    const reveal = async () => {
      if (await ratio().isVisible()) return
      const fold = page.getByText(/Defaults in use/).first()
      if (await fold.count()) { await fold.click(); await page.waitForTimeout(500) }
    }
    const save = async () => {
      await page.getByRole('button', { name: 'Save', exact: true }).last().click()
      await page.waitForTimeout(1500)
    }

    await step('1-blank', async () => {
      await open()
      if (!(await page.getByText('DC/AC ratio').count())) await open()
      await reveal(); await ratio().waitFor()
      await ratio().scrollIntoViewIfNeeded(); await shot('1-blank')
    })
    await step('2-set', async () => {
      await ratio().fill('1.25'); await ratio().press('Tab'); await save()
      await open(); await reveal(); await ratio().waitFor()
      if ((await ratio().inputValue()) !== '1.25') throw new Error(`saved ratio reads ${await ratio().inputValue()}`)
      await ratio().scrollIntoViewIfNeeded(); await shot('2-set')
    })
    await step('3-refused', async () => {
      await ratio().fill('125'); await ratio().press('Tab'); await save()
      await page.getByText(/125 % is 1\.25/).first().waitFor()
      await shot('3-refused')
      await page.getByRole('button', { name: 'Cancel', exact: true }).last().click().catch(() => {})
      await page.waitForTimeout(6000)                                  // the refusal toast fades
    })
    await step('4-apply-refused', async () => {
      await page.evaluate(async (gen) => {
        const { default: client } = await import('/src/api/client.ts')
        await client.patch('/network/_bulk', { component_class: 'Generator', names: [gen], updates: { dc_ac_ratio: '' } })
      }, GEN)
      // The real list, with utility PV relabelled per MW of DC (no shipped row is DC).
      await page.route('**/api/library/references', async r => {
        const rows = await (await r.fetch()).json()
        await r.fulfill({ json: rows.map(x => x.technology === 'solar-utility'
          ? { ...x, capacity_basis_code: 'output_electric_dc', capacity_basis: 'per MW DC capacity' } : x) })
      })
      await page.route('**/api/library_reference/preview', r => r.fulfill({
        status: 422, contentType: 'application/json', body: JSON.stringify({ detail: REFUSAL }) }))
      await open()
      await page.getByTestId('asset-compare-references').first().click()
      const pick = page.getByRole('combobox', { name: /Reference/ }).first()
      await pick.waitFor()
      await pick.locator('option').nth(2).waitFor({ state: 'attached' })
      const label = (await pick.locator('option').allTextContents()).find(t => /^Utility-scale PV.*2030/.test(t))
      if (!label) throw new Error(`no PV reference in the drawer: ${(await pick.locator('option').allTextContents()).join(' | ')}`)
      await pick.selectOption({ label })
      await page.getByTestId('ref-apply-refused').waitFor()
      await shot('4-apply-refused')
      await page.unroute('**/api/library_reference/preview'); await page.unroute('**/api/library/references')
    })
    await ctx.close()
  }
} finally {
  await browser.close()
}
console.log(`wrote ${written.length}: ${written.join(', ')}`)
if (problems.length) { console.log(problems.join('\n')); process.exitCode = 1 }
